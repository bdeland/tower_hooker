import frida
import time # Will be needed for attach with timeout later
import os # Added for reading script file
from datetime import datetime

# Import new unified logging system
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_critical, get_logging_manager
from src.managers.unified_logging_definitions import LogSource, LogLevel

class FridaInjector:
    def __init__(self, on_message_callback, frida_device=None, serial=None):
        """Initializes the FridaInjector.

        Args:
            on_message_callback: A function to be called when a message is received from the Frida script.
                                 It should accept two arguments: payload and data.
            frida_device: A pre-configured Frida device object.
            serial: The ADB/Frida serial to use for device targeting.
        """
        self.on_message_callback = on_message_callback
        self.session = None
        self.script = None
        self.device = frida_device # Store pre-configured device
        self.serial = serial
        self.timeout = 10 # Default timeout for getting USB device if not pre-configured
        
        # Get the unified logging manager instance
        self.logging_manager = get_logging_manager()
        
        log_info(LogSource.FRIDA, "FridaInjector initialized", 
                 serial=serial, 
                 device_configured=frida_device is not None,
                 timeout=self.timeout)

    def _internal_on_message(self, message, data):
        """Internal handler for messages from Frida scripts.
        This method is passed to script.on('message', ...).
        """
        if message['type'] == 'error':
            log_error(LogSource.FRIDA, "Frida script error occurred", 
                      description=message.get('description', 'No description'),
                      stack=message.get('stack', 'No stack trace'),
                      file_name=message.get('fileName', 'N/A'),
                      line_number=message.get('lineNumber', 'N/A'),
                      column_number=message.get('columnNumber', 'N/A'))
            # Pass the error to the main handler for logging to DB if desired
            if self.on_message_callback:
                self.on_message_callback(message, data) # Pass full error object
        elif message['type'] == 'send':
            log_info(LogSource.FRIDA, "Received 'send' message from script", 
                     payload_type=type(message['payload']).__name__,
                     payload_preview=str(message['payload'])[:100] if len(str(message['payload'])) > 100 else str(message['payload']))
            if self.on_message_callback:
                self.on_message_callback(message['payload'], data)
        else:
            log_warning(LogSource.FRIDA, "Unknown message type received from script", 
                        message_type=message.get('type', 'None'),
                        message_keys=list(message.keys()) if isinstance(message, dict) else 'Not dict')
            if self.on_message_callback: # Still forward, maybe orchestrator knows
                 self.on_message_callback(message, data)

    def attach_to_process(self, process_identifier, realm='emulated'):
        """Attaches to the specified process (name or PID) on the USB device."""
        if not self.device:
            log_warning(LogSource.FRIDA, "Frida device not set, attempting to acquire device", 
                        serial=self.serial)
            try:
                if self.serial:
                    self.device = frida.get_device(self.serial, timeout=10)
                else:
                    self.device = frida.get_usb_device(timeout=10)
                log_info(LogSource.FRIDA, "Successfully acquired Frida device", 
                         device_name=self.device.name,
                         device_id=self.device.id)
            except Exception as e:
                log_error(LogSource.FRIDA, "Device acquisition failed", 
                          serial=self.serial,
                          error=str(e),
                          error_type=type(e).__name__)
                raise frida.NotSupportedError(f"Frida device not available for attach and fallback failed: {e}")
        
        log_info(LogSource.FRIDA, "Preparing to attach to process", 
                 target_process=process_identifier, 
                 device_name=self.device.name, 
                 realm=realm)

        attempts = 3
        for i in range(attempts):
            try:
                log_info(LogSource.FRIDA, "Starting attach attempt", 
                         attempt=i + 1, 
                         max_attempts=attempts, 
                         target_process=process_identifier)
                # Add a small delay before each attempt, increasing for later attempts
                # Also a base delay before the first attempt, as suggested.
                current_delay = 2 + (i * 2) # e.g., 2s, 4s, 6s
                log_info(LogSource.FRIDA, "Pausing before attach", 
                         delay_seconds=current_delay)
                time.sleep(current_delay)
                
                self.session = self.device.attach(process_identifier, realm=realm)
                log_info(LogSource.FRIDA, "Successfully attached to process", 
                         attempt=i + 1, 
                         session_id=str(self.session), 
                         target_process=process_identifier,
                         realm=realm)
                return # Success
            except frida.ProcessNotRespondingError as e:
                log_error(LogSource.FRIDA, "Attach failed - process not responding", 
                          attempt=i + 1, 
                          target_process=process_identifier, 
                          error=str(e),
                          remaining_attempts=attempts - i - 1)
                if i == attempts - 1:
                    log_error(LogSource.FRIDA, "All attach attempts failed - process not responding", 
                              target_process=process_identifier)
                    raise  # Re-raise the exception if all attempts fail
            except frida.ServerNotRunningError as e:
                log_error(LogSource.FRIDA, "Attach failed - Frida server not running", 
                          attempt=i + 1, 
                          target_process=process_identifier, 
                          error=str(e),
                          remaining_attempts=attempts - i - 1)
                if i == attempts - 1:
                    log_error(LogSource.FRIDA, "All attach attempts failed - server not running", 
                              target_process=process_identifier)
                    raise
            except frida.TransportError as e: # Catching this explicitly as it was seen before
                log_error(LogSource.FRIDA, "Attach failed - transport error (e.g. connection closed)", 
                          attempt=i + 1, 
                          target_process=process_identifier, 
                          error=str(e),
                          remaining_attempts=attempts - i - 1)
                if i == attempts - 1:
                    log_error(LogSource.FRIDA, "All attach attempts failed - transport error", 
                              target_process=process_identifier)
                    raise
            except frida.ProcessNotFoundError as e: # Added this from previous observations
                log_error(LogSource.FRIDA, "Attach failed - process not found", 
                          attempt=i + 1, 
                          target_process=process_identifier, 
                          error=str(e),
                          remaining_attempts=attempts - i - 1)
                if i == attempts - 1:
                    log_error(LogSource.FRIDA, "All attach attempts failed - process not found", 
                              target_process=process_identifier)
                    raise
            except Exception as e: # Catch any other unexpected Frida/attach errors
                log_error(LogSource.FRIDA, "Attach failed with unexpected error", 
                          attempt=i + 1, 
                          target_process=process_identifier, 
                          error_type=type(e).__name__, 
                          error=str(e),
                          remaining_attempts=attempts - i - 1)
                if i == attempts - 1:
                    log_error(LogSource.FRIDA, "All attach attempts failed - unexpected error", 
                              target_process=process_identifier,
                              error_type=type(e).__name__)
                    raise
        
        # Should not be reached if logic is correct (either returns on success or raises on all failures)
        log_error(LogSource.FRIDA, "attach_to_process finished without success or explicit error after loop - this should not happen")
        raise frida.InvalidOperationError("Attach failed after all retries without specific exception propagation.")

    def load_and_run_script(self, script_path):
        """Loads a Frida script from a file and runs it in the current session.

        Args:
            script_path: Path to the JavaScript file.
        Raises:
            IOError: If the script file cannot be read.
            frida.FridaError: If there's an error creating or loading the script (e.n.g., syntax error in script).
            AttributeError: If no session is active (attach_to_process was not called or failed).
        """
        if not self.session:
            log_warning(LogSource.FRIDA, "Cannot load script - no active session", 
                        script_path=script_path)
            raise frida.InvalidOperationError("Cannot load script without an active session.")
        
        log_info(LogSource.FRIDA, "Loading script from file", 
                 script_path=script_path,
                 session_active=self.session is not None)
        try:
            # Specify utf-8-sig encoding to handle potential BOM
            with open(script_path, 'r', encoding='utf-8-sig') as f:
                script_content = f.read()
            
            log_info(LogSource.FRIDA, "Script file read successfully", 
                     script_path=script_path,
                     script_length=len(script_content))
            
            self.script = self.session.create_script(script_content)
            log_info(LogSource.FRIDA, "Script created successfully")
            
            self.script.on('message', self._internal_on_message)
            log_info(LogSource.FRIDA, "Message handler set for script")
            
            self.script.load()
            log_info(LogSource.FRIDA, "Script loaded and running", 
                     script_path=script_path)
            return True # Indicate success
        except frida.InvalidOperationError as e: # e.g. session already detached
            log_error(LogSource.FRIDA, "Error loading/running script - invalid operation", 
                      script_path=script_path,
                      error=str(e))
            raise
        except IOError as e:
            log_error(LogSource.FRIDA, "Error reading script file", 
                      script_path=script_path,
                      error=str(e))
            raise
        except Exception as e:
            log_error(LogSource.FRIDA, "Unexpected error during script load/run", 
                      script_path=script_path,
                      error_type=type(e).__name__,
                      error=str(e))
            import traceback
            log_error(LogSource.FRIDA, "Script load error traceback", 
                      traceback=traceback.format_exc())
            raise
        return False # Should not be reached if exceptions are raised

    def detach(self):
        """Detaches from the process."""
        # Early exit if nothing to detach
        if not self.session and not self.script:
            log_info(LogSource.FRIDA, "No active session or script to detach")
            return
        
        log_info(LogSource.FRIDA, "Starting detach process", 
                 has_session=self.session is not None,
                 has_script=self.script is not None)
        
        # Unload script if it exists
        if self.script:
            try:
                self.script.unload()
                log_info(LogSource.FRIDA, "Script unloaded successfully")
            except frida.InvalidOperationError as e:
                log_warning(LogSource.FRIDA, "Script already unloaded or session dead", 
                            error=str(e))
            except Exception as e:
                log_error(LogSource.FRIDA, "Error unloading script", 
                          error_type=type(e).__name__,
                          error=str(e))
            finally:
                self.script = None  # Always clear script object
        
        # Detach session if it exists
        if self.session:
            try:
                # Check if session is already detached
                if not self.session.is_detached:
                    self.session.detach()
                    log_info(LogSource.FRIDA, "Detached from process successfully")
                else:
                    log_info(LogSource.FRIDA, "Session already detached")
            except frida.InvalidOperationError as e:
                log_warning(LogSource.FRIDA, "Session already detached or invalid", 
                            error=str(e))
            except Exception as e:
                log_error(LogSource.FRIDA, "Error detaching session", 
                          error_type=type(e).__name__,
                          error=str(e))
            finally:
                self.session = None  # Always clear session object

if __name__ == '__main__':
    # For demo/testing, set serial to None or a test value if needed
    test_serial = None
    log_info(LogSource.FRIDA, "--- Test Suite for Basic Structure and Message Handling ---")

    # 1. Define a dummy callback
    def my_callback(payload, data_):
        log_info(LogSource.FRIDA, "Test callback received message", 
                 payload=payload, 
                 data_present=data_ is not None)

    # 2. Instantiate injector
    log_info(LogSource.FRIDA, "Instantiating FridaInjector with test callback...")
    injector = FridaInjector(my_callback, serial=test_serial)
    log_info(LogSource.FRIDA, "FridaInjector instantiated")

    # 3. Manually call _internal_on_message with a 'send' type message
    log_info(LogSource.FRIDA, "Testing _internal_on_message with type 'send'...")
    test_payload_send = {'message': 'hello world from test', 'value': 42}
    injector._internal_on_message({'type': 'send', 'payload': test_payload_send}, None)
    log_info(LogSource.FRIDA, "Expected: Callback received message with test payload")

    # 4. Manually call _internal_on_message with an 'error' type message
    log_info(LogSource.FRIDA, "Testing _internal_on_message with type 'error'...")
    test_payload_error = {
        'type': 'error', 
        'description': 'ReferenceError: some_undefined_variable is not defined',
        'stack': 'ReferenceError: some_undefined_variable is not defined\n    at frida/runtime/core.js:123\n    at repl:1:1',
        'fileName': 'script1.js',
        'lineNumber': 5,
        'columnNumber': 10
    }
    injector._internal_on_message(test_payload_error, b"some_binary_data_if_any") # data can be bytes
    log_info(LogSource.FRIDA, "Expected: Error logged and callback received with error details")

    # Test with on_message_callback = None
    log_info(LogSource.FRIDA, "Testing _internal_on_message with no callback set (type 'send')...")
    injector_no_cb = FridaInjector(on_message_callback=None)
    injector_no_cb._internal_on_message({'type': 'send', 'payload': 'should not invoke callback'}, None)
    log_info(LogSource.FRIDA, "Expected: Message logged but no callback invoked")

    log_info(LogSource.FRIDA, "--- FridaInjector Basic Tests Completed ---")

    # --- Test Suite for Attach, Load Script, Detach ---
    # Note: These tests require a running Frida server on a connected USB device
    # and a known running process (e.g., 'com.android.settings').
    # These will be more thoroughly tested by the AppOrchestrator later.
    # For now, this is a placeholder to demonstrate the structure.

    log_info(LogSource.FRIDA, "--- Placeholder Tests for Attach/Load/Detach (Manual/Visual Verification Needed) ---")
    
    test_script_content = """
    console.log('[TestScript] Test script loaded via FridaInjector!');
    send({ message: 'Hello from FridaInjector test_script.js!' });
    setTimeout(() => {
        send({ message: '[TestScript] Still alive after 2s!' });
    }, 2000);
    """
    test_script_filename = "./test_injector_script.js"

    with open(test_script_filename, "w") as f:
        f.write(test_script_content)
    log_info(LogSource.FRIDA, "Created dummy test script", filename=test_script_filename)

    # Dummy callback for attach/load tests
    attach_test_events = []
    def attach_load_callback(payload, data_):
        log_info(LogSource.FRIDA, "Attach/Load test callback received message", payload=payload)
        attach_test_events.append(payload)

    injector_for_attach = FridaInjector(attach_load_callback)

    # Example target_process - CHOOSE A SAFE AND ALWAYS RUNNING SYSTEM APP FOR TESTING
    # e.g., settings app on Android. Find its package name.
    # On an emulator, 'com.android.settings' is usually available.
    target_process = "com.android.settings" 
    # Alternatively, find a PID using `adb shell ps -A | grep settings` or similar.

    log_info(LogSource.FRIDA, "To test attach/load/detach, ensure Frida server is running on your USB device")
    log_info(LogSource.FRIDA, "And that the process is running", target_process=target_process)
    log_info(LogSource.FRIDA, "Test will attempt:")
    log_info(LogSource.FRIDA, "1. Attach to process", target_process=target_process)
    log_info(LogSource.FRIDA, "2. Load and run script", script_filename=test_script_filename)
    log_info(LogSource.FRIDA, "3. Wait for ~3 seconds to receive messages")
    log_info(LogSource.FRIDA, "4. Detach")
    log_info(LogSource.FRIDA, "Manually verify console output for messages from the script and callback")

    # Simple manual test sequence (uncomment to run, requires setup)
    # try:
    #     log_info(LogSource.FRIDA, "Attempting to attach to process", target_process=target_process)
    #     injector_for_attach.attach_to_process(target_process)
    #     log_info(LogSource.FRIDA, "Attached successfully")

    #     log_info(LogSource.FRIDA, "Attempting to load script", script_filename=test_script_filename)
    #     injector_for_attach.load_and_run_script(test_script_filename)
    #     log_info(LogSource.FRIDA, "Script loaded")

    #     log_info(LogSource.FRIDA, "Waiting for 3 seconds for messages...")
    #     time.sleep(3)

    #     log_info(LogSource.FRIDA, "Messages received", messages=attach_test_events)

    # except frida.ProcessNotFoundError:
    #     log_error(LogSource.FRIDA, "Process not found", target_process=target_process, 
    #               message="Is it running?")
    # except frida.NotSupportedError as e:
    #     log_error(LogSource.FRIDA, "Frida device not found or not supported", error=str(e),
    #               message="Is Frida server running on device?")
    # except Exception as e:
    #     log_error(LogSource.FRIDA, "Unexpected error occurred", 
    #               error_type=type(e).__name__, error=str(e))
    #     import traceback
    #     log_error(LogSource.FRIDA, "Full traceback", traceback=traceback.format_exc())
    # finally:
    #     log_info(LogSource.FRIDA, "Attempting to detach...")
    #     injector_for_attach.detach()
    #     log_info(LogSource.FRIDA, "Detached")

    #     # Clean up dummy script
    #     if os.path.exists(test_script_filename):
    #         os.remove(test_script_filename)
    #         log_info(LogSource.FRIDA, "Cleaned up dummy script", filename=test_script_filename)

    log_info(LogSource.FRIDA, "--- FridaInjector Attach/Load/Detach Placeholder Tests Done ---") 