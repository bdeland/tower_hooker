import subprocess
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Optional, Dict, List, Union, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager

from src.utils.config import get_emulator_config, get_enable_logcat_logging, get_enable_pslist_logging, get_log_to_file

# Import unified logging system
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_critical, log_debug, get_logging_manager
from src.managers.unified_logging_definitions import LogSource, LogLevel


# Constants
class EmulatorConstants:
    DEFAULT_TIMEOUT = 10
    FRIDA_TIMEOUT = 10
    FRIDA_START_DELAY = 3
    ADB_RESTART_DELAY = 2
    PORT_SCAN_DELAY = 0.2
    PSLIST_INTERVAL = 1
    PORT_RANGE_START = 5555
    PORT_RANGE_END = 5585
    LOGCAT_BUFFER_SIZE = 1


@dataclass
class DeviceInfo:
    """Device information structure."""
    serial: str
    model: str
    device_name: str
    foreground_app: str


@dataclass
class FridaServerStatus:
    """Frida server status structure."""
    installed: bool = False
    running: bool = False
    version: Optional[str] = None
    success: bool = False
    device: Optional[Any] = None  # frida.Device type
    error: Optional[str] = None


class EmulatorManager:
    """
    Generic Android Emulator Manager
    
    Works with any Android emulator that supports ADB (BlueStacks, MuMu Player, 
    NoxPlayer, LDPlayer, Genymotion, etc.)
    """
    
    def __init__(self, adb_path: Union[str, Path], data_manager=None, emulator_type: str = "generic") -> None:
        self.adb_path = str(adb_path) if isinstance(adb_path, Path) else adb_path
        self.emulator_type = emulator_type
        self.selected_serial: Optional[str] = None
        self.logcat_proc: Optional[subprocess.Popen] = None
        self.logcat_thread: Optional[threading.Thread] = None
        self.pslist_thread: Optional[threading.Thread] = None
        self._pslist_stop = threading.Event()
        
        # Get the unified logging manager
        self.logging_manager = get_logging_manager()
        
        # Add the data ingestion manager (used for InfluxDB writes)
        self.data_manager = data_manager
        
        log_info(LogSource.EMULATOR, f"Initialized EmulatorManager for {emulator_type}", 
                 adb_path=self.adb_path, emulator_type=emulator_type)

    def _execute_adb(self, command_parts: List[str], timeout: int = EmulatorConstants.DEFAULT_TIMEOUT, 
                    serial: Optional[str] = None, require_device: bool = True) -> subprocess.CompletedProcess:
        """Low-level ADB command execution with explicit device context."""
        if not command_parts:
            raise ValueError("Command parts cannot be empty")
        
        # Determine which serial to use
        target_serial = serial or self.selected_serial
        
        # Validate device requirement
        if require_device and not target_serial:
            raise ValueError(
                f"Device serial required for command '{' '.join(command_parts)}'. "
                f"Either connect to a device first or provide explicit serial."
            )
            
        command = [self.adb_path]
        if target_serial:
            command.extend(["-s", target_serial])
        command.extend(command_parts)
        
        # Log which device we're targeting
        if target_serial:
            log_debug(LogSource.EMULATOR, "Executing ADB command", 
                     command=' '.join(command_parts), target_device=target_serial)
        
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            return result
        except subprocess.TimeoutExpired as e:
            log_warning(LogSource.EMULATOR, "ADB command timed out", command=' '.join(command), timeout=timeout)
            return subprocess.CompletedProcess(command, timeout, stdout="", stderr=str(e))
        except FileNotFoundError:
            log_error(LogSource.EMULATOR, "ADB executable not found", adb_path=self.adb_path)
            return subprocess.CompletedProcess(command, -1, stdout="", stderr=f"ADB not found at {self.adb_path}")
        except Exception as e:
            log_error(LogSource.EMULATOR, "Unexpected error in ADB command", command=' '.join(command), error=str(e))
            return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(e))

    # High-level command methods
    
    # Server management commands (no device required)
    def devices(self) -> List[str]:
        """Get list of connected device serials."""
        result = self._execute_adb(["devices"], require_device=False)
        return self._parse_device_list(result)

    def kill_server(self) -> subprocess.CompletedProcess:
        """Kill ADB server."""
        return self._execute_adb(["kill-server"], require_device=False)

    def start_server(self) -> subprocess.CompletedProcess:
        """Start ADB server."""
        return self._execute_adb(["start-server"], require_device=False)

    def connect(self, host: str, port: int = 5555) -> subprocess.CompletedProcess:
        """Connect to ADB over network."""
        return self._execute_adb(["connect", f"{host}:{port}"], require_device=False)

    def disconnect(self, endpoint: Optional[str] = None) -> subprocess.CompletedProcess:
        """Disconnect from ADB endpoint."""
        cmd = ["disconnect"]
        if endpoint:
            cmd.append(endpoint)
        return self._execute_adb(cmd, require_device=False)
    
    # Device-specific commands (require device context)
    def shell(self, command: str, as_root: bool = False, timeout: int = EmulatorConstants.DEFAULT_TIMEOUT, 
             serial: Optional[str] = None) -> subprocess.CompletedProcess:
        """Execute shell command on device. Requires device context."""
        if not command.strip():
            raise ValueError("Shell command cannot be empty")
            
        if as_root:
            return self._execute_adb(["shell", "su", "-c", command], timeout=timeout, serial=serial)
        else:
            # Use shlex for proper argument parsing
            import shlex
            cmd_parts = ["shell"] + shlex.split(command)
            return self._execute_adb(cmd_parts, timeout=timeout, serial=serial)

    def push(self, local_path: Union[str, Path], remote_path: str, 
            serial: Optional[str] = None) -> subprocess.CompletedProcess:
        """Push file to device. Requires device context."""
        local_path = str(local_path)
        if not Path(local_path).exists():
            log_error(LogSource.EMULATOR, "Local file not found", path=local_path)
            return subprocess.CompletedProcess([], -1, "", "File not found")
        return self._execute_adb(["push", local_path, remote_path], serial=serial)

    def pull(self, remote_path: str, local_path: Union[str, Path], 
            serial: Optional[str] = None) -> subprocess.CompletedProcess:
        """Pull file from device. Requires device context."""
        return self._execute_adb(["pull", remote_path, str(local_path)], serial=serial)

    def install(self, apk_path: Union[str, Path], serial: Optional[str] = None, 
               **kwargs) -> subprocess.CompletedProcess:
        """Install APK on device. Requires device context."""
        cmd = ["install"]
        if kwargs.get('replace', False):
            cmd.append("-r")
        if kwargs.get('allow_test', False):
            cmd.append("-t")
        cmd.append(str(apk_path))
        return self._execute_adb(cmd, serial=serial)

    def uninstall(self, package_name: str, keep_data: bool = False, 
                 serial: Optional[str] = None) -> subprocess.CompletedProcess:
        """Uninstall package from device. Requires device context."""
        cmd = ["uninstall"]
        if keep_data:
            cmd.append("-k")
        cmd.append(package_name)
        return self._execute_adb(cmd, serial=serial)

    def get_property(self, property_name: str, serial: Optional[str] = None) -> str:
        """Get device property value. Requires device context."""
        result = self.shell(f"getprop {property_name}", serial=serial)
        return result.stdout.strip() if result.returncode == 0 else ""



    def _restart_adb_server(self) -> None:
        """Restart the ADB server."""
        log_info(LogSource.EMULATOR, "Restarting ADB server...")
        
        kill_result = self.kill_server()
        log_info(LogSource.EMULATOR, "adb kill-server result", 
                 return_code=kill_result.returncode, 
                 stdout=kill_result.stdout.strip(), 
                 stderr=kill_result.stderr.strip())
        
        time.sleep(EmulatorConstants.ADB_RESTART_DELAY // 2)
        
        start_result = self.start_server()
        log_info(LogSource.EMULATOR, "adb start-server result", 
                 return_code=start_result.returncode, 
                 stdout=start_result.stdout.strip(), 
                 stderr=start_result.stderr.strip())
        
        time.sleep(EmulatorConstants.ADB_RESTART_DELAY)

    def _try_adb_connect_scan_ports(self, port_start: int = EmulatorConstants.PORT_RANGE_START, 
                                   port_end: int = EmulatorConstants.PORT_RANGE_END) -> None:
        """Scan ports and attempt ADB connections."""
        log_info(LogSource.EMULATOR, "Scanning odd ports for emulator adb connections", 
                 port_start=port_start, port_end=port_end)
        
        for port in range(port_start, port_end + 1, 2):
            log_info(LogSource.EMULATOR, "Trying adb connect", address=f"127.0.0.1:{port}")
            result = self.connect("127.0.0.1", port)
            log_info(LogSource.EMULATOR, "adb connect result", 
                     address=f"127.0.0.1:{port}",
                     return_code=result.returncode, 
                     stdout=result.stdout.strip(), 
                     stderr=result.stderr.strip())
            time.sleep(EmulatorConstants.PORT_SCAN_DELAY)

    def _get_connected_devices(self) -> List[str]:
        """Get list of connected device serials with recovery attempts."""
        devices = self.devices()
        
        if not devices:
            log_warning(LogSource.EMULATOR, "No active devices found. Attempting to recover...")
            self._restart_adb_server()
            self._try_adb_connect_scan_ports()
            
            # Try again after recovery
            devices = self.devices()
            
            if not devices:
                log_error(LogSource.EMULATOR, "Still no active devices found after recovery attempts.")
        
        return devices

    def _parse_device_list(self, result: subprocess.CompletedProcess) -> List[str]:
        """Parse device list from adb devices output."""
        devices = []
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().splitlines()
            for line in lines[1:]:  # Skip header
                if "\t" in line:
                    serial, state = line.split("\t")
                    if state == "device":
                        devices.append(serial)
        return devices

    def _get_device_info(self, serial: str) -> DeviceInfo:
        """Get comprehensive device information."""
        with self.device_context(serial):
            # Get model and device name using property getter
            model = self.get_property("ro.product.model") or "?"
            device_name = self.get_property("ro.product.device") or "?"
            
            # Get current foreground app
            foreground_app = self._get_foreground_app(serial)
            
            return DeviceInfo(
                serial=serial,
                model=model,
                device_name=device_name,
                foreground_app=foreground_app
            )

    def _get_foreground_app(self, serial: str) -> str:
        """Extract foreground app information."""
        app_result = self.shell("dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'")
        
        if app_result.returncode == 0 and app_result.stdout:
            import re
            match = re.search(r"([a-zA-Z0-9_.]+/[a-zA-Z0-9_.]+)", app_result.stdout)
            return match.group(1) if match else app_result.stdout.strip()
        return "?"

    def connect_to_emulator(self, host: str = "127.0.0.1", port: Optional[int] = None) -> bool:
        """Connect to emulator with proper device selection."""
        log_info(LogSource.EMULATOR, f"Checking for connected {self.emulator_type} emulator device", host=host)
        
        devices = self._get_connected_devices()
        if not devices:
            log_info(LogSource.EMULATOR, "No active device found after recovery attempts.")
            return False

        device_infos = [self._get_device_info(serial) for serial in devices]
        
        if len(devices) == 1:
            return self._connect_single_device(device_infos[0])
        else:
            return self._connect_multiple_devices(device_infos)

    def _connect_single_device(self, device_info: DeviceInfo) -> bool:
        """Handle single device connection."""
        self.selected_serial = device_info.serial
        log_info(LogSource.EMULATOR, f"Connected to {self.emulator_type} emulator", serial=self.selected_serial)
        log_info(LogSource.EMULATOR, "Device info", 
                 serial=device_info.serial, 
                 model=device_info.model, 
                 device_name=device_info.device_name, 
                 foreground_app=device_info.foreground_app)
        
        return self._finalize_connection()

    def _connect_multiple_devices(self, device_infos: List[DeviceInfo]) -> bool:
        """Handle multiple device selection."""
        # Show device list
        log_info(LogSource.EMULATOR, "Multiple emulators detected. Please select one:")
        for idx, info in enumerate(device_infos):
            log_info(LogSource.EMULATOR, f"Device option {idx+1}", 
                     serial=info.serial, 
                     model=info.model, 
                     device_name=info.device_name, 
                     foreground_app=info.foreground_app)
        log_info(LogSource.EMULATOR, "--- Device List Above ---")
        
        # Get user selection
        selected_device = self._get_user_device_selection(device_infos)
        self.selected_serial = selected_device.serial
        log_info(LogSource.EMULATOR, "Selected device", serial=self.selected_serial)
        
        return self._finalize_connection()

    def _get_user_device_selection(self, device_infos: List[DeviceInfo]) -> DeviceInfo:
        """Get user device selection with proper error handling."""
        try:
            choice = input(f"Enter number (1-{len(device_infos)}): ").strip()
            if not choice.isdigit():
                log_warning(LogSource.EMULATOR, "Invalid input. Using first device as default.")
                return device_infos[0]
                
            idx = int(choice) - 1
            if 0 <= idx < len(device_infos):
                return device_infos[idx]
            else:
                log_warning(LogSource.EMULATOR, "Choice out of range. Using first device as default.")
                return device_infos[0]
                
        except EOFError:
            log_warning(LogSource.EMULATOR, "Input not available (EOFError). Using first device as default.")
            return device_infos[0]
        except Exception as e:
            log_error(LogSource.EMULATOR, "Error during device selection. Using first device as default.", error=str(e))
            return device_infos[0]

    def _finalize_connection(self) -> bool:
        """Finalize connection setup with root check and logging."""
        if not self.is_rooted():
            log_error(LogSource.EMULATOR, "Selected emulator is not rooted. Root access is required.")
            return False
        
        # Setup logging with error handling
        if get_enable_logcat_logging():
            self._safe_logging_setup(self._start_logcat_logging, "logcat")
        
        if get_enable_pslist_logging():
            self._safe_logging_setup(self._start_pslist_logging, "process list")
        else:
            log_info(LogSource.EMULATOR, "Process list logging is disabled in configuration.")
        
        return True

    def _safe_logging_setup(self, setup_func, logging_type: str) -> bool:
        """Common pattern for logging setup with error handling."""
        try:
            setup_func()
            log_info(LogSource.EMULATOR, f"{logging_type} logging started successfully.")
            return True
        except Exception as e:
            log_error(LogSource.EMULATOR, f"Failed to start {logging_type} logging", error=str(e))
            log_warning(LogSource.EMULATOR, f"Continuing without {logging_type} logging...")
            return False

    def is_rooted(self) -> bool:
        """Check if the connected emulator instance is rooted."""
        if not self.selected_serial:
            log_error(LogSource.EMULATOR, "No device selected")
            return False
            
        log_info(LogSource.EMULATOR, "Checking if emulator instance is rooted...")
        result = self.shell("whoami", as_root=True)
        
        if result.returncode == 0 and result.stdout and "root" in result.stdout.strip().lower():
            log_info(LogSource.EMULATOR, "Emulator instance is rooted.")
            return True
        else:
            log_warning(LogSource.EMULATOR, "Emulator instance is not rooted, or 'whoami' failed", 
                        stdout=result.stdout.strip(), 
                        stderr=result.stderr.strip())
            return False

    def push_frida_server(self, local_frida_path: Union[str, Path], remote_frida_path: str) -> subprocess.CompletedProcess:
        """Push Frida server to the device with validation."""
        local_path = Path(local_frida_path)
        if not local_path.exists():
            log_error(LogSource.EMULATOR, "Local Frida server not found", path=str(local_path))
            return subprocess.CompletedProcess([], -1, "", "File not found")
        
        if not remote_frida_path.strip():
            raise ValueError("Remote Frida path cannot be empty")
        
        log_info(LogSource.EMULATOR, "Pushing Frida server", 
                 local_path=str(local_path), 
                 remote_path=remote_frida_path)
        
        result = self.push(local_path, remote_frida_path)
        
        if result.returncode == 0:
            log_info(LogSource.EMULATOR, "Frida server pushed successfully.")
        else:
            log_warning(LogSource.EMULATOR, "Failed to push Frida server", stderr=result.stderr.strip())
        
        return result

    def start_frida_server(self, remote_frida_path: str) -> Optional[Any]:
        """Start Frida server on the device."""
        assert self.selected_serial, "Device must be selected before starting Frida server"
        assert remote_frida_path.strip(), "Remote Frida path cannot be empty"
        
        log_info(LogSource.EMULATOR, "Attempting to start Frida server", 
                frida_path=remote_frida_path, serial=self.selected_serial)
        
        # Make executable
        chmod_result = self.shell(f"chmod 755 {remote_frida_path}", as_root=True)
        if chmod_result.returncode != 0:
            log_warning(LogSource.EMULATOR, "Error making Frida server executable", 
                       error=chmod_result.stderr, frida_path=remote_frida_path)
            return None
        
        # Start server
        start_command = f"nohup {remote_frida_path} > /dev/null 2>&1 &"
        start_result = self.shell(start_command, as_root=True)
        
        if start_result.returncode != 0 and "already running" not in start_result.stderr.lower():
            log_warning(LogSource.EMULATOR, "Error starting Frida server", 
                       error=start_result.stderr, command=start_command)
            return None
        
        log_info(LogSource.EMULATOR, "Frida server start command issued. Verifying responsiveness...")
        time.sleep(EmulatorConstants.FRIDA_START_DELAY)
        
        return self._verify_frida_server(remote_frida_path)

    def _verify_frida_server(self, remote_frida_path: str) -> Optional[Any]:
        """Verify Frida server is responsive."""
        try:
            # Lazy import to avoid import errors when frida isn't available
            import frida
            
            device = frida.get_device(self.selected_serial, timeout=EmulatorConstants.FRIDA_TIMEOUT)
            apps = device.enumerate_applications()
            log_info(LogSource.EMULATOR, "Frida server responsive", app_count=len(apps))
            return device
            
        except ImportError:
            log_error(LogSource.EMULATOR, "Frida module not available")
            return None
        except Exception as e:
            log_warning(LogSource.EMULATOR, "Frida server verification failed", error=str(e))
            
            # Fallback: check if process is running
            ps_output = self.shell("ps -A | grep frida-server", as_root=True)
            if remote_frida_path in ps_output.stdout or "frida-server" in ps_output.stdout:
                log_info(LogSource.EMULATOR, "Frida server process found via 'ps' command")
                log_warning(LogSource.EMULATOR, "Could not verify responsiveness via Frida API, but process seems running.")
            
            return None

    def _kill_and_verify_frida(self, name: str) -> bool:
        """Issues a kill command and then verifies the process has terminated."""
        log_info(LogSource.EMULATOR, "Issuing command to stop Frida server process...")
        self.shell(f"pkill -f {os.path.basename(name)}", as_root=True, timeout=2)
        
        # Fast, responsive polling loop to verify termination
        timeout = 2.0  # Max wait time in seconds
        poll_interval = 0.1  # Check every 100ms
        end_time = time.time() + timeout

        while time.time() < end_time:
            if not self._is_frida_running():
                log_info(LogSource.EMULATOR, "Frida server process confirmed stopped.")
                return True
            time.sleep(poll_interval)

        log_warning(LogSource.EMULATOR, "Timed out waiting for Frida server process to stop.")
        return False

    def stop_frida_server(self, remote_frida_path_or_name: str = "frida-server") -> bool:
        """Stops Frida server on the device and verifies its termination."""
        self.ensure_device_selected()
        log_info(LogSource.EMULATOR, "Attempting to stop Frida server", target=remote_frida_path_or_name)
        
        if not self._is_frida_running():
            log_info(LogSource.EMULATOR, "No Frida server process was found running.")
            return True
        
        return self._kill_and_verify_frida(remote_frida_path_or_name)

    def check_frida_server_status(self, remote_frida_path: str) -> FridaServerStatus:
        """Check Frida server installation and runtime status."""
        if not self.selected_serial:
            return FridaServerStatus(error="No device selected")
            
        log_info(LogSource.EMULATOR, "Checking Frida server status", 
                frida_path=remote_frida_path, serial=self.selected_serial)
        
        status = FridaServerStatus()
        
        # Check installation
        if not self._is_frida_installed(remote_frida_path):
            return status
        status.installed = True
        
        # Check if running
        if not self._is_frida_running():
            return status
        status.running = True
        
        # Get version
        status.version = self._get_frida_version(remote_frida_path)
        
        log_info(LogSource.EMULATOR, "Frida server status check complete", 
                installed=status.installed, running=status.running, version=status.version)
        
        return status

    def _is_frida_installed(self, remote_frida_path: str) -> bool:
        """Check if Frida server is installed."""
        ls_result = self.shell(f"ls -l {remote_frida_path}", as_root=True, timeout=5)
        return ls_result.returncode == 0 and remote_frida_path in ls_result.stdout

    def _is_frida_running(self) -> bool:
        """Check if Frida server is running."""
        ps_result = self.shell("ps -A | grep frida-server", as_root=True, timeout=2)
        return ps_result.returncode == 0 and "frida-server" in ps_result.stdout

    def _get_frida_version(self, remote_frida_path: str) -> Optional[str]:
        """Get Frida server version."""
        try:
            version_result = self.shell(f"{remote_frida_path} --version", as_root=True, timeout=5)
            if version_result.returncode == 0 and version_result.stdout.strip():
                return version_result.stdout.strip()
            
            # Fallback: try to infer from client version
            if self._is_frida_running():
                try:
                    import frida
                    frida.get_device(self.selected_serial, timeout=5)
                    return frida.__version__
                except Exception:
                    pass
                    
        except Exception as e:
            log_warning(LogSource.EMULATOR, "Error checking Frida server version", error=str(e))
        
        return None

    def ensure_frida_server(self, local_frida_path: Union[str, Path], remote_frida_path: str, 
                           expected_version: str, timeout: int = EmulatorConstants.FRIDA_TIMEOUT) -> FridaServerStatus:
        """Ensure correct Frida server version is installed and running."""
        result = FridaServerStatus()
        
        try:
            # Check current status
            status = self.check_frida_server_status(remote_frida_path)
            result.installed = status.installed
            result.running = status.running
            result.version = status.version
            
            log_info(LogSource.EMULATOR, "Current Frida server status", 
                    installed=status.installed, running=status.running,
                    device_version=status.version, expected_version=expected_version)
            
            # Determine required actions
            need_to_push = not status.installed or (
                status.version and expected_version not in status.version
            )
            need_to_restart = (need_to_push and status.running) or (
                status.installed and not status.running
            )
            
            # Execute required actions
            if need_to_restart and status.running:
                self._stop_frida_with_timeout(remote_frida_path, timeout)
            
            if need_to_push:
                push_result = self.push_frida_server(local_frida_path, remote_frida_path)
                result.installed = push_result.returncode == 0
            
            if need_to_restart or need_to_push:
                device = self.start_frida_server(remote_frida_path)
                result.device = device
                result.running = device is not None or self._is_frida_running()
            else:
                # Try to get device object for running server
                result.device = self._get_frida_device_safe()
            
            result.success = result.installed and result.running
            
        except Exception as e:
            log_error(LogSource.EMULATOR, "Error in ensure_frida_server", error=str(e))
            result.error = str(e)
        
        return result

    def _stop_frida_with_timeout(self, remote_frida_path: str, timeout: int) -> None:
        """Stop Frida server with timeout."""
        log_info(LogSource.EMULATOR, "Stopping existing Frida server")
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(self.stop_frida_server, remote_frida_path)
                stop_result = future.result(timeout=timeout)
                if stop_result:
                    log_info(LogSource.EMULATOR, "Existing Frida server stopped.")
                else:
                    log_warning(LogSource.EMULATOR, "Failed to stop Frida server. Continuing...")
            except TimeoutError:
                log_warning(LogSource.EMULATOR, "stop_frida_server operation timed out", timeout=timeout)

    def _get_frida_device_safe(self) -> Optional[Any]:
        """Safely get Frida device object."""
        try:
            import frida
            return frida.get_device(self.selected_serial, timeout=EmulatorConstants.FRIDA_TIMEOUT)
        except Exception as e:
            log_warning(LogSource.EMULATOR, "Could not get Frida device object", error=str(e))
            return None

    def list_processes(self, parsed: bool = True) -> Union[List[str], List[Dict[str, str]]]:
        """Unified process listing method."""
        if not self.selected_serial:
            log_error(LogSource.EMULATOR, "No device selected")
            return []
            
        log_info(LogSource.EMULATOR, "Listing processes on device", 
                serial=self.selected_serial, parsed=parsed)
        
        process_result = self.shell("ps -A", timeout=20)
        
        if not process_result or process_result.returncode != 0 or not process_result.stdout:
            log_warning(LogSource.EMULATOR, "Error running 'ps -A'", 
                       stderr=process_result.stderr if process_result else 'N/A')
            return []
        
        lines = process_result.stdout.strip().splitlines()
        
        if not parsed:
            log_info(LogSource.EMULATOR, "'ps -A' successful", output_lines=len(lines))
            return lines
        
        return [p for p in self._parse_ps_output(process_result.stdout) if not p.get('malformed', False)]

    def _parse_ps_output(self, stdout: str) -> List[Dict[str, Any]]:
        """Parse ps output for both logging and general use."""
        process_list = []
        lines = stdout.strip().split('\n')
        
        if not lines:
            return process_list
        
        header = lines[0].strip().split()
        
        for line_idx, line in enumerate(lines[1:], 1):
            parts = line.strip().split(None, len(header)-1)
            if len(parts) >= len(header):
                process_data = {header[i].lower(): parts[i] for i in range(len(header))}
                process_list.append(process_data)
            else:
                process_list.append({
                    "raw_line": line.strip(),
                    "line_num": line_idx,
                    "malformed": True
                })
                log_warning(LogSource.EMULATOR, "Skipping malformed ps output line", 
                           line_number=line_idx, raw_line=line.strip())
        
        log_info(LogSource.EMULATOR, f"Parsed {len(process_list)} processes from ps output")
        return process_list



    def get_pid_for_package(self, package_name: str) -> Optional[int]:
        """Get PID for a package with proper validation."""
        if not package_name or not package_name.strip():
            log_error(LogSource.EMULATOR, "Package name cannot be empty")
            return None
        
        if not self.selected_serial:
            log_error(LogSource.EMULATOR, "No device selected")
            return None
        
        log_info(LogSource.EMULATOR, "Getting PID for package", 
                package_name=package_name, method="pidof")
        
        # Try pidof first
        pid = self._get_pid_via_pidof(package_name)
        if pid is not None:
            return pid
        
        # Fallback to ps parsing
        log_warning(LogSource.EMULATOR, "'pidof' failed. Trying fallback with 'ps -A'", 
                   package_name=package_name)
        return self._get_pid_via_ps_fallback(package_name)

    def _get_pid_via_pidof(self, package_name: str) -> Optional[int]:
        """Get PID using pidof command."""
        pidof_command = f"pidof {package_name}"
        pid_result = self.shell(pidof_command, as_root=True, timeout=10)

        if pid_result and pid_result.returncode == 0 and pid_result.stdout:
            pid_str = pid_result.stdout.strip()
            pids = pid_str.split()
            if pids:
                try:
                    pid = int(pids[0])
                    log_info(LogSource.EMULATOR, "Found PID for package", 
                            pid=pid, package_name=package_name, method="pidof")
                    return pid
                except ValueError:
                    log_warning(LogSource.EMULATOR, "Could not convert PID to int", 
                               pid_str=pids[0], package_name=package_name)
        return None

    def _get_pid_via_ps_fallback(self, package_name: str) -> Optional[int]:
        """Get PID using an efficient `ps | grep` fallback."""
        command = f"ps -A | grep {package_name}"
        result = self.shell(command, timeout=5)
        
        if result.returncode == 0 and result.stdout:
            # Find the line that ends with the exact package name to avoid partial matches
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                # Check that we have enough parts and the last part is the exact package name
                if len(parts) > 1 and parts[-1] == package_name:
                    try:
                        pid = int(parts[1])
                        log_info(LogSource.EMULATOR, "Found PID for package", 
                                 pid=pid, package_name=package_name, method="ps_fallback")
                        return pid
                    except (ValueError, IndexError):
                        continue
        
        log_warning(LogSource.EMULATOR, "Could not find PID for package", package_name=package_name)
        return None

    def get_selected_serial(self) -> Optional[str]:
        """Get the currently selected emulator serial."""
        return self.selected_serial

    @contextmanager
    def device_context(self, serial: Optional[str] = None):
        """
        Context manager for device operations.
        
        Usage:
            with manager.device_context("emulator-5554"):
                manager.shell("ls /data")
                manager.push("file.txt", "/data/")
        """
        original_serial = self.selected_serial
        try:
            if serial:
                self.selected_serial = serial
                log_debug(LogSource.EMULATOR, "Entering device context", device=serial)
            elif not self.selected_serial:
                raise ValueError("No device serial provided and no device currently selected")
            yield self
        finally:
            self.selected_serial = original_serial
            if serial:
                log_debug(LogSource.EMULATOR, "Exiting device context", device=serial)

    def ensure_device_selected(self) -> str:
        """Ensure a device is selected and return its serial."""
        if not self.selected_serial:
            raise ValueError(
                "No device selected. Call connect_to_emulator() first or use device_context()."
            )
        return self.selected_serial

    def shutdown(self):
        """Stops all background logging threads and processes for a clean exit."""
        log_info(LogSource.EMULATOR, "Shutting down EmulatorManager...")
        
        if self.pslist_thread and self.pslist_thread.is_alive():
            self._pslist_stop.set()
            self.pslist_thread.join(timeout=2)
            log_info(LogSource.PSLIST, "PSList logging stopped.")

        if self.logcat_proc:
            self.logcat_proc.terminate()
            try:
                self.logcat_proc.wait(timeout=2)
                log_info(LogSource.LOGCAT, "Logcat process terminated.")
            except subprocess.TimeoutExpired:
                log_warning(LogSource.LOGCAT, "Logcat process did not terminate gracefully, killing.")
                self.logcat_proc.kill()
        
        self.selected_serial = None

    def _start_logcat_logging(self) -> None:
        """Start ADB logcat logging using unified logging system."""
        if self.logcat_proc is not None:
            log_info(LogSource.LOGCAT, "Logcat logging already started for this instance.")
            return
        
        if not self.selected_serial:
            log_warning(LogSource.LOGCAT, "No selected serial for logcat logging.")
            return
        
        log_info(LogSource.LOGCAT, "Starting adb logcat via unified logging manager", 
                serial=self.selected_serial)
        
        def logcat_thread_func():
            try:
                with subprocess.Popen(
                    [self.adb_path, '-s', self.selected_serial, 'logcat', '-v', 'threadtime'], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=EmulatorConstants.LOGCAT_BUFFER_SIZE
                ) as proc:
                    self.logcat_proc = proc
                    
                    for line in iter(proc.stdout.readline, ''):
                        self._process_logcat_line(line.strip())
                        
            except Exception as e:
                log_error(LogSource.LOGCAT, "Logcat subprocess error", 
                         device=self.selected_serial, error=str(e))
        
        self.logcat_thread = threading.Thread(target=logcat_thread_func, daemon=True)
        self.logcat_thread.start()

    def _process_logcat_line(self, line: str) -> None:
        """Process individual logcat line."""
        try:
            parts = line.split(None, 6)
            if len(parts) >= 7:
                date, time_str, pid, tid, priority, tag, message = parts
                log_message = f"[{priority}] {tag}: {message}"
                
                log_info(LogSource.LOGCAT, log_message,
                        timestamp=f"{date} {time_str}",
                        pid=pid, tid=tid, priority=priority, tag=tag,
                        device=self.selected_serial, type="logcat", parsed=True)
            else:
                log_info(LogSource.LOGCAT, f"[RAW] {line}",
                        device=self.selected_serial, type="logcat", 
                        raw=True, parsed=False)
        except Exception as e:
            log_error(LogSource.LOGCAT, "Logcat processing error", 
                     device=self.selected_serial, error=str(e), type="logcat_error")

    def _start_pslist_logging(self) -> None:
        """Start periodic process list logging."""
        if self.pslist_thread is not None and self.pslist_thread.is_alive():
            log_info(LogSource.PSLIST, "pslist logging already started for this instance.")
            return
        
        if not self.selected_serial:
            log_warning(LogSource.PSLIST, "No selected serial for pslist logging.")
            return
        
        log_info(LogSource.PSLIST, "Starting periodic ps -A logging", serial=self.selected_serial)
        self._pslist_stop.clear()
        
        def pslist_thread_func():
            try:
                while not self._pslist_stop.is_set():
                    self._capture_process_snapshot()
                    time.sleep(EmulatorConstants.PSLIST_INTERVAL)
            except Exception as e:
                log_error(LogSource.PSLIST, "PSList thread error", 
                         device=self.selected_serial, error=str(e))
                    
        self.pslist_thread = threading.Thread(target=pslist_thread_func, daemon=True)
        self.pslist_thread.start()

    def _capture_process_snapshot(self) -> None:
        """Capture and log process snapshot."""
        try:
            result = subprocess.run(
                [self.adb_path, '-s', self.selected_serial, 'shell', 'ps', '-A'], 
                capture_output=True, text=True, timeout=10
            )
            
            process_list = self._parse_ps_output(result.stdout)
            
            # Log summary
            log_info(LogSource.PSLIST, f"Process list snapshot: {len(process_list)} processes",
                    device=self.selected_serial, process_count=len(process_list), type="pslist_summary")
            
            # Log individual processes
            for process in process_list:
                self._log_process_entry(process)
                
        except Exception as e:
            log_error(LogSource.PSLIST, "PSList processing error", 
                     device=self.selected_serial, error=str(e), type="pslist_error")



    def _log_process_entry(self, process: Dict[str, Any]) -> None:
        """Log individual process entry."""
        if process.get('malformed', False):
            log_warning(LogSource.PSLIST, f"Malformed ps output line: {process.get('raw_line', 'unknown')}",
                       device=self.selected_serial, type="pslist_error",
                       raw_line=process.get('raw_line', ''), 
                       line_number=process.get('line_num', 0))
        else:
            process_name = process.get('name', 'unknown')
            process_pid = process.get('pid', '0')
            process_rss = process.get('rss', '0')
            
            process_message = f"Process {process_name} (PID: {process_pid}) - RSS: {process_rss}KB"
            
            log_info(LogSource.PSLIST, process_message,
                    device=self.selected_serial, type="pslist_process",
                    process_name=process_name,
                    process_pid=int(process_pid) if process_pid.isdigit() else 0,
                    process_user=process.get('user', 'unknown'),
                    process_ppid=int(process.get('ppid', '0')) if process.get('ppid', '0').isdigit() else 0,
                    process_rss=int(process_rss) if process_rss.isdigit() else 0,
                    process_vsz=int(process.get('vsz', '0')) if process.get('vsz', '0').isdigit() else 0,
                    process_state=process.get('s', 'unknown'))





if __name__ == '__main__':
    # Get emulator ADB path from centralized config
    emulator_config = get_emulator_config()
    ADB_PATH = emulator_config['adb_path']

    # Example usage with improved error handling
    manager = EmulatorManager(ADB_PATH, emulator_type="MuMu Player")

    log_info(LogSource.EMULATOR, "--- Testing ADB Connection ---")
    if manager.connect_to_emulator():
        log_info(LogSource.EMULATOR, "Emulator connection successful.")

        log_info(LogSource.EMULATOR, "--- Testing Root Check ---")
        
        # Create mock frida server for testing
        mock_frida_server_local_path = Path("./mock_frida_server")
        if not mock_frida_server_local_path.exists():
            mock_frida_server_local_path.write_text("this is a mock frida server")
            log_info(LogSource.EMULATOR, "Created mock frida server for testing", 
                    path=str(mock_frida_server_local_path))
        
        remote_path = "/data/local/tmp/test-frida-server"

        log_info(LogSource.EMULATOR, "--- Testing Frida Server Operations ---")
        manager.push_frida_server(mock_frida_server_local_path, remote_path)
        manager.start_frida_server(remote_path)

        log_info(LogSource.EMULATOR, "--- Testing PID Fetch ---")
        test_package = "com.android.settings" 
        pid = manager.get_pid_for_package(test_package)
        if pid:
            log_info(LogSource.EMULATOR, "Found PID for test package", package=test_package, pid=pid)
        else:
            log_warning(LogSource.EMULATOR, "Could not get PID for test package", package=test_package)

        manager.stop_frida_server(remote_path)

        # Cleanup
        if mock_frida_server_local_path.exists():
            mock_frida_server_local_path.unlink()
            log_info(LogSource.EMULATOR, "Cleaned up mock frida server")

    else:
        log_info(LogSource.EMULATOR, "Emulator connection failed. Cannot run further tests.")

    log_info(LogSource.EMULATOR, "--- EmulatorManager tests completed. ---") 