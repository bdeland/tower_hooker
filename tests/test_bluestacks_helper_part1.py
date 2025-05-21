import sys
sys.path.append('.') # Ensure frida_tower_logger can be imported
from frida_tower_logger import config
from frida_tower_logger.blue_stacks_helper import BlueStacksHelper

# Ensure the main project directory is in the path for frida_tower_logger imports
# This assumes the test script is run from the root of the project, or sys.path is adjusted accordingly.

helper = BlueStacksHelper(config.BLUESTACKS_ADB_PATH)

print("--- Testing run_adb_command(['devices']) ---")
result_devices = helper.run_adb_command(["devices"])
if result_devices:
    print(f"STDOUT:\n{result_devices.stdout}")
    print(f"STDERR:\n{result_devices.stderr}")
    print(f"Return Code: {result_devices.returncode}")

print("\n--- Testing run_adb_shell_command('echo hello') ---")
result_echo = helper.run_adb_shell_command("echo hello")
if result_echo:
    print(f"STDOUT:\n{result_echo.stdout}")
    print(f"STDERR:\n{result_echo.stderr}")
    print(f"Return Code: {result_echo.returncode}")

print("\n--- Testing run_adb_shell_command('whoami', as_root=True) ---")
result_whoami_root = helper.run_adb_shell_command("whoami", as_root=True)
if result_whoami_root:
    print(f"STDOUT:\n{result_whoami_root.stdout}")
    print(f"STDERR:\n{result_whoami_root.stderr}")
    print(f"Return Code: {result_whoami_root.returncode}")

print("\n--- Testing with an invalid ADB path (simulated error) ---")
bad_helper = BlueStacksHelper("nonexistent/path/to/adb.exe")
result_bad_path = bad_helper.run_adb_command(["devices"])
if result_bad_path:
    print(f"STDOUT:\n{result_bad_path.stdout}")
    print(f"STDERR:\n{result_bad_path.stderr}")
    print(f"Return Code: {result_bad_path.returncode}") 