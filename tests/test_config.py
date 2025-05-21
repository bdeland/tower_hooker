import sys
sys.path.append('.') # Ensure frida_tower_logger can be imported
from frida_tower_logger import config

output = f"BLUESTACKS_ADB_PATH: {config.BLUESTACKS_ADB_PATH}\nFRIDA_SERVER_DIR: {config.FRIDA_SERVER_DIR}\nDATABASE_PATH: {config.DATABASE_PATH}"

with open("test_output.txt", "w") as f:
    f.write(output) 