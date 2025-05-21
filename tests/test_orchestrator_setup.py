import os
import sys

# Add frida_tower_logger to sys.path to allow direct imports
# This assumes the script is run from the tower_hooker directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR # If test script is in tower_hooker/
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'frida_tower_logger'))

try:
    from frida_tower_logger.main import AppOrchestrator
    from frida_tower_logger import config # To verify paths if needed
    print("Successfully imported AppOrchestrator and config.")
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Please ensure that frida_tower_logger and its contents are structured correctly")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

def run_tests():
    print("\n--- Test: Instantiate AppOrchestrator ---")
    try:
        orchestrator = AppOrchestrator()
        print("AppOrchestrator instantiated successfully.")
    except Exception as e:
        print(f"Error instantiating AppOrchestrator: {e}")
        return # Stop if orchestrator cannot be created

    print("\n--- Test: Initialize Dependencies and DB ---")
    local_fs_path = None
    try:
        local_fs_path = orchestrator.initialize_dependencies_and_db()
        if local_fs_path and os.path.exists(local_fs_path):
            print(f"initialize_dependencies_and_db successful. Frida server at: {local_fs_path}")
            # Verify DB directory and file (conceptual)
            db_dir = os.path.dirname(config.DATABASE_PATH)
            if os.path.exists(db_dir) and os.path.isdir(db_dir):
                print(f"Database directory '{db_dir}' exists.")
                if os.path.exists(config.DATABASE_PATH):
                     print(f"Database file '{config.DATABASE_PATH}' potentially created/verified by logger.")
                else:
                    print(f"Warning: Database file '{config.DATABASE_PATH}' not found, though directory exists.")
            else:
                print(f"Error: Database directory '{db_dir}' NOT found.")
        else:
            print("initialize_dependencies_and_db failed to return a valid Frida server path or path does not exist.")
            return # Stop if frida server not found
    except Exception as e:
        print(f"Error during initialize_dependencies_and_db: {e}")
        import traceback
        traceback.print_exc()
        return

    if not local_fs_path:
        print("Skipping BlueStacks and Frida setup due to previous errors.")
        return

    print("\n--- Test: Setup BlueStacks and Frida Server ---")
    try:
        orchestrator.setup_bluestacks_and_frida(local_fs_path)
        print("setup_bluestacks_and_frida completed.")
        print("Verification: Check BlueStacks for running Frida server.")
        print("  - On your host machine, run: frida-ps -U")
        print("  - This should list processes from the emulator.")
        print("  - Alternatively, use ADB: adb shell su -c \"ps -A | grep frida-server\"")
    except SystemExit as e:
        print(f"SystemExit during setup_bluestacks_and_frida: {e}. This is expected if critical checks fail.")
    except Exception as e:
        print(f"Error during setup_bluestacks_and_frida: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Ensure BlueStacks is running and an emulator instance is active.
    # Ensure the ADB path in config.py is correct for your system.
    print("Starting Orchestrator Setup Test Script...")
    print(f"Expected BlueStacks ADB Path (from config): {config.BLUESTACKS_ADB_PATH}")
    if not os.path.exists(config.BLUESTACKS_ADB_PATH):
        print(f"WARNING: BlueStacks ADB path '{config.BLUESTACKS_ADB_PATH}' does not exist. Tests will likely fail.")
    
    run_tests()
    print("\nTest script finished.") 