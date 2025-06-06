import subprocess
import time
import os
import frida
import json
import threading
# Removed old logging_manager import - now using unified logging system
from concurrent.futures import ThreadPoolExecutor
from src.utils.config import get_bluestacks_config, get_enable_logcat_logging, get_enable_pslist_logging, get_log_to_file

# Import new unified logging system
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_critical, log_debug, get_logging_manager
from src.managers.unified_logging_definitions import LogSource, LogLevel

# Old logging system has been replaced with UnifiedLoggingManager

class BlueStacksHelper:
    def __init__(self, adb_path, data_manager=None):
        self.adb_path = adb_path
        self.logcat_proc = None
        self.logcat_thread = None
        self.pslist_thread = None
        self._pslist_stop = threading.Event()
        
        # Get the unified logging manager
        self.logging_manager = get_logging_manager()
        
        # Add the data ingestion manager (used for InfluxDB writes, but logging now goes through ULM)
        self.data_manager = data_manager
        # Delay logging about data manager availability to avoid "No async loop" errors
        # The information will be logged when methods that use data_manager are actually called

    def run_adb_command(self, command_list, timeout=10, serial=None):
        """Uses subprocess.run to execute an ADB command. Optionally targets a specific device by serial."""
        command = [self.adb_path]
        if serial:
            command += ["-s", serial]
        command += command_list
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            return result
        except subprocess.TimeoutExpired as e:
            log_warning(LogSource.BLUESTACKS, "ADB command timed out", command=' '.join(command), timeout=timeout)
            return subprocess.CompletedProcess(command, timeout, stdout="", stderr=str(e))
        except FileNotFoundError:
            log_error(LogSource.BLUESTACKS, "ADB executable not found. Please check config.py.", adb_path=self.adb_path)
            return subprocess.CompletedProcess(command, -1, stdout="", stderr=f"ADB not found at {self.adb_path}")
        except Exception as e:
            log_error(LogSource.BLUESTACKS, "Unexpected error occurred with ADB command", command=' '.join(command), error=str(e))
            return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(e))

    def run_adb_shell_command(self, shell_command_str, as_root=False, timeout=10, serial=None):
        """Builds on run_adb_command. Prepends ["shell"] and if as_root, ["su", "-c", shell_command_str]."""
        if serial is None and hasattr(self, 'selected_serial'):
            serial = self.selected_serial
        command_list = ["shell"]
        if as_root:
            command_list.extend(["su", "-c", shell_command_str])
        else:
            command_list.extend(shell_command_str.split())
        return self.run_adb_command(command_list, timeout=timeout, serial=serial)

    def _restart_adb_server(self):
        """Restarts the ADB server."""
        log_info(LogSource.BLUESTACKS, "Restarting ADB server (adb kill-server && adb start-server)...")
        kill_result = self.run_adb_command(["kill-server"])
        log_info(LogSource.BLUESTACKS, "adb kill-server result", 
                 return_code=kill_result.returncode, 
                 stdout=kill_result.stdout.strip(), 
                 stderr=kill_result.stderr.strip())
        time.sleep(1)
        start_result = self.run_adb_command(["start-server"])
        log_info(LogSource.BLUESTACKS, "adb start-server result", 
                 return_code=start_result.returncode, 
                 stdout=start_result.stdout.strip(), 
                 stderr=start_result.stderr.strip())
        time.sleep(2)

    def _try_adb_connect_scan_ports(self, port_start=5555, port_end=5585):
        """Scans all odd ports in the range and attempts adb connect to each."""
        log_info(LogSource.BLUESTACKS, "Scanning odd ports for emulator adb connections", 
                 port_start=port_start, port_end=port_end)
        for port in range(port_start, port_end + 1, 2):
            addr = f"127.0.0.1:{port}"
            log_info(LogSource.BLUESTACKS, "Trying adb connect", address=addr)
            result = self.run_adb_command(["connect", addr])
            log_info(LogSource.BLUESTACKS, "adb connect result", 
                     address=addr,
                     return_code=result.returncode, 
                     stdout=result.stdout.strip(), 
                     stderr=result.stderr.strip())
            time.sleep(0.2)

    def _get_connected_devices(self):
        """Returns a list of (serial, state) tuples for connected devices. Tries to recover if none found."""
        result = self.run_adb_command(["devices"])
        devices = []
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().splitlines()
            for line in lines[1:]:
                if "\t" in line:
                    serial, state = line.split("\t")
                    if state == "device":
                        devices.append(serial)
        if not devices:
            log_warning(LogSource.BLUESTACKS, "No active devices found. Attempting to recover...")
            self._restart_adb_server()
            self._try_adb_connect_scan_ports()
            # Try again
            result = self.run_adb_command(["devices"])
            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().splitlines()
                for line in lines[1:]:
                    if "\t" in line:
                        serial, state = line.split("\t")
                        if state == "device":
                            devices.append(serial)
            if not devices:
                log_error(LogSource.BLUESTACKS, "Still no active devices found after ADB restart and port scan connect attempts.")
        return devices

    def _get_device_info(self, serial):
        """Returns a dict with serial, model, device name, and current foreground app for a device."""
        info = {"serial": serial}
        # Get model
        model_result = self.run_adb_shell_command("getprop ro.product.model", serial=serial)
        info["model"] = model_result.stdout.strip() if model_result.returncode == 0 else "?"
        # Get device name
        name_result = self.run_adb_shell_command("getprop ro.product.device", serial=serial)
        info["device_name"] = name_result.stdout.strip() if name_result.returncode == 0 else "?"
        # Get current foreground app (Android 5+)
        app_result = self.run_adb_shell_command(
            "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'", serial=serial)
        if app_result.returncode == 0 and app_result.stdout:
            # Try to extract package/activity
            import re
            match = re.search(r"([a-zA-Z0-9_.]+/[a-zA-Z0-9_.]+)", app_result.stdout)
            info["foreground_app"] = match.group(1) if match else app_result.stdout.strip()
        else:
            info["foreground_app"] = "?"
        return info

    def connect_to_emulator(self, host="127.0.0.1", port=None):
        log_info(LogSource.BLUESTACKS, "Checking for connected emulator device", host=host)
        devices = self._get_connected_devices()
        if not devices:
            log_info(LogSource.BLUESTACKS, "No active device found or device is offline after recovery attempts.")
            return False
        device_infos = [self._get_device_info(serial) for serial in devices]
        if len(devices) == 1:
            self.selected_serial = devices[0]
            log_info(LogSource.BLUESTACKS, "Connected to emulator", serial=self.selected_serial)
            log_info(LogSource.BLUESTACKS, "Device info", 
                     serial=device_infos[0]['serial'], 
                     model=device_infos[0]['model'], 
                     device_name=device_infos[0]['device_name'], 
                     foreground_app=device_infos[0]['foreground_app'])
            # Root check after selecting device
            if not self.is_rooted():
                log_error(LogSource.BLUESTACKS, "Selected emulator is not rooted. Root access is required.")
                return False
                
            # Add error handling for logging setup
            if get_enable_logcat_logging():
                try:
                    self._start_logcat_logging()
                    log_info(LogSource.BLUESTACKS, "Logcat logging started successfully.")
                except Exception as e:
                    log_error(LogSource.BLUESTACKS, "Failed to start logcat logging", error=str(e))
                    log_warning(LogSource.BLUESTACKS, "Continuing without logcat logging...")
            
            if get_enable_pslist_logging():
                try:
                    self._start_pslist_logging()
                    log_info(LogSource.BLUESTACKS, "Process list logging started successfully.")
                except Exception as e:
                    log_error(LogSource.BLUESTACKS, "Failed to start process list logging", error=str(e))
                    log_warning(LogSource.BLUESTACKS, "Continuing without process list logging...")
            else:
                log_info(LogSource.BLUESTACKS, "Process list logging is disabled in configuration.")
                
            return True

        # Always show the device list before prompting
        log_info(LogSource.BLUESTACKS, "Multiple emulators detected. Please select one:")
        for idx, info in enumerate(device_infos):
            log_info(LogSource.BLUESTACKS, f"Device option {idx+1}", 
                     serial=info['serial'], 
                     model=info['model'], 
                     device_name=info['device_name'], 
                     foreground_app=info['foreground_app'])
        log_info(LogSource.BLUESTACKS, "--- Device List Above ---")
        
        # Handle non-interactive environments or user input for multiple devices
        try:
            choice = input(f"Enter number (1-{len(device_infos)}): ").strip()
            if not choice.isdigit():
                log_warning(LogSource.BLUESTACKS, "Invalid input. Please enter a number.")
                 # Fallback to first device if input is not a digit in a potentially non-interactive script
                log_warning(LogSource.BLUESTACKS, "Using first device as default due to invalid input.")
                self.selected_serial = device_infos[0]["serial"]
            else:
                idx = int(choice) - 1
                if 0 <= idx < len(device_infos):
                    self.selected_serial = device_infos[idx]["serial"]
                else:
                    log_warning(LogSource.BLUESTACKS, "Choice out of range. Using first device as default.")
                    self.selected_serial = device_infos[0]["serial"]

        except EOFError:
            log_warning(LogSource.BLUESTACKS, "Input not available (EOFError). Using first device as default.")
            self.selected_serial = device_infos[0]["serial"]
        except Exception as e:
            log_error(LogSource.BLUESTACKS, "Error during device selection. Using first device as default.", error=str(e))
            self.selected_serial = device_infos[0]["serial"]

        log_info(LogSource.BLUESTACKS, "Selected device", serial=self.selected_serial)
        # Root check after selecting device
        if not self.is_rooted():
            log_error(LogSource.BLUESTACKS, "Selected emulator is not rooted. Root access is required.")
            return False
            
        # Add error handling for logging setup
        if get_enable_logcat_logging():
            try:
                self._start_logcat_logging()
                log_info(LogSource.BLUESTACKS, "Logcat logging started successfully.")
            except Exception as e:
                log_error(LogSource.BLUESTACKS, "Failed to start logcat logging", error=str(e))
                log_warning(LogSource.BLUESTACKS, "Continuing without logcat logging...")
        
        if get_enable_pslist_logging():
            try:
                self._start_pslist_logging()
                log_info(LogSource.BLUESTACKS, "Process list logging started successfully.")
            except Exception as e:
                log_error(LogSource.BLUESTACKS, "Failed to start process list logging", error=str(e))
                log_warning(LogSource.BLUESTACKS, "Continuing without process list logging...")
        else:
            log_info(LogSource.BLUESTACKS, "Process list logging is disabled in configuration.")
            
        return True

    def is_rooted(self):
        """Checks if the connected BlueStacks instance is rooted."""
        log_info(LogSource.BLUESTACKS, "Checking if instance is rooted...")
        result = self.run_adb_shell_command("whoami", as_root=True)
        if result.returncode == 0 and result.stdout and "root" in result.stdout.strip().lower():
            log_info(LogSource.BLUESTACKS, "Instance is rooted.")
            return True
        else:
            log_warning(LogSource.BLUESTACKS, "Instance is not rooted, or 'whoami' failed", 
                        stdout=result.stdout.strip(), 
                        stderr=result.stderr.strip())
            return False 

    def push_frida_server(self, local_frida_path, remote_frida_path):
        """Pushes the Frida server to the device."""
        serial = getattr(self, 'selected_serial', None)
        log_info(LogSource.BLUESTACKS, "Pushing Frida server", 
                 local_path=local_frida_path, 
                 remote_path=remote_frida_path)
        result = self.run_adb_command(["push", local_frida_path, remote_frida_path], serial=serial)
        if result.returncode == 0:
            log_info(LogSource.BLUESTACKS, "Frida server pushed successfully.")
        else:
            log_warning(LogSource.BLUESTACKS, "Failed to push Frida server", stderr=result.stderr.strip())
        return result

    def start_frida_server(self, remote_frida_path):
        """Starts the Frida server on the device."""
        serial = getattr(self, 'selected_serial', None)
        log_info(LogSource.BLUESTACKS, "Attempting to start Frida server", frida_path=remote_frida_path, serial=serial)
        chmod_result = self.run_adb_shell_command(f"chmod 755 {remote_frida_path}", as_root=True, serial=serial)
        if chmod_result.returncode != 0:
            log_warning(LogSource.BLUESTACKS, "Error making Frida server executable", error=chmod_result.stderr, frida_path=remote_frida_path)
            return None
        start_command = f"nohup {remote_frida_path} > /dev/null 2>&1 &"
        start_result = self.run_adb_shell_command(start_command, as_root=True, serial=serial)
        if start_result.returncode != 0 and "already running" not in start_result.stderr.lower():
            log_warning(LogSource.BLUESTACKS, "Error starting Frida server", error=start_result.stderr, command=start_command)
            return None
        log_info(LogSource.BLUESTACKS, "Frida server start command issued. Verifying responsiveness...")
        time.sleep(3)
        try:
            if serial:
                device = frida.get_device(serial, timeout=10)
            else:
                device = frida.get_usb_device(timeout=10)
            apps = device.enumerate_applications()
            log_info(LogSource.BLUESTACKS, "Frida server responsive", app_count=len(apps))
            return device
        except frida.TimedOutError:
            log_warning(LogSource.BLUESTACKS, "Frida server verification timed out (default realm). It might not have started correctly.")
        except frida.NotSupportedError as e:
            log_warning(LogSource.BLUESTACKS, "Frida error during verification (default realm)", error=str(e))
        except frida.TransportError as e:
            log_warning(LogSource.BLUESTACKS, "Frida transport error during verification (default realm)", error=str(e))
        except Exception as e:
            log_error(LogSource.BLUESTACKS, "Unexpected error during Frida server verification (default realm)", error=str(e))
        ps_output = self.run_adb_shell_command("ps -A | grep frida-server", as_root=True, serial=serial)
        if remote_frida_path in ps_output.stdout or "frida-server" in ps_output.stdout:
            log_info(LogSource.BLUESTACKS, "Frida server process found via 'ps' command", ps_output=ps_output.stdout.strip())
            log_warning(LogSource.BLUESTACKS, "Warning: Could not verify responsiveness via Frida API, but process seems to be running. Returning None for device object.")
            return None
        else:
            log_warning(LogSource.BLUESTACKS, "Frida server does not appear to be running after start attempt (verified by ps).")
        return None

    def stop_frida_server(self, remote_frida_path_or_name="frida-server"):
        """Stops the Frida server on the device using pkill."""
        serial = getattr(self, 'selected_serial', None)
        log_info(LogSource.BLUESTACKS, "Attempting to stop Frida server", target=remote_frida_path_or_name, serial=serial)
        try:
            # First, check if the process is running at all
            ps_output = self.run_adb_shell_command("ps -A | grep frida-server", as_root=True, serial=serial, timeout=5)
            if ps_output.returncode != 0 or not ps_output.stdout.strip():
                log_info(LogSource.BLUESTACKS, "No Frida server process found running. Nothing to stop.")
                return True
            
            # Process found, try to kill it
            import threading
            import time
            
            command_executed = False
            command_failed = False
            result_container = [None]
            
            def run_command_with_timeout():
                try:
                    result = self.run_adb_shell_command(
                        f"pkill -f {os.path.basename(remote_frida_path_or_name)}", 
                        as_root=True, 
                        timeout=5,  # Reduced timeout
                        serial=serial
                    )
                    result_container[0] = result
                    nonlocal command_executed
                    command_executed = True
                except Exception as e:
                    log_error(LogSource.BLUESTACKS, "Error in pkill command thread", error=str(e))
                    nonlocal command_failed
                    command_failed = True
            
            # Run the command in a separate thread
            command_thread = threading.Thread(target=run_command_with_timeout)
            command_thread.daemon = True  # Allow the thread to be killed if the program exits
            command_thread.start()
            
            # Wait for command with our own timeout
            timeout_sec = 7
            start_time = time.time()
            while not command_executed and not command_failed and (time.time() - start_time) < timeout_sec:
                time.sleep(0.1)  # Short sleep to avoid high CPU
            
            if not command_executed:
                log_warning(LogSource.BLUESTACKS, "pkill command timed out", timeout_sec=timeout_sec)
                return False
            
            result = result_container[0]
            if result and result.returncode == 0:
                log_info(LogSource.BLUESTACKS, "Successfully sent pkill command", target=remote_frida_path_or_name)
                return True
            else:
                if result and "no process found" in result.stderr.lower() or (result and result.returncode == 1):
                    log_info(LogSource.BLUESTACKS, "Frida server was not running or pkill found no match", target=remote_frida_path_or_name)
                    return True
                else:
                    log_warning(LogSource.BLUESTACKS, "pkill command may have failed", 
                               target=remote_frida_path_or_name,
                               return_code=result.returncode if result else 'N/A',
                               stderr=result.stderr.strip() if result and result.stderr else 'N/A')
                    return False
        except Exception as e:
            log_error(LogSource.BLUESTACKS, "Error during stop_frida_server with pkill", error=str(e))
            log_warning(LogSource.BLUESTACKS, "Continuing despite error in stop_frida_server...")
            return False

    def check_frida_server_status(self, remote_frida_path):
        """
        Checks if Frida server is installed, running, and what version it is.
        
        Args:
            remote_frida_path (str): Path to the Frida server on the device
            
        Returns:
            dict: A dictionary containing status information:
                - installed (bool): True if frida-server exists on the device
                - running (bool): True if frida-server is currently running
                - version (str or None): Version string of the frida-server or None if not available
        """
        serial = getattr(self, 'selected_serial', None)
        log_info(LogSource.BLUESTACKS, "Checking Frida server status", frida_path=remote_frida_path, serial=serial)
        
        # Prepare result dictionary
        result = {
            "installed": False,
            "running": False,
            "version": None
        }
        
        # Check if frida-server exists on the device
        ls_result = self.run_adb_shell_command(f"ls -l {remote_frida_path}", as_root=True, serial=serial, timeout=5)
        result["installed"] = ls_result.returncode == 0 and remote_frida_path in ls_result.stdout
        
        # Check if frida-server is running
        ps_result = self.run_adb_shell_command("ps -A | grep frida-server", as_root=True, serial=serial, timeout=5)
        result["running"] = ps_result.returncode == 0 and ps_result.stdout.strip() != ""
        
        # Get frida-server version if installed
        if result["installed"]:
            try:
                # Try to get version using the --version flag
                version_result = self.run_adb_shell_command(f"{remote_frida_path} --version", as_root=True, serial=serial, timeout=5)
                if version_result.returncode == 0 and version_result.stdout.strip():
                    result["version"] = version_result.stdout.strip()
                else:
                    # If the server is running but --version doesn't work, try to infer from process listing
                    log_info(LogSource.BLUESTACKS, "Could not get version using --version flag. Trying alternative method...")
                    if result["running"]:
                        # If the server is running, try to connect using Frida client and get version from there
                        try:
                            if serial:
                                device = frida.get_device(serial, timeout=5)
                            else:
                                device = frida.get_usb_device(timeout=5)
                                
                            # If we can connect to the device, it's likely that the Frida server is compatible
                            # with the current client. Let's get the client version and assume server is similar
                            result["version"] = frida.__version__
                            log_info(LogSource.BLUESTACKS, "Inferred Frida server version from client", version=result['version'])
                        except Exception as e:
                            log_warning(LogSource.BLUESTACKS, "Could not infer Frida server version from client", error=str(e))
            except Exception as e:
                log_warning(LogSource.BLUESTACKS, "Error checking Frida server version", error=str(e))
        
        log_info(LogSource.BLUESTACKS, "Frida server status check complete", 
                installed=result['installed'], 
                running=result['running'], 
                version=result['version'])
        return result

    def ensure_frida_server(self, local_frida_path, remote_frida_path, expected_version, timeout=10):
        """
        Ensures that the correct version of Frida server is installed and running on the device.
        
        This method checks if Frida server is installed, if it's the correct version,
        and if it's running. It will stop, push, and start the server as needed.
        
        Args:
            local_frida_path (str): Path to the local Frida server binary
            remote_frida_path (str): Path where Frida server should be installed on the device
            expected_version (str): Expected version string of the Frida server
            timeout (int, optional): Timeout in seconds for various operations. Defaults to 10.
            
        Returns:
            dict: A dictionary containing status information about the operation:
                - success (bool): True if the operation was successful
                - installed (bool): True if Frida server is installed
                - running (bool): True if Frida server is running
                - version (str): Version of the installed Frida server
                - device (frida.Device or None): Frida device object if successfully connected
                - error (str or None): Error message if any error occurred
        """
        import concurrent.futures
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        
        result = {
            "success": False,
            "installed": False,
            "running": False,
            "version": None,
            "device": None,
            "error": None
        }
        
        try:
            # Check current server status
            status = self.check_frida_server_status(remote_frida_path)
            
            result["installed"] = status["installed"]
            result["running"] = status["running"]
            result["version"] = status["version"]
            
            log_info(LogSource.BLUESTACKS, "Frida server status", 
                installed=status["installed"],
                running=status["running"],
                device_version=status["version"],
                expected_version=expected_version)
            
            # Determine required actions
            need_to_push = not status["installed"] or (
                status["version"] and expected_version not in status["version"]
            )
            need_to_restart = (need_to_push and status["running"]) or (
                status["installed"] and not status["running"]
            )
            
            # Stop server if needed
            if need_to_restart and status["running"]:
                log_info(LogSource.BLUESTACKS, "Stopping existing Frida server", frida_path=remote_frida_path)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    try:
                        future = executor.submit(self.stop_frida_server, remote_frida_path)
                        stop_result = future.result(timeout=timeout)
                        if stop_result:
                            log_info(LogSource.BLUESTACKS, "Existing Frida server stopped.")
                        else:
                            log_warning(LogSource.BLUESTACKS, "Failed to stop Frida server. Will try to continue.")
                    except TimeoutError:
                        log_warning(LogSource.BLUESTACKS, "stop_frida_server operation timed out", timeout=timeout)
                    except Exception as e:
                        log_error(LogSource.BLUESTACKS, "Error stopping Frida server", error=str(e))
                        log_warning(LogSource.BLUESTACKS, "Continuing despite Frida server stop failure...")
            
            # Push server if needed
            if need_to_push:
                log_info(LogSource.BLUESTACKS, "Pushing Frida server", 
                        local_path=local_frida_path, remote_path=remote_frida_path)
                push_result = self.push_frida_server(local_frida_path, remote_frida_path)
                if push_result and push_result.returncode == 0:
                    log_info(LogSource.BLUESTACKS, "Frida server pushed successfully.")
                    result["installed"] = True
                else:
                    log_warning(LogSource.BLUESTACKS, "Push operation failed. Will try to continue anyway.")
            
            # Start server if needed
            if need_to_restart or need_to_push:
                log_info(LogSource.BLUESTACKS, "Starting Frida server on device", frida_path=remote_frida_path)
                try:
                    device = self.start_frida_server(remote_frida_path)
                    if device:
                        result["device"] = device
                        result["running"] = True
                    else:
                        # Check if server is running despite not getting a device object
                        updated_status = self.check_frida_server_status(remote_frida_path)
                        result["running"] = updated_status["running"]
                except Exception as e:
                    log_error(LogSource.BLUESTACKS, "Error starting Frida server", error=str(e))
                    log_warning(LogSource.BLUESTACKS, "Continuing despite Frida server start failure...")
                    result["error"] = f"Error starting Frida server: {str(e)}"
            else:
                # Server is already running with correct version
                result["success"] = True
                
                # Try to get a device object for the running server
                try:
                    serial = self.get_selected_serial()
                    if serial:
                        device = frida.get_device(serial, timeout=timeout)
                        result["device"] = device
                except Exception as e:
                    log_warning(LogSource.BLUESTACKS, "Could not get Frida device object for running server", error=str(e))
            
            # Update success flag based on current state
            if result["installed"] and result["running"]:
                result["success"] = True
            
            return result
            
        except Exception as e:
            log_error(LogSource.BLUESTACKS, "Error in ensure_frida_server", error=str(e))
            result["error"] = str(e)
            return result

    def list_processes_basic(self):
        """Runs 'adb shell ps -A' and returns a list of raw process lines."""
        serial = getattr(self, 'selected_serial', None)
        log_info(LogSource.BLUESTACKS, "Listing all processes on device (ps -A)", serial=serial)
        process_result = self.run_adb_shell_command("ps -A", timeout=20, serial=serial)
        if process_result and process_result.returncode == 0 and process_result.stdout:
            line_count = len(process_result.stdout.strip().splitlines())
            log_info(LogSource.BLUESTACKS, "'ps -A' successful", output_lines=line_count)
            return process_result.stdout.strip().splitlines()
        else:
            log_warning(LogSource.BLUESTACKS, "Error running 'ps -A'", 
                       stderr=process_result.stderr if process_result else 'N/A')
            return []

    def list_processes_parsed(self):
        """Runs 'adb shell ps -A' and returns a list of parsed process dictionaries."""
        serial = getattr(self, 'selected_serial', None)
        log_info(LogSource.BLUESTACKS, "Listing and parsing processes on device (ps -A)", serial=serial)
        process_result = self.run_adb_shell_command("ps -A", timeout=20, serial=serial)
        
        if not process_result or process_result.returncode != 0 or not process_result.stdout:
            log_warning(LogSource.BLUESTACKS, "Error running 'ps -A'", 
                       stderr=process_result.stderr if process_result else 'N/A')
            return []
        
        # Parse ps output into structured data
        process_list = []
        lines = process_result.stdout.strip().split('\n')
        
        if not lines:
            return []
            
        # First line is header
        header = lines[0].strip().split()
        
        # Process each process entry
        for line_idx, line in enumerate(lines[1:], 1):
            parts = line.strip().split(None, len(header)-1)
            if len(parts) >= len(header):
                # Create dictionary with header keys mapped to values
                process_data = {header[i].lower(): parts[i] for i in range(len(header))}
                process_list.append(process_data)
            else:
                # Handle malformed lines by skipping them
                log_warning(LogSource.BLUESTACKS, "Skipping malformed ps output line", 
                           line_number=line_idx, raw_line=line.strip())
        
        log_info(LogSource.BLUESTACKS, f"Parsed {len(process_list)} processes from ps output")
        return process_list

    def get_pid_for_package(self, package_name):
        """Gets the PID for a given package name using 'pidof'. Returns PID as int or None."""
        serial = getattr(self, 'selected_serial', None)
        if not package_name:
            log_error(LogSource.BLUESTACKS, "Error: package_name cannot be empty for get_pid_for_package.")
            return None
        
        log_info(LogSource.BLUESTACKS, "Attempting to get PID for package", 
                package_name=package_name, log_event="pid.attempt", method="pidof")
        pidof_command = f"pidof {package_name}"
        pid_result = self.run_adb_shell_command(pidof_command, as_root=True, timeout=10, serial=serial)

        if pid_result and pid_result.returncode == 0 and pid_result.stdout:
            pid_str = pid_result.stdout.strip()
            pids = pid_str.split()
            if pids:
                try:
                    pid = int(pids[0])
                    log_info(LogSource.BLUESTACKS, "Found PID for package", 
                            pid=pid, package_name=package_name, log_event="pid.found", method="pidof")
                    return pid
                except ValueError:
                    log_warning(LogSource.BLUESTACKS, "Error: Could not convert PID to int for package", 
                               pid_str=pids[0], package_name=package_name, log_event="pid.conversion_error", method="pidof")
                    return None
            else:
                log_warning(LogSource.BLUESTACKS, "No PID found in 'pidof' output for package", 
                           package_name=package_name, output=pid_str, log_event="pid.not_found", method="pidof")
                return None
        else:
            log_warning(LogSource.BLUESTACKS, "'pidof' failed or returned no output. Trying fallback with 'ps -A'", 
                       package_name=package_name, log_event="pid.pidof_failed", method="pidof")
            all_processes = self.list_processes_basic()
            if not all_processes:
                log_warning(LogSource.BLUESTACKS, "Fallback failed: Could not list processes", 
                           package_name=package_name, log_event="pid.fallback_failed_list_processes")
                return None
            
            found_pid = None
            for line in all_processes:
                if package_name in line:
                    parts = line.split()
                    if len(parts) > 1:
                        try:
                            if parts[-1] == package_name or parts[-1].startswith(package_name + ":"):
                                pid_candidate = parts[1]
                                pid = int(pid_candidate)
                                log_info(LogSource.BLUESTACKS, "Fallback found PID for package", 
                                        pid=pid, package_name=package_name, line=line, log_event="pid.found", method="ps_fallback")
                                found_pid = pid
                                break
                        except (ValueError, IndexError):
                            continue
            
            if found_pid:
                return found_pid
            else:
                log_warning(LogSource.BLUESTACKS, "Could not find PID for package using 'pidof' or fallback 'ps -A'", 
                           package_name=package_name, log_event="pid.not_found", method="ps_fallback")
                return None

    def get_selected_serial(self):
        """Returns the currently selected emulator serial, or None if not set."""
        return getattr(self, 'selected_serial', None)

    def _start_logcat_logging(self):
        """Starts adb logcat logging using the unified logging system."""
        if self.logcat_proc is not None:
            log_info(LogSource.LOGCAT, "Logcat logging already started for this instance.")
            return
        serial = getattr(self, 'selected_serial', None)
        if not serial:
            log_warning(LogSource.LOGCAT, "No selected serial for logcat logging.")
            return
        
        log_info(LogSource.LOGCAT, "Starting adb logcat via unified logging manager", serial=serial)
        
        def logcat_thread_func():
            
            try:
                proc = subprocess.Popen(
                    [self.adb_path, '-s', serial, 'logcat', '-v', 'threadtime'], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                self.logcat_proc = proc
                
                # Process each line from logcat
                for line in iter(proc.stdout.readline, ''):
                    try:
                        # Parse logcat line into components
                        # Format: date time PID TID priority tag: message
                        parts = line.strip().split(None, 6)
                        if len(parts) >= 7:
                            date, time_str, pid, tid, priority, tag, message = parts
                            
                            # Create the log message for unified logging
                            log_message = f"[{priority}] {tag}: {message}"
                            
                            # Send to unified logging manager
                            log_info(LogSource.LOGCAT, log_message,
                                    timestamp=f"{date} {time_str}",
                                    pid=pid,
                                    tid=tid,
                                    priority=priority,
                                    tag=tag,
                                    device=serial,
                                    type="logcat",
                                    parsed=True)
                        else:
                            # Handle unparseable lines
                            log_info(LogSource.LOGCAT, f"[RAW] {line.strip()}",
                                    device=serial,
                                    type="logcat",
                                    raw=True,
                                    parsed=False)
                    except Exception as e:
                        # Log any errors in processing
                        log_error(LogSource.LOGCAT, f"Logcat processing error: {str(e)}",
                                 device=serial,
                                 error=str(e),
                                 type="logcat_error")
                        
                # Wait for process to finish
                proc.wait()
            except Exception as e:
                log_error(LogSource.LOGCAT, f"Logcat subprocess error: {str(e)}", device=serial, error=str(e))
        
        t = threading.Thread(target=logcat_thread_func, daemon=True)
        t.start()
        self.logcat_thread = t

    def _start_pslist_logging(self):
        """Starts periodic adb shell ps -A logging using the unified logging system."""
        if self.pslist_thread is not None and self.pslist_thread.is_alive():
            log_info(LogSource.PSLIST, "pslist logging already started for this instance.")
            return
        serial = getattr(self, 'selected_serial', None)
        if not serial:
            log_warning(LogSource.PSLIST, "No selected serial for pslist logging.")
            return
        
        log_info(LogSource.PSLIST, "Starting periodic ps -A logging via unified logging manager", serial=serial)
        self._pslist_stop.clear()
        
        def pslist_thread_func():
            
            try:
                while not self._pslist_stop.is_set():
                    from datetime import datetime, timezone
                    import time
                    
                    timestamp_iso = datetime.now(timezone.utc).isoformat()
                    timestamp_unix = time.time()
                    
                    try:
                        result = subprocess.run(
                            [self.adb_path, '-s', serial, 'shell', 'ps', '-A'], 
                            capture_output=True, 
                            text=True, 
                            timeout=10
                        )
                        
                        # Process ps output into structured data
                        process_list = []
                        lines = result.stdout.strip().split('\n')
                        
                        # Parse header and data
                        if lines:
                            # First line is header
                            header = lines[0].strip().split()
                            
                            # Process each process entry
                            for line_idx, line in enumerate(lines[1:], 1):
                                parts = line.strip().split(None, len(header)-1)
                                if len(parts) >= len(header):
                                    process_data = {header[i].lower(): parts[i] for i in range(len(header))}
                                    process_list.append(process_data)
                                else:
                                    # Handle malformed lines
                                    process_list.append({
                                        "raw_line": line.strip(),
                                        "line_num": line_idx,
                                        "malformed": True
                                    })
                        
                        # Log summary entry first (for dashboard overview)
                        log_info(LogSource.PSLIST, f"Process list snapshot: {len(process_list)} processes",
                                device=serial,
                                process_count=len(process_list),
                                type="pslist_summary")
                        
                        # Log individual process entries
                        for process in process_list:
                            if not process.get('malformed', False):
                                process_name = process.get('name', 'unknown')
                                process_pid = process.get('pid', '0')
                                process_rss = process.get('rss', '0')
                                
                                process_message = f"Process {process_name} (PID: {process_pid}) - RSS: {process_rss}KB"
                                
                                log_info(LogSource.PSLIST, process_message,
                                        device=serial,
                                        type="pslist_process",
                                        process_name=process_name,
                                        process_pid=int(process_pid) if process_pid.isdigit() else 0,
                                        process_user=process.get('user', 'unknown'),
                                        process_ppid=int(process.get('ppid', '0')) if process.get('ppid', '0').isdigit() else 0,
                                        process_rss=int(process_rss) if process_rss.isdigit() else 0,
                                        process_vsz=int(process.get('vsz', '0')) if process.get('vsz', '0').isdigit() else 0,
                                        process_state=process.get('s', 'unknown'))
                            else:
                                # Log malformed entries as warnings
                                malformed_message = f"Malformed ps output line: {process.get('raw_line', 'unknown')}"
                                log_warning(LogSource.PSLIST, malformed_message,
                                           device=serial,
                                           type="pslist_error",
                                           raw_line=process.get('raw_line', ''),
                                           line_number=process.get('line_num', 0))
                        
                    except Exception as e:
                        # Log any errors via unified logging
                        log_error(LogSource.PSLIST, f"PSList processing error: {str(e)}",
                                 device=serial,
                                 error=str(e),
                                 type="pslist_error")
                        
                    time.sleep(1)
            except Exception as e:
                log_error(LogSource.PSLIST, f"PSList thread error: {str(e)}", device=serial, error=str(e))
                    
        t = threading.Thread(target=pslist_thread_func, daemon=True)
        t.start()
        self.pslist_thread = t


if __name__ == '__main__':
    # Get BLUESTACKS_ADB_PATH from centralized config
    bluestacks_config = get_bluestacks_config()
    BLUESTACKS_ADB_PATH = bluestacks_config['adb_path']

    # Example usage:
    helper = BlueStacksHelper(BLUESTACKS_ADB_PATH)

    log_info(LogSource.BLUESTACKS, "--- Testing ADB Connection ---")
    if helper.connect_to_emulator():
        log_info(LogSource.BLUESTACKS, "Emulator connection successful.")

        log_info(LogSource.BLUESTACKS, "--- Testing Root Check ---")
        # if helper.is_rooted():
        #     log_info(LogSource.BLUESTACKS, "Emulator is rooted.")
        # else:
        #     log_info(LogSource.BLUESTACKS, "Emulator is NOT rooted.")

        # Assuming you have a frida-server binary for testing push/start/stop
        # You might need to download one manually or use DependencyDownloader for a full test
        # For this isolated test, we'll assume a placeholder path
        mock_frida_server_local_path = "./mock_frida_server"
        if not os.path.exists(mock_frida_server_local_path):
            with open(mock_frida_server_local_path, "w") as f:
                f.write("this is a mock frida server")
            log_info(LogSource.BLUESTACKS, "Created mock frida server for testing", path=mock_frida_server_local_path)
        
        remote_path = "/data/local/tmp/test-frida-server"

        log_info(LogSource.BLUESTACKS, "--- Testing Frida Server Push ---")
        helper.push_frida_server(mock_frida_server_local_path, remote_path)

        log_info(LogSource.BLUESTACKS, "--- Testing Frida Server Start ---")
        # This will likely fail to verify with frida API if it's a mock binary, but will test ADB commands
        helper.start_frida_server(remote_path)

        log_info(LogSource.BLUESTACKS, "--- Testing PID Fetch for a common package (e.g., settings) ---")
        # Replace with a package name you know is running on your emulator
        # For example, the default Android settings app package name often is "com.android.settings"
        # or a game like "com.TechTreeGames.TheTower"
        test_package = "com.android.settings" 
        pid = helper.get_pid_for_package(test_package)
        if pid:
            log_info(LogSource.BLUESTACKS, "Found PID for test package", package=test_package, pid=pid)
        else:
            log_warning(LogSource.BLUESTACKS, "Could not get PID for test package", package=test_package)
        
        # Example: Get PID for a non-existent package
        # log_info(LogSource.BLUESTACKS, "--- Testing PID Fetch for a non-existent package ---")
        # non_existent_pid = helper.get_pid_for_package("com.example.nonexistent")
        # if non_existent_pid is None:
        #     log_info(LogSource.BLUESTACKS, "Correctly failed to get PID for non-existent package.")
        # else:
        #     log_info(LogSource.BLUESTACKS, "Incorrectly found PID for non-existent package", pid=non_existent_pid)

        log_info(LogSource.BLUESTACKS, "--- Testing Frida Server Stop ---")
        helper.stop_frida_server(remote_path)

        # Clean up mock server if created
        if os.path.exists(mock_frida_server_local_path):
            os.remove(mock_frida_server_local_path)
            log_info(LogSource.BLUESTACKS, "Cleaned up mock frida server", path=mock_frida_server_local_path)

    else:
        log_info(LogSource.BLUESTACKS, "Emulator connection failed. Cannot run further tests.")

    log_info(LogSource.BLUESTACKS, "--- BlueStacksHelper tests completed. ---") 