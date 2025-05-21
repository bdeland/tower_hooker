import os

# --- Paths ---
# Try to autodetect, provide common defaults, or allow user override via environment variable
PROGRAM_FILES = os.environ.get("ProgramFiles", r"C:\Program Files")
BLUESTACKS_ADB_PATH = os.path.join(PROGRAM_FILES, "BlueStacks_nxt", "HD-Adb.exe") # Common for BS5/X
# Add other common paths if known, or make it fully configurable
# e.g. os.path.join(os.environ.get("LOCALAPPDATA"), "BlueStacks", "HD-Adb.exe")

FRIDA_SERVER_DIR = os.path.join(os.getcwd(), "frida_server")
DB_DIR = os.path.join(os.getcwd(), "data")
DATABASE_PATH = os.path.join(DB_DIR, "tower_data.db")

# --- Frida ---
FRIDA_SERVER_VERSION = "16.7.19" # Aligning with working Python frida client version
FRIDA_SERVER_ARCH = "x86_64" # or "arm", "x86", "x86_64" depending on emulator
FRIDA_SERVER_REMOTE_PATH = "/data/local/tmp/frida-server"

# --- Target ---
DEFAULT_TARGET_PACKAGE = "com.TechTreeGames.TheTower" # Was com.android.settings 