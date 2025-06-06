import logging
import sys
import os
import asyncio
import traceback
import time
import signal
import threading
from typing import Optional

# --- PHASE 1: IMMEDIATE SETUP (before any other imports or operations) ---

# Set up basic project paths first (needed for logging setup)
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Global variables for graceful shutdown
shutdown_event = threading.Event()
data_manager = None
orchestrator = None
logger = None
unified_logger = None

def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown"""
    def signal_handler(signum, frame):
        print("\n🛑 Shutdown signal received. Shutting down gracefully...")
        if logger:
            logger.info("Shutdown signal received", signal=signum)
        shutdown_event.set()
    
    # Set up signal handlers for both SIGINT (Ctrl+C) and SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def safe_async_log(coro_func, *args, timeout=2.0, **kwargs):
    """Safely execute async logging with timeout and error handling"""
    if shutdown_event.is_set():
        return  # Don't create or run coroutine if shutdown is in progress
    
    try:
        # Run the coroutine in a separate thread to avoid blocking
        import threading
        
        def run_async():
            try:
                # Create a new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Create the coroutine inside the thread
                    coro = coro_func(*args, **kwargs)
                    # Run with timeout
                    loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
                finally:
                    loop.close()
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Timeout or cancellation is fine during shutdown
                pass
            except Exception as e:
                # Log error but don't block shutdown
                if logger:
                    try:
                        logger.warning(f"Failed to send async log: {e}")
                    except:
                        pass
        
        # Run in a daemon thread so it doesn't block shutdown
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()
        
    except Exception as e:
        # Log error but don't block shutdown
        if logger:
            try:
                logger.warning(f"Failed to start async log thread: {e}")
            except:
                pass

# Set up signal handlers immediately
setup_signal_handlers()

# --- PHASE 1.5: INFRASTRUCTURE SETUP VERIFICATION (before logging with DB) ---
print("Performing infrastructure setup verification...")

# Smart infrastructure check - fast and intelligent
from src.utils.setup_wizard import smart_setup_check, run_setup_wizard, print_setup_status, get_compose_command_windows_wsl2
from src.utils.terminal_ui import ui

setup_status = smart_setup_check()
infrastructure_ready = setup_status['ready']

if setup_status['first_time']:
    ui.print_welcome_banner()
    ui.show_info(setup_status['message'])
    ui.show_info("Let's get your infrastructure set up...")
    
    try:
        if ui.confirm_setup():
            if run_setup_wizard():
                ui.show_success("Setup completed successfully!")
                infrastructure_ready = True
            else:
                ui.show_error("Setup failed. Please check the errors above and try again.")
                sys.exit(1)
        else:
            ui.show_info("Please set up the infrastructure manually and try again.")
            ui.show_info("You can run the setup wizard anytime with:")
            ui.show_info("python -c \"from src.utils.setup_wizard import run_setup_wizard; run_setup_wizard()\"")
            sys.exit(1)
    except KeyboardInterrupt:
        ui.show_warning("Setup interrupted by user. Exiting...")
        ui.show_info("You can run the setup wizard anytime with:")
        ui.show_info("python -c \"from src.utils.setup_wizard import run_setup_wizard; run_setup_wizard()\"")
        sys.exit(0)

elif setup_status['needs_restart']:
    if setup_status.get('docker_issue', False):
        # Specific Docker issue detected
        ui.show_docker_not_running_error()
        
        # Ask if user wants to continue without Docker
        try:
            if ui.confirm_continue_without_docker():
                ui.show_monitoring_only_mode()
                infrastructure_ready = False
            else:
                ui.show_info("Please start Docker Desktop and try again.")
                sys.exit(1)
        except KeyboardInterrupt:
            ui.show_warning("Interrupted by user. Exiting...")
            sys.exit(0)
    else:
        # Regular restart scenario
        ui.show_warning(setup_status['message'])
        
        try:
            if ui.confirm_start_services():
                ui.show_info("Starting Docker services...")
                import subprocess
                try:
                    # Get the appropriate compose command for WSL2
                    compose_cmd_parts = get_compose_command_windows_wsl2()
                    if not compose_cmd_parts:
                        ui.show_error("Docker Compose not found. Please ensure Docker Engine is correctly installed in WSL2.")
                        if ui.confirm_continue_without_docker():
                            ui.show_monitoring_only_mode()
                            infrastructure_ready = False
                        else:
                            sys.exit(1)
                    else:
                        with ui.with_progress_context("Starting services...") as progress:
                            result = subprocess.run(compose_cmd_parts + ['up', '-d'], 
                                                  capture_output=True, text=True, timeout=30, shell=True)
                            
                        if result.returncode == 0:
                            ui.show_success("Services started successfully!")
                            infrastructure_ready = True
                            # Give services a moment to start
                            import time
                            time.sleep(3)
                        else:
                            # Check for Docker-specific errors
                            error_msg = result.stderr.lower()
                            if "pipe" in error_msg or "cannot connect" in error_msg:
                                ui.show_docker_not_running_error()
                            elif "permission" in error_msg:
                                ui.show_docker_permission_error()
                            else:
                                ui.show_error(f"Failed to start services: {result.stderr}")
                            
                            # Ask if user wants to continue without Docker
                            if ui.confirm_continue_without_docker():
                                ui.show_monitoring_only_mode()
                                infrastructure_ready = False
                            else:
                                ui.show_info("Please fix Docker issues and try again.")
                                sys.exit(1)
                except Exception as e:
                    ui.show_error(f"Error starting services: {e}")
                    
                    # Ask if user wants to continue without Docker
                    if ui.confirm_continue_without_docker():
                        ui.show_monitoring_only_mode()
                        infrastructure_ready = False
                    else:
                        sys.exit(1)
            else:
                ui.show_warning("Continuing without database logging...")
                infrastructure_ready = False
        except KeyboardInterrupt:
            ui.show_warning("Interrupted by user. Continuing without database logging...")
            infrastructure_ready = False

elif setup_status['needs_full_setup']:
    ui.show_error(f"Infrastructure issue detected: {setup_status['message']}")
    from src.utils.setup_wizard import print_setup_status
    print_setup_status()
    
    ui.show_info("Options:")
    ui.show_info("1. Run automated setup wizard")
    ui.show_info("2. Exit and set up manually")
    
    try:
        choice = input("\nChoose an option (1/2): ").strip()
        
        if choice == '1':
            if run_setup_wizard():
                ui.show_success("Setup completed successfully!")
                infrastructure_ready = True
            else:
                ui.show_error("Setup failed. Please check the errors above and try again.")
                sys.exit(1)
        else:
            ui.show_info("Please set up the infrastructure manually and try again.")
            ui.show_info("You can run the setup wizard anytime with:")
            ui.show_info("python -c \"from src.utils.setup_wizard import run_setup_wizard; run_setup_wizard()\"")
            sys.exit(1)
    except KeyboardInterrupt:
        ui.show_warning("Setup interrupted by user. Exiting...")
        ui.show_info("You can run the setup wizard anytime with:")
        ui.show_info("python -c \"from src.utils.setup_wizard import run_setup_wizard; run_setup_wizard()\"")
        sys.exit(0)

else:
    # Infrastructure is ready
    ui.show_success(setup_status['message'])
    infrastructure_ready = True

# --- PHASE 2: LOGGING INITIALIZATION (with conditional database logging) ---

# Import the UnifiedLoggingManager and global functions for Task 6.1 integration
from src.managers.unified_logging_manager_v2 import (
    UnifiedLoggingManager, 
    log_info, log_error, log_warning, log_critical, log_debug, get_logging_manager, set_logging_manager
)
from src.managers.unified_logging_definitions import LogSource, LogLevel
from src.utils.config_loader import load_app_config
import os
import asyncio

async def setup_unified_logging():
    """Setup the UnifiedLoggingManager according to Task 6.1 requirements"""
    # Load application configuration
    yaml_config_path = os.path.join(PROJECT_ROOT_DIR, "config", "main_config.yaml")
    env_config_path = os.path.join(PROJECT_ROOT_DIR, ".env")
    
    try:
        # Load configuration using the config loader
        app_config = load_app_config(yaml_path=yaml_config_path, env_path=env_config_path)
        
        # Extract InfluxDB configuration into the format expected by UnifiedLoggingManager
        influx_config = None
        if (app_config.get("enable_influxdb", False) and 
            app_config.get("influxdb_url") and 
            app_config.get("influxdb_token")):
            influx_config = {
                "url": app_config["influxdb_url"],
                "token": app_config["influxdb_token"],
                "org": app_config.get("influxdb_org", "tower_hooker"),
                "bucket": app_config.get("influxdb_bucket", "metrics")
            }
        
        # Initialize UnifiedLoggingManager with loaded configuration
        manager = UnifiedLoggingManager(
            enable_console=app_config.get('logging_console_enabled', True),
            console_min_level_str=app_config.get('logging_console_min_level_str', 'INFO'),
            console_filters_config=app_config.get('logging_console_filters', {}),
            fallback_logger_config={
                'emergency_file_path': app_config.get('logging_file_fallback_emergency_log_path'),
                'max_bytes': app_config.get('logging_file_fallback_max_bytes', 5*1024*1024),
                'backup_count': app_config.get('logging_file_fallback_backup_count', 2),
            },
            loki_failure_fallback_config={
                'file_path': app_config.get('logging_file_fallback_loki_failure_log_path'),
                'max_bytes': app_config.get('logging_file_fallback_max_bytes', 5*1024*1024),
                'backup_count': app_config.get('logging_file_fallback_backup_count', 2),
            },
            enable_loki=app_config.get('enable_loki', False),
            loki_url=app_config.get('loki_url'),
            loki_default_labels=app_config.get('loki_default_labels', {}),
            enable_influxdb=app_config.get('enable_influxdb', False),
            influx_config=influx_config
        )
        
        # Make the manager globally accessible
        set_logging_manager(manager)
        
        # Start the log processor
        await manager.start_log_processor()
        
        # Initial log messages using the new system
        log_info(LogSource.MAIN_APP, "UnifiedLoggingManager initialized successfully")
        log_info(LogSource.MAIN_APP, "Infrastructure setup verification passed - all services are properly configured", 
                infrastructure_ready=infrastructure_ready)
        
        # Log configuration status
        if app_config.get("enable_loki", False):
            if app_config.get("loki_url"):
                log_info(LogSource.MAIN_APP, "Loki logging enabled", loki_url=app_config["loki_url"])
            else:
                log_warning(LogSource.MAIN_APP, "Loki enabled but no URL configured")
        
        if app_config.get("enable_influxdb", False):
            if influx_config:
                log_info(LogSource.MAIN_APP, "InfluxDB logging enabled", influxdb_url=app_config["influxdb_url"])
            else:
                log_warning(LogSource.MAIN_APP, "InfluxDB enabled but configuration incomplete")
        
        return manager
        
    except Exception as e:
        # Fallback to basic logging if UnifiedLoggingManager fails to initialize
        print(f"Failed to initialize UnifiedLoggingManager: {e}")
        print("Falling back to basic logging...")
        
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('main')
        
        logger.error(f"UnifiedLoggingManager initialization failed: {e}")
        logger.info("Using fallback logging system")
        
        return None

# Create a simple wrapper function for logging compatibility with existing code
class LoggerWrapper:
    def __init__(self):
        pass
        
    def info(self, message, **kwargs):
        log_info(LogSource.MAIN_APP, message, **kwargs)
        
    def warning(self, message, **kwargs):
        log_warning(LogSource.MAIN_APP, message, **kwargs)
        
    def error(self, message, **kwargs):
        log_error(LogSource.MAIN_APP, message, **kwargs)
        
    def critical(self, message, **kwargs):
        log_critical(LogSource.MAIN_APP, message, **kwargs)
        
    def debug(self, message, **kwargs):
        log_debug(LogSource.MAIN_APP, message, **kwargs)

# Set up the unified logging system
def start_logging_system():
    """Start the unified logging system in a background thread"""
    def run_setup():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            manager = loop.run_until_complete(setup_unified_logging())
            if manager:
                # Keep the event loop running for the log processor
                loop.run_forever()
        except Exception as e:
            print(f"Error in logging system: {e}")
        finally:
            loop.close()
    
    import threading
    logging_thread = threading.Thread(target=run_setup, daemon=True)
    logging_thread.start()
    
    # Give the logging system a moment to start
    import time
    time.sleep(0.5)
    
    return LoggerWrapper()

# Initialize the logging system and create logger wrapper
try:
    logger = start_logging_system()
    unified_logger = get_logging_manager()
except Exception as e:
    # Ultimate fallback
    print(f"Critical error starting logging system: {e}")
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('main')
    unified_logger = None

# Initialize unified data ingestion manager if infrastructure is ready
if infrastructure_ready:
    try:
        from src.managers.database_manager import DatabaseManager
        from src.managers.data_ingestion_manager import DataIngestionManager
        
        db_manager = DatabaseManager()
        if db_manager:
            data_manager = DataIngestionManager(db_manager)
            logger.info("Unified data ingestion manager initialized")
            
            # Log application startup through unified system
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Tower Hooker application starting",
                level="INFO",
                extra_data={"phase": "startup", "infrastructure_ready": infrastructure_ready}
            )
        else:
            logger.warning("Database manager not available - unified data ingestion disabled")
    except Exception as e:
        logger.warning(f"Failed to initialize unified data ingestion manager: {e}")
        logger.warning("Continuing with standard logging only")

# --- PHASE 3: IMPORT OTHER MODULES (after logging is set up) ---
logger.info("Importing application modules...")
from src.frida_app import AppOrchestrator 
from src.utils.config import get_default_target_package, hook_script_path
logger.info("Application modules imported successfully")

def get_target_package(orchestrator):
    """Get target package from user input or use default."""
    if shutdown_event.is_set():
        return None
        
    target_package = ""
    default_pid = None
    
    # Get the default target package using the proper getter function
    default_target_package = get_default_target_package()
    
    # Validate that we have a default target package
    if not default_target_package:
        logger.error("No default target package configured. Please check DEFAULT_TARGET_PACKAGE in main_config.yaml")
        try:
            target_package_input = ui.get_target_package("", None)
            if not target_package_input:
                logger.error("No target package specified. Exiting.")
                sys.exit(1)
            return target_package_input
        except RuntimeError:
            logger.error("Input not available and no default target package configured. Exiting.")
            sys.exit(1)
    
    # Check if default package is running
    if hasattr(orchestrator, 'emulator_manager') and orchestrator.emulator_manager is not None:
        default_pid = orchestrator.emulator_manager.get_pid_for_package(default_target_package)
    else:
        logger.warning("BlueStacks helper not available for PID check.")
    
    # Use the sleek UI to get target package with timeout
    try:
        # Cross-platform timeout implementation
        import threading
        import queue
        
        result_queue = queue.Queue()
        input_exception = None
        
        def input_thread():
            nonlocal input_exception
            try:
                result = ui.get_target_package(default_target_package, default_pid)
                result_queue.put(result)
            except Exception as e:
                input_exception = e
                result_queue.put(None)
        
        # Start input thread
        thread = threading.Thread(target=input_thread, daemon=True)
        thread.start()
        
        # Wait for result with timeout
        try:
            target_package = result_queue.get(timeout=10.0)  # 10 second timeout
            if input_exception:
                raise input_exception
        except queue.Empty:
            logger.info("Input timeout reached, using default target")
            target_package = default_target_package
            
    except (RuntimeError, EOFError, TimeoutError, KeyboardInterrupt):
        logger.info("Input not available or interrupted. Using default target.")
        target_package = default_target_package
    
    if not target_package:
        logger.error("No target package specified. Exiting.")
        sys.exit(1)
    
    return target_package

def keep_alive():
    """Keep the main thread alive while monitoring."""
    ui.show_application_running()
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)  # Check every second
    except KeyboardInterrupt:
        pass  # Signal handler will have set shutdown_event
    finally:
        ui.show_shutdown_message()

def cleanup_application():
    """Clean up application resources"""
    global orchestrator, data_manager, logger, unified_logger
    
    try:
        if orchestrator:
            logger.info("Shutting down orchestrator...")
            orchestrator.shutdown()
            orchestrator = None
    except Exception as e:
        if logger:
            logger.error(f"Error during orchestrator shutdown: {e}")
    
    try:
        if data_manager:
            # Final log before shutdown
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Application shutdown complete",
                level="INFO",
                extra_data={"status": "shutdown_complete"}
            )
            data_manager = None
    except Exception as e:
        if logger:
            logger.error(f"Error during data manager cleanup: {e}")
    
    # Shutdown UnifiedLoggingManager if it was initialized
    try:
        if 'unified_logger' in globals() and unified_logger is not None:
            logger.info("Shutting down UnifiedLoggingManager...")
            
            # Create a simple sync wrapper to shutdown the async manager
            def shutdown_ulm():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(unified_logger.shutdown())
                finally:
                    loop.close()
            
            import threading
            shutdown_thread = threading.Thread(target=shutdown_ulm)
            shutdown_thread.start()
            shutdown_thread.join(timeout=5.0)  # Wait max 5 seconds for shutdown
            
            unified_logger = None
    except Exception as e:
        if logger:
            logger.error(f"Error during UnifiedLoggingManager shutdown: {e}")
    
    if logger:
        logger.info("Application cleanup complete")

def run_application():
    """Initialize and run the Frida Tower Logger application."""
    global orchestrator, data_manager, logger
    
    logger.info("Starting Frida Tower Logger application...")
    
    try:
        orchestrator = AppOrchestrator()

        # Phase 1: Initialize dependencies and database
        logger.info("Phase 1: Initializing dependencies and database...")
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Phase 1: Initializing dependencies and database",
                level="INFO",
                extra_data={"phase": "dependency_init"}
            )
        
        if shutdown_event.is_set():
            return
            
        local_frida_server_path = orchestrator.initialize_dependencies_and_db()
        if not local_frida_server_path:
            logger.critical("Critical error during dependency initialization. Exiting.")
            if data_manager:
                safe_async_log(data_manager.ingest_system_log,
                    source="th_main_app",
                    message="Critical error during dependency initialization",
                    level="CRITICAL",
                    extra_data={"phase": "dependency_init", "status": "failed"}
                )
            sys.exit(1)
        log_info(LogSource.MAIN_APP, "Dependencies initialized.", frida_server_path=local_frida_server_path)
        
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Dependencies initialized successfully",
                level="INFO",
                extra_data={"phase": "dependency_init", "status": "success", "frida_server_path": local_frida_server_path}
            )

        if shutdown_event.is_set():
            return

        # Phase 2: Setup emulator and Frida
        logger.info("Phase 2: Setting up BlueStacks and Frida server...")
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Phase 2: Setting up BlueStacks and Frida server",
                level="INFO",
                extra_data={"phase": "bluestacks_frida_setup"}
            )
        
        orchestrator.setup_bluestacks_and_frida(local_frida_server_path)
        logger.info("BlueStacks and Frida server setup complete.")
        
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="BlueStacks and Frida server setup complete",
                level="INFO",
                extra_data={"phase": "bluestacks_frida_setup", "status": "success"}
            )

        if shutdown_event.is_set():
            return

        # Set Frida device on injector
        if orchestrator.frida_device:
            log_info(LogSource.MAIN_APP, "Setting Frida device on injector:", frida_device=orchestrator.frida_device.name)
            orchestrator.injector.device = orchestrator.frida_device
            
            if data_manager:
                safe_async_log(data_manager.ingest_system_log,
                    source="th_main_app",
                    message="Frida device set on injector",
                    level="INFO",
                    extra_data={"frida_device": orchestrator.frida_device.name, "device_id": orchestrator.frida_device.id}
                )
        else:
            logger.warning("No Frida device available. Limited functionality (no code injection).")
            if data_manager:
                safe_async_log(data_manager.ingest_system_log,
                    source="th_main_app",
                    message="No Frida device available - limited functionality",
                    level="WARNING",
                    extra_data={"functionality": "limited", "code_injection": False}
                )

        if shutdown_event.is_set():
            return

        # Phase 3: Get target and run hook
        target_package = get_target_package(orchestrator)
        if not target_package or shutdown_event.is_set():
            return
            
        log_info(LogSource.MAIN_APP, "Selected target package:", target=target_package)
        
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Target package selected",
                level="INFO",
                extra_data={"target_package": target_package, "phase": "target_selection"}
            )

        logger.info("Phase 3: Running hook on target...")
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Phase 3: Running hook on target",
                level="INFO",
                extra_data={"phase": "hook_execution", "target_package": target_package}
            )
        
        if shutdown_event.is_set():
            return
            
        success = False
        if orchestrator.frida_device and orchestrator.injector:
            logger.info("Frida device available. Attempting to run hook...")
            success = orchestrator.run_hook_on_target(target_package, hook_script_path)
            
            if data_manager:
                safe_async_log(data_manager.ingest_system_log,
                    source="th_main_app",
                    message="Hook execution completed",
                    level="INFO" if success else "ERROR",
                    extra_data={"phase": "hook_execution", "success": success, "target_package": target_package}
                )
        else:
            logger.warning("Skipping hook phase. Running in monitoring-only mode.")
            if data_manager:
                safe_async_log(data_manager.ingest_system_log,
                    source="th_main_app",
                    message="Running in monitoring-only mode",
                    level="WARNING",
                    extra_data={"mode": "monitoring_only", "hook_execution": False}
                )
            success = True

        if success and not shutdown_event.is_set():
            if data_manager:
                safe_async_log(data_manager.ingest_system_log,
                    source="th_main_app",
                    message="Application startup completed successfully - entering monitoring mode",
                    level="INFO",
                    extra_data={"status": "running", "mode": "monitoring"}
                )
            keep_alive()
        else:
            if not shutdown_event.is_set():
                logger.error("Failed to run hook on target. Check logs for errors.")
                if data_manager:
                    safe_async_log(data_manager.ingest_system_log,
                        source="th_main_app",
                        message="Failed to run hook on target",
                        level="ERROR",
                        extra_data={"status": "failed", "target_package": target_package}
                    )

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Initiating shutdown...")
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Application shutdown initiated by user",
                level="INFO",
                extra_data={"shutdown_reason": "keyboard_interrupt"}
            )
    except SystemExit as e:
        log_info(LogSource.MAIN_APP, "SystemExit caught:", e=e, proceeding="to shutdown")
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Application shutdown via SystemExit",
                level="INFO",
                extra_data={"shutdown_reason": "system_exit", "exit_code": str(e)}
            )
    except ImportError as e:
        log_error(LogSource.MAIN_APP, "ImportError:", error=e)
        ui.show_error("Failed to import required modules. Check your Python environment and dependencies.")
        log_error(LogSource.MAIN_APP, "PROJECT_ROOT_DIR:", PROJECT_ROOT_DIR=PROJECT_ROOT_DIR)
        log_error(LogSource.MAIN_APP, "SRC_DIR:", SRC_DIR=SRC_DIR)
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Application failed due to ImportError",
                level="ERROR",
                extra_data={"error_type": "ImportError", "error": str(e)}
            )
        sys.exit(1)
    except Exception as e:
        log_error(LogSource.MAIN_APP, "Unhandled error:", error=e)
        traceback.print_exc()
        if data_manager:
            safe_async_log(data_manager.ingest_system_log,
                source="th_main_app",
                message="Application failed due to unhandled error",
                level="ERROR",
                extra_data={"error_type": type(e).__name__, "error": str(e)}
            )
    finally:
        cleanup_application()

if __name__ == "__main__":
    logger.info("Starting application via main.py")
    try:
        run_application()
    except Exception as e:
        logger.exception("Application failed", error=str(e))
        raise
    finally:
        logger.info("Application shutdown complete") 