#!/usr/bin/env python3
"""
Main Application Entry Point for Tower Hooker GUI

This script initializes and runs the Tower Hooker application with its
PyQt6 graphical user interface.
"""

import sys
import asyncio
import threading
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication

from src.frida_app import AppOrchestrator
from src.gui import AppController, MainWindow
from src.gui.setup_wizard_dialog import SetupWizardDialog
from src.utils.config_loader import load_app_config
from src.utils.setup_wizard import InfrastructureSetupWizard


def start_async_backend(orchestrator: AppOrchestrator, event_loop: asyncio.AbstractEventLoop):
    """
    Start the backend async loop in a separate thread.
    
    Args:
        orchestrator: AppOrchestrator instance to run
        event_loop: Event loop to run the orchestrator in
    """
    asyncio.set_event_loop(event_loop)
    # Note: Using print here since this runs before the logging system is fully initialized
    # This debug info is useful for development but should not appear in production
    try:
        event_loop.run_until_complete(orchestrator.run_main_loop())
    except Exception as e:
        print(f"Backend thread error: {e}")
    finally:
        event_loop.close()


def cleanup_orchestrator_and_backend(orchestrator, backend_thread, backend_event_loop):
    """
    Properly clean up the orchestrator and backend thread.
    
    Args:
        orchestrator: AppOrchestrator instance to cleanup
        backend_thread: Thread running the backend
        backend_event_loop: Event loop of the backend
    """
    if orchestrator is not None:
        try:
            print("Requesting graceful shutdown of backend...")
            # Request shutdown
            orchestrator.request_shutdown()
            
            # Wait for backend thread to finish with timeout
            if backend_thread and backend_thread.is_alive():
                print("Waiting for backend thread to finish...")
                backend_thread.join(timeout=5.0)
                
                if backend_thread.is_alive():
                    print("Backend thread did not finish in time, forcing cleanup...")
                    # Force close the event loop if still running
                    if backend_event_loop and not backend_event_loop.is_closed():
                        try:
                            # Schedule shutdown in the loop
                            backend_event_loop.call_soon_threadsafe(backend_event_loop.stop)
                            backend_thread.join(timeout=2.0)
                        except Exception as e:
                            print(f"Error forcing backend shutdown: {e}")
                
        except Exception as e:
            print(f"Error during orchestrator cleanup: {e}")


def main():
    """Main application entry point."""
    try:
        # 1. Load Application Configuration
        app_config_dict = load_app_config(yaml_path="config/main_config.yaml", env_path=".env")
        
        # Debug: Show main thread event loop ID (avoid deprecation warning)
        try:
            # Check if there's a running loop first
            try:
                main_loop = asyncio.get_running_loop()
                # Running loop found in main thread (uncommon for GUI apps)
                pass
            except RuntimeError:
                # No running loop in this thread, which is normal for GUI main thread
                pass
        except Exception as e:
            # Could not check main thread event loop
            pass
        
        # 2. Initialize PyQt Application
        qt_app = QApplication(sys.argv)
        
        # 3. Check if wizard should be shown
        setup_checker = InfrastructureSetupWizard()
        is_first_run = setup_checker.is_first_time_setup()
        show_wizard = is_first_run  # Can also be set to True for testing
        
        proceed_with_main_app = True
        orchestrator = None
        backend_thread = None
        backend_event_loop = None
        
        # 4. Show setup wizard if needed
        if show_wizard:
            print("First run detected - showing setup wizard...")
            
            # Initialize AppOrchestrator for wizard
            orchestrator = AppOrchestrator(initial_app_config=app_config_dict)
            
            # Prepare Asyncio Backend Loop and set it in orchestrator BEFORE wizard
            backend_event_loop = asyncio.new_event_loop()
            orchestrator.set_backend_loop(backend_event_loop)
            
            # Start Backend Async Loop in a Separate Thread BEFORE wizard dialog
            backend_thread = threading.Thread(
                target=start_async_backend,
                args=(orchestrator, backend_event_loop),
                daemon=True
            )
            backend_thread.start()
            
            # Give backend thread a moment to start
            import time
            time.sleep(0.1)
            
            # Create and configure wizard dialog
            wizard_dialog = SetupWizardDialog(orchestrator)
            
            # Connect AppOrchestrator wizard signals to SetupWizardDialog slots
            orchestrator.wizard_progress_text.connect(wizard_dialog.update_progress_text)
            orchestrator.wizard_step_status.connect(wizard_dialog.update_step_status)
            orchestrator.wizard_progress_bar_update.connect(wizard_dialog.update_progress_bar)
            orchestrator.wizard_sequence_complete.connect(wizard_dialog.setup_sequence_complete)
            orchestrator.wizard_detailed_completion.connect(wizard_dialog.setup_sequence_detailed_complete)
            
            # Show wizard dialog (blocks until user closes it)
            dialog_result = wizard_dialog.exec()
            
            # Check wizard result
            if dialog_result == wizard_dialog.DialogCode.Accepted:
                proceed_with_main_app = True
                # Check if user chose degraded mode
                if wizard_dialog.is_degraded_mode():
                    print("Setup wizard completed in degraded mode - proceeding with main application")
                    # Mark as overridden to allow degraded mode
                    orchestrator.setup_is_complete_or_overridden = True
                else:
                    print("Setup wizard completed successfully - proceeding with main application")
                    # Mark setup as complete for successful completion
                    orchestrator.setup_is_complete_or_overridden = True
            else:
                proceed_with_main_app = False
                print("Setup wizard was aborted by user")
                
                # Properly clean up before exiting
                cleanup_orchestrator_and_backend(orchestrator, backend_thread, backend_event_loop)
                
                # Clean up Qt application
                qt_app.quit()
                return 1  # Exit with error code
        else:
            print("Setup already completed - proceeding directly to main application")
            # Initialize orchestrator now and mark setup as complete
            orchestrator = AppOrchestrator(initial_app_config=app_config_dict)
            orchestrator.setup_is_complete_or_overridden = True
            
            # Prepare Asyncio Backend Loop and set it in orchestrator
            backend_event_loop = asyncio.new_event_loop()
            orchestrator.set_backend_loop(backend_event_loop)
        
        # 5. Only proceed with main app if wizard succeeded or was skipped
        if proceed_with_main_app:
            # Initialize orchestrator if not already done for wizard
            if orchestrator is None:
                orchestrator = AppOrchestrator(initial_app_config=app_config_dict)
                # Prepare Asyncio Backend Loop and set it in orchestrator
                backend_event_loop = asyncio.new_event_loop()
                orchestrator.set_backend_loop(backend_event_loop)
            
            # Initialize GUI Controller and Main Window
            app_controller = AppController(orchestrator, backend_event_loop)
            main_window = MainWindow(app_controller)
            main_window.show()
        
            # 6. Start Backend Async Loop in a Separate Thread (if not already started for wizard)
            if not show_wizard:  # Only start if we didn't already start it for wizard
                backend_thread = threading.Thread(
                    target=start_async_backend,
                    args=(orchestrator, backend_event_loop),
                    daemon=True
                )
                backend_thread.start()
            
            # 7. Execute PyQt Application Event Loop
            exit_code = qt_app.exec()
            
            # 8. Clean up properly on exit
            cleanup_orchestrator_and_backend(orchestrator, backend_thread, backend_event_loop)
            
            return exit_code
        else:
            # User aborted setup - exit gracefully
            return 1
        
    except Exception as e:
        print(f"Critical error starting application: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main()) 