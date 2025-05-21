import subprocess
import time
import os
import frida

class BlueStacksHelper:
    def __init__(self, adb_path):
        self.adb_path = adb_path

    def run_adb_command(self, command_list, timeout=10):
        """Uses subprocess.run to execute an ADB command."""
        command = [self.adb_path] + command_list
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            return result
        except subprocess.TimeoutExpired as e:
            print(f"ADB command timed out: {' '.join(command)}")
            return subprocess.CompletedProcess(command, timeout, stdout="", stderr=str(e))
        except FileNotFoundError:
            print(f"Error: ADB executable not found at {self.adb_path}. Please check config.py.")
            return subprocess.CompletedProcess(command, -1, stdout="", stderr=f"ADB not found at {self.adb_path}")
        except Exception as e:
            print(f"An unexpected error occurred with ADB command: {' '.join(command)}. Error: {e}")
            return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(e))

    def run_adb_shell_command(self, shell_command_str, as_root=False, timeout=10):
        """Builds on run_adb_command. Prepends ["shell"] and if as_root, ["su", "-c", shell_command_str]."""
        command_list = ["shell"]
        if as_root:
            # For 'su -c "command with spaces"', the command needs to be a single string argument to su.
            command_list.extend(["su", "-c", shell_command_str])
        else:
            # For simple shell commands, splitting helps if shell_command_str has multiple parts.
            # However, adb shell itself handles the string fine.
            command_list.extend(shell_command_str.split())
        
        return self.run_adb_command(command_list, timeout=timeout)

    def connect_to_emulator(self, host="127.0.0.1", port=None):
        """Checks if a device is connected and in 'device' state."""
        # Simplified: Assumes user has run HD-Adb connect if needed, or it's auto-connected.
        # For this task, we just run 'adb devices' and check the output.
        # A more robust version might parse BlueStacks config or use `adb connect host:port`.
        # The 'port' argument is kept for future extensibility but not used in this simplified version.
        print(f"Checking for connected emulator device (host: {host})...")
        result = self.run_adb_command(["devices"])
        if result.returncode == 0 and result.stdout:
            print(result.stdout) # Print the output of 'adb devices'
            lines = result.stdout.strip().splitlines()
            # Skip the header line "List of devices attached"
            for line in lines[1:]:
                if "\tdevice" in line and not "offline" in line:
                    print(f"Connected to: {line.split()[0]}")
                    return True
            print("No active device found or device is offline.")
            return False
        else:
            print(f"Failed to execute 'adb devices'. STDERR: {result.stderr}")
            return False

    def is_rooted(self):
        """Checks if the connected BlueStacks instance is rooted."""
        print("Checking if instance is rooted...")
        result = self.run_adb_shell_command("whoami", as_root=True)
        if result.returncode == 0 and result.stdout and "root" in result.stdout.strip().lower():
            print("Instance is rooted.")
            return True
        else:
            print(f"Instance is not rooted, or 'whoami' failed. STDOUT: {result.stdout.strip()}, STDERR: {result.stderr.strip()}")
            return False 

    def push_frida_server(self, local_frida_path, remote_frida_path):
        """Pushes the Frida server to the device."""
        print(f"Pushing Frida server from {local_frida_path} to {remote_frida_path}...")
        result = self.run_adb_command(["push", local_frida_path, remote_frida_path])
        if result.returncode == 0:
            print("Frida server pushed successfully.")
        else:
            print(f"Failed to push Frida server. STDERR: {result.stderr.strip()}")
        return result

    def start_frida_server(self, remote_frida_path):
        print(f"Attempting to start Frida server from {remote_frida_path}...")
        chmod_result = self.run_adb_shell_command(f"chmod 755 {remote_frida_path}", as_root=True)
        if chmod_result.returncode != 0:
            print(f"Error making Frida server executable: {chmod_result.stderr}")
            return None

        start_command = f"nohup {remote_frida_path} > /dev/null 2>&1 &"
        start_result = self.run_adb_shell_command(start_command, as_root=True)
        if start_result.returncode != 0 and "already running" not in start_result.stderr.lower():
            print(f"Error starting Frida server: {start_result.stderr}")
            return None
        
        print("Frida server start command issued. Verifying responsiveness...")
        time.sleep(3) # Give it a moment to start up

        try:
            device = frida.get_usb_device(timeout=10) # Use default realm
            apps = device.enumerate_applications()
            print(f"Frida server responsive. Found {len(apps)} applications.")
            return device
        except frida.TimedOutError:
            print("Frida server verification timed out (default realm). It might not have started correctly.")
        except frida.NotSupportedError as e:
            print(f"Frida error during verification (default realm): {e}")
        except frida.TransportError as e:
            print(f"Frida transport error during verification (default realm): {e}.")
        except Exception as e:
            print(f"An unexpected error occurred during Frida server verification (default realm): {e}")
        
        # Fallback PS check if Frida API verification fails
        ps_output = self.run_adb_shell_command("ps -A | grep frida-server", as_root=True)
        if remote_frida_path in ps_output.stdout or "frida-server" in ps_output.stdout:
            print(f"Frida server process found via 'ps' command: {ps_output.stdout.strip()}")
            print("Warning: Could not verify responsiveness via Frida API, but process seems to be running. Returning None for device object.")
            return None 
        else:
            print("Frida server does not appear to be running after start attempt (verified by ps).")
        
        return None

    def stop_frida_server(self, remote_frida_path_or_name="frida-server"):
        """Stops the Frida server on the device using pkill."""
        print(f"Attempting to stop Frida server (pkill -f {remote_frida_path_or_name})...")
        # It's possible pkill is not available or Frida server is not running.
        # We can make this more robust by checking output, but for now, it's a best-effort stop.
        try:
            # result = self.run_adb_shell_command(f"pkill -f {remote_frida_path_or_name}", as_root=True, timeout=5)
            # A more reliable way might be to find PID then kill, but pkill is simpler if available.
            # Example: pid_cmd_output = self.run_adb_shell_command(f"pidof {remote_frida_path_or_name}", as_root=True)
            # if pid_cmd_output.stdout.strip():
            #     pid = pid_cmd_output.stdout.strip().split()[0]
            #     self.run_adb_shell_command(f"kill -9 {pid}", as_root=True)
            # else:
            #     print(f"Frida server {remote_frida_path_or_name} not found running (pidof). ")
            # Using pkill as per original spec, but noting its limitations.
            result = self.run_adb_shell_command(f"pkill -f {os.path.basename(remote_frida_path_or_name)}", as_root=True, timeout=10)
            if result.returncode == 0:
                print(f"Successfully sent pkill command for {remote_frida_path_or_name}.")
                return True
            else:
                # 1 means no process found, which is fine. Other codes might be errors.
                if "no process found" in result.stderr.lower() or result.returncode == 1:
                    print(f"Frida server {remote_frida_path_or_name} was not running or pkill found no match.")
                    return True # Considered successful if not running or stopped
                else:
                    print(f"pkill command for {remote_frida_path_or_name} may have failed. RC: {result.returncode}, STDERR: {result.stderr.strip()}")
                    return False
        except Exception as e:
            print(f"Error during stop_frida_server with pkill: {e}")
            return False

    def list_processes_basic(self):
        """Runs 'adb shell ps -A' and returns a list of raw process lines."""
        print("Listing all processes on device (ps -A)...")
        # Use a longer timeout as ps -A can be lengthy
        process_result = self.run_adb_shell_command("ps -A", timeout=20) 
        if process_result and process_result.returncode == 0 and process_result.stdout:
            print(f"'ps -A' successful. Output lines: {len(process_result.stdout.strip().splitlines())}")
            return process_result.stdout.strip().splitlines()
        else:
            print(f"Error running 'ps -A'. stderr: {process_result.stderr if process_result else 'N/A'}")
            return []

    def get_pid_for_package(self, package_name):
        """Gets the PID for a given package name using 'pidof'. Returns PID as int or None."""
        if not package_name:
            print("Error: package_name cannot be empty for get_pid_for_package.")
            return None
        
        print(f"Attempting to get PID for package: {package_name} using 'pidof'...")
        # pidof might require root, but usually available on rooted emulators
        # Some systems might not have pidof. A fallback could be to parse `ps -A` output.
        pidof_command = f"pidof {package_name}"
        # Run as root because pidof might need it, or for restricted packages
        pid_result = self.run_adb_shell_command(pidof_command, as_root=True, timeout=10)

        if pid_result and pid_result.returncode == 0 and pid_result.stdout:
            pid_str = pid_result.stdout.strip()
            # pidof can return multiple PIDs space-separated, typically we want the first one (main process)
            pids = pid_str.split()
            if pids:
                try:
                    pid = int(pids[0])
                    print(f"Found PID {pid} for package {package_name}.")
                    return pid
                except ValueError:
                    print(f"Error: Could not convert PID '{pids[0]}' to int for package {package_name}.")
                    return None
            else:
                print(f"No PID found in 'pidof' output for package {package_name}. Output: '{pid_str}'")
                return None
        else:
            # Fallback: try ps -ef | grep <package_name> if pidof fails or is not available
            # This is more complex to parse reliably, so pidof is preferred.
            # stdout might be empty if process not found or pidof not present.
            # stderr might contain "pidof: not found" or other errors.
            # print(f"'pidof {package_name}' command failed or returned no output.")
            # if pid_result and pid_result.stderr:
            #     print(f"pidof stderr: {pid_result.stderr.strip()}")
            # Let's try with ps -A as a fallback
            print(f"'pidof {package_name}' failed or returned no output. Trying fallback with 'ps -A'...")
            all_processes = self.list_processes_basic()
            if not all_processes:
                print(f"Fallback failed: Could not list processes for {package_name}.")
                return None
            
            found_pid = None
            for line in all_processes:
                # A common format is USER PID PPID VSIZE RSS WCHAN ADDR S NAME
                # We are interested in the NAME (last column) and PID (second column typically)
                if package_name in line:
                    parts = line.split()
                    if len(parts) > 1: # Ensure there are enough parts for a PID
                        try:
                            # The process name (package_name) is often the last field.
                            # The PID is usually the second field after USER.
                            # Example line: u0_a169   12345 1826  198... S com.example.app
                            # Sometimes ps output is different, e.g. on some systems PID is the first column if USER is not shown.
                            # We need to be careful. Let's assume standard Android `ps -A` output which usually has USER first.
                            if parts[-1] == package_name or parts[-1].startswith(package_name + ":"):
                                pid_candidate = parts[1]
                                pid = int(pid_candidate)
                                print(f"Fallback found PID {pid} for package {package_name} in line: {line}")
                                found_pid = pid
                                break # Take the first match
                        except (ValueError, IndexError):
                            # print(f"Could not parse PID from line: {line}")
                            continue # Continue to next line
            
            if found_pid:
                return found_pid
            else:
                print(f"Could not find PID for package {package_name} using 'pidof' or fallback 'ps -A'.")
                return None


if __name__ == '__main__':
    # Conditional import for config when running directly
    if __package__ is None: # Script run directly
        import sys
        # Assuming config.py is in the same directory as blue_stacks_helper.py for direct execution
        # or one level up if blue_stacks_helper is in a subdirectory of the project root.
        # For this project structure, frida_tower_logger is the package dir.
        # So, to get to project root (where config might be if it's not in frida_tower_logger) :
        sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/..')
        try:
            import config # If config.py is at project root
        except ImportError:
            # If config.py is meant to be a module within frida_tower_logger
            # This case is tricky for direct execution without running as `python -m frida_tower_logger.blue_stacks_helper`
            # For simplicity, we'll assume a config.py exists at the project root for this test block.
            # Or, it's better to use absolute paths or known defaults for tests here.
            class MockConfig:
                BLUESTACKS_ADB_PATH = os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "BlueStacks_nxt", "HD-Adb.exe")
                # Add other necessary config vars if BlueStacksHelper depends on them for these tests.
            config = MockConfig()
            print("Warning: Using mock config for direct script execution as 'frida_tower_logger.config' might not be directly importable this way.")
    else: # Script imported as a module
        from . import config

    print("--- BlueStacksHelper Test --- (Task 10 focus)")
    helper = BlueStacksHelper(adb_path=config.BLUESTACKS_ADB_PATH)

    print("\n--- Testing connect_to_emulator and is_rooted (Prerequisites) ---")
    if not helper.connect_to_emulator():
        print("TEST SKIPPED: Could not connect to emulator. Ensure BlueStacks is running and ADB connected.")
        exit(1)
    if not helper.is_rooted():
        print("TEST SKIPPED: Emulator is not rooted. Root is required for some operations.")
        # We can still try list_processes_basic and get_pid_for_package (non-root part)
        # exit(1) # Don't exit, allow further tests

    print("\n--- BlueStacksHelper Process Listing Tests ---")
    # Prerequisite: BlueStacks and an emulator instance should be running.
    # The ADB path in config.py should be correct.
    # Example package name to test with (e.g., settings app, usually present)
    # You might need to adjust this if com.android.settings is not running or named differently.
    test_package_name = "com.TechTreeGames.TheTower"

    print(f"\nAttempting to connect to emulator for process tests...")
    if not helper.connect_to_emulator():
        print("Failed to connect to emulator. Process listing tests will be skipped.")
    else:
        print("Connected to emulator for process tests.")
        processes = helper.list_processes_basic()
        if processes:
            print(f"Successfully listed {len(processes)} process lines. First 5 lines:")
            for p_line in processes[:5]:
                print(p_line)
        else:
            print("Failed to list processes or no processes found.")

        print(f"\nAttempting to get PID for package: '{test_package_name}'")
        pid = helper.get_pid_for_package(test_package_name)
        if pid:
            print(f"PID for '{test_package_name}': {pid}")
            # Verification step you can do manually or by another adb command:
            # print(f"Verify with: adb shell ps -A | grep {test_package_name}")
            # print(f"Or: adb shell su -c \"pidof {test_package_name}\"")
        else:
            print(f"Could not get PID for '{test_package_name}'. It might not be running or not found.")
        
        # Test with a non-existent package
        non_existent_package = "com.example.nonexistent.app"
        print(f"\nAttempting to get PID for non-existent package: '{non_existent_package}'")
        pid_non_existent = helper.get_pid_for_package(non_existent_package)
        if pid_non_existent is None:
            print(f"Correctly failed to find PID for non-existent package '{non_existent_package}'.")
        else:
            print(f"Error: Found PID {pid_non_existent} for non-existent package '{non_existent_package}'. This is unexpected.")

    print("\nProcess listing tests finished.")

    print("\nBlueStacksHelper Task 10 tests finished.") 