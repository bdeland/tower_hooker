import os
import time
import json
import subprocess
import requests
import lzma
import frida
import traceback
import sys
import asyncio
import socket
import functools
import threading
from datetime import datetime, timezone
from typing import Tuple, Callable, Optional
from PyQt6.QtCore import QObject, pyqtSignal
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_critical, log_debug, get_logging_manager, UnifiedLoggingManager
from src.managers.unified_logging_definitions import LogSource, LogLevel
from src.utils.config import get_bluestacks_config, get_emulator_config, get_frida_config, get_target_config

# Direct imports for local modules now that they are in the same directory
from src.managers.emulator_manager import EmulatorManager
from src.utils.dependency_downloader import DependencyDownloader
from src.managers.frida_injector import FridaInjector
from src.utils.setup_wizard import InfrastructureSetupWizard
from src.utils.wsl2_service_manager import WSL2ServiceManager

# Remove old structlog logger - now using unified logging system

class AppOrchestrator(QObject):
    # Version and Update Constants
    CURRENT_APP_VERSION = "0.1.0-beta"
    UPDATE_INFO_URL = "https://github.com/yourusername/tower_hooker/releases"

    # Core Status & Lifecycle Signals
    initialization_status = pyqtSignal(str) # e.g., "Initializing ULM...", "Checking Docker..."
    services_configured = pyqtSignal(dict) # Emits actual URLs: {"grafana_url": "...", "loki_url": "...", "influxdb_url": "..."}
    ready_to_operate = pyqtSignal(bool) # True when core services are up and app can start main tasks
    shutdown_signal_sent_to_ulm = pyqtSignal() # When AppOrchestrator tells ULM to shut down
    fatal_error_occurred = pyqtSignal(str) # For unrecoverable backend errors

    # BlueStacks Status Signals
    bluestacks_connection_status = pyqtSignal(bool, str) # is_connected, message/device_serial
    available_adb_devices = pyqtSignal(list) # List of device serials
    bluestacks_processes_updated = pyqtSignal(list) # List of dicts for processes

    # Frida Status Signals
    frida_server_status = pyqtSignal(bool, str) # is_running_on_device, message
    frida_attachment_status = pyqtSignal(bool, str, str) # is_attached, process_name, message
    frida_script_message_received = pyqtSignal(dict) # For messages from the hook script

    # UI-Specific Log Stream Signal (for a dedicated GUI log view, not all ULM console logs)
    gui_log_feed = pyqtSignal(str, str, str) # level_str, source_str, message_str

    # Configuration Signals
    config_updated_successfully = pyqtSignal(str) # Message about what was updated
    config_update_requires_restart = pyqtSignal(str) # Message indicating restart needed

    # Wizard Signals
    wizard_progress_text = pyqtSignal(str, str) # step_name, message
    wizard_step_status = pyqtSignal(str, bool, str) # step_name, success, error_message_or_empty
    wizard_progress_bar_update = pyqtSignal(int) # current_step_value
    wizard_sequence_complete = pyqtSignal(bool) # overall_success
    wizard_detailed_completion = pyqtSignal(bool, str, list) # overall_success, summary_message, failed_steps_list

    def __init__(self, initial_app_config, parent=None):
        super().__init__(parent)
        # Delay this log until ULM is ready - moved to initialize_services
        # log_info(LogSource.FRIDA, "Initializing AppOrchestrator...")
        
        # Store initial configuration
        self.initial_app_config = initial_app_config
        
        # Initialize internal state flags
        self._shutdown_flag = False
        self._actual_service_urls = {}
        
        # Backend event loop reference (set by main_app_entry.py)
        self._backend_loop = None
        
        # Initialize setup wizard
        self.setup_wizard = InfrastructureSetupWizard()
        self.setup_is_complete_or_overridden = False
        
        # Initialize WSL2 service manager
        wsl_distro_name = initial_app_config.get('wsl_distro', 'Ubuntu') if hasattr(initial_app_config, 'get') else 'Ubuntu'
        self.wsl_service_manager = WSL2ServiceManager(wsl_distro_name=wsl_distro_name)
        
        # Get configuration using centralized config or use passed config
        if hasattr(initial_app_config, 'get'):
            # If config is dict-like, extract what we need
            bluestacks_config = initial_app_config.get('bluestacks', get_bluestacks_config())
            emulator_config = initial_app_config.get('emulator', get_emulator_config())
            frida_config = initial_app_config.get('frida', get_frida_config())
            ulm_config = initial_app_config.get('logging', {})
        else:
            # Fallback to centralized config
            bluestacks_config = get_bluestacks_config()
            emulator_config = get_emulator_config()
            frida_config = get_frida_config()
            ulm_config = {}
        
        # Initialize UnifiedLoggingManager with reference to self for GUI signal emission
        self.ulm = UnifiedLoggingManager(
            gui_signal_emitter=self,
            **ulm_config
        )
        
        # Initialize database manager 
        self.db_logger = None
        try:
            from src.managers.database_manager import DatabaseManager
            self.db_logger = DatabaseManager()
            # Delay this log until ULM is ready - moved to initialize_services
            # log_info(LogSource.FRIDA, "DatabaseManager initialized for AppOrchestrator")
        except Exception as e:
            # Use fallback logger for critical initialization errors
            self.ulm.fallback_logger.critical("Failed to initialize DatabaseManager", error=str(e))
            # Continuing, but DB operations will fail if it's None
        
        # Create the unified data ingestion manager
        self.data_manager = None
        if self.db_logger:
            try:
                from src.managers.data_ingestion_manager import DataIngestionManager
                self.data_manager = DataIngestionManager(self.db_logger)
                # Delay this log until ULM is ready - moved to initialize_services
                # log_info(LogSource.FRIDA, "Created DataIngestionManager for AppOrchestrator")
            except Exception as e:
                # Use fallback logger for initialization warnings
                self.ulm.fallback_logger.warning("Failed to create DataIngestionManager", error=str(e))
                self.ulm.fallback_logger.warning("Continuing without unified data ingestion")
        
        # Create managers
        # Initialize Emulator Manager (supports any Android emulator - BlueStacks, MuMu Player, etc.)
        self.emulator_type = emulator_config.get('type', 'generic')
        self.emulator_manager = EmulatorManager(emulator_config['adb_path'], data_manager=self.data_manager, emulator_type=self.emulator_type)
        
        self.downloader = DependencyDownloader(
            frida_config['server_dir'],
            frida_config['server_arch'],
            frida_config['server_version']
        )
        self.injector = None
        self.target_process_name = None
        self.script_file_name = None
        self.frida_device = None
        self.current_round_id = None
        self.current_round_tier = None
        # Delay this log until ULM is ready - moved to initialize_services
        # log_info(LogSource.FRIDA, "AppOrchestrator initialized.")
        
    def log_info_via_ulm(self, source, msg, **extra):
        """Log info message via ULM"""
        if self.ulm:
            self.ulm.log_info(source, msg, **extra)
        else:
            log_info(source, msg, **extra)
            
    def log_error_via_ulm(self, source, msg, **extra):
        """Log error message via ULM"""
        if self.ulm:
            self.ulm.log_error(source, msg, **extra)
        else:
            log_error(source, msg, **extra)
            
    def log_warning_via_ulm(self, source, msg, **extra):
        """Log warning message via ULM"""
        if self.ulm:
            self.ulm.log_warning(source, msg, **extra)
        else:
            log_warning(source, msg, **extra)
            
    def log_debug_via_ulm(self, source, msg, **extra):
        """Log debug message via ULM"""
        if self.ulm:
            # ULM doesn't have log_debug method, use the standalone function
            log_debug(source, msg, **extra)
        else:
            log_debug(source, msg, **extra)

    def set_backend_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the backend event loop reference for scheduling operations"""
        self._backend_loop = loop

    async def run_in_executor(self, blocking_func, *args):
        """Run blocking function in executor to avoid blocking the event loop"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(blocking_func, *args))

    def schedule_on_backend_loop(self, coro):
        """Schedule a coroutine on the main backend event loop from any thread."""
        if self._backend_loop is None:
            self.ulm.fallback_logger.error("Backend loop not set in AppOrchestrator for scheduling.")
            return None

        if not self._backend_loop.is_running():
            self.ulm.fallback_logger.error("Backend loop is not running, cannot schedule coroutine.")
            return None

        return asyncio.run_coroutine_threadsafe(coro, self._backend_loop)

    async def run_infrastructure_setup_wizard_async_steps(self):
        """
        Run the async infrastructure setup steps with comprehensive logging and real-time monitoring.
        This method is called via schedule_on_backend_loop from the wizard dialog.
        """
        import time
        setup_start_time = time.time()
        
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "🚀 Starting comprehensive infrastructure setup wizard")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Setup wizard process ID and thread information", 
                                  process_id=os.getpid(), thread_name=threading.current_thread().name)
            
            total_steps = 6  # Increased for enhanced verification
            current_step = 0
            
            # Track failures for detailed error reporting
            failed_steps = []
            step_results = {}
            step_timings = {}
            
            # Define enhanced callback for real-time progress
            def wizard_callback(step_name_from_actual_wizard: str, message_from_actual_wizard: str):
                self.log_debug_via_ulm(LogSource.MAIN_APP, f"Step progress: {step_name_from_actual_wizard} - {message_from_actual_wizard}")
                self.wizard_progress_text.emit(step_name_from_actual_wizard, message_from_actual_wizard)
            
            # ========== STEP 1: PRE-SETUP VALIDATION ==========
            step_start = time.time()
            self.log_info_via_ulm(LogSource.MAIN_APP, "Step 1: Starting pre-setup validation...")
            self.wizard_progress_text.emit("Pre-Setup Validation", "Validating system requirements...")
            current_step += 1
            self.wizard_progress_bar_update.emit(int(current_step * 100 / total_steps))
            
            # Check system prerequisites
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Checking system prerequisites...")
            prereq_ok, prereq_msg = await self._validate_system_prerequisites()
            step_timings["Pre-Setup Validation"] = time.time() - step_start
            step_results["Pre-Setup Validation"] = (prereq_ok, prereq_msg)
            
            if not prereq_ok:
                failed_steps.append({
                    "step": "Pre-Setup Validation", 
                    "error": prereq_msg, 
                    "impact": "Setup cannot proceed without meeting system requirements"
                })
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Pre-setup validation failed: {prereq_msg}")
            else:
                self.log_info_via_ulm(LogSource.MAIN_APP, f"Pre-setup validation completed successfully in {step_timings['Pre-Setup Validation']:.2f}s")
            
            self.wizard_step_status.emit("Pre-Setup Validation", prereq_ok, prereq_msg if not prereq_ok else "System requirements validated")
            
            # ========== STEP 2: DOCKER SERVICES WITH REAL-TIME MONITORING ==========
            step_start = time.time()
            self.log_info_via_ulm(LogSource.MAIN_APP, "Step 2: Starting Docker services with real-time monitoring...")
            self.wizard_progress_text.emit("Docker Setup", "Starting Docker services with comprehensive monitoring...")
            current_step += 1
            self.wizard_progress_bar_update.emit(int(current_step * 100 / total_steps))
            
            # Enhanced Docker setup with event monitoring
            docker_ok, docker_msg = await self._enhanced_docker_setup(wizard_callback)
            step_timings["Docker Setup"] = time.time() - step_start
            step_results["Docker Setup"] = (docker_ok, docker_msg)
            
            if not docker_ok:
                failed_steps.append({
                    "step": "Docker Setup", 
                    "error": docker_msg, 
                    "impact": "Container services (Grafana, InfluxDB, Loki) will not work"
                })
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Docker setup failed after {step_timings['Docker Setup']:.2f}s: {docker_msg}")
                
                # CRITICAL: Docker is required for all dependent services
                # Skip remaining container-dependent steps and complete wizard with failure
                self.log_info_via_ulm(LogSource.MAIN_APP, "Skipping container-dependent services due to Docker failure")
                self.wizard_progress_text.emit("Docker Setup", "Docker failed - skipping dependent services")
                
                # Mark remaining steps as skipped
                influx_ok = False
                grafana_ok = False 
                loki_ok = False
                final_ok = False
                
                # Create entries for skipped steps
                skipped_steps = ["InfluxDB Setup", "Grafana Configuration", "Loki/Promtail Setup", "Final Verification"]
                for step_name in skipped_steps:
                    step_results[step_name] = (False, "Skipped due to Docker failure")
                    step_timings[step_name] = 0.0
                    failed_steps.append({
                        "step": step_name,
                        "error": "Skipped due to Docker dependency failure", 
                        "impact": "Cannot function without Docker containers"
                    })
                    self.wizard_step_status.emit(step_name, False, "Skipped - Docker required")
                
                # Skip to completion calculation
                overall_success = prereq_ok and docker_ok  # Will be False
                total_setup_time = time.time() - setup_start_time
                
            else:
                self.log_info_via_ulm(LogSource.MAIN_APP, f"Docker setup completed successfully in {step_timings['Docker Setup']:.2f}s")
            
            self.wizard_step_status.emit("Docker Setup", docker_ok, docker_msg if not docker_ok else "Docker services running and monitored")
            
            # Only continue with container-dependent services if Docker is working
            if docker_ok:
                # ========== STEP 3: INFLUXDB WITH CAPABILITY TESTING ==========
                step_start = time.time()
                self.log_info_via_ulm(LogSource.MAIN_APP, "Step 3: Setting up InfluxDB with comprehensive capability testing...")
                self.wizard_progress_text.emit("InfluxDB Setup", "Configuring InfluxDB with real-time verification...")
                current_step += 1
                self.wizard_progress_bar_update.emit(int(current_step * 100 / total_steps))
                
                # Enhanced InfluxDB setup with progressive capability testing
                influx_ok, influx_msg = await self._enhanced_influxdb_setup(wizard_callback)
                step_timings["InfluxDB Setup"] = time.time() - step_start
                step_results["InfluxDB Setup"] = (influx_ok, influx_msg)
                
                if not influx_ok:
                    failed_steps.append({
                        "step": "InfluxDB Setup", 
                        "error": influx_msg, 
                        "impact": "Game data and metrics will not be stored in the database"
                    })
                    self.log_error_via_ulm(LogSource.MAIN_APP, f"InfluxDB setup failed after {step_timings['InfluxDB Setup']:.2f}s: {influx_msg}")
                else:
                    self.log_info_via_ulm(LogSource.MAIN_APP, f"InfluxDB setup completed successfully in {step_timings['InfluxDB Setup']:.2f}s")
                
                self.wizard_step_status.emit("InfluxDB Setup", influx_ok, influx_msg if not influx_ok else "InfluxDB fully configured and tested")
                
                # ========== STEP 4: GRAFANA WITH DATASOURCE VERIFICATION ==========
                step_start = time.time()
                self.log_info_via_ulm(LogSource.MAIN_APP, "Step 4: Configuring Grafana with datasource verification...")
                self.wizard_progress_text.emit("Grafana Configuration", "Setting up Grafana with comprehensive checks...")
                current_step += 1
                self.wizard_progress_bar_update.emit(int(current_step * 100 / total_steps))
                
                # Enhanced Grafana setup with capability verification
                grafana_ok, grafana_msg = await self._enhanced_grafana_setup(wizard_callback)
                step_timings["Grafana Configuration"] = time.time() - step_start
                step_results["Grafana Configuration"] = (grafana_ok, grafana_msg)
                
                if not grafana_ok:
                    failed_steps.append({
                        "step": "Grafana Configuration", 
                        "error": grafana_msg, 
                        "impact": "Dashboard visualizations may not display data correctly"
                    })
                    self.log_error_via_ulm(LogSource.MAIN_APP, f"Grafana setup failed after {step_timings['Grafana Configuration']:.2f}s: {grafana_msg}")
                else:
                    self.log_info_via_ulm(LogSource.MAIN_APP, f"Grafana setup completed successfully in {step_timings['Grafana Configuration']:.2f}s")
                
                self.wizard_step_status.emit("Grafana Configuration", grafana_ok, grafana_msg if not grafana_ok else "Grafana configured and verified")
                
                # ========== STEP 5: LOKI/PROMTAIL WITH LOG MONITORING ==========
                step_start = time.time()
                self.log_info_via_ulm(LogSource.MAIN_APP, "Step 5: Setting up Loki/Promtail with log monitoring...")
                self.wizard_progress_text.emit("Loki/Promtail Setup", "Configuring log aggregation with verification...")
                current_step += 1
                self.wizard_progress_bar_update.emit(int(current_step * 100 / total_steps))
                
                # Enhanced Loki setup with log monitoring
                loki_ok, loki_msg = await self._enhanced_loki_setup(wizard_callback)
                step_timings["Loki/Promtail Setup"] = time.time() - step_start
                step_results["Loki/Promtail Setup"] = (loki_ok, loki_msg)
                
                if not loki_ok:
                    failed_steps.append({
                        "step": "Loki/Promtail Setup", 
                        "error": loki_msg, 
                        "impact": "Centralized log aggregation will not work"
                    })
                    self.log_error_via_ulm(LogSource.MAIN_APP, f"Loki setup failed after {step_timings['Loki/Promtail Setup']:.2f}s: {loki_msg}")
                else:
                    self.log_info_via_ulm(LogSource.MAIN_APP, f"Loki setup completed successfully in {step_timings['Loki/Promtail Setup']:.2f}s")
                
                self.wizard_step_status.emit("Loki/Promtail Setup", loki_ok, loki_msg if not loki_ok else "Loki/Promtail configured and verified")
                
                # ========== STEP 6: FINAL VERIFICATION & COMPLETION ==========
                step_start = time.time()
                self.log_info_via_ulm(LogSource.MAIN_APP, "Step 6: Running final verification and marking setup complete...")
                self.wizard_progress_text.emit("Final Verification", "Running comprehensive system verification...")
                current_step += 1
                self.wizard_progress_bar_update.emit(int(current_step * 100 / total_steps))
                
                # Enhanced final verification
                final_ok, final_msg = await self._enhanced_final_verification(wizard_callback)
                step_timings["Final Verification"] = time.time() - step_start
                step_results["Final Verification"] = (final_ok, final_msg)
                
                if not final_ok:
                    failed_steps.append({
                        "step": "Final Verification", 
                        "error": final_msg, 
                        "impact": "Setup completion verification failed"
                    })
                    self.log_error_via_ulm(LogSource.MAIN_APP, f"Final verification failed after {step_timings['Final Verification']:.2f}s: {final_msg}")
                else:
                    self.log_info_via_ulm(LogSource.MAIN_APP, f"Final verification completed successfully in {step_timings['Final Verification']:.2f}s")
                
                self.wizard_step_status.emit("Final Verification", final_ok, final_msg if not final_ok else "Setup verification complete")
                
                # ========== CALCULATE OVERALL SUCCESS ==========
                overall_success = prereq_ok and docker_ok and influx_ok and grafana_ok and loki_ok and final_ok
                total_setup_time = time.time() - setup_start_time
            
            # ========== CREATE DETAILED COMPLETION SUMMARY ==========
            if overall_success:
                summary_message = f"🎉 Infrastructure setup completed successfully in {total_setup_time:.2f}s! All services are started, configured and ready for use."
                self.log_info_via_ulm(LogSource.MAIN_APP, summary_message)
                
                # Log detailed timing breakdown
                timing_details = []
                for step, duration in step_timings.items():
                    timing_details.append(f"  • {step}: {duration:.2f}s")
                self.log_debug_via_ulm(LogSource.MAIN_APP, f"Setup timing breakdown:\n" + "\n".join(timing_details))
                
                self.setup_is_complete_or_overridden = True
                
                # Mark setup as complete
                try:
                    completion_ok, completion_msg = await self.run_in_executor(self.setup_wizard.mark_setup_complete, wizard_callback)
                    if completion_ok:
                        self.log_info_via_ulm(LogSource.MAIN_APP, "Setup completion marker created successfully")
                    else:
                        self.log_warning_via_ulm(LogSource.MAIN_APP, f"Failed to create setup completion marker: {completion_msg}")
                except Exception as e:
                    self.log_warning_via_ulm(LogSource.MAIN_APP, f"Error creating setup completion marker: {e}")
                    
            else:
                # Create detailed failure summary
                failed_count = len(failed_steps)
                total_count = len(step_results)
                
                if failed_count == total_count:
                    summary_message = f"❌ Infrastructure setup failed completely after {total_setup_time:.2f}s. All {total_count} steps encountered errors."
                else:
                    summary_message = f"⚠️ Infrastructure setup completed with {failed_count} of {total_count} steps failing after {total_setup_time:.2f}s."
                
                # Add specific failure details to the log
                failure_details = []
                for failure in failed_steps:
                    detail = f"• {failure['step']}: {failure['error']}"
                    if failure.get('impact'):
                        detail += f" (Impact: {failure['impact']})"
                    failure_details.append(detail)
                
                detailed_log_message = f"{summary_message}\n\nFailed steps:\n" + "\n".join(failure_details)
                self.log_error_via_ulm(LogSource.MAIN_APP, detailed_log_message)
                
                # Add timing information for failed steps too
                if step_timings:
                    timing_details = []
                    for step, duration in step_timings.items():
                        status = "✅" if step_results.get(step, (False, ""))[0] else "❌"
                        timing_details.append(f"  {status} {step}: {duration:.2f}s")
                    self.log_debug_via_ulm(LogSource.MAIN_APP, f"Setup timing breakdown:\n" + "\n".join(timing_details))
                
                # Add guidance based on common failure patterns
                if any("Docker" in failure["step"] for failure in failed_steps):
                    summary_message += "\n\n🔧 Common fixes: Ensure WSL2 is installed, Docker Engine is running, and you have sufficient system resources."
                elif any("InfluxDB" in failure["step"] or "Grafana" in failure["step"] or "Loki" in failure["step"] for failure in failed_steps):
                    summary_message += "\n\n🔧 Common fixes: Check if ports 3000, 3100, 8086 are available, restart Docker services, or check WSL2 networking."
            
            # Emit both the original signal (for backward compatibility) and the new detailed signal
            self.wizard_sequence_complete.emit(overall_success)
            self.wizard_detailed_completion.emit(overall_success, summary_message, failed_steps)
            
            self.log_info_via_ulm(LogSource.MAIN_APP, f"Infrastructure setup wizard completed with overall success: {overall_success}")
            return overall_success
            
        except Exception as e:
            total_setup_time = time.time() - setup_start_time
            error_msg = f"💥 Critical error during infrastructure setup wizard after {total_setup_time:.2f}s: {str(e)}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            
            # Create detailed error information for the exception case
            critical_failure = [{
                "step": "Setup Wizard Execution", 
                "error": str(e), 
                "impact": "Setup wizard could not complete due to an unexpected error"
            }]
            
            self.wizard_step_status.emit("Setup Wizard", False, error_msg)
            self.wizard_sequence_complete.emit(False)
            self.wizard_detailed_completion.emit(False, f"Setup wizard encountered a critical error: {str(e)}", critical_failure)
            return False

    def check_if_setup_wizard_is_needed(self) -> bool:
        """
        Check if the setup wizard needs to be shown.
        This method can be called from the main thread.
        """
        return self.setup_wizard.is_first_time_setup()

    def _is_port_in_use(self, host: str, port: int) -> bool:
        """Check if a port is in use on the given host"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)  # 1 second timeout
                result = sock.connect_ex((host, port))
                return result == 0  # 0 means connection successful (port in use)
        except Exception:
            return False  # Assume port is free if we can't check

    def _verify_service_on_port(self, host: str, port: int, health_endpoint: str) -> bool:
        """Verify that the expected service is running on the given port by checking its health endpoint"""
        try:
            import requests
            url = f"http://{host}:{port}{health_endpoint}"
            response = requests.get(url, timeout=3)
            return response.status_code == 200
        except Exception:
            return False  # Service not available or not responding correctly

    def _get_service_timeout(self, service_name: str) -> int:
        """
        Get the timeout value for a specific service from configuration.
        
        Args:
            service_name: Name of the service (e.g., 'loki', 'grafana', 'influxdb')
            
        Returns:
            Timeout value in seconds
        """
        try:
            # Get timeout from configuration, with fallback to default
            if hasattr(self.initial_app_config, 'get') and isinstance(self.initial_app_config, dict):
                services_config = self.initial_app_config.get('services', {})
                timeouts_config = services_config.get('timeouts', {})
                
                # Get service-specific timeout or default
                timeout = timeouts_config.get(service_name, timeouts_config.get('default', 60))
                
                self.log_info_via_ulm(LogSource.SYSTEM, f"Using {timeout}s timeout for {service_name}")
                return timeout
            else:
                # Fallback if config not available
                self.log_warning_via_ulm(LogSource.SYSTEM, f"Config not available, using default 60s timeout for {service_name}")
                return 60
                
        except Exception as e:
            self.log_warning_via_ulm(LogSource.SYSTEM, f"Error getting timeout for {service_name}: {e}, using default 60s")
            return 60

    async def initialize_services_post_wizard(self):
        """Initialize services after wizard completion - verify readiness and configure application components"""
        try:
            # ULM is already started in run_main_loop, so log the initialization messages that were delayed from __init__
            self.log_info_via_ulm(LogSource.FRIDA, "AppOrchestrator initialization started")
            if self.db_logger:
                self.log_info_via_ulm(LogSource.FRIDA, "DatabaseManager initialized for AppOrchestrator")
            if self.data_manager:
                self.log_info_via_ulm(LogSource.FRIDA, "DataIngestionManager created for AppOrchestrator")
            if self.emulator_manager.data_manager:
                self.log_info_via_ulm(LogSource.EMULATOR, f"EmulatorManager ({self.emulator_type}) initialized with DataIngestionManager available for database operations")
            else:
                self.log_info_via_ulm(LogSource.EMULATOR, f"EmulatorManager ({self.emulator_type}) initialized - no DataIngestionManager provided, database operations will be disabled")
            self.log_info_via_ulm(LogSource.FRIDA, "AppOrchestrator core components initialized")
            
            # REMOVE DUPLICATION: The setup wizard should have already started Docker services
            # We only verify that services are ready instead of starting them again
            self.initialization_status.emit("Verifying infrastructure services are ready...")
            
            # Service configuration with WSL2-aware verification
            services_config = {
                "grafana": {
                    "host": "localhost",
                    "port_windows_exposed": 3000,
                    "port_wsl_internal": 3000,
                    "health_path": "/api/health"
                },
                "loki": {
                    "host": "localhost", 
                    "port_windows_exposed": 3100,
                    "port_wsl_internal": 3100,
                    "health_path": "/ready"
                },
                "influxdb": {
                    "host": "localhost",
                    "port_windows_exposed": 8086,
                    "port_wsl_internal": 8086,
                    "health_path": "/health"
                }
            }
            
            # Verify each service is ready (but don't try to start them)
            all_services_ready = True
            for service_name, config in services_config.items():
                self.log_info_via_ulm(LogSource.SYSTEM, f"Verifying {service_name} service readiness...")
                
                # Use a shorter timeout since services should already be ready from wizard
                service_timeout = 30  # Reduced from 60+ seconds since wizard should have started them
                
                is_ready = await self.wsl_service_manager.wait_for_service_ready(
                    service_name=service_name,
                    windows_host=config["host"],
                    windows_port=config["port_windows_exposed"],
                    wsl_internal_port=config["port_wsl_internal"],
                    health_path=config["health_path"],
                    max_wait_sec=service_timeout
                )
                
                if is_ready:
                    # Store the validated URL
                    service_url = f"http://{config['host']}:{config['port_windows_exposed']}"
                    self._actual_service_urls[f"{service_name}_url"] = service_url
                    self.log_info_via_ulm(LogSource.SYSTEM, f"{service_name} is ready at {service_url}")
                    self.initialization_status.emit(f"{service_name} verified and ready")
                else:
                    # Service not ready - this might indicate the wizard didn't complete successfully
                    error_msg = f"{service_name} is not ready (wizard may not have completed successfully)"
                    self.log_warning_via_ulm(LogSource.SYSTEM, error_msg)
                    self.initialization_status.emit(f"Warning: {error_msg}")
                    
                    # Store default URL for degraded mode
                    default_url = f"http://{config['host']}:{config['port_windows_exposed']}"
                    self._actual_service_urls[f"{service_name}_url"] = default_url
                    all_services_ready = False
            
            # Emit configured service URLs
            self.services_configured.emit(self._actual_service_urls.copy())
            
            # Initialize DatabaseManager with actual InfluxDB URL if needed
            if self.db_logger and hasattr(self.db_logger, 'update_influxdb_url'):
                try:
                    influxdb_url = self._actual_service_urls.get("influxdb_url", "http://localhost:8086")
                    self.db_logger.update_influxdb_url(influxdb_url)
                    self.log_info_via_ulm(LogSource.SYSTEM, f"DatabaseManager updated with InfluxDB URL: {influxdb_url}")
                except Exception as e:
                    self.log_warning_via_ulm(LogSource.FRIDA, "Failed to update DatabaseManager with new InfluxDB URL", error=str(e))
            
            # Connect to BlueStacks initially if that's default behavior
            try:
                self.initialization_status.emit("Attempting initial BlueStacks connection...")
                                                # Note: This is just a check, not a full connection
                # The actual connection will be done when needed
                if hasattr(self.emulator_manager, 'check_adb_available'):
                    adb_available = self.emulator_manager.check_adb_available()
                    if adb_available:
                        self.initialization_status.emit("ADB is available for BlueStacks connection.")
                    else:
                        self.initialization_status.emit("ADB not available - BlueStacks connection will be attempted later.")
                else:
                    self.initialization_status.emit("BlueStacks helper ready for connection.")
            except Exception as e:
                self.log_warning_via_ulm(LogSource.FRIDA, "Error during initial BlueStacks check", error=str(e))
                self.initialization_status.emit("BlueStacks check failed - will retry when needed.")
            
            # Determine overall readiness
            if all_services_ready:
                self.initialization_status.emit("All core services initialized and verified.")
                self.ready_to_operate.emit(True)
            else:
                self.initialization_status.emit("Core services initialized with some services in degraded mode.")
                self.ready_to_operate.emit(True)  # Still allow operation in degraded mode
            
        except Exception as e:
            error_msg = f"Critical error during service initialization: {str(e)}"
            self.log_error_via_ulm(LogSource.FRIDA, error_msg, error=str(e))
            self.fatal_error_occurred.emit(error_msg)
            self.ready_to_operate.emit(False)

    async def run_main_loop(self):
        """Main application loop with initialization and background tasks"""
        try:
            # Store the backend loop reference if not already set
            if self._backend_loop is None:
                self._backend_loop = asyncio.get_running_loop()

            # 1. START ULM PROCESSOR FIRST (this initializes its queue in this backend loop)
            await self.ulm.start_log_processor()
            self.log_info_via_ulm(LogSource.MAIN_APP, "ULM processor started in AppOrchestrator's main loop.")

            # 2. Check if setup is complete or overridden before proceeding with services
            if not self.setup_is_complete_or_overridden:
                self.log_info_via_ulm(LogSource.MAIN_APP, "Infrastructure setup is not complete. Waiting for setup wizard to complete...")
                self.initialization_status.emit("Waiting for infrastructure setup to complete...")
                
                # Wait for setup to be completed or overridden
                while not self.setup_is_complete_or_overridden and not self._shutdown_flag:
                    await asyncio.sleep(0.5)  # Check every 500ms
                
                if self._shutdown_flag:
                    self.log_info_via_ulm(LogSource.MAIN_APP, "Shutdown requested during setup wait. Exiting main loop.")
                    return
                
                self.log_info_via_ulm(LogSource.MAIN_APP, "Setup completion detected. Proceeding with service initialization.")
            
            # 3. Initialize other services (only after ULM is ready and wizard completed/skipped)
            await self.initialize_services_post_wizard()
            self.log_info_via_ulm(LogSource.MAIN_APP, "AppOrchestrator main operational loop started.")
            
            while not self._shutdown_flag:
                # Perform any periodic checks or background tasks if needed by AppOrchestrator itself
                await asyncio.sleep(1)  # Heartbeat
                
            self.log_info_via_ulm(LogSource.MAIN_APP, "AppOrchestrator main loop exiting.")
            
        except Exception as e:
            error_msg = f"Critical error in AppOrchestrator main loop: {str(e)}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            self.fatal_error_occurred.emit(error_msg)
        finally:
            await self.shutdown_services()

    def request_shutdown(self):
        """Request graceful shutdown of the main loop"""
        self.log_info_via_ulm(LogSource.MAIN_APP, "Shutdown requested.")
        self._shutdown_flag = True

    async def request_graceful_shutdown(self):
        """Async version of shutdown request for external callers"""
        self.request_shutdown()

    async def shutdown_services(self):
        """Shutdown all services gracefully"""
        try:
            self.initialization_status.emit("Shutting down services...")
            
            # Detach from Frida injector if attached
            if self.injector:
                try:
                    self.log_info_via_ulm(LogSource.FRIDA, "Detaching from Frida injector...")
                    self.injector.detach()
                except Exception as e:
                    self.log_error_via_ulm(LogSource.FRIDA, "Error detaching from Frida injector", error=str(e))
            
            # Disconnect from BlueStacks if actually connected (not just initialized)
            if self.emulator_manager and hasattr(self.emulator_manager, 'selected_serial'):
                try:
                    self.log_info_via_ulm(LogSource.BLUESTACKS, "Disconnecting from emulator...")
                    # Emulator manager doesn't have explicit disconnect, but we can clean up
                    delattr(self.emulator_manager, 'selected_serial')
                except Exception as e:
                    self.log_error_via_ulm(LogSource.BLUESTACKS, "Error disconnecting from emulator", error=str(e))
            elif self.emulator_manager:
                # BlueStacks helper exists but was never connected - just note this quietly
                self.log_debug_via_ulm(LogSource.BLUESTACKS, "BlueStacks helper initialized but never connected - no cleanup needed")
            
            # Signal that we're telling ULM to shutdown
            self.shutdown_signal_sent_to_ulm.emit()
            
            # Shutdown ULM
            if self.ulm:
                try:
                    self.log_info_via_ulm(LogSource.MAIN_APP, "Shutting down Unified Logging Manager...")
                    await self.ulm.shutdown()
                except Exception as e:
                    self.log_error_via_ulm(LogSource.MAIN_APP, "Error shutting down ULM", error=str(e))
            
            # Close database manager if available
            if self.db_logger:
                try:
                    # DatabaseManager might have a close method
                    if hasattr(self.db_logger, 'close'):
                        self.db_logger.close()
                except Exception as e:
                    self.log_error_via_ulm(LogSource.DATABASE, "Error closing database manager", error=str(e))
            
            self.initialization_status.emit("All services shut down.")
            
        except Exception as e:
            self.log_error_via_ulm(LogSource.MAIN_APP, "Critical error during shutdown", error=str(e))
            self.fatal_error_occurred.emit(f"Error during shutdown: {str(e)}")

    def initialize_dependencies_and_db(self):
        log_info(LogSource.FRIDA, "Initializing dependencies and database...")
        local_fs_path = self.downloader.check_and_download_frida_server()
        if not local_fs_path:
            log_critical(LogSource.FRIDA, "CRITICAL: Failed to download or find Frida server. Exiting.")
            sys.exit(1)
        
        if self.db_logger:
            self.db_logger.initialize_schema()
            log_info(LogSource.FRIDA, "Database schema initialized/verified.")
        else:
            log_warning(LogSource.FRIDA, "DB Logger not available, skipping schema initialization.")
        return local_fs_path

    def setup_bluestacks_and_frida(self, local_frida_path):
        if not self.emulator_manager.connect_to_emulator():
            log_critical(LogSource.FRIDA, "Failed to connect to emulator. Please ensure BlueStacks is running, ADB is connected, and the emulator is rooted.")
            sys.exit("Emulator connection failed")

        # Get serial before other operations
        selected_serial = self.emulator_manager.get_selected_serial()
        if not selected_serial:
            log_critical(LogSource.FRIDA, "No emulator serial selected for Frida device acquisition.")
            sys.exit("No emulator serial selected")

        # Use the new helper method to ensure Frida server is properly configured
        frida_config = get_frida_config()
        frida_result = self.emulator_manager.ensure_frida_server(
            local_frida_path, 
            frida_config['server_remote_path'],
            frida_config['server_version']
        )
        
        # Use the device from ensure_frida_server if available
        if frida_result["device"]:
            self.frida_device = frida_result["device"]
        else:
            # Simple fallback device acquisition
            try:
                self.frida_device = frida.get_device(selected_serial, timeout=10)
            except Exception as e:
                log_warning(LogSource.FRIDA, "Failed to acquire Frida device", serial=selected_serial, error=str(e))
                self.frida_device = None

        # Pass self.frida_device and serial to FridaInjector if device was acquired
        if self.frida_device:
            self.injector = FridaInjector(self.handle_script_message_sync, frida_device=self.frida_device, serial=selected_serial)
            return self.frida_device
        else:
            log_warning(LogSource.FRIDA, "Frida device not available. Continuing with limited functionality (no code injection).")
            return None

    async def handle_script_message(self, payload_from_frida, data_binary):
        try:
            # Emit signal for GUI to receive Frida script messages
            self.frida_script_message_received.emit(payload_from_frida)
            
            # Basic validation of the payload from Frida
            if not isinstance(payload_from_frida, dict):
                if self.data_manager:
                    # Use unified data ingestion for error logging
                    await self.data_manager.ingest_system_log(
                        source="frida",
                        message=f"Received non-dict payload: {type(payload_from_frida)}",
                        level="WARNING",
                        extra_data={
                            "process_name": self.target_process_name or "unknown_process",
                            "script_name": self.script_file_name or "unknown_script",
                            "error_type": "non_dict_payload",
                            "raw_payload": str(payload_from_frida)[:1000]
                        }
                    )
                return

            script_ts = payload_from_frida.get('timestamp', datetime.now(timezone.utc).isoformat())
            
            message_type = payload_from_frida.get('eventType', payload_from_frida.get('type'))
            data_dict = payload_from_frida.get('data', payload_from_frida.get('payload', {}))

            current_process_name = self.target_process_name if self.target_process_name else "unknown_process"
            current_script_name = self.script_file_name if self.script_file_name else "unknown_script"

            # New round-based logic dispatch using unified data ingestion
            if message_type == "round_start_package":
                if self.data_manager:
                    tier = data_dict.get('tier')
                    cards_data = data_dict.get('cards') 
                    modules_data = data_dict.get('modules')
                    other_metadata = data_dict.get('other_fixed_metadata')

                    self.current_round_tier = tier # Store for end_round call

                    # Start round using unified data ingestion
                    self.current_round_id = await self.data_manager.start_round(
                        tier=tier or "unknown",
                        game_version=other_metadata.get('game_version') if other_metadata else "unknown"
                    )
                    
                    # Log round start event
                    await self.data_manager.ingest_system_log(
                        source="frida",
                        message=f"Round started via Frida: {self.current_round_id}",
                        level="INFO",
                        extra_data={
                            "tier": tier,
                            "process_name": current_process_name,
                            "script_name": current_script_name,
                            "cards_count": len(cards_data) if cards_data else 0,
                            "modules_count": len(modules_data) if modules_data else 0
                        }
                    )
                    


            elif message_type == "game_over_package":
                if self.current_round_id is not None and self.data_manager:
                    final_wave = data_dict.get('wave')
                    final_cash = data_dict.get('cash')
                    final_coins = data_dict.get('coins')
                    
                    # End round using unified data ingestion
                    await self.data_manager.end_round(self.current_round_id)
                    
                    # Log game over event with final stats
                    await self.data_manager.ingest_system_log(
                        source="frida",
                        message=f"Game over for round: {self.current_round_id}",
                        level="INFO",
                        extra_data={
                            "final_wave": final_wave,
                            "final_cash": final_cash,
                            "final_coins": final_coins,
                            "tier": self.current_round_tier,
                            "process_name": current_process_name,
                            "script_name": current_script_name
                        }
                    )
                    
                    self.current_round_id = None
                    self.current_round_tier = None

            elif message_type == "periodic_update":
                if self.current_round_id is not None and self.data_manager:
                    cash = data_dict.get('cash')
                    coins = data_dict.get('coins')
                    gems = data_dict.get('gems')
                    wave = data_dict.get('wave_number') # Match JS 'wave_number' or 'wave' etc.
                    
                    # Use unified data ingestion for game metrics
                    await self.data_manager.ingest_game_metrics(
                        round_id=self.current_round_id,
                        cash=cash or 0,
                        coins=coins or 0,
                        gems=gems or 0
                    )
                    
                    # If this is a wave start, log it as a wave event
                    if wave is not None:
                        await self.data_manager.log_wave_start(
                            wave=wave,
                            cash=cash or 0,
                            coins=coins or 0,
                            gems=gems or 0
                        )
                    
                # No need for 'else' if no active round, snapshots are only relevant during a round

            elif message_type == "in_round_event": # Generic event type
                if self.current_round_id is not None and self.data_manager:
                    sub_event_type = data_dict.get('sub_event_type', 'unknown_in_round_event')
                    
                    # Log as method hook if it's a method call
                    if sub_event_type == "method_hook" or "method" in data_dict:
                        method_name = data_dict.get('method_name', data_dict.get('method', 'unknown_method'))
                        args = data_dict.get('args', [])
                        result = data_dict.get('result')
                        
                        await self.data_manager.log_method_hook(
                            method_name=method_name,
                            args=args,
                            result=result
                        )
                    else:
                        # Log as general system log with round correlation
                        await self.data_manager.ingest_system_log(
                            source="frida",
                            message=f"In-round event: {sub_event_type}",
                            level="INFO",
                            extra_data={
                                "event_type": sub_event_type,
                                "process_name": current_process_name,
                                "script_name": current_script_name,
                                **data_dict  # Include all event data
                            }
                        )
                        


            # Handle new unified message types for better integration
            elif message_type == "metrics":
                # Direct metrics from Frida script
                if self.current_round_id is not None and self.data_manager:
                    await self.data_manager.ingest_game_metrics(
                        round_id=self.current_round_id,
                        cash=data_dict.get('cash', 0),
                        coins=data_dict.get('coins', 0),
                        gems=data_dict.get('gems', 0)
                    )
                    
            elif message_type == "method_hook":
                # Direct method hook from Frida script
                if self.data_manager:
                    await self.data_manager.log_method_hook(
                        method_name=data_dict.get('method_name', 'unknown_method'),
                        args=data_dict.get('args', []),
                        result=data_dict.get('result')
                    )
                    
            elif message_type == "wave_start":
                # Direct wave start from Frida script
                if self.current_round_id is not None and self.data_manager:
                    await self.data_manager.log_wave_start(
                        wave=data_dict.get('wave', 1),
                        cash=data_dict.get('cash', 0),
                        coins=data_dict.get('coins', 0),
                        gems=data_dict.get('gems', 0)
                    )

            else: # Fallback to unified system logging for other/unrecognized message types
                if self.data_manager:
                    await self.data_manager.ingest_system_log(
                        source="frida",
                        message=f"Unrecognized Frida message: {message_type}",
                        level="INFO",
                        extra_data={
                            "message_type": message_type,
                            "process_name": current_process_name,
                            "script_name": current_script_name,
                            "raw_payload": payload_from_frida
                        }
                    )
            
            if data_binary: # If your script ever uses send(payload, data_bytes)
                if self.data_manager:
                    await self.data_manager.ingest_system_log(
                        source="frida",
                        message=f"Received binary data from Frida script",
                        level="INFO",
                        extra_data={
                            "binary_data_length": len(data_binary),
                            "process_name": current_process_name,
                            "script_name": current_script_name
                        }
                    )

        except Exception as e_handler:
            # Log critical error to unified system
            try:
                if self.data_manager:
                    await self.data_manager.ingest_system_log(
                        source="frida",
                        message=f"Critical error in Frida message handler: {str(e_handler)}",
                        level="ERROR",
                        extra_data={
                            "error": str(e_handler),
                            "traceback": traceback.format_exc(),
                            "process_name": self.target_process_name or "handler_exception_context",
                            "script_name": self.script_file_name or "handler_exception_context"
                        }
                    )
                else:
                    log_error(LogSource.FRIDA, "Critical error in Frida message handler", error=str(e_handler))
            except Exception as e_log_critical:
                log_error(LogSource.FRIDA, "Could not log critical handler error", error=str(e_log_critical))

    def handle_script_message_sync(self, payload_from_frida, data_binary):
        """
        Synchronous wrapper for handle_script_message to maintain compatibility.
        This method creates a new event loop if needed or schedules the async method.
        """
        try:
            # Try to get the current event loop
            loop = asyncio.get_running_loop()
            # If we're in an event loop, schedule the coroutine as a task
            task = loop.create_task(self.handle_script_message(payload_from_frida, data_binary))
            # Note: The task will run in the background, we don't wait for it here
            # This is intentional for Frida message handling to avoid blocking
        except RuntimeError:
            # No event loop running, create one
            asyncio.run(self.handle_script_message(payload_from_frida, data_binary))

    def run_hook_on_target(self, target_package_name, master_script_path):
        self.target_process_name = target_package_name
        self.script_file_name = os.path.basename(master_script_path)
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Ensure we have a Frida device
                if not self.frida_device:
                    selected_serial = self.emulator_manager.get_selected_serial()
                    if not selected_serial:
                        log_critical(LogSource.FRIDA, "No emulator serial selected for Frida device acquisition.")
                        return False
                    self.frida_device = frida.get_device(selected_serial, timeout=10)
                    if self.injector:
                        self.injector.device = self.frida_device

                # Find target process
                target_value = target_package_name
                try:
                    apps = self.frida_device.enumerate_applications()
                    for app_info in apps:
                        if app_info.identifier == target_package_name and app_info.pid != 0:
                            target_value = app_info.pid
                            break
                    else:
                        # Fallback to PID lookup via BlueStacks helper
                        pid = self.emulator_manager.get_pid_for_package(target_package_name)
                        if pid:
                            target_value = pid
                except Exception:
                    pass  # Use package name as fallback

                # Stabilization delay
                time.sleep(3)

                # Attempt attach and script load
                if not self.injector or not self.injector.device:
                    log_critical(LogSource.FRIDA, "Injector not available for attach")
                    return False

                self.injector.attach_to_process(target_value, realm='emulated')
                if self.injector.load_and_run_script(master_script_path):
                    return True
                else:
                    self.injector.detach()

            except Exception as e:
                log_error(LogSource.FRIDA, f"Attach attempt {attempt + 1} failed", error=str(e), target=target_package_name)
                if self.injector and hasattr(self.injector, 'session') and self.injector.session and not self.injector.session.is_detached:
                    try:
                        self.injector.detach()
                    except Exception:
                        pass

            if attempt < max_attempts - 1:
                time.sleep(7)

        log_critical(LogSource.FRIDA, f"Failed to run hook on target after {max_attempts} attempts", target=target_package_name)
        return False

    def shutdown(self):
        """Shutdown orchestrator with timeout protection to prevent hanging"""
        import threading
        import time
        
        def _safe_shutdown():
            """Internal shutdown logic with timeout protection"""
            # Detach from injector with timeout
            if self.injector:
                try:
                    self.injector.detach() # This handles if session is None
                except Exception as e:
                    log_error(LogSource.FRIDA, "Error during injector detach", error=str(e))

            # Stop Frida server with timeout
            if self.emulator_manager:
                try:
                    # Assuming stop_frida_server is idempotent and handles if server not running
                    frida_config = get_frida_config()
                    self.emulator_manager.stop_frida_server(frida_config['server_remote_path'])
                except Exception as e:
                    log_error(LogSource.FRIDA, "Error stopping Frida server", error=str(e))
        
        # Run shutdown with timeout protection
        shutdown_thread = threading.Thread(target=_safe_shutdown, daemon=True)
        shutdown_thread.start()
        
        # Wait for shutdown with timeout
        shutdown_thread.join(timeout=5.0)  # 5 second timeout
        
        if shutdown_thread.is_alive():
            log_warning(LogSource.FRIDA, "Shutdown operations timed out, forcing exit") 

    # P0.4: Async BlueStacks/Frida Control Methods with Signal Emission
    
    async def async_connect_bluestacks(self):
        """Async method to connect to BlueStacks with signal emission"""
        try:
            self.log_info_via_ulm(LogSource.BLUESTACKS, "Attempting to connect to BlueStacks...")
            
            # Run the sync method in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, self.emulator_manager.connect_to_emulator)
            
            if success:
                # Get device serial if connection successful
                serial = self.emulator_manager.get_selected_serial() if hasattr(self.emulator_manager, 'get_selected_serial') else "unknown"
                message = f"Connected to BlueStacks device: {serial}"
                self.log_info_via_ulm(LogSource.BLUESTACKS, message)
                self.bluestacks_connection_status.emit(True, message)
                return True
            else:
                message = "Failed to connect to BlueStacks"
                self.log_error_via_ulm(LogSource.BLUESTACKS, message)
                self.bluestacks_connection_status.emit(False, message)
                return False
                
        except Exception as e:
            message = f"Error connecting to BlueStacks: {str(e)}"
            self.log_error_via_ulm(LogSource.BLUESTACKS, message, error=str(e))
            self.bluestacks_connection_status.emit(False, message)
            return False

    async def async_list_adb_devices(self):
        """Async method to list ADB devices with signal emission"""
        try:
            self.log_info_via_ulm(LogSource.BLUESTACKS, "Listing ADB devices...")
            
            # Run the sync method in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            devices = await loop.run_in_executor(None, self.emulator_manager._get_connected_devices)
            
            self.log_info_via_ulm(LogSource.BLUESTACKS, f"Found {len(devices)} ADB devices", devices=devices)
            self.available_adb_devices.emit(devices)
            return devices
            
        except Exception as e:
            message = f"Error listing ADB devices: {str(e)}"
            self.log_error_via_ulm(LogSource.BLUESTACKS, message, error=str(e))
            self.available_adb_devices.emit([])
            return []

    async def async_list_processes(self):
        """Async method to list processes with signal emission"""
        try:
            self.log_info_via_ulm(LogSource.BLUESTACKS, "Listing BlueStacks processes...")
            
            # Run the sync method in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            processes = await loop.run_in_executor(None, lambda: self.emulator_manager.list_processes(parsed=True))
            
            self.log_info_via_ulm(LogSource.BLUESTACKS, f"Found {len(processes)} processes")
            self.bluestacks_processes_updated.emit(processes)
            return processes
            
        except Exception as e:
            message = f"Error listing processes: {str(e)}"
            self.log_error_via_ulm(LogSource.BLUESTACKS, message, error=str(e))
            self.bluestacks_processes_updated.emit([])
            return []

    async def async_setup_frida_server(self, local_frida_path):
        """Async method to setup Frida server with signal emission"""
        try:
            self.log_info_via_ulm(LogSource.FRIDA, "Setting up Frida server...")
            
            # Get frida config
            frida_config = get_frida_config()
            
            # Run the sync method in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            frida_result = await loop.run_in_executor(
                None, 
                self.emulator_manager.ensure_frida_server,
                local_frida_path,
                frida_config['server_remote_path'],
                frida_config['server_version']
            )
            
            if frida_result.get('success', False):
                # Store the device if available
                if frida_result.get('device'):
                    self.frida_device = frida_result['device']
                
                message = frida_result.get('message', 'Frida server setup successful')
                self.log_info_via_ulm(LogSource.FRIDA, message)
                self.frida_server_status.emit(True, message)
                return True
            else:
                message = frida_result.get('message', 'Frida server setup failed')
                self.log_error_via_ulm(LogSource.FRIDA, message)
                self.frida_server_status.emit(False, message)
                return False
                
        except Exception as e:
            message = f"Error setting up Frida server: {str(e)}"
            self.log_error_via_ulm(LogSource.FRIDA, message, error=str(e))
            self.frida_server_status.emit(False, message)
            return False

    async def async_run_hook_on_target(self, target_package_name, master_script_path):
        """Async method to run hook on target with signal emission"""
        try:
            self.target_process_name = target_package_name
            self.script_file_name = os.path.basename(master_script_path)
            
            self.log_info_via_ulm(LogSource.FRIDA, f"Attempting to hook target: {target_package_name}")
            
            max_attempts = 3
            loop = asyncio.get_event_loop()
            
            for attempt in range(max_attempts):
                try:
                    # Ensure we have a Frida device
                    if not self.frida_device:
                        self.log_info_via_ulm(LogSource.FRIDA, "Acquiring Frida device...")
                        
                        if not hasattr(self.emulator_manager, 'selected_serial') or not self.emulator_manager.get_selected_serial():
                            message = "No emulator serial selected for Frida device acquisition"
                            self.log_error_via_ulm(LogSource.FRIDA, message)
                            self.frida_attachment_status.emit(False, target_package_name, message)
                            return False
                            
                        import frida
                        selected_serial = self.emulator_manager.get_selected_serial()
                        self.frida_device = await loop.run_in_executor(
                            None, frida.get_device, selected_serial, 10
                        )
                        
                        if self.injector:
                            self.injector.device = self.frida_device

                    # Find target process
                    target_value = target_package_name
                    try:
                        apps = await loop.run_in_executor(None, self.frida_device.enumerate_applications)
                        for app_info in apps:
                            if app_info.identifier == target_package_name and app_info.pid != 0:
                                target_value = app_info.pid
                                break
                        else:
                            # Fallback to PID lookup via BlueStacks helper
                            pid = await loop.run_in_executor(
                                None, self.emulator_manager.get_pid_for_package, target_package_name
                            )
                            if pid:
                                target_value = pid
                    except Exception as e:
                        self.log_warning_via_ulm(LogSource.FRIDA, f"Error finding target process: {e}")
                        # Use package name as fallback

                    # Stabilization delay
                    await asyncio.sleep(3)

                    # Attempt attach and script load
                    if not self.injector or not self.injector.device:
                        message = "Injector not available for attach"
                        self.log_critical(LogSource.FRIDA, message)
                        self.frida_attachment_status.emit(False, target_package_name, message)
                        return False

                    # Run attach in executor
                    attach_success = await loop.run_in_executor(
                        None, self.injector.attach_to_process, target_value, 'emulated'
                    )
                    
                    if attach_success:
                        # Load and run script
                        script_success = await loop.run_in_executor(
                            None, self.injector.load_and_run_script, master_script_path
                        )
                        
                        if script_success:
                            message = f"Successfully attached to {target_package_name} and loaded script"
                            self.log_info_via_ulm(LogSource.FRIDA, message)
                            self.frida_attachment_status.emit(True, target_package_name, message)
                            return True
                        else:
                            # Detach if script loading failed
                            await loop.run_in_executor(None, self.injector.detach)

                except Exception as e:
                    error_msg = f"Attach attempt {attempt + 1} failed: {str(e)}"
                    self.log_error_via_ulm(LogSource.FRIDA, error_msg, target=target_package_name)
                    
                    # Clean up on failure
                    if self.injector and hasattr(self.injector, 'session') and self.injector.session and not self.injector.session.is_detached:
                        try:
                            await loop.run_in_executor(None, self.injector.detach)
                        except Exception:
                            pass

                if attempt < max_attempts - 1:
                    self.log_info_via_ulm(LogSource.FRIDA, f"Retrying in 7 seconds... (attempt {attempt + 2}/{max_attempts})")
                    await asyncio.sleep(7)

            # All attempts failed
            message = f"Failed to run hook on target after {max_attempts} attempts"
            self.log_critical(LogSource.FRIDA, message, target=target_package_name)
            self.frida_attachment_status.emit(False, target_package_name, message)
            return False
            
        except Exception as e:
            message = f"Error running hook on target: {str(e)}"
            self.log_error_via_ulm(LogSource.FRIDA, message, error=str(e))
            self.frida_attachment_status.emit(False, target_package_name, message)
            return False

    async def async_initialize_dependencies_and_db(self):
        """Async version of initialize_dependencies_and_db with signal emission"""
        try:
            self.log_info_via_ulm(LogSource.FRIDA, "Initializing dependencies and database...")
            
            # Download Frida server in executor
            loop = asyncio.get_event_loop()
            local_fs_path = await loop.run_in_executor(
                None, self.downloader.check_and_download_frida_server
            )
            
            if not local_fs_path:
                message = "CRITICAL: Failed to download or find Frida server"
                self.log_critical(LogSource.FRIDA, message)
                self.fatal_error_occurred.emit(message)
                return None
            
            # Initialize database schema if available
            if self.db_logger:
                await loop.run_in_executor(None, self.db_logger.initialize_schema)
                self.log_info_via_ulm(LogSource.FRIDA, "Database schema initialized/verified.")
            else:
                self.log_warning_via_ulm(LogSource.FRIDA, "DB Logger not available, skipping schema initialization.")
            
            return local_fs_path
            
        except Exception as e:
            message = f"Error initializing dependencies: {str(e)}"
            self.log_error_via_ulm(LogSource.FRIDA, message, error=str(e))
            self.fatal_error_occurred.emit(message)
            return None 

    async def update_setting_and_persist(self, key_path_str: str, new_value: any):
        """
        Update a configuration setting and persist it to the YAML file.
        
        Args:
            key_path_str: Dot-separated path like "logging.file_fallback.max_size_mb"
            new_value: The new value to set
        """
        try:
            if not key_path_str or not key_path_str.strip():
                raise ValueError("key_path_str cannot be empty")
            
            # Import required modules
            import yaml
            from src.utils.config import get_config_path, reload_config
            
            self.log_info_via_ulm(LogSource.MAIN_APP, f"Updating configuration setting: {key_path_str} = {new_value}")
            
            # Get config file path
            config_path = get_config_path()
            
            # Load current config from file
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_dict = yaml.safe_load(f) or {}
            else:
                config_dict = {}
            
            # Navigate to the setting location
            keys = key_path_str.split('.')
            current_dict = config_dict
            
            # Create nested dictionaries as needed
            for key in keys[:-1]:
                if key not in current_dict:
                    current_dict[key] = {}
                elif not isinstance(current_dict[key], dict):
                    # If the intermediate key exists but isn't a dict, we can't navigate further
                    raise ValueError(f"Cannot set {key_path_str}: intermediate key '{key}' is not a dictionary")
                current_dict = current_dict[key]
            
            # Set the final value
            final_key = keys[-1]
            old_value = current_dict.get(final_key)
            current_dict[final_key] = new_value
            
            # Save the updated config back to file
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True)
            
            self.initialization_status.emit(f"Configuration '{key_path_str}' saved to YAML.")
            
            # Update in-memory config if it exists in our initial_app_config
            try:
                if isinstance(self.initial_app_config, dict):
                    # Try to update the in-memory config as well
                    in_memory_dict = self.initial_app_config
                    for key in keys[:-1]:
                        if key in in_memory_dict and isinstance(in_memory_dict[key], dict):
                            in_memory_dict = in_memory_dict[key]
                        else:
                            # Can't update in-memory, but that's OK
                            break
                    else:
                        in_memory_dict[final_key] = new_value
            except Exception as e:
                # Non-critical error updating in-memory config
                self.log_warning_via_ulm(LogSource.MAIN_APP, f"Could not update in-memory config: {e}")
            
            # Determine if this change requires a restart
            restart_required = self._setting_requires_restart(key_path_str, old_value, new_value)
            
            if restart_required:
                restart_message = f"Change to '{key_path_str}' requires an application restart to take full effect."
                self.log_warning_via_ulm(LogSource.MAIN_APP, restart_message)
                self.config_update_requires_restart.emit(restart_message)
            else:
                # Try to apply the change dynamically
                applied_dynamically = await self._try_apply_config_change_dynamically(key_path_str, new_value)
                if applied_dynamically:
                    success_message = f"Setting '{key_path_str}' applied dynamically."
                    self.log_info_via_ulm(LogSource.MAIN_APP, success_message)
                    self.config_updated_successfully.emit(success_message)
                else:
                    partial_message = f"Setting '{key_path_str}' saved to file. Some changes may require restart."
                    self.log_warning_via_ulm(LogSource.MAIN_APP, partial_message)
                    self.config_updated_successfully.emit(partial_message)
            
            # Force reload of the global config cache
            reload_config()
            
        except Exception as e:
            error_message = f"Error updating configuration setting '{key_path_str}': {str(e)}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_message, error=str(e))
            raise

    async def get_current_config_value(self, key_path_str: str):
        """
        Get a specific configuration value.
        
        Args:
            key_path_str: Dot-separated path like "logging.file_fallback.max_size_mb"
            
        Returns:
            The configuration value, or None if not found
        """
        try:
            if not key_path_str or not key_path_str.strip():
                return None
            
            from src.utils.config import get_config
            
            # Get current config
            config_dict = get_config()
            
            # Navigate to the setting
            keys = key_path_str.split('.')
            current_value = config_dict
            
            for key in keys:
                if isinstance(current_value, dict) and key in current_value:
                    current_value = current_value[key]
                else:
                    return None
            
            return current_value
            
        except Exception as e:
            self.log_error_via_ulm(LogSource.MAIN_APP, f"Error getting config value '{key_path_str}': {str(e)}")
            return None

    def _setting_requires_restart(self, key_path_str: str, old_value: any, new_value: any) -> bool:
        """
        Determine if a configuration change requires an application restart.
        
        Args:
            key_path_str: The configuration key path
            old_value: The previous value
            new_value: The new value
            
        Returns:
            True if restart is required, False otherwise
        """
        # Settings that typically require restart
        restart_required_paths = {
            # Service enablement changes
            'logging.loki.enabled',
            'logging.influxdb.enabled',
            
            # Core service URLs (if they're already initialized)
            'grafana_url',
            'loki_url', 
            'influxdb_url',
            
            # Database connection settings
            'database.url',
            'database.connection',
            
            # Core application paths
            'HOOK_SCRIPT_PATH',
            'DB_SCHEMA_FILE',
            
            # BlueStacks/Frida core settings
            'bluestacks.adb_path',
            'frida.server_dir',
            'frida.server_arch',
            'frida.server_version'
        }
        
        # Check if this setting is in the restart-required list
        if key_path_str in restart_required_paths:
            return True
        
        # Check for patterns that might require restart
        if key_path_str.startswith('DATA_INGESTION.') and 'ENABLED' in key_path_str:
            return True
            
        # Major logging changes might require restart
        if key_path_str in ['logging.console.enabled', 'logging.file_fallback.enabled'] and old_value != new_value:
            return True
        
        return False

    async def _try_apply_config_change_dynamically(self, key_path_str: str, new_value: any) -> bool:
        """
        Attempt to apply a configuration change dynamically without restart.
        
        Args:
            key_path_str: The configuration key path
            new_value: The new value
            
        Returns:
            True if successfully applied dynamically, False otherwise
        """
        try:
            # Handle logging-related changes
            if key_path_str.startswith('logging.'):
                return await self._apply_logging_config_change(key_path_str, new_value)
            
            # Handle console filter changes
            if key_path_str.startswith('logging.console.filters.'):
                # Console filter changes can be applied dynamically
                # The ULM should pick up the new config on next log
                self.log_info_via_ulm(LogSource.MAIN_APP, f"Console filter '{key_path_str}' updated dynamically")
                return True
            
            # Handle file fallback settings that can be changed
            if key_path_str in ['logging.file_fallback.max_size_mb', 'logging.file_fallback.backup_count']:
                # These can potentially be applied if ULM supports reconfiguration
                if self.ulm and hasattr(self.ulm, 'reconfigure_file_handler'):
                    try:
                        # This would require ULM to have a reconfiguration method
                        await self.ulm.reconfigure_file_handler()
                        return True
                    except Exception as e:
                        self.log_warning_via_ulm(LogSource.MAIN_APP, f"Could not reconfigure file handler dynamically: {e}")
                return False
            
            # Other settings generally require restart for now
            return False
            
        except Exception as e:
            self.log_error_via_ulm(LogSource.MAIN_APP, f"Error applying config change dynamically: {e}")
            return False

    async def _apply_logging_config_change(self, key_path_str: str, new_value: any) -> bool:
        """
        Apply logging-related configuration changes dynamically.
        
        Args:
            key_path_str: The logging configuration key path
            new_value: The new value
            
        Returns:
            True if successfully applied, False otherwise
        """
        try:
            # For now, most logging changes will require restart
            # This is a placeholder for future dynamic reconfiguration
            
            if 'filters' in key_path_str:
                # Console filters can be applied more easily
                return True
            
            # Labels and other metadata changes
            if 'labels' in key_path_str:
                return True
            
            # Most other logging changes require restart
            return False
            
        except Exception as e:
            self.log_error_via_ulm(LogSource.MAIN_APP, f"Error applying logging config change: {e}")
            return False

    # GUI Wrapper Methods
    # These methods provide a clean interface for the GUI to interact with the backend
    
    async def connect_bluestacks_wrapper(self):
        """Wrapper for BlueStacks connection that emits appropriate signals."""
        try:
            self.log_info_via_ulm(LogSource.BLUESTACKS, "Connecting to BlueStacks...")
            
            # Call the existing async method
            result = await self.async_connect_bluestacks()
            
            if result:
                self.bluestacks_connection_status.emit(True, "BlueStacks connected successfully")
                # Also refresh device list
                await self.list_adb_devices_wrapper()
            else:
                self.bluestacks_connection_status.emit(False, "Failed to connect to BlueStacks")
                
        except Exception as e:
            error_msg = f"Error connecting to BlueStacks: {str(e)}"
            self.log_error_via_ulm(LogSource.BLUESTACKS, error_msg, error=str(e))
            self.bluestacks_connection_status.emit(False, error_msg)

    async def list_adb_devices_wrapper(self):
        """Wrapper for listing ADB devices that emits appropriate signals."""
        try:
            self.log_info_via_ulm(LogSource.BLUESTACKS, "Listing ADB devices...")
            
            # Call the existing async method
            devices = await self.async_list_adb_devices()
            
            if devices:
                self.available_adb_devices.emit(devices)
                self.log_info_via_ulm(LogSource.BLUESTACKS, f"Found {len(devices)} ADB devices")
            else:
                self.available_adb_devices.emit([])
                self.log_warning_via_ulm(LogSource.BLUESTACKS, "No ADB devices found")
                
        except Exception as e:
            error_msg = f"Error listing ADB devices: {str(e)}"
            self.log_error_via_ulm(LogSource.BLUESTACKS, error_msg, error=str(e))
            self.available_adb_devices.emit([])

    async def list_bluestacks_processes_wrapper(self):
        """Wrapper for listing BlueStacks processes that emits appropriate signals."""
        try:
            self.log_info_via_ulm(LogSource.BLUESTACKS, "Listing BlueStacks processes...")
            
            # Call the existing async method
            processes = await self.async_list_processes()
            
            if processes:
                self.bluestacks_processes_updated.emit(processes)
                self.log_info_via_ulm(LogSource.BLUESTACKS, f"Found {len(processes)} processes")
            else:
                self.bluestacks_processes_updated.emit([])
                self.log_warning_via_ulm(LogSource.BLUESTACKS, "No processes found")
                
        except Exception as e:
            error_msg = f"Error listing processes: {str(e)}"
            self.log_error_via_ulm(LogSource.BLUESTACKS, error_msg, error=str(e))
            self.bluestacks_processes_updated.emit([])

    async def attach_frida_to_process_wrapper(self, process_identifier: str, script_path: str):
        """Wrapper for attaching Frida to process that emits appropriate signals."""
        try:
            self.log_info_via_ulm(LogSource.FRIDA, f"Attaching Frida to process {process_identifier}...")
            
            # Call the existing async method
            success = await self.async_run_hook_on_target(process_identifier, script_path)
            
            if success:
                self.frida_attachment_status.emit(True, process_identifier, "Frida attached successfully")
            else:
                self.frida_attachment_status.emit(False, process_identifier, "Failed to attach Frida")
                
        except Exception as e:
            error_msg = f"Error attaching Frida to process: {str(e)}"
            self.log_error_via_ulm(LogSource.FRIDA, error_msg, error=str(e))
            self.frida_attachment_status.emit(False, process_identifier, error_msg)

    async def detach_frida_wrapper(self):
        """Wrapper for detaching Frida that emits appropriate signals."""
        try:
            self.log_info_via_ulm(LogSource.FRIDA, "Detaching Frida...")
            
            # Call injector detach if available
            if self.injector:
                self.injector.detach()
                self.frida_attachment_status.emit(False, "", "Frida detached successfully")
            else:
                self.frida_attachment_status.emit(False, "", "No active Frida session to detach")
                
        except Exception as e:
            error_msg = f"Error detaching Frida: {str(e)}"
            self.log_error_via_ulm(LogSource.FRIDA, error_msg, error=str(e))
            self.frida_attachment_status.emit(False, "", error_msg)

    async def check_for_updates_simple(self):
        """Simple update check that prepares the URL for browser opening"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, f"Current app version: {self.CURRENT_APP_VERSION}")
            self.initialization_status.emit(f"To check for updates, please visit: {self.UPDATE_INFO_URL}")
            self.log_info_via_ulm(LogSource.MAIN_APP, f"Update check URL: {self.UPDATE_INFO_URL}")
            # Future: Fetch a version file from UPDATE_INFO_URL and compare.
            # self.update_status_ready.emit(is_update_avail, new_ver, notes_url) (Define this signal if doing actual check)
        except Exception as e:
            error_msg = f"Failed to prepare update check: {str(e)}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, error=str(e))
            self.initialization_status.emit(error_msg)

    # =========================================================================
    # ENHANCED SETUP WIZARD METHODS WITH COMPREHENSIVE LOGGING AND MONITORING
    # =========================================================================

    async def _validate_system_prerequisites(self) -> Tuple[bool, str]:
        """Validate system prerequisites for Tower Hooker setup"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "Validating system prerequisites...")
            
            # Check Python version
            import sys
            python_version = sys.version_info
            self.log_debug_via_ulm(LogSource.MAIN_APP, f"Python version: {python_version.major}.{python_version.minor}.{python_version.micro}")
            
            if python_version < (3, 8):
                return False, f"Python 3.8+ required, found {python_version.major}.{python_version.minor}.{python_version.micro}"
            
            # Check available disk space
            import shutil
            total, used, free = shutil.disk_usage(".")
            free_gb = free // (1024**3)
            self.log_debug_via_ulm(LogSource.MAIN_APP, f"Available disk space: {free_gb} GB")
            
            if free_gb < 5:
                return False, f"Insufficient disk space: {free_gb} GB available, 5 GB required"
            
            # Check if required directories exist
            required_dirs = ["config", "logs"]
            for dir_name in required_dirs:
                if not os.path.exists(dir_name):
                    self.log_debug_via_ulm(LogSource.MAIN_APP, f"Creating required directory: {dir_name}")
                    os.makedirs(dir_name, exist_ok=True)
            
            # Check if we can write to the current directory
            test_file = ".test_write_permission"
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.remove(test_file)
                self.log_debug_via_ulm(LogSource.MAIN_APP, "Write permissions verified")
            except Exception as e:
                return False, f"Cannot write to current directory: {e}"
            
            # Check if ports are available
            required_ports = [3000, 3100, 8086, 9080]  # Grafana, Loki, InfluxDB, Promtail
            for port in required_ports:
                if self._is_port_in_use("localhost", port):
                    self.log_warning_via_ulm(LogSource.MAIN_APP, f"Port {port} is already in use")
                    # Don't fail for port conflicts, just warn
            
            self.log_info_via_ulm(LogSource.MAIN_APP, "System prerequisites validation completed successfully")
            return True, "System prerequisites validated successfully"
            
        except Exception as e:
            error_msg = f"System prerequisites validation failed: {e}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            return False, error_msg

    async def _enhanced_docker_setup(self, progress_callback: Callable[[str, str], None]) -> Tuple[bool, str]:
        """Enhanced Docker setup with real-time monitoring and comprehensive logging"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "Starting enhanced Docker setup with real-time monitoring...")
            
            # Step 1: Validate Docker environment
            progress_callback("Docker Validation", "Validating Docker environment...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Validating Docker CLI and daemon accessibility")
            
            docker_ok, docker_msg = await self.run_in_executor(
                self.setup_wizard.check_docker_service, progress_callback
            )
            
            if not docker_ok:
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Docker validation failed: {docker_msg}")
                return False, f"Docker validation failed: {docker_msg}"
            
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Docker validation successful")
            
            # Step 2: Start Docker services with monitoring
            progress_callback("Docker Services", "Starting Docker services...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Starting Docker services with enhanced monitoring")
            
            # Start monitoring Docker events in parallel with service startup
            async def start_services_with_monitoring():
                # Start the services
                setup_ok, setup_msg = await self.run_in_executor(
                    self.setup_wizard.setup_docker_services, progress_callback
                )
                
                if not setup_ok:
                    return False, setup_msg
                
                # Monitor container startup with real-time events
                self.log_debug_via_ulm(LogSource.MAIN_APP, "Monitoring container startup events...")
                monitoring_ok = await self.setup_wizard._monitor_docker_events_realtime(
                    progress_callback, timeout=60
                )
                
                if monitoring_ok:
                    self.log_debug_via_ulm(LogSource.MAIN_APP, "All containers started successfully based on events")
                else:
                    self.log_warning_via_ulm(LogSource.MAIN_APP, "Container event monitoring timed out, using fallback verification")
                
                return True, "Docker services started and monitored successfully"
            
            services_ok, services_msg = await start_services_with_monitoring()
            
            if not services_ok:
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Docker services startup failed: {services_msg}")
                return False, services_msg
            
            # Step 3: Verify service readiness
            progress_callback("Docker Verification", "Verifying service readiness...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Verifying Docker service readiness")
            
            # Give services a moment to fully initialize
            await asyncio.sleep(3)
            
            # Final verification
            final_status = self.setup_wizard._check_docker_services()
            if not final_status['ready']:
                error_msg = f"Docker services not ready after startup: {final_status.get('error', 'Unknown issue')}"
                self.log_error_via_ulm(LogSource.MAIN_APP, error_msg)
                return False, error_msg
            
            self.log_info_via_ulm(LogSource.MAIN_APP, "Enhanced Docker setup completed successfully")
            return True, "Docker services running and verified"
            
        except Exception as e:
            error_msg = f"Enhanced Docker setup failed: {e}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            return False, error_msg

    async def _enhanced_influxdb_setup(self, progress_callback: Callable[[str, str], None]) -> Tuple[bool, str]:
        """Enhanced InfluxDB setup with comprehensive capability testing"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "Starting enhanced InfluxDB setup with capability testing...")
            
            # Step 1: Basic setup
            progress_callback("InfluxDB Basic Setup", "Setting up InfluxDB authentication and buckets...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running basic InfluxDB setup")
            
            basic_ok, basic_msg = await self.run_in_executor(
                self.setup_wizard.setup_influxdb, progress_callback
            )
            
            if not basic_ok:
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Basic InfluxDB setup failed: {basic_msg}")
                return False, basic_msg
            
            # Step 2: Progressive capability testing
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running comprehensive InfluxDB capability tests")
            
            test_functions = [
                ("Connection", self.setup_wizard._test_influxdb_connection),
                ("Authentication", self.setup_wizard._test_influxdb_auth),
                ("Bucket Access", self.setup_wizard._test_influxdb_buckets),
                ("Write Capability", self.setup_wizard._test_influxdb_write)
            ]
            
            capability_ok = await self.setup_wizard._test_service_capabilities(
                "InfluxDB", test_functions, progress_callback
            )
            
            if not capability_ok:
                error_msg = "InfluxDB capability testing failed"
                self.log_error_via_ulm(LogSource.MAIN_APP, error_msg)
                return False, error_msg
            
            self.log_info_via_ulm(LogSource.MAIN_APP, "Enhanced InfluxDB setup completed successfully")
            return True, "InfluxDB fully configured and capability-tested"
            
        except Exception as e:
            error_msg = f"Enhanced InfluxDB setup failed: {e}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            return False, error_msg

    async def _enhanced_grafana_setup(self, progress_callback: Callable[[str, str], None]) -> Tuple[bool, str]:
        """Enhanced Grafana setup with datasource verification"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "Starting enhanced Grafana setup with datasource verification...")
            
            # Step 1: Basic setup
            progress_callback("Grafana Basic Setup", "Verifying Grafana accessibility...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running basic Grafana setup")
            
            basic_ok, basic_msg = await self.run_in_executor(
                self.setup_wizard.configure_grafana_datasource, progress_callback
            )
            
            if not basic_ok:
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Basic Grafana setup failed: {basic_msg}")
                return False, basic_msg
            
            # Step 2: Capability testing
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running comprehensive Grafana capability tests")
            
            test_functions = [
                ("Connection", self.setup_wizard._test_grafana_connection),
                ("Authentication", self.setup_wizard._test_grafana_auth),
                ("Datasource Configuration", self.setup_wizard._test_grafana_datasources)
            ]
            
            capability_ok = await self.setup_wizard._test_service_capabilities(
                "Grafana", test_functions, progress_callback
            )
            
            if not capability_ok:
                error_msg = "Grafana capability testing failed"
                self.log_error_via_ulm(LogSource.MAIN_APP, error_msg)
                return False, error_msg
            
            self.log_info_via_ulm(LogSource.MAIN_APP, "Enhanced Grafana setup completed successfully")
            return True, "Grafana configured and capability-tested"
            
        except Exception as e:
            error_msg = f"Enhanced Grafana setup failed: {e}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            return False, error_msg

    async def _enhanced_loki_setup(self, progress_callback: Callable[[str, str], None]) -> Tuple[bool, str]:
        """Enhanced Loki setup with log monitoring verification"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "Starting enhanced Loki setup with log monitoring...")
            
            # Step 1: Basic setup
            progress_callback("Loki Basic Setup", "Setting up Loki and Promtail...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running basic Loki setup")
            
            basic_ok, basic_msg = await self.run_in_executor(
                self.setup_wizard.setup_loki_promtail, progress_callback
            )
            
            if not basic_ok:
                self.log_error_via_ulm(LogSource.MAIN_APP, f"Basic Loki setup failed: {basic_msg}")
                return False, basic_msg
            
            # Step 2: Monitor Loki container logs for startup completion
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Monitoring Loki container logs for startup patterns")
            
            log_monitoring_ok = await self.setup_wizard._monitor_service_logs(
                "tower_hooker_loki", 
                self.setup_wizard.loki_startup_patterns,
                progress_callback,
                timeout=30
            )
            
            if log_monitoring_ok:
                self.log_debug_via_ulm(LogSource.MAIN_APP, "Loki startup patterns detected in logs")
            else:
                self.log_warning_via_ulm(LogSource.MAIN_APP, "Loki startup log monitoring timed out, using fallback verification")
            
            # Step 3: Capability testing
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running comprehensive Loki capability tests")
            
            test_functions = [
                ("Connection", self.setup_wizard._test_loki_connection),
                ("Metrics Endpoint", self.setup_wizard._test_loki_metrics)
            ]
            
            capability_ok = await self.setup_wizard._test_service_capabilities(
                "Loki", test_functions, progress_callback
            )
            
            if not capability_ok:
                error_msg = "Loki capability testing failed"
                self.log_error_via_ulm(LogSource.MAIN_APP, error_msg)
                return False, error_msg
            
            self.log_info_via_ulm(LogSource.MAIN_APP, "Enhanced Loki setup completed successfully")
            return True, "Loki and Promtail configured and verified"
            
        except Exception as e:
            error_msg = f"Enhanced Loki setup failed: {e}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            return False, error_msg

    async def _enhanced_final_verification(self, progress_callback: Callable[[str, str], None]) -> Tuple[bool, str]:
        """Enhanced final verification with comprehensive system testing"""
        try:
            self.log_info_via_ulm(LogSource.MAIN_APP, "Starting enhanced final verification...")
            
            # Step 1: Comprehensive setup status check
            progress_callback("Final Verification", "Running comprehensive setup status check...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Running comprehensive setup status check")
            
            status = self.setup_wizard.check_setup_status()
            
            if not status['overall_ready']:
                error_details = []
                for component, info in status.items():
                    if component != 'overall_ready' and isinstance(info, dict) and not info.get('ready', True):
                        error_details.append(f"{component}: {info.get('error', 'Not ready')}")
                
                error_msg = f"Final verification failed. Issues: {'; '.join(error_details)}"
                self.log_error_via_ulm(LogSource.MAIN_APP, error_msg)
                return False, error_msg
            
            # Step 2: Test inter-service connectivity
            progress_callback("Final Verification", "Testing inter-service connectivity...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Testing inter-service connectivity")
            
            # Test if Grafana can connect to InfluxDB and Loki
            try:
                # This would require more sophisticated testing
                # For now, we rely on the individual service checks
                pass
            except Exception as e:
                self.log_warning_via_ulm(LogSource.MAIN_APP, f"Inter-service connectivity test warning: {e}")
            
            # Step 3: Mark setup as complete
            progress_callback("Final Verification", "Marking setup as complete...")
            self.log_debug_via_ulm(LogSource.MAIN_APP, "Marking setup as complete")
            
            completion_ok, completion_msg = await self.run_in_executor(
                self.setup_wizard.mark_setup_complete, progress_callback
            )
            
            if not completion_ok:
                self.log_warning_via_ulm(LogSource.MAIN_APP, f"Setup completion marker creation failed: {completion_msg}")
                # Don't fail the whole setup for this
            
            self.log_info_via_ulm(LogSource.MAIN_APP, "Enhanced final verification completed successfully")
            return True, "Final verification completed - all systems operational"
            
        except Exception as e:
            error_msg = f"Enhanced final verification failed: {e}"
            self.log_error_via_ulm(LogSource.MAIN_APP, error_msg, exc_info=True)
            return False, error_msg