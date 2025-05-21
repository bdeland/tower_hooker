import os

# Path for the Frida server executable
FRIDA_SERVER_PATH = "frida_server" # Assumes it's in PATH or a local directory

# Directory to download Frida server to (if downloaded by a script)
# Let's place it in a 'frida_server' subdirectory in the project root
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
FRIDA_SERVER_DOWNLOAD_DIR = os.path.join(PROJECT_ROOT, "frida_server")

# Path to the BlueStacks ADB executable
# Example: r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"
# User should update this to their actual BlueStacks ADB path
BLUESTACKS_ADB_PATH = r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe" # Placeholder, adjust as needed

# Database file path
DB_DIR = os.path.join(PROJECT_ROOT, "data")
DATABASE_PATH = os.path.join(DB_DIR, "tower_data.db")

# Ensure the download directory for Frida server exists
if not os.path.exists(FRIDA_SERVER_DOWNLOAD_DIR):
    os.makedirs(FRIDA_SERVER_DOWNLOAD_DIR, exist_ok=True)

# (No need to create DB_DIR here, DatabaseLogger will handle it)
print(f"Config loaded. DATABASE_PATH set to: {DATABASE_PATH}") 

# --- PostgreSQL Configuration ---
POSTGRES_HOST = "localhost"
POSTGRES_PORT = 5432 # Usually an integer
POSTGRES_DB = "tower_gamedata"
POSTGRES_USER = "tower_user"
POSTGRES_PASSWORD = "yoursecurepassword" # Consider using environment variables for passwords in production 