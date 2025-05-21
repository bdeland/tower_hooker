import sys
sys.path.append('.') # Ensure frida_tower_logger can be imported
from frida_tower_logger import config
from frida_tower_logger.blue_stacks_helper import BlueStacksHelper

helper = BlueStacksHelper(config.BLUESTACKS_ADB_PATH)

print("--- Testing connect_to_emulator() ---")
connected = helper.connect_to_emulator()
print(f"connect_to_emulator() returned: {connected}")

print("\n--- Testing is_rooted() ---")
rooted = helper.is_rooted()
print(f"is_rooted() returned: {rooted}") 