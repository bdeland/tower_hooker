"""
WSL2 Service Manager for Tower Hooker

This module provides comprehensive Docker service management for WSL2 environments,
including service startup, verification, and robust health checking with WSL2-aware
port forwarding detection.
"""

import subprocess
import time
import asyncio
import shutil
import requests
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

# Import unified logging system
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_debug
from src.managers.unified_logging_definitions import LogSource


class WSL2ServiceManager:
    """
    Manages Docker services in WSL2 environment with robust service verification
    and WSL2-aware port forwarding detection.
    """
    
    def __init__(self, wsl_distro_name: str = "Ubuntu", compose_file_path: Optional[str] = None):
        """
        Initialize WSL2ServiceManager
        
        Args:
            wsl_distro_name: Name of WSL2 distribution (default: "Ubuntu")
            compose_file_path: Path to docker-compose.yml file
        """
        self.wsl_distro_name = wsl_distro_name
        self.compose_file_path = compose_file_path or "docker-compose.yml"
        
        # Cache for compose command to avoid repeated detection
        self._compose_command_cache = None
        
    def get_compose_command(self) -> Optional[List[str]]:
        """
        Get the appropriate docker compose command for Windows/WSL2 environment.
        
        Returns:
            List of command parts or None if not available
        """
        if self._compose_command_cache is not None:
            return self._compose_command_cache
            
        # First try Windows Docker CLI with WSL2 backend
        if shutil.which("docker"):
            try:
                # Test if Windows Docker CLI can connect to daemon
                version_result = subprocess.run(
                    ["docker", "version"], 
                    capture_output=True, 
                    text=True, 
                    shell=True,
                    timeout=5
                )
                
                if version_result.returncode == 0:
                    # Windows CLI works, test compose
                    compose_result = subprocess.run(
                        ["docker", "compose", "version"], 
                        capture_output=True, 
                        text=True, 
                        shell=True,
                        timeout=5
                    )
                    if compose_result.returncode == 0:
                        self._compose_command_cache = ["docker", "compose"]
                        return self._compose_command_cache
            except (subprocess.TimeoutExpired, Exception):
                pass
        
        # Windows Docker CLI failed, try WSL2 fallback
        try:
            # Check if WSL2 distro is available and has Docker
            wsl_test = subprocess.run(
                ["wsl", "-d", self.wsl_distro_name, "--", "docker", "version"],
                capture_output=True,
                text=True,
                shell=True,
                timeout=10
            )
            
            if wsl_test.returncode == 0:
                # WSL2 Docker works, test compose
                wsl_compose_test = subprocess.run(
                    ["wsl", "-d", self.wsl_distro_name, "--", "docker", "compose", "version"],
                    capture_output=True,
                    text=True,
                    shell=True,
                    timeout=10
                )
                if wsl_compose_test.returncode == 0:
                    self._compose_command_cache = ["wsl", "-d", self.wsl_distro_name, "--", "docker", "compose"]
                    return self._compose_command_cache
        except (subprocess.TimeoutExpired, Exception):
            pass
        
        # Fallback for standalone docker-compose.exe if needed
        if shutil.which("docker-compose"):
            self._compose_command_cache = ["docker-compose"]
            return self._compose_command_cache
        
        return None
    
    def start_docker_services(self, services: Optional[List[str]] = None) -> bool:
        """
        Start Docker services using docker compose.
        
        Args:
            services: Optional list of specific services to start
            
        Returns:
            True if services started successfully, False otherwise
        """
        try:
            compose_cmd = self.get_compose_command()
            if not compose_cmd:
                log_error(LogSource.SYSTEM, "Docker compose command not available")
                return False
            
            # Build command
            cmd = compose_cmd + ["-f", self.compose_file_path, "up", "-d"]
            if services:
                cmd.extend(services)
            
            log_info(LogSource.SYSTEM, f"Starting Docker services with command: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                shell=True  # shell=True for Windows compatibility
            )
            
            if result.returncode == 0:
                log_info(LogSource.SYSTEM, "Docker services started successfully")
                log_debug(LogSource.SYSTEM, f"Docker compose output: {result.stdout}")
                return True
            else:
                log_error(LogSource.SYSTEM, f"Failed to start Docker services: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            log_error(LogSource.SYSTEM, "Docker service startup timed out")
            return False
        except Exception as e:
            log_error(LogSource.SYSTEM, f"Error starting Docker services: {str(e)}")
            return False
    
    def stop_docker_services(self) -> bool:
        """
        Stop Docker services using docker compose.
        
        Returns:
            True if services stopped successfully, False otherwise
        """
        try:
            compose_cmd = self.get_compose_command()
            if not compose_cmd:
                log_error(LogSource.SYSTEM, "Docker compose command not available")
                return False
            
            cmd = compose_cmd + ["-f", self.compose_file_path, "down"]
            
            log_info(LogSource.SYSTEM, f"Stopping Docker services with command: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                shell=True
            )
            
            if result.returncode == 0:
                log_info(LogSource.SYSTEM, "Docker services stopped successfully")
                return True
            else:
                log_error(LogSource.SYSTEM, f"Failed to stop Docker services: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            log_error(LogSource.SYSTEM, "Docker service shutdown timed out")
            return False
        except Exception as e:
            log_error(LogSource.SYSTEM, f"Error stopping Docker services: {str(e)}")
            return False
    
    def get_docker_service_logs(self, service_name: str, lines: int = 50) -> Optional[str]:
        """
        Get logs for a specific Docker service.
        
        Args:
            service_name: Name of the service to get logs for
            lines: Number of log lines to retrieve
            
        Returns:
            Service logs as string or None if failed
        """
        try:
            compose_cmd = self.get_compose_command()
            if not compose_cmd:
                log_error(LogSource.SYSTEM, "Docker compose command not available")
                return None
            
            cmd = compose_cmd + ["-f", self.compose_file_path, "logs", "--tail", str(lines), service_name]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                shell=True
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                log_error(LogSource.SYSTEM, f"Failed to get logs for service {service_name}: {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            log_error(LogSource.SYSTEM, f"Getting logs for service {service_name} timed out")
            return None
        except Exception as e:
            log_error(LogSource.SYSTEM, f"Error getting logs for service {service_name}: {str(e)}")
            return None
    
    def _verify_service_on_windows_host(self, host: str, port: int, path: str = "/", timeout: int = 5) -> bool:
        """
        Verify service accessibility directly from Windows host.
        
        Args:
            host: Host to check (usually "localhost")
            port: Port to check
            path: Health check path
            timeout: Request timeout in seconds
            
        Returns:
            True if service is accessible, False otherwise
        """
        try:
            url = f"http://{host}:{port}{path}"
            response = requests.get(url, timeout=timeout)
            return response.status_code in [200, 204]  # Accept both OK and No Content
        except Exception:
            return False
    
    def _verify_service_wsl2_aware(self, windows_host: str, windows_port: int, 
                                   wsl_internal_port: int, path: str = "/", timeout: int = 5) -> bool:
        """
        Verify service accessibility with WSL2-aware checking.
        
        This method first tries direct Windows connection (for WSL2 forwarded ports),
        then falls back to checking inside WSL2 if direct connection fails.
        
        Args:
            windows_host: Host on Windows side (usually "localhost")
            windows_port: Port exposed to Windows
            wsl_internal_port: Port inside WSL2 container
            path: Health check path
            timeout: Request timeout in seconds
            
        Returns:
            True if service is accessible, False otherwise
        """
        # First, try direct Windows connection (assuming WSL2 port forwarding)
        if windows_host.lower() == "localhost" and self._verify_service_on_windows_host(
            windows_host, windows_port, path, timeout
        ):
            log_debug(LogSource.SYSTEM, f"Service on localhost:{windows_port}{path} verified directly from Windows")
            return True
        
        # If direct fails, try WSL2 internal check
        log_debug(LogSource.SYSTEM, 
                 f"Direct Windows check failed for {windows_host}:{windows_port}. "
                 f"Trying WSL2 internal check for port {wsl_internal_port}")
        
        try:
            cmd = [
                'wsl', '-d', self.wsl_distro_name, '--', 
                'curl', '--fail', '--silent', '--show-error',
                f'http://localhost:{wsl_internal_port}{path}'
            ]
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=timeout, 
                check=False
            )
            
            if result.returncode == 0:
                log_info(LogSource.SYSTEM, f"Service verified inside WSL2 on port {wsl_internal_port}{path}")
                return True
            else:
                log_warning(LogSource.SYSTEM, 
                           f"WSL2 internal check failed for port {wsl_internal_port}{path}. "
                           f"Curl stderr: {result.stderr.strip()}")
                return False
                
        except subprocess.TimeoutExpired:
            log_warning(LogSource.SYSTEM, f"WSL2 internal check timed out for port {wsl_internal_port}{path}")
            return False
        except Exception as e:
            log_error(LogSource.SYSTEM, f"Error during WSL2 internal check for port {wsl_internal_port}{path}: {e}")
            return False
    
    async def wait_for_service_ready(self, service_name: str, windows_host: str, windows_port: int, 
                                   wsl_internal_port: int, health_path: str = "/", 
                                   max_wait_sec: int = 60) -> bool:
        """
        Wait for a service to become ready with exponential backoff.
        
        Args:
            service_name: Name of the service for logging
            windows_host: Host on Windows side
            windows_port: Port exposed to Windows
            wsl_internal_port: Port inside WSL2 container
            health_path: Health check endpoint path
            max_wait_sec: Maximum time to wait in seconds
            
        Returns:
            True if service becomes ready, False if timeout
        """
        log_info(LogSource.SYSTEM, f"Waiting for {service_name} to become ready (max {max_wait_sec}s)...")
        start_time = time.monotonic()
        wait_interval = 1.0  # Start with 1 second
        
        while time.monotonic() - start_time < max_wait_sec:
            # Use WSL2-aware check for services started in WSL2
            if self._verify_service_wsl2_aware(windows_host, windows_port, wsl_internal_port, health_path, timeout=2):
                log_info(LogSource.SYSTEM, 
                        f"{service_name} is ready on {windows_host}:{windows_port} (internally {wsl_internal_port})")
                return True
            
            log_debug(LogSource.SYSTEM, f"{service_name} not ready yet, waiting {wait_interval:.1f}s...")
            await asyncio.sleep(wait_interval)
            wait_interval = min(wait_interval * 1.5, 10.0)  # Exponential backoff, cap at 10s
        
        log_error(LogSource.SYSTEM, f"{service_name} did not become ready within {max_wait_sec} seconds")
        return False
    
    def get_service_status(self) -> Dict[str, Any]:
        """
        Get status of all Docker services.
        
        Returns:
            Dictionary with service status information
        """
        try:
            compose_cmd = self.get_compose_command()
            if not compose_cmd:
                return {"error": "Docker compose command not available", "services": {}}
            
            cmd = compose_cmd + ["-f", self.compose_file_path, "ps", "--format", "json"]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                shell=True
            )
            
            if result.returncode == 0:
                # Parse JSON output if available
                try:
                    import json
                    services = json.loads(result.stdout) if result.stdout.strip() else []
                    return {"services": services, "raw_output": result.stdout}
                except json.JSONDecodeError:
                    # Fallback to raw output
                    return {"services": {}, "raw_output": result.stdout}
            else:
                return {"error": f"Failed to get service status: {result.stderr}", "services": {}}
                
        except subprocess.TimeoutExpired:
            return {"error": "Service status check timed out", "services": {}}
        except Exception as e:
            return {"error": f"Error getting service status: {str(e)}", "services": {}} 