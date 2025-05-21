import os
import time
import json
import sqlite3
import subprocess
import requests
import lzma
import frida
import traceback
import sys
from datetime import datetime, timezone

# Conditional imports for direct execution vs. module import
if __name__ == "__main__" and __package__ is None:
    # sys.path.append(project_root) # Add current dir for sibling modules
    sys.path.append(os.path.dirname(os.path.abspath(__file__))) # Add parent dir (project root) for config if it's there

    # Direct imports for local modules when main.py is run directly
    try:
        import config # Changed from 'from config import config'
    except ImportError:
        print("Warning: config.py not found in standard locations for direct execution. Using mock if possible.")
        # Define a fallback mock config if necessary for the script to run standalone
        class MockConfig:
            BLUESTACKS_ADB_PATH = "HD-Adb.exe" # Placeholder
            FRIDA_SERVER_DIR = "frida_server"
            FRIDA_SERVER_ARCH = "arm64"
            FRIDA_SERVER_VERSION = "latest"
            DATABASE_PATH = "data/tower_data.db"
            DB_DIR = "data"
            FRIDA_SERVER_REMOTE_PATH = "/data/local/tmp/frida-server"
            DEFAULT_TARGET_PACKAGE = "com.TechTreeGames.TheTower"
        config = MockConfig()

    from blue_stacks_helper import BlueStacksHelper
    from dependency_downloader import DependencyDownloader
    from database_logger import DatabaseLogger
    from frida_injector import FridaInjector
else:
    # Relative imports for when main.py is run as part of the frida_tower_logger package
    from . import config
    from .blue_stacks_helper import BlueStacksHelper
    from .dependency_downloader import DependencyDownloader
    from .database_logger import DatabaseLogger
    from .frida_injector import FridaInjector

class AppOrchestrator:
    def __init__(self):
        print("Initializing AppOrchestrator...")
        self.bs_helper = BlueStacksHelper(config.BLUESTACKS_ADB_PATH)
        self.downloader = DependencyDownloader(
            config.FRIDA_SERVER_DIR,
            config.FRIDA_SERVER_ARCH,
            config.FRIDA_SERVER_VERSION
        )
        self.db_logger = DatabaseLogger()
        self.injector = FridaInjector(self.handle_script_message)
        self.target_process_name = None
        self.script_file_name = None
        self.frida_device = None
        self.current_round_id = None
        print("AppOrchestrator initialized.")

    def initialize_dependencies_and_db(self):
        print("Initializing dependencies and database...")
        local_fs_path = self.downloader.check_and_download_frida_server()
        if not local_fs_path:
            print("CRITICAL: Failed to download or find Frida server. Exiting.")
            sys.exit(1)
        print(f"Frida server local path: {local_fs_path}")
        
        self.db_logger.create_tables_if_not_exists()
        print("Database and tables initialized.")
        return local_fs_path

    def setup_bluestacks_and_frida(self, local_frida_path):
        print("Connecting to emulator...")
        if not self.bs_helper.connect_to_emulator():
            print("Failed to connect to emulator. Please ensure BlueStacks is running and ADB is connected.")
            sys.exit("Emulator connection failed")
        print("Emulator connected.")

        print("Checking if emulator is rooted...")
        if not self.bs_helper.is_rooted():
            print("Emulator is not rooted. Root access is required.")
            sys.exit("Emulator not rooted")
        print("Emulator is rooted.")

        print(f"Stopping any existing Frida server on {config.FRIDA_SERVER_REMOTE_PATH}...")
        self.bs_helper.stop_frida_server(config.FRIDA_SERVER_REMOTE_PATH)
        print("Existing Frida server stopped (if any).")

        print(f"Pushing Frida server from {local_frida_path} to {config.FRIDA_SERVER_REMOTE_PATH}...")
        self.bs_helper.push_frida_server(local_frida_path, config.FRIDA_SERVER_REMOTE_PATH)
        print("Frida server pushed.")

        print(f"Starting Frida server ({config.FRIDA_SERVER_REMOTE_PATH}) on device...")
        self.bs_helper.start_frida_server(config.FRIDA_SERVER_REMOTE_PATH) # This method now handles verification
        # print("Frida server started command issued.") # start_frida_server now verifies

        # Robust device acquisition and verification
        try:
            print("Acquiring Frida USB device...")
            # Increased timeout, as sometimes enumeration can be slow
            self.frida_device = frida.get_usb_device(timeout=15) 
            print(f"Frida: Acquired device: {self.frida_device.name} (ID: {self.frida_device.id})")
            
            print("Verifying Frida server responsiveness by enumerating applications...")
            apps = self.frida_device.enumerate_applications()
            print(f"Frida: Enumerated {len(apps)} applications. Server seems responsive.")

        except frida.TimedOutError:
            print("Frida: Timed out trying to get USB device. Is frida-server truly running and accessible?")
            print("Ensure BlueStacks is the primary/only Android device/emulator connected via ADB.")
            sys.exit("Frida device timeout")
        except frida.NotSupportedError as e:
            print(f"Frida: NotSupportedError while trying to enumerate applications: {e}")
            print("This can sometimes happen if the frida-server version is incompatible or the device state is unusual.")
            sys.exit("Frida NotSupportedError during app enumeration")
        except frida.TransportError as e:
            print(f"Frida: Transport error getting device or enumerating apps: {e}.")
            print("This often means the frida-server on the device died or is not running correctly.")
            sys.exit("Frida transport error")
        except Exception as e: # Catch any other frida-related or unexpected errors
            print(f"Frida: An unexpected error occurred during device acquisition/verification: {e}")
            import traceback
            traceback.print_exc()
            sys.exit("Frida unexpected error during setup")

        # Pass self.frida_device to FridaInjector
        self.injector.device = self.frida_device # Store the device in the injector

    def handle_script_message(self, payload_from_frida, data_binary):
        # This callback is invoked by FridaInjector._internal_on_message
        # The _internal_on_message already prints raw Frida errors and then calls this.
        # This handler focuses on logging structured messages from the JS script.

        # Task 11.X: Integrate DB Logging into AppOrchestrator
        try:
            # Basic validation of the payload from Frida
            if not isinstance(payload_from_frida, dict):
                print(f"  [HANDLE_MSG_WARN] Received non-dict payload: {type(payload_from_frida)}")
                if self.db_logger and self.target_process_name and self.script_file_name:
                    self.db_logger.log_message(
                        script_timestamp=datetime.now(timezone.utc).isoformat(),
                        process_name=self.target_process_name,
                        script_name=self.script_file_name,
                        message_type="frida_payload_error",
                        event_subtype="non_dict_payload",
                        data_payload_dict={"raw_payload": str(payload_from_frida)[:1000]}
                    )
                return

            # Extract data based on the structure your JS script sends
            # Current JS simple_test_hooker.js sends:
            # { script: "TestHooker", timestamp: "...", eventType: "...", data: { ... } }
            # The 'master_hooker.js' sent:
            # { frida_type: 'game_data', type: type, timestamp: ..., payload: {event:..., data:...} }

            script_ts = payload_from_frida.get('timestamp', datetime.now(timezone.utc).isoformat())
            
            message_type = payload_from_frida.get('eventType', payload_from_frida.get('type'))
            data_dict = payload_from_frida.get('data', payload_from_frida.get('payload', {}))

            # Ensure process_name and script_file_name are available, use placeholders if not
            # These should be set by run_hook_on_target before messages are received
            current_process_name = self.target_process_name if self.target_process_name else "unknown_process"
            current_script_name = self.script_file_name if self.script_file_name else "unknown_script"

            print(f"  [HANDLE_MSG_INFO] Received: Type='{message_type}', Proc='{current_process_name}', Script='{current_script_name}'")
            if data_dict:
                 print(f"    Data: {str(data_dict)[:200]}...") # Print snippet of data

            # New round-based logic dispatch
            if message_type == "round_start_package":
                if self.db_logger:
                    tier = data_dict.get('tier')
                    cards_json = json.dumps(data_dict.get('cards')) # Assuming cards is a list/dict
                    modules_json = json.dumps(data_dict.get('modules')) if 'modules' in data_dict else None
                    # other_metadata for future use, pass as None for now or extract if sent
                    self.current_round_id = self.db_logger.start_new_round(
                        script_timestamp=script_ts,
                        process_name=current_process_name,
                        script_name=current_script_name,
                        tier=tier,
                        initial_cards_json=cards_json,
                        # other_fixed_metadata_json will be added if JS sends it
                    )
                    print(f"    Started new round, ID: {self.current_round_id}")
                else:
                    print("    DB Logger not available for round_start_package.")

            elif message_type == "game_over_package":
                if self.current_round_id is not None and self.db_logger:
                    final_wave = data_dict.get('wave')
                    final_cash = data_dict.get('cash')
                    final_coins = data_dict.get('coins')
                    # duration_seconds could be calculated here if start_time was stored or passed in game_over_package
                    # For now, assuming it might come from JS or be handled by DB default/later update
                    duration = data_dict.get('duration_seconds') # If JS calculates and sends it
                    self.db_logger.end_round(
                        round_id=self.current_round_id,
                        end_timestamp=script_ts, # Use the game_over_package timestamp as end_timestamp
                        final_wave=final_wave,
                        final_cash=final_cash,
                        final_coins=final_coins,
                        duration_seconds=duration
                    )
                    print(f"    Ended round, ID: {self.current_round_id}")
                    self.current_round_id = None
                elif self.db_logger is None:
                     print("    DB Logger not available for game_over_package.")
                else:
                    print("    Received game_over_package but no active round_id.")

            elif message_type == "periodic_update":
                if self.current_round_id is not None and self.db_logger:
                    cash = data_dict.get('cash')
                    coins = data_dict.get('coins')
                    gems = data_dict.get('gems')
                    wave = data_dict.get('wave_number') # Match JS 'wave_number' or 'wave' etc.
                    health = data_dict.get('tower_health')
                    self.db_logger.log_round_snapshot(
                        round_id=self.current_round_id,
                        snapshot_timestamp=script_ts,
                        cash=cash,
                        coins=coins,
                        gems=gems,
                        wave_number=wave,
                        tower_health=health
                    )
                    # print(f"    Logged snapshot for round ID: {self.current_round_id}") # DB method already prints this
                elif self.db_logger is None:
                     print("    DB Logger not available for periodic_update.")
                # No need for 'else' if no active round, snapshots are only relevant during a round

            elif message_type == "in_round_event": # Generic event type
                if self.current_round_id is not None and self.db_logger:
                    sub_event_type = data_dict.get('sub_event_type', 'unknown_in_round_event')
                    event_data_json = json.dumps(data_dict) # Log the whole data_dict for this event
                    self.db_logger.log_round_event(
                        round_id=self.current_round_id,
                        event_timestamp=script_ts,
                        event_type=sub_event_type, # Use the specific sub-type from JS
                        event_data_json=event_data_json
                    )
                    # print(f"    Logged in_round_event '{sub_event_type}' for round ID: {self.current_round_id}")
                elif self.db_logger is None:
                     print("    DB Logger not available for in_round_event.")

            else: # Fallback to old logging for other/unrecognized message types
                print(f"    Fallback: Logging message type '{message_type}' to old script_logs.")
                event_subtype_old = "unknown_subtype"
                data_for_old_log = {}
                if 'frida_type' in payload_from_frida: # Original master_hooker style
                    inner_payload = payload_from_frida.get('payload', {})
                    event_subtype_old = inner_payload.get('event', 'unknown_event')
                    data_for_old_log = inner_payload.get('data', {})
                elif 'eventType' in payload_from_frida: # simple_test_hooker style
                    data_for_old_log = payload_from_frida.get('data', {})
                    event_subtype_old = data_for_old_log.get('name', data_for_old_log.get('point', data_for_old_log.get('message', 'N/A')))
                    if not isinstance(event_subtype_old, str):
                        event_subtype_old = str(event_subtype_old)[:100]
                else: # Truly unknown structure
                    event_subtype_old = "payload_parse_error"
                    data_for_old_log = {"raw_payload": payload_from_frida}

                if self.db_logger:
                    self.db_logger.log_message(
                        script_timestamp=script_ts,
                        process_name=current_process_name,
                        script_name=current_script_name,
                        message_type=message_type, # The original 'eventType' or 'type'
                        event_subtype=event_subtype_old,
                        data_payload_dict=data_for_old_log
                    )
                else:
                    print("    DB Logger not available for fallback logging.")
            
            if data_binary: # If your script ever uses send(payload, data_bytes)
                print(f"  [HANDLE_MSG_INFO] Received binary data of length: {len(data_binary)}")

        except Exception as e_handler:
            print(f"  [HANDLE_MSG_ERROR] CRITICAL Error processing message in handle_script_message: {e_handler}")
            print("  TRACEBACK for handle_script_message error:")
            # import traceback # Already imported at the top of the file
            traceback.print_exc()
            # Optionally, try to log this critical error to the DB as well
            try:
                if self.db_logger:
                     self.db_logger.log_message(
                        script_timestamp=datetime.now(timezone.utc).isoformat(),
                        process_name=self.target_process_name or "handler_exception_context",
                        script_name=self.script_file_name or "handler_exception_context",
                        message_type="handler_critical_error",
                        event_subtype=str(e_handler)[:100], # Truncate error message
                        data_payload_dict={"error": str(e_handler), "traceback": traceback.format_exc()}
                    )
            except Exception as e_log_critical:
                print(f"  [HANDLE_MSG_ERROR] Could not even log the critical handler error to DB: {e_log_critical}")

    def run_hook_on_target(self, target_package_name, master_script_path):
        self.target_process_name = target_package_name
        self.script_file_name = os.path.basename(master_script_path)
        
        max_outer_attempts = 3
        overall_success = False

        for attempt_num in range(max_outer_attempts):
            print(f"\nMAIN_HOOK_LOOP: Outer attach attempt {attempt_num + 1}/{max_outer_attempts} for package '{target_package_name}'...")
            pid_to_attach = None
            # app_name_to_attach will default to target_package_name if PID isn't found
            app_name_to_attach = target_package_name 
            attach_target_display_name = "" # For logging

            try:
                print(f"MAIN_HOOK_LOOP: Discovering target process (Attempt {attempt_num + 1})...")
                if not self.frida_device:
                    print("MAIN_HOOK_LOOP: Frida device not set. Attempting to reacquire...")
                    try:
                        self.frida_device = frida.get_usb_device(timeout=10)
                        self.injector.device = self.frida_device # Update injector's device
                        print(f"MAIN_HOOK_LOOP: Reacquired device: {self.frida_device.name}")
                    except Exception as e_reacquire:
                        print(f"MAIN_HOOK_LOOP: Failed to reacquire Frida device: {e_reacquire}")
                        if attempt_num < max_outer_attempts - 1:
                            print(f"MAIN_HOOK_LOOP: Pausing for 7 seconds before next outer attempt due to device issue...")
                            time.sleep(7)
                            continue # To next outer attempt
                        else:
                            print("MAIN_HOOK_LOOP: All outer attempts failed due to device issue.")
                            return False # Critical failure

                apps = self.frida_device.enumerate_applications()
                target_app_info = None
                for app_info in apps:
                    if app_info.identifier == target_package_name:
                        target_app_info = app_info
                        break
                
                if target_app_info and target_app_info.pid != 0: # Check for valid PID
                    pid_to_attach = target_app_info.pid
                    # Use the name from enumerated app if available, otherwise keep package name
                    app_name_to_attach = target_app_info.name if target_app_info.name else target_package_name
                    attach_target_display_name = f"Name='{app_name_to_attach}', PID='{pid_to_attach}'"
                    print(f"MAIN_HOOK_LOOP: Found running app via enumerate_applications: {attach_target_display_name}.")
                else:
                    print(f"MAIN_HOOK_LOOP: Could not find running app '{target_package_name}' with a valid PID via enumerate_applications().")
                    print(f"MAIN_HOOK_LOOP: Checking PID via BlueStacksHelper as a fallback...")
                    pid_from_bs_helper = self.bs_helper.get_pid_for_package(target_package_name)
                    if pid_from_bs_helper:
                        pid_to_attach = pid_from_bs_helper
                        app_name_to_attach = target_package_name # Name is just the package name here
                        attach_target_display_name = f"Package='{target_package_name}', PID='{pid_to_attach}' (from BS Helper)"
                        print(f"MAIN_HOOK_LOOP: Found PID {pid_to_attach} via BlueStacksHelper.")
                    else:
                        print(f"MAIN_HOOK_LOOP: Could not find PID for '{target_package_name}' via BlueStacksHelper either.")
                        print(f"MAIN_HOOK_LOOP: Will attempt to attach by package name '{target_package_name}' as a last resort.")
                        app_name_to_attach = target_package_name 
                        pid_to_attach = None # Explicitly ensure pid_to_attach is None
                        attach_target_display_name = f"Package Name='{target_package_name}' (no PID)"
            
            except Exception as e_pid_discovery:
                print(f"MAIN_HOOK_LOOP: Error during PID discovery (Outer attempt {attempt_num + 1}): {e_pid_discovery}")
                print(f"MAIN_HOOK_LOOP: Falling back to attaching by package name: {target_package_name}")
                app_name_to_attach = target_package_name
                pid_to_attach = None
                attach_target_display_name = f"Package Name='{target_package_name}' (fallback after error)"

            # Determine final attach target for this outer attempt
            current_attach_target_value = pid_to_attach if pid_to_attach else app_name_to_attach
            
            if not current_attach_target_value:
                print(f"MAIN_HOOK_LOOP: Error - Could not determine a valid target for {target_package_name} in outer attempt {attempt_num + 1}.")
                if attempt_num < max_outer_attempts - 1:
                    print("MAIN_HOOK_LOOP: Pausing for 7 seconds before next outer attempt to find target...")
                    time.sleep(7) 
                    continue # To next outer attempt
                else:
                    print("MAIN_HOOK_LOOP: All outer attempts to determine a target failed.")
                    return False

            # Task 11.5: Stabilization delay (already implemented, now part of the loop)
            print(f"MAIN_HOOK_LOOP: INFO - Allowing a 3-second delay for target '{attach_target_display_name}' to stabilize before attach...")
            time.sleep(3)

            try:
                print(f"MAIN_HOOK_LOOP: Attempting to attach to: {attach_target_display_name} (Actual Target Value: '{current_attach_target_value}')...")
                
                if not self.injector.device: # Ensure injector's device is current
                     if self.frida_device:
                         self.injector.device = self.frida_device
                     else: # Should have been caught by re-acquire logic above
                         print("MAIN_HOOK_LOOP: CRITICAL - Injector has no device and re-acquisition failed. Cannot attach.")
                         return False


                # The injector.attach_to_process itself has internal retries
                self.injector.attach_to_process(current_attach_target_value, realm='emulated') 
                print(f"MAIN_HOOK_LOOP: Successfully attached to '{attach_target_display_name}' (Outer attempt {attempt_num + 1}).")
                
                print(f"MAIN_HOOK_LOOP: Loading script {master_script_path}...")
                script_load_success = self.injector.load_and_run_script(master_script_path)
                if script_load_success:
                    print("MAIN_HOOK_LOOP: Script loaded and running. Monitoring should begin.")
                    overall_success = True
                    break # Break from outer loop on full success (attach + load)
                else:
                    print("MAIN_HOOK_LOOP: Script loading failed after successful attach. Detaching.")
                    self.injector.detach() # Clean up
                    # Decide if we should retry outer loop or not. For now, script load failure is fatal for this outer attempt.
                    if attempt_num < max_outer_attempts - 1:
                        print(f"MAIN_HOOK_LOOP: Pausing for 7 seconds before next outer attempt due to script load failure...")
                        time.sleep(7)
                    else:
                        print(f"MAIN_HOOK_LOOP: All outer attempts failed due to script load failures.")
                        # overall_success remains False

            except (frida.ProcessNotFoundError, frida.ProcessNotRespondingError, frida.TransportError, frida.ServerNotRunningError, frida.InvalidOperationError) as e_attach:
                # Catching InvalidOperationError here too, as attach might fail if device is lost.
                print(f"MAIN_HOOK_LOOP: Attach or script load failed for target '{attach_target_display_name}' (Outer attempt {attempt_num + 1}): {type(e_attach).__name__} - {e_attach}")
                
                # Check injector's session, not orchestrator's session
                if self.injector.session and not self.injector.session.is_detached: # Ensure cleanup if session exists
                    try:
                        self.injector.detach()
                    except Exception as e_detach_cleanup:
                        print(f"MAIN_HOOK_LOOP: Error during cleanup detach: {e_detach_cleanup}")

                if attempt_num < max_outer_attempts - 1:
                    print(f"MAIN_HOOK_LOOP: Pausing for 7 seconds before next outer attach attempt...")
                    time.sleep(7) 
                else:
                    print(f"MAIN_HOOK_LOOP: All outer attach attempts failed for package '{target_package_name}'.")
            except Exception as e_unexpected: 
                print(f"MAIN_HOOK_LOOP: An unexpected error occurred (Outer attempt {attempt_num + 1}): {type(e_unexpected).__name__} - {e_unexpected}")
                import traceback
                traceback.print_exc()
                # Check injector's session, not orchestrator's session
                if self.injector.session and not self.injector.session.is_detached: # Ensure cleanup
                    try:
                        self.injector.detach()
                    except Exception as e_detach_cleanup:
                        print(f"MAIN_HOOK_LOOP: Error during cleanup detach on unexpected error: {e_detach_cleanup}")

                if attempt_num < max_outer_attempts - 1:
                    print(f"MAIN_HOOK_LOOP: Pausing for 7 seconds before next outer attach attempt due to unexpected error...")
                    time.sleep(7)
                else:
                    print(f"MAIN_HOOK_LOOP: All outer attach attempts failed for package '{target_package_name}' due to unexpected errors.")
            
            if overall_success: # If inner try/except succeeded and set overall_success
                break

        if not overall_success:
            print(f"MAIN_HOOK_LOOP: Failed to run hook on target '{target_package_name}' after {max_outer_attempts} outer attempts.")
            
        return overall_success

    def shutdown(self):
        print("\nInitiating shutdown sequence...")
        if self.injector:
            print("Detaching from process...")
            try:
                self.injector.detach() # This handles if session is None
                print("Detached from process successfully.")
            except Exception as e:
                print(f"Error during injector detach: {e}")
        else:
            print("Injector not available for detachment.")

        if self.bs_helper:
            print("Stopping Frida server on device...")
            try:
                # Assuming stop_frida_server is idempotent and handles if server not running
                self.bs_helper.stop_frida_server(config.FRIDA_SERVER_REMOTE_PATH)
                print("Attempted to stop Frida server on device.")
            except Exception as e:
                print(f"Error stopping Frida server: {e}")
        else:
            print("BlueStacks Helper not available for stopping Frida server.")
        
        print("Shutdown complete.")

if __name__ == "__main__":
    print("Starting Frida Tower Logger application...")
    # Ensure this script is in the project root or adjust path to compiled_master_hooker.js
    # For packaging, this path might need to be handled more robustly (e.g., via setup.py data_files or relative to a module)
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # master_hook_script_path = os.path.join(current_dir, "compiled_master_hooker.js")
    
    # Assuming main.py is in frida_tower_logger/ and compiled_master_hooker.js is in tower_hooker/
    project_root_dir = os.path.dirname(os.path.abspath(__file__)) # This gives frida_tower_logger directory
    if os.path.basename(project_root_dir) == 'frida_tower_logger':
        project_root_dir = os.path.dirname(project_root_dir) # Go up one level to tower_hooker/
    
    master_hook_script_path = os.path.join(project_root_dir, "scripts", "compiled_simple_test_hooker.js")
    
    orchestrator = None # Define orchestrator here to ensure it's in scope for finally block
    try:
        orchestrator = AppOrchestrator()

        print("\nPhase 1: Initializing dependencies and database...")
        local_frida_server_path = orchestrator.initialize_dependencies_and_db()
        if not local_frida_server_path:
            print("Critical error during dependency initialization. Exiting.")
            sys.exit(1)
        print(f"Dependencies initialized. Frida server is at: {local_frida_server_path}")
        print("Database initialized and ready.")

        print("\nPhase 2: Setting up BlueStacks and Frida server...")
        orchestrator.setup_bluestacks_and_frida(local_frida_server_path)
        # setup_bluestacks_and_frida will sys.exit on critical failure
        # It also now stores the frida.Device object in orchestrator.frida_device
        print("BlueStacks and Frida server setup complete.")

        # Set the obtained device on the injector
        if orchestrator.frida_device:
            print(f"Setting Frida device on injector: {orchestrator.frida_device.name}")
            orchestrator.injector.device = orchestrator.frida_device
        else:
            print("CRITICAL: No Frida device was obtained from setup. Cannot proceed to hook.")
            sys.exit(1)

        # --- Target Selection ---
        target_package = ""
        # Attempt to get PID for default package to see if it's running
        default_pid = orchestrator.bs_helper.get_pid_for_package(config.DEFAULT_TARGET_PACKAGE)
        if default_pid:
            print(f"Default target '{config.DEFAULT_TARGET_PACKAGE}' (PID: {default_pid}) seems to be running.")
            use_default = input(f"Use default target '{config.DEFAULT_TARGET_PACKAGE}'? (Y/n): ").strip().lower()
            if use_default == 'y' or use_default == '':
                target_package = config.DEFAULT_TARGET_PACKAGE
        
        if not target_package:
            target_package = input(f"Enter target package name (e.g., {config.DEFAULT_TARGET_PACKAGE}): ").strip()
            if not target_package:
                print("No target package specified. Exiting.")
                sys.exit(1)
        
        print(f"Selected target package: {target_package}")

        # Optional: PID check again for the selected target if it wasn't the default one checked
        # if target_package != config.DEFAULT_TARGET_PACKAGE or not default_pid:
        #     pid = orchestrator.bs_helper.get_pid_for_package(target_package)
        #     if not pid:
        #         print(f"Could not find PID for the selected target '{target_package}'. Ensure it is running.")
        #         # sys.exit(1) # Or allow attach by name to try anyway

        print("\nPhase 3: Running hook on target...")
        success = orchestrator.run_hook_on_target(target_package, master_hook_script_path)
        
        if success:
            print("\nApplication is running and monitoring. Press Ctrl+C to stop.")
            # Keep alive loop - Frida script runs in its own thread via frida.load()
            # The main thread here just needs to stay alive for Ctrl+C handling and messages.
            while True:
                time.sleep(1) # Keep main thread alive, print incoming messages via callback
        else:
            print("Failed to run hook on target. Check logs for errors.")
            # No sys.exit here, allow finally block to run for shutdown

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt (Ctrl+C) received. Initiating shutdown...")
    except SystemExit as e:
        # SystemExit can be raised by setup methods for critical errors.
        # No need to print stack trace, just acknowledge and proceed to shutdown.
        print(f"SystemExit caught: {e}. Proceeding to shutdown.")
    except Exception as e:
        print(f"An unhandled error occurred in the main execution block: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if orchestrator:
            orchestrator.shutdown() # Call the AppOrchestrator's shutdown method
        print("Application has been shut down.")
