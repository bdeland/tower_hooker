import frida
import time # Will be needed for attach with timeout later
import os # Added for reading script file
from datetime import datetime

class FridaInjector:
    def __init__(self, on_message_callback, frida_device=None):
        """Initializes the FridaInjector.

        Args:
            on_message_callback: A function to be called when a message is received from the Frida script.
                                 It should accept two arguments: payload and data.
            frida_device: A pre-configured Frida device object.
        """
        self.on_message_callback = on_message_callback
        self.session = None
        self.script = None
        self.device = frida_device # Store pre-configured device
        self.timeout = 10 # Default timeout for getting USB device if not pre-configured
        print("FridaInjector initialized.")

    def _internal_on_message(self, message, data):
        """Internal handler for messages from Frida scripts.
        This method is passed to script.on('message', ...).
        """
        # print(f"DEBUG: _internal_on_message received: Message type: {message['type']}") # Debug print
        if message['type'] == 'error':
            print(f"FRIDA SCRIPT ERROR: {message.get('description', 'No description')}")
            print(f"  Stack: {message.get('stack', 'No stack trace')}")
            print(f"  Details: {message.get('fileName', 'N/A')}:{message.get('lineNumber', 'N/A')}")
            # Pass the error to the main handler for logging to DB if desired
            if self.on_message_callback:
                self.on_message_callback(message, data) # Pass full error object
        elif message['type'] == 'send':
            # print(f"FridaInjector received 'send' payload: {message['payload']}") # Debug print
            if self.on_message_callback:
                self.on_message_callback(message['payload'], data)
        else:
            print(f"FridaInjector: Unknown message type: {message}")
            if self.on_message_callback: # Still forward, maybe orchestrator knows
                 self.on_message_callback(message, data)

    def attach_to_process(self, process_identifier, realm='emulated'):
        """Attaches to the specified process (name or PID) on the USB device."""
        if not self.device:
            print("FridaInjector: Error - Frida device not set. Attempting to get default USB device.")
            try:
                self.device = frida.get_usb_device(timeout=10)
                print(f"FridaInjector: Successfully acquired fallback device: {self.device.name}")
            except Exception as e:
                print(f"FridaInjector: Fallback device acquisition failed: {e}")
                raise frida.NotSupportedError(f"Frida device not available for attach and fallback failed: {e}")

        print(f"FridaInjector: Preparing to attach. Target='{process_identifier}', Device='{self.device.name}', Realm='{realm}'")

        attempts = 3
        for i in range(attempts):
            try:
                print(f"FridaInjector: Attach attempt {i+1}/{attempts} to '{process_identifier}'...")
                # Add a small delay before each attempt, increasing for later attempts
                # Also a base delay before the first attempt, as suggested.
                current_delay = 2 + (i * 2) # e.g., 2s, 4s, 6s
                print(f"FridaInjector: Pausing for {current_delay} seconds before attach...")
                time.sleep(current_delay)
                
                self.session = self.device.attach(process_identifier, realm=realm)
                print(f"FridaInjector: Successfully attached on attempt {i+1}. Session: {self.session}")
                return # Success
            except frida.ProcessNotRespondingError as e:
                print(f"FridaInjector: Attach attempt {i+1} failed (ProcessNotRespondingError): {e}")
                if i == attempts - 1:
                    print("FridaInjector: All attach attempts failed (ProcessNotRespondingError).")
                    raise  # Re-raise the exception if all attempts fail
            except frida.ServerNotRunningError as e:
                print(f"FridaInjector: Attach attempt {i+1} failed (ServerNotRunningError): {e}")
                if i == attempts - 1:
                    print("FridaInjector: All attach attempts failed (ServerNotRunningError).")
                    raise
            except frida.TransportError as e: # Catching this explicitly as it was seen before
                print(f"FridaInjector: Attach attempt {i+1} failed (TransportError - e.g. connection closed): {e}")
                if i == attempts - 1:
                    print("FridaInjector: All attach attempts failed (TransportError).")
                    raise
            except frida.ProcessNotFoundError as e: # Added this from previous observations
                print(f"FridaInjector: Attach attempt {i+1} failed (ProcessNotFoundError): {e}")
                if i == attempts - 1:
                    print("FridaInjector: All attach attempts failed (ProcessNotFoundError).")
                    raise
            except Exception as e: # Catch any other unexpected Frida/attach errors
                print(f"FridaInjector: Attach attempt {i+1} failed with unexpected error: {type(e).__name__} - {e}")
                if i == attempts - 1:
                    print(f"FridaInjector: All attach attempts failed ({type(e).__name__}).")
                    raise
        
        # Should not be reached if logic is correct (either returns on success or raises on all failures)
        print("FridaInjector: attach_to_process finished without success or explicit error after loop (should not happen).")
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
            print("FridaInjector: Cannot load script, no active session. Attach might have failed.")
            raise frida.InvalidOperationError("Cannot load script without an active session.")
        
        print(f"FridaInjector: Loading script from {script_path}...")
        try:
            # Specify utf-8-sig encoding to handle potential BOM
            with open(script_path, 'r', encoding='utf-8-sig') as f:
                script_content = f.read()
            
            self.script = self.session.create_script(script_content)
            print("FridaInjector: Script created.")
            
            self.script.on('message', self._internal_on_message)
            print("FridaInjector: Message handler set.")
            
            self.script.load()
            print("FridaInjector: Script loaded and running.")
            return True # Indicate success
        except frida.InvalidOperationError as e: # e.g. session already detached
            print(f"FridaInjector: Error loading/running script (InvalidOperationError): {e}")
            raise
        except Exception as e:
            print(f"FridaInjector: An unexpected error occurred during script load/run: {e}")
            import traceback
            traceback.print_exc()
            raise
        return False # Should not be reached if exceptions are raised

    def detach(self):
        """Detaches from the process."""
        print(f"FridaInjector: Detaching. Current session: {self.session}, script: {self.script}")
        if self.script:
            try:
                self.script.unload() # Unload script first
                print("FridaInjector: Script unloaded.")
            except frida.InvalidOperationError as e:
                print(f"FridaInjector: Script already unloaded or session dead: {e}")
            except Exception as e:
                print(f"FridaInjector: Error unloading script: {e}")
            self.script = None # Clear script object

        if self.session:
            try:
                # Check if session is already detached
                if not self.session.is_detached:
                    self.session.detach()
                    print("FridaInjector: Detached from process.")
                else:
                    print("FridaInjector: Session already detached.")
            except frida.InvalidOperationError as e:
                print(f"FridaInjector: Session already detached or invalid: {e}")
            except Exception as e:
                print(f"FridaInjector: Error detaching session: {e}")
            self.session = None # Clear session object
        else:
            print("FridaInjector: No active session to detach.")

if __name__ == '__main__':
    print("--- Test Suite for FridaInjector: Basic Structure and Message Handling ---")

    # 1. Define a dummy callback
    def my_callback(payload, data_):
        print(f"Callback received: Payload: {payload}, Data: {data_}")

    # 2. Instantiate injector
    print("\nInstantiating FridaInjector with my_callback...")
    injector = FridaInjector(my_callback)
    print("FridaInjector instantiated.")

    # 3. Manually call _internal_on_message with a 'send' type message
    print("\nTesting _internal_on_message with type 'send'...")
    test_payload_send = {'message': 'hello world from test', 'value': 42}
    injector._internal_on_message({'type': 'send', 'payload': test_payload_send}, None)
    print("Expected: Callback received: Payload: {'message': 'hello world from test', 'value': 42}, Data: None")

    # 4. Manually call _internal_on_message with an 'error' type message
    print("\nTesting _internal_on_message with type 'error'...")
    test_payload_error = {
        'type': 'error', 
        'description': 'ReferenceError: some_undefined_variable is not defined',
        'stack': 'ReferenceError: some_undefined_variable is not defined\n    at frida/runtime/core.js:123\n    at repl:1:1',
        'fileName': 'script1.js',
        'lineNumber': 5,
        'columnNumber': 10
    }
    injector._internal_on_message(test_payload_error, b"some_binary_data_if_any") # data can be bytes
    print("Expected: Error printed to console, and Callback received with error details.")

    # Test with on_message_callback = None
    print("\nTesting _internal_on_message with no callback set (type 'send')...")
    injector_no_cb = FridaInjector(on_message_callback=None)
    injector_no_cb._internal_on_message({'type': 'send', 'payload': 'should not invoke callback'}, None)
    print("Expected: Warning printed: 'Message received from script, but no on_message_callback is set.'")

    print("\n--- FridaInjector Basic Tests Completed ---")

    # --- Test Suite for FridaInjector: Attach, Load Script, Detach ---
    # Note: These tests require a running Frida server on a connected USB device
    # and a known running process (e.g., 'com.android.settings').
    # These will be more thoroughly tested by the AppOrchestrator later.
    # For now, this is a placeholder to demonstrate the structure.

    print("\n--- Placeholder Tests for Attach/Load/Detach (Manual/Visual Verification Needed) ---")
    
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
    print(f"Created dummy script: {test_script_filename}")

    # Dummy callback for attach/load tests
    attach_test_events = []
    def attach_load_callback(payload, data_):
        print(f"[Attach/Load Test Callback] Received: {payload}")
        attach_test_events.append(payload)

    injector_for_attach = FridaInjector(attach_load_callback)

    # Example target_process - CHOOSE A SAFE AND ALWAYS RUNNING SYSTEM APP FOR TESTING
    # e.g., settings app on Android. Find its package name.
    # On an emulator, 'com.android.settings' is usually available.
    target_process = "com.android.settings" 
    # Alternatively, find a PID using `adb shell ps -A | grep settings` or similar.

    print(f"\nTo test attach/load/detach, ensure Frida server is running on your USB device.")
    print(f"And that the process '{target_process}' is running.")
    print("Test will attempt to:")
    print(f"1. Attach to '{target_process}'")
    print(f"2. Load and run '{test_script_filename}'")
    print("3. Wait for ~3 seconds to receive messages")
    print("4. Detach")
    print("Manually verify console output for messages from the script and callback.")

    # Simple manual test sequence (uncomment to run, requires setup)
    # try:
    #     print("\nAttempting attach...")
    #     injector_for_attach.attach_to_process(target_process)
    #     if injector_for_attach.session:
    #         print("\nAttempting load_and_run_script...")
    #         injector_for_attach.load_and_run_script(test_script_filename)
    #         print("\nWaiting for script messages (approx 3s)...")
    #         time.sleep(3.5) # Give script time to send initial and delayed message
    #         print(f"\nReceived {len(attach_test_events)} messages via callback.")
    # except Exception as e:
    #     print(f"Error during attach/load test: {e}")
    # finally:
    #     print("\nAttempting detach...")
    #     injector_for_attach.detach()
    #     if os.path.exists(test_script_filename):
    #         os.remove(test_script_filename)
    #     print("Attach/Load/Detach test sequence finished.")

    # For automated testing, this would require a mock Frida environment or a live device.
    # The task asks for implementation, full E2E test comes with AppOrchestrator.
    print("\n(Attach/Load/Detach test part is currently commented out for automated runs without live Frida setup)")
    if os.path.exists(test_script_filename):
        os.remove(test_script_filename) # Clean up dummy script

    print("\n--- FridaInjector Attach/Load/Detach Method Implementations Completed ---") 