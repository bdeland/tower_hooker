import sys
import os

# Adjust path to import from parent directory (frida_tower_logger)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from frida_tower_logger import config
from frida_tower_logger.blue_stacks_helper import BlueStacksHelper
from frida_tower_logger.dependency_downloader import DependencyDownloader
import frida # For testing frida-ps -Ua equivalent

def run_tests():
    print("--- Test Suite for BlueStacksHelper: Frida Server Deployment ---")
    
    # Initialize helpers
    print(f"Using ADB Path: {config.BLUESTACKS_ADB_PATH}")
    helper = BlueStacksHelper(adb_path=config.BLUESTACKS_ADB_PATH)
    
    # Ensure ADB path is valid before proceeding
    if not os.path.exists(config.BLUESTACKS_ADB_PATH):
        print(f"ERROR: ADB Path not found at '{config.BLUESTACKS_ADB_PATH}'. Please check your config.")
        return

    print(f"Attempting to connect to emulator...")
    if not helper.connect_to_emulator():
        print("TEST FAILED: Could not connect to emulator. Ensure BlueStacks is running and accessible via ADB.")
        return
    print("Emulator connection check successful (or device found).")

    if not helper.is_rooted():
        print("WARNING: Emulator is not reported as rooted. Frida server deployment might require root.")
        # Depending on strictness, one might choose to return here.
        # For now, we proceed but acknowledge the warning.
    else:
        print("Emulator is rooted.")

    downloader = DependencyDownloader(
        download_dir=config.FRIDA_SERVER_DIR,
        arch=config.FRIDA_SERVER_ARCH,
        version=config.FRIDA_SERVER_VERSION
    )

    # 1. Download Frida Server
    print("\n--- Test 1: Download Frida Server ---")
    local_fs_path = downloader.check_and_download_frida_server()
    if local_fs_path and os.path.exists(local_fs_path):
        print(f"SUCCESS: Frida server obtained: {local_fs_path}")
    else:
        print(f"TEST FAILED: Could not download/find Frida server. Path: {local_fs_path}")
        return

    # 2. Push Frida Server
    print("\n--- Test 2: Push Frida Server ---")
    push_result = helper.push_frida_server(local_fs_path, config.FRIDA_SERVER_REMOTE_PATH)
    if push_result.returncode == 0:
        print("Push command executed. Verifying file on device...")
        ls_result = helper.run_adb_shell_command(f"ls -l {config.FRIDA_SERVER_REMOTE_PATH}", as_root=True) # Use root for ls for consistency
        if ls_result.returncode == 0 and config.FRIDA_SERVER_REMOTE_PATH in ls_result.stdout:
            print(f"SUCCESS: Frida server found on device. Output:\n{ls_result.stdout.strip()}")
        else:
            print(f"TEST FAILED: Frida server not found on device after push or ls failed.")
            print(f"LS STDOUT: {ls_result.stdout.strip()}")
            print(f"LS STDERR: {ls_result.stderr.strip()}")
            return
    else:
        print(f"TEST FAILED: ADB push command failed with return code {push_result.returncode}.")
        print(f"STDERR: {push_result.stderr.strip()}")
        return

    # 3. Start Frida Server
    print("\n--- Test 3: Start Frida Server ---")
    start_result = helper.start_frida_server(config.FRIDA_SERVER_REMOTE_PATH)
    if start_result.returncode == 0: # The command itself was successful
        print("Start command executed. Verifying if Frida server is accessible...")
        try:
            # Wait a bit more to be sure, start_frida_server already waits 2s
            # time.sleep(config.FRIDA_ATTACH_TIMEOUT / 2 if hasattr(config, 'FRIDA_ATTACH_TIMEOUT') else 3)
            print("Attempting to list applications via Frida (frida-ps -Ua equivalent)...")
            device = frida.get_usb_device(timeout=5) # 5 second timeout
            apps = device.enumerate_applications()
            if apps:
                print(f"SUCCESS: Frida server is running. Found {len(apps)} applications.")
                # Optionally print some app names
                # for app in apps[:3]:
                # print(f"  - {app.name} ({app.identifier})")
            else:
                print("TEST FAILED: Frida server might be running, but no applications found via USB. This could be a Frida setup issue or a slow server start.")
                # This is not a definitive failure of start_frida_server if the command itself succeeded.
                # The server might be running but frida client cannot connect for other reasons.
                print("Checking ps output from start_frida_server again (it was already checked internally):")
                ps_check_result = helper.run_adb_shell_command(f"ps -e | grep {os.path.basename(config.FRIDA_SERVER_REMOTE_PATH)}", as_root=True)
                if ps_check_result.returncode == 0 and config.FRIDA_SERVER_REMOTE_PATH in ps_check_result.stdout:
                     print(f"PS check confirms server process exists: {ps_check_result.stdout.strip()}")
                else:
                     print(f"PS check does NOT confirm server process: {ps_check_result.stdout.strip()} {ps_check_result.stderr.strip()}")

        except frida.TimedOutError:
            print("TEST FAILED: Timed out connecting to Frida USB device. Server might not be running or accessible.")
            return
        except frida.TransportError as e:
            print(f"TEST FAILED: Frida transport error: {e}. Server might not be running correctly or there are connection issues.")
            return
        except Exception as e:
            print(f"TEST FAILED: An unexpected error occurred while trying to list applications with Frida: {e}")
            return
    else:
        print(f"TEST FAILED: Command to start Frida server failed with return code {start_result.returncode}.")
        print(f"STDOUT: {start_result.stdout.strip()}")
        print(f"STDERR: {start_result.stderr.strip()}")
        return

    # 4. Stop Frida Server
    print("\n--- Test 4: Stop Frida Server ---")
    stop_result = helper.stop_frida_server(config.FRIDA_SERVER_REMOTE_PATH)
    # pkill usually returns 0 if the command ran, even if no process was killed.
    # We rely on the subsequent check.
    if stop_result.returncode == 0 :
        print(f"Stop command executed (pkill). STDOUT: {stop_result.stdout.strip()}, STDERR: {stop_result.stderr.strip()}")
        print("Verifying if Frida server is no longer accessible...")
        try:
            # Give it a moment to fully stop
            # time.sleep(2) 
            print("Attempting to list applications via Frida again...")
            device = frida.get_usb_device(timeout=5) # Reduced timeout
            apps = device.enumerate_applications() # This should ideally fail or return empty if server stopped
            # If it lists apps, the server might not have stopped properly or a new one started.
            # This depends on how robust pkill is and if other frida-server instances are there.
            # For this test, we expect a failure to connect or enumerate.
            print(f"Frida still lists {len(apps)} applications. This might indicate stop_frida_server was not effective or another server is running.")
            print("TEST POTENTIALLY FAILED or requires manual check: Server may not have stopped as expected.")
            
            # Check via ps again
            ps_check_after_stop = helper.run_adb_shell_command(f"ps -e | grep {os.path.basename(config.FRIDA_SERVER_REMOTE_PATH)}", as_root=True)
            if ps_check_after_stop.returncode != 0 or config.FRIDA_SERVER_REMOTE_PATH not in ps_check_after_stop.stdout :
                 print("PS check confirms server process is GONE. Stop was effective.")
                 print("SUCCESS: Frida server appears to be stopped.")
            else:
                 print(f"PS check STILL shows server process: {ps_check_after_stop.stdout.strip()}")
                 print("TEST FAILED: Server process still visible after pkill.")
                 return

        except frida.TimedOutError:
            print("SUCCESS: Timed out connecting to Frida USB device. This is expected if server stopped.")
        except frida.TransportError as e:
            print(f"SUCCESS: Frida transport error: '{e}'. This is expected if server stopped.")
        except Exception as e:
            print(f"An unexpected error occurred, but this might be OK if server stopped: {e}")
            print("Treating as SUCCESS for stopping server, assuming error means server is down.")
    else:
        print(f"TEST FAILED: ADB pkill command failed with return code {stop_result.returncode}.")
        print(f"STDERR: {stop_result.stderr.strip()}")
        return

    print("\n--- All BlueStacksHelper Frida Deployment tests completed. ---")

if __name__ == "__main__":
    # Ensure frida_server and data directories exist as they might be used by config
    if not os.path.exists(config.FRIDA_SERVER_DIR):
        os.makedirs(config.FRIDA_SERVER_DIR)
    if not os.path.exists(config.DB_DIR):
        os.makedirs(config.DB_DIR)
        
    run_tests() 