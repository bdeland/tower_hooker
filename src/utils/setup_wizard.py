"""
Tower Hooker Infrastructure Setup Wizard

This module provides comprehensive setup checking and interactive setup for:
- InfluxDB authentication and bucket creation
- Grafana data source configuration
- Loki log aggregation setup
- Complete infrastructure verification
"""

import requests
import json
import time
import re
import asyncio
import aiohttp
import socket
from typing import Dict, List, Tuple, Optional, Any, Callable
import subprocess
import sys
import os
import shutil
import yaml
from pathlib import Path

# Import the new sleek UI
from .terminal_ui import ui

# Import unified logging system
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_debug
from src.managers.unified_logging_definitions import LogSource

# Setup completion marker file
SETUP_COMPLETE_MARKER = ".tower_hooker_setup_complete"

# Docker status constants for WSL2 environment
DOCKER_OK_WSL2 = "DOCKER_OK_WSL2"
DOCKER_CLI_NOT_FOUND_WINDOWS = "DOCKER_CLI_NOT_FOUND_WINDOWS"
DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2 = "DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2"
DOCKER_COMPOSE_V2_NOT_FOUND = "DOCKER_COMPOSE_V2_NOT_FOUND"

# WSL2 automation constants
WSL2_UBUNTU_DISTRO = "Ubuntu"
WSL2_DEFAULT_DISTROS = ["Ubuntu", "Ubuntu-20.04", "Ubuntu-22.04", "Ubuntu-24.04"]
DOCKER_SERVICE_START_TIMEOUT = 30

def load_service_timeouts() -> Dict[str, int]:
    """Load service timeout configurations from config file"""
    try:
        config_path = Path("config/main_config.yaml")
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                services_config = config.get('services', {})
                timeouts_config = services_config.get('timeouts', {})
                
                # Return timeout values with defaults
                return {
                    'grafana': timeouts_config.get('grafana', 60),
                    'influxdb': timeouts_config.get('influxdb', 90),
                    'loki': timeouts_config.get('loki', 180),
                    'default': timeouts_config.get('default', 60)
                }
    except Exception as e:
        log_warning(LogSource.SYSTEM, f"Failed to load service timeouts from config: {e}")
    
    # Return default values if config loading fails
    return {
        'grafana': 60,
        'influxdb': 90,
        'loki': 180,
        'default': 60
    }

class SetupError(Exception):
    """Custom exception for setup failures"""
    pass

def is_first_time_setup() -> bool:
    """Check if this is the first time the user is running the application"""
    return not Path(SETUP_COMPLETE_MARKER).exists()

def mark_setup_complete():
    """Mark setup as completed by creating a marker file"""
    try:
        with open(SETUP_COMPLETE_MARKER, 'w') as f:
            f.write(f"Setup completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("This file indicates that Tower Hooker infrastructure setup has been completed.\n")
            f.write("Delete this file to force setup wizard to run again.\n")
        log_info(LogSource.SYSTEM, "Setup completion marked")
    except Exception as e:
        log_warning(LogSource.SYSTEM, "Failed to create setup completion marker", error=str(e))

def has_infrastructure_files() -> bool:
    """Check if key infrastructure files exist (docker-compose.yml, .env, etc.)"""
    required_files = [
        "docker-compose.yml",
        ".env",
        "config/main_config.yaml"
    ]
    
    return all(Path(file).exists() for file in required_files)

def quick_infrastructure_check() -> Dict[str, bool]:
    """Quick check of infrastructure without full setup verification - Updated for WSL2"""
    try:
        # Quick Docker check - check CLI accessibility and WSL2 daemon
        docker_running = False
        docker_accessible = False
        
        try:
            # Check if docker CLI is available in Windows PATH
            if not shutil.which("docker"):
                docker_accessible = False
                docker_running = False
            else:
                docker_version_result = subprocess.run(
                    ['docker', 'version'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=True  # shell=True for Windows CLI
                )
                docker_accessible = docker_version_result.returncode == 0
                
                if docker_accessible:
                    # Docker is accessible, now check if compose services are running
                    compose_cmd_parts = get_compose_command_windows_wsl2()
                    if compose_cmd_parts:
                        docker_result = subprocess.run(
                            compose_cmd_parts + ['ps', '-q'],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            shell=True  # shell=True for Windows
                        )
                        docker_running = docker_result.returncode == 0 and docker_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            docker_accessible = False
            docker_running = False
        
        # Quick InfluxDB ping
        influxdb_running = False
        try:
            response = requests.get("http://localhost:8086/health", timeout=3)
            influxdb_running = response.status_code == 200
        except:
            pass
        
        return {
            'docker': docker_running,
            'docker_accessible': docker_accessible,
            'influxdb': influxdb_running,
            'files': has_infrastructure_files()
        }
    except Exception:
        return {
            'docker': False,
            'docker_accessible': False,
            'influxdb': False,
            'files': has_infrastructure_files()
        }

def get_compose_command_windows_wsl2():
    """Get the appropriate docker compose command for Windows/WSL2 environment"""
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
                    return ["docker", "compose"]
        except (subprocess.TimeoutExpired, Exception):
            pass
    
    # Windows Docker CLI failed, try WSL2 fallback
    try:
        # Check if WSL2 Ubuntu is available and has Docker
        wsl_test = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "docker", "version"],
            capture_output=True,
            text=True,
            shell=True,
            timeout=10
        )
        
        if wsl_test.returncode == 0:
            # WSL2 Docker works, test compose
            wsl_compose_test = subprocess.run(
                ["wsl", "-d", "Ubuntu", "--", "docker", "compose", "version"],
                capture_output=True,
                text=True,
                shell=True,
                timeout=10
            )
            if wsl_compose_test.returncode == 0:
                return ["wsl", "-d", "Ubuntu", "--", "docker", "compose"]
    except (subprocess.TimeoutExpired, Exception):
        pass
    
    # Fallback for standalone docker-compose.exe if needed
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    
    return None

class InfrastructureSetupWizard:
    """Interactive setup wizard for Tower Hooker infrastructure"""
    
    def __init__(self):
        # Load timeout configurations from config file
        self.timeouts = load_service_timeouts()
        
        # Use default ports for services - no longer configurable
        self.influxdb_url = 'http://localhost:8086'
        self.grafana_url = "http://localhost:3000"
        self.loki_url = 'http://localhost:3100'
        
        # Load credentials from environment or config
        self.influxdb_token = os.getenv('INFLUXDB_TOKEN', 'tower_hooker_token_123')
        self.influxdb_org = os.getenv('INFLUXDB_ORG', 'tower_hooker')
        self.influxdb_username = os.getenv('INFLUXDB_USERNAME', 'admin')
        self.influxdb_password = os.getenv('INFLUXDB_PASSWORD', 'password123')
        self.grafana_password = os.getenv('GRAFANA_ADMIN_PASSWORD', 'admin123')
        
        self.required_buckets = ['tower_data', 'logs']
        
        # Container startup patterns for log monitoring
        self.influxdb_startup_patterns = [
            (r"Listening on \[::1\]:8086", "Server listening on port 8086"),
            (r"Created default user", "Default user created"),
            (r"Created bucket", "Required buckets created"),
            (r"Ready for queries", "Ready for queries")
        ]
        
        self.grafana_startup_patterns = [
            (r"HTTP Server Listen", "HTTP server started"),
            (r"Database migration", "Database initialization complete"),
            (r"plugins loaded", "Plugins loaded successfully"),
            (r"HTTP Server running", "Ready for connections")
        ]
        
        self.loki_startup_patterns = [
            (r"Starting Loki", "Loki service starting"),
            (r"server listening on addresses", "Server listening"),
            (r"table manager started", "Table manager initialized"),
            (r"ingester started", "Ingester ready for logs")
        ]
        
    def check_setup_status(self) -> Dict[str, Any]:
        """Check the current setup status of all infrastructure components"""
        log_info(LogSource.SYSTEM, "Checking infrastructure setup status...")
        
        status = {
            'docker_services': self._check_docker_services(),
            'influxdb': self._check_influxdb_setup(),
            'grafana': self._check_grafana_setup(),
            'loki': self._check_loki_setup(),
            'overall_ready': False
        }
        
        # Determine overall readiness
        status['overall_ready'] = (
            status['docker_services']['ready'] and
            status['influxdb']['ready'] and
            status['grafana']['ready'] and
            status['loki']['ready']
        )
        
        return status
    
    async def _monitor_docker_events_realtime(self, progress_callback: Callable[[str, str], None], timeout: int = 60) -> bool:
        """Monitor Docker events in real-time for container status changes"""
        log_debug(LogSource.SYSTEM, "Starting real-time Docker event monitoring")
        
        try:
            # Get our container names
            container_names = ['tower_hooker_influxdb', 'tower_hooker_grafana', 'tower_hooker_loki', 'tower_hooker_promtail']
            container_status = {name: False for name in container_names}
            
            cmd = ["docker", "events", "--filter", "type=container", "--format", "{{json .}}"]
            process = await asyncio.create_subprocess_exec(
                *cmd, 
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            start_time = time.time()
            
            async def process_events():
                async for line in process.stdout:
                    try:
                        if time.time() - start_time > timeout:
                            log_debug(LogSource.SYSTEM, "Docker event monitoring timeout reached")
                            break
                            
                        event = json.loads(line.decode().strip())
                        container_name = event.get('Actor', {}).get('Attributes', {}).get('name', '')
                        action = event.get('Action', '')
                        
                        if container_name in container_names:
                            log_debug(LogSource.SYSTEM, f"Docker event: {container_name} -> {action}")
                            
                            if action == 'start':
                                progress_callback("Docker Events", f"✅ {container_name} started")
                                container_status[container_name] = True
                            elif action == 'health_status: healthy':
                                progress_callback("Docker Events", f"🟢 {container_name} is healthy")
                            elif action == 'die' or action == 'kill':
                                progress_callback("Docker Events", f"❌ {container_name} stopped")
                                container_status[container_name] = False
                        
                        # Check if all containers are started
                        if all(container_status.values()):
                            log_debug(LogSource.SYSTEM, "All containers started successfully")
                            return True
                            
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        log_debug(LogSource.SYSTEM, f"Error processing Docker event: {e}")
                        
                return False
            
            try:
                result = await asyncio.wait_for(process_events(), timeout=timeout)
                return result
            except asyncio.TimeoutError:
                log_debug(LogSource.SYSTEM, "Docker event monitoring timed out")
                return False
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                    
        except Exception as e:
            log_error(LogSource.SYSTEM, f"Docker event monitoring failed: {e}")
            return False
    
    async def _monitor_service_logs(self, container_name: str, patterns: List[Tuple[str, str]], 
                                   progress_callback: Callable[[str, str], None], timeout: int = 60) -> bool:
        """Monitor container logs for specific startup completion patterns"""
        log_debug(LogSource.SYSTEM, f"Starting log monitoring for {container_name}")
        
        try:
            completed_patterns = set()
            cmd = ["docker", "logs", "-f", container_name]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            start_time = time.time()
            
            async def process_logs():
                async for line in process.stdout:
                    try:
                        if time.time() - start_time > timeout:
                            log_debug(LogSource.SYSTEM, f"Log monitoring timeout for {container_name}")
                            break
                            
                        log_line = line.decode().strip()
                        log_debug(LogSource.SYSTEM, f"{container_name} log: {log_line}")
                        
                        for i, (pattern, message) in enumerate(patterns):
                            if re.search(pattern, log_line, re.IGNORECASE) and i not in completed_patterns:
                                completed_patterns.add(i)
                                progress_callback("Service Logs", f"🔍 {container_name}: {message}")
                                log_debug(LogSource.SYSTEM, f"Pattern matched for {container_name}: {message}")
                                
                                # If this is the last pattern, service is ready
                                if i == len(patterns) - 1:
                                    return True
                                    
                    except Exception as e:
                        log_debug(LogSource.SYSTEM, f"Error processing log line: {e}")
                        
                return len(completed_patterns) > 0  # Return True if we saw at least some patterns
                
            try:
                result = await asyncio.wait_for(process_logs(), timeout=timeout)
                return result
            except asyncio.TimeoutError:
                log_debug(LogSource.SYSTEM, f"Log monitoring timed out for {container_name}")
                return len(completed_patterns) > 0
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                    
        except Exception as e:
            log_error(LogSource.SYSTEM, f"Log monitoring failed for {container_name}: {e}")
            return False
    
    async def _test_service_capabilities(self, service_name: str, test_functions: List[Tuple[str, Callable]], 
                                       progress_callback: Callable[[str, str], None]) -> bool:
        """Test specific service capabilities progressively"""
        log_debug(LogSource.SYSTEM, f"Testing {service_name} capabilities")
        
        for capability_name, test_func in test_functions:
            progress_callback(f"{service_name} Verification", f"Testing {capability_name.lower()}...")
            log_debug(LogSource.SYSTEM, f"Testing {service_name} {capability_name}")
            
            # Retry with exponential backoff
            for attempt in range(5):
                try:
                    if asyncio.iscoroutinefunction(test_func):
                        result = await test_func()
                    else:
                        result = test_func()
                        
                    if result:
                        progress_callback(f"{service_name} Verification", f"✅ {capability_name} verified")
                        log_debug(LogSource.SYSTEM, f"{service_name} {capability_name} verified successfully")
                        break
                    else:
                        wait_time = 2 ** attempt
                        log_debug(LogSource.SYSTEM, f"{service_name} {capability_name} test failed, retrying in {wait_time}s")
                        await asyncio.sleep(wait_time)
                        
                except Exception as e:
                    wait_time = 2 ** attempt
                    log_debug(LogSource.SYSTEM, f"{service_name} {capability_name} test error: {e}, retrying in {wait_time}s")
                    if attempt == 4:  # Last attempt
                        progress_callback(f"{service_name} Verification", f"❌ {capability_name} failed: {str(e)}")
                        log_error(LogSource.SYSTEM, f"{service_name} {capability_name} failed: {e}")
                        return False
                    await asyncio.sleep(wait_time)
            else:
                # All retries exhausted
                progress_callback(f"{service_name} Verification", f"❌ {capability_name} failed after all retries")
                log_error(LogSource.SYSTEM, f"{service_name} {capability_name} failed after all retries")
                return False
        
        return True
    
    def _check_docker_services(self) -> Dict[str, Any]:
        """Test Grafana datasource configuration"""
        try:
            auth = ('admin', self.grafana_password)
            response = requests.get(f"{self.grafana_url}/api/datasources", auth=auth, timeout=3)
            if response.status_code == 200:
                datasources = response.json()
                ds_names = [ds.get('name', '').lower() for ds in datasources]
                return 'influxdb' in ds_names and 'loki' in ds_names
            return False
        except:
            return False
    
    async def _test_loki_connection(self) -> bool:
        """Test basic Loki connection"""
        try:
            response = requests.get(f"{self.loki_url}/ready", timeout=3)
            return response.status_code == 200
        except:
            return False
    
    async def _test_loki_metrics(self) -> bool:
        """Test Loki metrics endpoint"""
        try:
            response = requests.get(f"{self.loki_url}/metrics", timeout=3)
            return response.status_code == 200 and 'loki_build_info' in response.text
        except:
            return False
    

    
    def _check_docker_services(self) -> Dict[str, Any]:
        """Check if Docker services are running - Updated for Windows/WSL2 environment"""
        try:
            # Primary Check: Docker CLI accessibility from Windows and Daemon in WSL2
            # First check if docker.exe is available in PATH
            if not shutil.which("docker"):
                return {
                    'ready': False,
                    'error': "Docker CLI not found in Windows PATH. Please ensure Docker Engine is installed in WSL2 and docker.exe is available.",
                    'services': [],
                    'docker_accessible': False,
                    'status_code': DOCKER_CLI_NOT_FOUND_WINDOWS
                }
            
            # Check if Docker CLI can communicate with daemon 
            docker_version_result = subprocess.run(
                ['docker', 'version'],
                capture_output=True,
                text=True,
                timeout=10,
                shell=True  # shell=True for Windows CLI
            )
            
            # If Windows Docker CLI fails, try WSL2 fallback
            use_wsl2_fallback = False
            if docker_version_result.returncode != 0:
                # Try WSL2 Docker instead
                try:
                    wsl_version_result = subprocess.run(
                        ['wsl', '-d', 'Ubuntu', '--', 'docker', 'version'],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        shell=True
                    )
                    if wsl_version_result.returncode == 0:
                        use_wsl2_fallback = True
                    else:
                        # Both Windows and WSL2 Docker failed
                        error_msg = "Cannot connect to Docker daemon in WSL2"
                        if "pipe" in docker_version_result.stderr.lower() or "cannot connect" in docker_version_result.stderr.lower():
                            error_msg = "Docker daemon in WSL2 not running or Windows CLI cannot connect"
                        elif "permission" in docker_version_result.stderr.lower():
                            error_msg = "Docker permission denied - check Docker setup in WSL2"
                        
                        return {
                            'ready': False,
                            'error': error_msg,
                            'services': [],
                            'docker_accessible': False,
                            'status_code': DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2
                        }
                except Exception:
                    # WSL2 fallback failed
                    error_msg = "Cannot connect to Docker daemon in WSL2"
                    return {
                        'ready': False,
                        'error': error_msg,
                        'services': [],
                        'docker_accessible': False,
                        'status_code': DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2
                    }
            
            # Docker info check for more detailed daemon status
            if use_wsl2_fallback:
                docker_info_result = subprocess.run(
                    ['wsl', '-d', 'Ubuntu', '--', 'docker', 'info'],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=True
                )
            else:
                docker_info_result = subprocess.run(
                    ['docker', 'info'],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=True
                )
            
            if docker_info_result.returncode != 0:
                return {
                    'ready': False,
                    'error': f"Docker daemon not accessible: {docker_info_result.stderr}",
                    'services': [],
                    'docker_accessible': False,
                    'status_code': DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2
                }
            
            # Docker Compose V2 Detection
            compose_cmd_parts = get_compose_command_windows_wsl2()
            if not compose_cmd_parts:
                return {
                    'ready': False,
                    'error': "Docker Compose V2 not found. Modern Docker Engine installations usually include this.",
                    'services': [],
                    'docker_accessible': True,
                    'status_code': DOCKER_COMPOSE_V2_NOT_FOUND
                }
            
            # Check compose services
            result = subprocess.run(
                compose_cmd_parts + ['ps', '--format', 'json'],
                capture_output=True,
                text=True,
                timeout=10,
                shell=True  # shell=True for Windows
            )
            
            if result.returncode != 0:
                return {
                    'ready': False,
                    'error': f"Docker Compose check failed: {result.stderr}",
                    'services': [],
                    'docker_accessible': True,
                    'compose_command': compose_cmd_parts,
                    'status_code': DOCKER_OK_WSL2
                }
            
            services = []
            running_count = 0
            
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    try:
                        service_info = json.loads(line)
                        service_name = service_info.get('Service', 'Unknown')
                        status = service_info.get('State', 'Unknown')
                        
                        services.append({
                            'name': service_name,
                            'status': status,
                            'running': status.lower() == 'running'
                        })
                        
                        if status.lower() == 'running':
                            running_count += 1
                    except json.JSONDecodeError:
                        continue
            
            expected_services = ['influxdb', 'loki', 'promtail', 'grafana']
            ready = running_count >= len(expected_services)
            
            return {
                'ready': ready,
                'services': services,
                'running_count': running_count,
                'expected_count': len(expected_services),
                'docker_accessible': True,
                'compose_command': compose_cmd_parts,
                'status_code': DOCKER_OK_WSL2,
                'using_wsl2_fallback': use_wsl2_fallback
            }
            
        except subprocess.TimeoutExpired:
            return {
                'ready': False,
                'error': "Docker command timed out - Docker daemon in WSL2 may be starting up",
                'services': [],
                'docker_accessible': False,
                'status_code': DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2
            }
        except FileNotFoundError:
            return {
                'ready': False,
                'error': "Docker CLI not found in Windows PATH",
                'services': [],
                'docker_accessible': False,
                'status_code': DOCKER_CLI_NOT_FOUND_WINDOWS
            }
        except Exception as e:
            return {
                'ready': False,
                'error': str(e),
                'services': [],
                'docker_accessible': False,
                'status_code': "UNKNOWN_ERROR"
            }
    
    def _check_influxdb_setup(self) -> Dict[str, Any]:
        """Check InfluxDB authentication and bucket setup"""
        try:
            # Check basic connectivity
            health_response = requests.get(f"{self.influxdb_url}/health", timeout=10)
            if health_response.status_code != 200:
                return {
                    'ready': False,
                    'error': f"InfluxDB health check failed: {health_response.status_code}",
                    'auth_valid': False,
                    'buckets_exist': False
                }
            
            # Check authentication
            headers = {'Authorization': f'Token {self.influxdb_token}'}
            
            # Test auth by listing organizations
            org_response = requests.get(f"{self.influxdb_url}/api/v2/orgs", headers=headers, timeout=10)
            auth_valid = org_response.status_code == 200
            
            if not auth_valid:
                return {
                    'ready': False,
                    'error': f"InfluxDB authentication failed: {org_response.status_code}",
                    'auth_valid': False,
                    'buckets_exist': False
                }
            
            # Check if organization exists
            orgs = org_response.json().get('orgs', [])
            org_exists = any(org.get('name') == self.influxdb_org for org in orgs)
            
            if not org_exists:
                return {
                    'ready': False,
                    'error': f"Organization '{self.influxdb_org}' not found",
                    'auth_valid': True,
                    'buckets_exist': False,
                    'org_exists': False
                }
            
            # Get organization ID
            org_id = next(org.get('id') for org in orgs if org.get('name') == self.influxdb_org)
            
            # Check buckets
            bucket_response = requests.get(
                f"{self.influxdb_url}/api/v2/buckets",
                headers=headers,
                params={'orgID': org_id},
                timeout=10
            )
            
            if bucket_response.status_code != 200:
                return {
                    'ready': False,
                    'error': f"Failed to list buckets: {bucket_response.status_code}",
                    'auth_valid': True,
                    'buckets_exist': False
                }
            
            buckets = bucket_response.json().get('buckets', [])
            existing_bucket_names = [bucket.get('name') for bucket in buckets]
            
            missing_buckets = [name for name in self.required_buckets if name not in existing_bucket_names]
            buckets_exist = len(missing_buckets) == 0
            
            return {
                'ready': auth_valid and buckets_exist,
                'auth_valid': True,
                'org_exists': True,
                'org_id': org_id,
                'buckets_exist': buckets_exist,
                'existing_buckets': existing_bucket_names,
                'missing_buckets': missing_buckets
            }
            
        except requests.exceptions.ConnectionError:
            return {
                'ready': False,
                'error': "Cannot connect to InfluxDB - service may not be running",
                'auth_valid': False,
                'buckets_exist': False
            }
        except Exception as e:
            return {
                'ready': False,
                'error': str(e),
                'auth_valid': False,
                'buckets_exist': False
            }
    
    def _check_grafana_setup(self) -> Dict[str, Any]:
        """Check Grafana accessibility and basic setup"""
        try:
            response = requests.get(f"{self.grafana_url}/api/health", timeout=10)
            
            if response.status_code == 200:
                # Try to authenticate
                auth_response = requests.get(
                    f"{self.grafana_url}/api/user",
                    auth=('admin', self.grafana_password),
                    timeout=10
                )
                
                auth_valid = auth_response.status_code == 200
                
                return {
                    'ready': True,
                    'accessible': True,
                    'auth_valid': auth_valid
                }
            else:
                return {
                    'ready': False,
                    'accessible': False,
                    'error': f"Grafana health check failed: {response.status_code}"
                }
                
        except requests.exceptions.ConnectionError:
            return {
                'ready': False,
                'accessible': False,
                'error': "Cannot connect to Grafana - service may not be running"
            }
        except Exception as e:
            return {
                'ready': False,
                'accessible': False,
                'error': str(e)
            }
    
    def _check_loki_setup(self) -> Dict[str, Any]:
        """Check Loki accessibility"""
        try:
            response = requests.get(f"{self.loki_url}/ready", timeout=15)
            
            if response.status_code == 200:
                return {
                    'ready': True,
                    'accessible': True
                }
            else:
                return {
                    'ready': False,
                    'accessible': False,
                    'error': f"Loki readiness check failed: {response.status_code}"
                }
                
        except requests.exceptions.ConnectionError:
            return {
                'ready': False,
                'accessible': False,
                'error': "Cannot connect to Loki - service may not be running"
            }
        except Exception as e:
            return {
                'ready': False,
                'accessible': False,
                'error': str(e)
            }
    
    def print_setup_status(self, status: Dict[str, Any]) -> None:
        """Print a formatted setup status report using sleek UI"""
        ui.print_setup_status(status)
    
    def run_interactive_setup(self) -> bool:
        """Run interactive setup wizard with sleek UI"""
        try:
            ui.show_setup_wizard_header()
            
            ui.show_info("This wizard will check and configure Docker, InfluxDB, Grafana, and Loki.")
            
            # Check current status
            with ui.with_progress_context("Checking infrastructure status...") as progress:
                status = self.check_setup_status()
            
            if status['overall_ready']:
                ui.show_success("All infrastructure components are already set up and ready!")
                return True
            
            # Check for Docker-specific issues
            docker_status = status['docker_services']
            if not docker_status.get('docker_accessible', True):
                if "not running" in docker_status.get('error', '').lower():
                    ui.show_docker_not_running_error()
                elif "permission" in docker_status.get('error', '').lower():
                    ui.show_docker_permission_error()
                else:
                    ui.show_error(f"Docker issue: {docker_status.get('error', 'Unknown error')}")
                
                # Ask if user wants to continue without Docker
                if ui.confirm_continue_without_docker():
                    ui.show_monitoring_only_mode()
                    return False  # Return False to indicate limited mode
                else:
                    ui.show_info("Please fix Docker issues and try again.")
                    return False
            
            ui.show_warning("Current setup issues found:")
            self.print_setup_status(status)
            
            # Ask user if they want to proceed
            if not ui.confirm_setup():
                ui.show_info("Setup cancelled by user.")
                return False
            
            success = True
            
            # Step 1: Start Docker services
            if not status['docker_services']['ready']:
                ui.show_setup_step("Docker Services", "Starting Docker containers...")
                
                with ui.with_progress_context("Starting Docker services...") as progress:
                    if self._setup_docker_services():
                        ui.show_success("Docker services started successfully")
                        time.sleep(5)  # Wait for services to initialize
                    else:
                        ui.show_error("Failed to start Docker services")
                        ui.show_error("Cannot proceed with setup - Docker is required for all services")
                        return False  # Stop here if Docker fails
            
            # Step 2: Setup InfluxDB (only if Docker is ready)
            if not status['influxdb']['ready']:
                ui.show_setup_step("InfluxDB", "Configuring authentication and buckets...")
                
                with ui.with_progress_context("Setting up InfluxDB...") as progress:
                    if self._setup_influxdb():
                        ui.show_success("InfluxDB setup completed successfully")
                    else:
                        ui.show_error("Failed to setup InfluxDB")
                        success = False
            
            # Step 3: Verify Grafana (only if previous steps succeeded)
            if success and not status['grafana']['ready']:
                ui.show_setup_step("Grafana", "Verifying dashboard service...")
                
                with ui.with_progress_context("Verifying Grafana...") as progress:
                    if self._verify_grafana():
                        ui.show_success("Grafana is accessible")
                    else:
                        ui.show_error("Grafana verification failed")
                        success = False
            
            # Step 4: Verify Loki (only if previous steps succeeded)
            if success and not status['loki']['ready']:
                ui.show_setup_step("Loki", "Verifying log aggregation service...")
                
                with ui.with_progress_context("Verifying Loki...") as progress:
                    if self._verify_loki():
                        ui.show_success("Loki is accessible")
                    else:
                        ui.show_error("Loki verification failed")
                        success = False
            
            # Final verification
            ui.show_setup_step("Final Check", "Running comprehensive verification...")
            
            with ui.with_progress_context("Running final verification...") as progress:
                final_status = self.check_setup_status()
            
            if final_status['overall_ready']:
                ui.show_completion_banner(success=True)
                mark_setup_complete()  # Mark setup as completed
                return True
            else:
                ui.show_completion_banner(success=False)
                self.print_setup_status(final_status)
                return False
                
        except KeyboardInterrupt:
            ui.show_warning("Setup wizard interrupted by user.")
            ui.show_info("You can run the setup wizard again anytime with:")
            ui.show_info("python -c \"from src.utils.setup_wizard import run_setup_wizard; run_setup_wizard()\"")
            return False
    
    def _setup_docker_services(self) -> bool:
        """Start Docker services (updated for Windows/WSL2 with Compose V2)"""
        try:
            # Get the appropriate compose command for WSL2
            compose_cmd_parts = get_compose_command_windows_wsl2()
            if not compose_cmd_parts:
                error_msg = "Docker Compose functionality not found. Please ensure Docker Engine is correctly installed in WSL2 and 'docker.exe' is in your Windows PATH."
                log_error(LogSource.SYSTEM, error_msg)
                print(f"Error: {error_msg}")
                return False
            
            result = subprocess.run(
                compose_cmd_parts + ['up', '-d'],
                capture_output=True,
                text=True,
                timeout=120,
                shell=True  # shell=True for Windows
            )
            
            if result.returncode == 0:
                log_info(LogSource.SYSTEM, "Docker services started successfully")
                return True
            else:
                log_error(LogSource.SYSTEM, "Failed to start Docker services", stderr=result.stderr)
                print(f"Error: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            log_error(LogSource.SYSTEM, "Docker startup timed out")
            print("Error: Docker startup timed out")
            return False
        except Exception as e:
            log_error(LogSource.SYSTEM, "Failed to start Docker services", error=str(e))
            print(f"Error: {e}")
            return False
    
    def check_docker_service(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Check if Docker services are accessible and running - Updated for Windows/WSL2"""
        def report_progress(message: str):
            if progress_callback:
                progress_callback("Docker Check", message)
            else:
                print(f"   {message}")
        
        try:
            report_progress("Checking Docker CLI availability and WSL2 daemon connectivity...")
            
            # Use the updated _check_docker_services method
            docker_status = self._check_docker_services()
            
            if docker_status['ready']:
                report_progress("Docker services are running")
                return True, "Docker services are running"
            else:
                # Provide specific error messages based on status code
                status_code = docker_status.get('status_code', 'UNKNOWN_ERROR')
                error_msg = docker_status.get('error', 'Docker services not running')
                
                if status_code == DOCKER_CLI_NOT_FOUND_WINDOWS:
                    detailed_msg = self._get_wsl2_docker_cli_guidance()
                    report_progress("Docker CLI not found - WSL2 setup required")
                    return False, detailed_msg
                elif status_code == DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2:
                    detailed_msg = self._get_wsl2_daemon_guidance()
                    report_progress("Cannot connect to Docker daemon in WSL2")
                    return False, detailed_msg
                elif status_code == DOCKER_COMPOSE_V2_NOT_FOUND:
                    detailed_msg = self._get_compose_v2_guidance()
                    report_progress("Docker Compose V2 not available")
                    return False, detailed_msg
                else:
                    report_progress(f"Docker services issue: {error_msg}")
                    return False, error_msg
                
        except Exception as e:
            error_msg = f"Docker check failed: {str(e)}"
            report_progress(f"Error: {e}")
            return False, error_msg
    
    def _get_wsl2_docker_cli_guidance(self) -> str:
        """Provide detailed guidance for WSL2 and Docker Engine setup"""
        return """Docker CLI not found in Windows PATH. Please set up Docker Engine in WSL2:

🚀 AUTOMATED SETUP (Recommended):
   Run our setup script: scripts/setup_docker_engine_windows.ps1
   This script will guide you through the entire process automatically.

📝 MANUAL SETUP:

1. Install/Enable WSL2:
   Windows Subsystem for Linux 2 (WSL2) is required. Please ensure it's installed and enabled.
   You can typically install it by running 'wsl --install' in an Administrator PowerShell.
   Reference: https://docs.microsoft.com/en-us/windows/wsl/install

2. Install a Linux Distribution in WSL2:
   Install a Linux distribution (e.g., Ubuntu) from the Microsoft Store or via 'wsl --install -d Ubuntu'.

3. Install Docker Engine within WSL2:
   Once your Linux distribution is set up in WSL2, open its terminal and install Docker Engine.
   For Ubuntu, you can use the convenience script:
   - curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh
   - Then, add your user to the docker group: sudo usermod -aG docker $USER
   - Log out and back into WSL2 or run 'newgrp docker' for this to take effect.
   Reference: https://docs.docker.com/engine/install/

4. Ensure Docker Service Starts:
   Make sure the Docker service is started within your WSL2 Linux distribution:
   - sudo systemctl start docker && sudo systemctl enable docker"""
    
    def _get_wsl2_daemon_guidance(self) -> str:
        """Provide guidance for WSL2 Docker daemon connectivity issues"""
        return """Cannot connect to the Docker daemon inside your WSL2 Linux distribution.

Please check the following:

1. Ensure your WSL2 distribution is running:
   Open your WSL2 terminal (e.g., Ubuntu) to ensure it's active.

2. Ensure the Docker service is started within WSL2:
   Run 'sudo systemctl status docker' to check if it's active.
   If not running, try 'sudo systemctl start docker'.

3. Check Docker daemon startup:
   If the service fails to start, try 'sudo dockerd' to see error messages.

4. Restart components if needed:
   You might need to restart your Windows terminal or even your PC after initial 
   Docker setup within WSL2 for the Windows Docker CLI to connect properly.

5. Verify Docker group membership:
   Ensure your user is in the docker group: 'groups | grep docker'
   If not, run 'sudo usermod -aG docker $USER' and restart WSL2."""
    
    def _get_compose_v2_guidance(self) -> str:
        """Provide guidance for Docker Compose V2 setup"""
        return """The 'docker compose' command is not available.

Modern Docker Engine installations usually include Docker Compose V2. Please:

1. Ensure your Docker Engine installation is up-to-date:
   Within your WSL2 distribution, update Docker Engine to the latest version.

2. Verify Compose V2 availability:
   Run 'docker compose version' within your WSL2 terminal to check if it's available.

3. Re-run Docker installation if needed:
   Re-running the Docker installation script or checking Docker's official documentation 
   for your Linux distribution might help.

4. Check Docker CLI installation:
   Ensure 'docker.exe' is properly installed and accessible from Windows PATH."""
    
    def _setup_influxdb(self) -> bool:
        """Setup InfluxDB authentication and buckets (using config timeouts)"""
        try:
            # Wait for InfluxDB to be ready
            print("   Waiting for InfluxDB to be ready...")
            max_wait = self.timeouts['influxdb']  # Use config timeout instead of hardcoded 60
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(f"{self.influxdb_url}/health", timeout=5)
                    if response.status_code == 200:
                        break
                except:
                    pass
                time.sleep(2)
            else:
                print(f"   InfluxDB did not become ready in {max_wait}s")
                return False
            
            print("   InfluxDB is ready, checking authentication...")
            
            # Check authentication
            headers = {'Authorization': f'Token {self.influxdb_token}'}
            auth_response = requests.get(f"{self.influxdb_url}/api/v2/orgs", headers=headers, timeout=10)
            
            if auth_response.status_code != 200:
                print(f"   Authentication failed with token. Status: {auth_response.status_code}")
                print("   Please check your INFLUXDB_TOKEN in the .env file")
                return False
            
            print("   Authentication successful")
            
            # Get organization
            orgs = auth_response.json().get('orgs', [])
            org = next((org for org in orgs if org.get('name') == self.influxdb_org), None)
            
            if not org:
                print(f"   Organization '{self.influxdb_org}' not found")
                return False
            
            org_id = org.get('id')
            print(f"   Found organization: {self.influxdb_org}")
            
            # Check and create buckets
            bucket_response = requests.get(
                f"{self.influxdb_url}/api/v2/buckets",
                headers=headers,
                params={'orgID': org_id},
                timeout=10
            )
            
            if bucket_response.status_code != 200:
                print(f"   Failed to list buckets: {bucket_response.status_code}")
                return False
            
            existing_buckets = [bucket.get('name') for bucket in bucket_response.json().get('buckets', [])]
            
            # Create missing buckets
            for bucket_name in self.required_buckets:
                if bucket_name not in existing_buckets:
                    print(f"   Creating bucket: {bucket_name}")
                    
                    bucket_data = {
                        'name': bucket_name,
                        'orgID': org_id,
                        'retentionRules': []
                    }
                    
                    create_response = requests.post(
                        f"{self.influxdb_url}/api/v2/buckets",
                        headers={**headers, 'Content-Type': 'application/json'},
                        json=bucket_data,
                        timeout=10
                    )
                    
                    if create_response.status_code == 201:
                        print(f"   ✅ Created bucket: {bucket_name}")
                    else:
                        print(f"   ❌ Failed to create bucket {bucket_name}: {create_response.status_code}")
                        print(f"      Response: {create_response.text}")
                        return False
                else:
                    print(f"   ✅ Bucket already exists: {bucket_name}")
            
            return True
            
        except Exception as e:
            log_error(LogSource.SYSTEM, "InfluxDB setup failed", error=str(e))
            print(f"   Error: {e}")
            return False
    
    def setup_influxdb(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Setup InfluxDB authentication and buckets (using config timeouts)"""
        def report_progress(message: str):
            if progress_callback:
                progress_callback("InfluxDB Setup", message)
            else:
                print(f"   {message}")
        
        try:
            # Wait for InfluxDB to be ready
            report_progress("Waiting for InfluxDB to be ready...")
            max_wait = self.timeouts['influxdb']  # Use config timeout instead of hardcoded 30
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(f"{self.influxdb_url}/health", timeout=3)
                    if response.status_code == 200:
                        break
                except:
                    pass
                time.sleep(1)  # Reduced from 2 seconds
            else:
                error_msg = f"InfluxDB did not become ready in {max_wait}s"
                report_progress(error_msg)
                return False, error_msg
            
            report_progress("InfluxDB is ready, checking authentication...")
            
            # Check authentication
            headers = {'Authorization': f'Token {self.influxdb_token}'}
            auth_response = requests.get(f"{self.influxdb_url}/api/v2/orgs", headers=headers, timeout=10)
            
            if auth_response.status_code != 200:
                error_msg = f"Authentication failed with token. Status: {auth_response.status_code}. Please check your INFLUXDB_TOKEN in the .env file"
                report_progress(error_msg)
                return False, error_msg
            
            report_progress("Authentication successful")
            
            # Get organization
            orgs = auth_response.json().get('orgs', [])
            org = next((org for org in orgs if org.get('name') == self.influxdb_org), None)
            
            if not org:
                error_msg = f"Organization '{self.influxdb_org}' not found"
                report_progress(error_msg)
                return False, error_msg
            
            org_id = org.get('id')
            report_progress(f"Found organization: {self.influxdb_org}")
            
            # Check and create buckets
            bucket_response = requests.get(
                f"{self.influxdb_url}/api/v2/buckets",
                headers=headers,
                params={'orgID': org_id},
                timeout=10
            )
            
            if bucket_response.status_code != 200:
                error_msg = f"Failed to list buckets: {bucket_response.status_code}"
                report_progress(error_msg)
                return False, error_msg
            
            existing_buckets = [bucket.get('name') for bucket in bucket_response.json().get('buckets', [])]
            
            # Create missing buckets
            for bucket_name in self.required_buckets:
                if bucket_name not in existing_buckets:
                    report_progress(f"Creating bucket: {bucket_name}")
                    
                    bucket_data = {
                        'name': bucket_name,
                        'orgID': org_id,
                        'retentionRules': []
                    }
                    
                    create_response = requests.post(
                        f"{self.influxdb_url}/api/v2/buckets",
                        headers={**headers, 'Content-Type': 'application/json'},
                        json=bucket_data,
                        timeout=10
                    )
                    
                    if create_response.status_code == 201:
                        report_progress(f"✅ Created bucket: {bucket_name}")
                    else:
                        error_msg = f"Failed to create bucket {bucket_name}: {create_response.status_code}. Response: {create_response.text}"
                        report_progress(f"❌ {error_msg}")
                        return False, error_msg
                else:
                    report_progress(f"✅ Bucket already exists: {bucket_name}")
            
            success_msg = "InfluxDB setup completed successfully"
            report_progress(success_msg)
            return True, success_msg
            
        except Exception as e:
            error_msg = f"InfluxDB setup failed: {str(e)}"
            log_error(LogSource.SYSTEM, "InfluxDB setup failed", error=str(e))
            report_progress(error_msg)
            return False, error_msg
    
    def _verify_grafana(self) -> bool:
        """Verify Grafana is accessible (using config timeouts)"""
        try:
            # Wait for Grafana to be ready
            print("   Waiting for Grafana to be ready...")
            max_wait = self.timeouts['grafana']  # Use config timeout instead of hardcoded 60
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(f"{self.grafana_url}/api/health", timeout=5)
                    if response.status_code == 200:
                        print("   Grafana is accessible")
                        return True
                except:
                    pass
                time.sleep(2)
            
            print(f"   Grafana did not become ready in {max_wait}s")
            return False
            
        except Exception as e:
            log_error(LogSource.SYSTEM, "Grafana verification failed", error=str(e))
            print(f"   Error: {e}")
            return False
    
    def configure_grafana_datasource(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Verify Grafana is accessible and configure data sources (using config timeouts)"""
        def report_progress(message: str):
            if progress_callback:
                progress_callback("Grafana Setup", message)
            else:
                print(f"   {message}")
        
        try:
            # Wait for Grafana to be ready
            report_progress("Waiting for Grafana to be ready...")
            max_wait = self.timeouts['grafana']  # Use config timeout instead of hardcoded 30
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(f"{self.grafana_url}/api/health", timeout=3)
                    if response.status_code == 200:
                        success_msg = "Grafana is accessible"
                        report_progress(success_msg)
                        return True, success_msg
                except:
                    pass
                time.sleep(1)  # Reduced from 2 seconds
            
            error_msg = f"Grafana did not become ready in {max_wait}s"
            report_progress(error_msg)
            return False, error_msg
            
        except Exception as e:
            error_msg = f"Grafana verification failed: {str(e)}"
            log_error(LogSource.SYSTEM, "Grafana verification failed", error=str(e))
            report_progress(error_msg)
            return False, error_msg
    
    def _verify_loki(self) -> bool:
        """Verify Loki is accessible (using config timeouts)"""
        try:
            # Wait for Loki to be ready
            print("   Waiting for Loki to be ready...")
            max_wait = self.timeouts['loki']  # Use config timeout instead of hardcoded 60
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(f"{self.loki_url}/ready", timeout=5)
                    if response.status_code == 200:
                        print("   Loki is ready")
                        return True
                except:
                    pass
                time.sleep(2)
            
            print(f"   Loki did not become ready in {max_wait}s")
            return False
            
        except Exception as e:
            log_error(LogSource.SYSTEM, "Loki verification failed", error=str(e))
            print(f"   Error: {e}")
            return False
    
    def setup_loki_promtail(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Verify Loki and Promtail are accessible (using config timeouts)"""
        def report_progress(message: str):
            if progress_callback:
                progress_callback("Loki Setup", message)
            else:
                print(f"   {message}")
        
        try:
            # Wait for Loki to be ready
            report_progress("Waiting for Loki to be ready...")
            max_wait = self.timeouts['loki']  # Use config timeout instead of hardcoded 30
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(f"{self.loki_url}/ready", timeout=3)
                    if response.status_code == 200:
                        success_msg = "Loki is ready"
                        report_progress(success_msg)
                        return True, success_msg
                except:
                    pass
                time.sleep(1)  # Reduced from 2 seconds
            
            error_msg = f"Loki did not become ready in {max_wait}s"
            report_progress(error_msg)
            return False, error_msg
            
        except Exception as e:
            error_msg = f"Loki verification failed: {str(e)}"
            log_error(LogSource.SYSTEM, "Loki verification failed", error=str(e))
            report_progress(error_msg)
            return False, error_msg
    
    def mark_setup_complete(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Mark setup as completed by creating a marker file"""
        def report_progress(message: str):
            if progress_callback:
                progress_callback("Finalization", message)
            else:
                print(f"   {message}")
        
        try:
            report_progress("Creating setup completion marker...")
            with open(SETUP_COMPLETE_MARKER, 'w') as f:
                f.write(f"Setup completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("This file indicates that Tower Hooker infrastructure setup has been completed.\n")
                f.write("Delete this file to force setup wizard to run again.\n")
            
            log_info(LogSource.SYSTEM, "Setup completion marked")
            success_msg = "Setup completion marker created successfully"
            report_progress(success_msg)
            return True, success_msg
            
        except Exception as e:
            error_msg = f"Failed to create setup completion marker: {str(e)}"
            log_warning(LogSource.SYSTEM, "Failed to create setup completion marker", error=str(e))
            report_progress(error_msg)
            return False, error_msg
    
    def is_first_time_setup(self) -> bool:
        """Check if this is the first time the user is running the application"""
        return is_first_time_setup()

    def setup_docker_services(self, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
        """Check Docker status and start services if needed - Updated for Windows/WSL2 with auto-fix"""
        def report_progress(message: str):
            if progress_callback:
                progress_callback("Docker Setup", message)
            else:
                print(f"   {message}")
        
        try:
            report_progress("Checking Docker CLI and WSL2 daemon status...")
            docker_status = self._check_docker_services()
            
            # Handle specific error cases for WSL2
            status_code = docker_status.get('status_code', 'UNKNOWN_ERROR')
            if not docker_status['docker_accessible']:
                error_msg = docker_status.get('error', 'Docker not accessible')
                
                # Attempt automatic fix for WSL2/Docker issues
                if status_code in [DOCKER_CLI_NOT_FOUND_WINDOWS, DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2]:
                    report_progress("Docker not accessible. Attempting automatic fix...")
                    
                    fix_success, fix_message = attempt_wsl2_docker_fix(progress_callback)
                    
                    if fix_success:
                        report_progress("Auto-fix successful! Rechecking Docker status...")
                        # Recheck Docker status after fix
                        docker_status = self._check_docker_services()
                        if docker_status['docker_accessible']:
                            report_progress("Docker is now accessible after auto-fix")
                        else:
                            report_progress("Auto-fix completed but Docker still not accessible")
                            # Fall back to manual instructions
                            if status_code == DOCKER_CLI_NOT_FOUND_WINDOWS:
                                detailed_msg = self._get_wsl2_docker_cli_guidance()
                                return False, f"Auto-fix attempted but failed.\n\n{detailed_msg}"
                            elif status_code == DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2:
                                detailed_msg = self._get_wsl2_daemon_guidance()
                                return False, f"Auto-fix attempted but failed.\n\n{detailed_msg}"
                    else:
                        report_progress(f"Auto-fix failed: {fix_message}")
                        # Fall back to manual instructions
                        if status_code == DOCKER_CLI_NOT_FOUND_WINDOWS:
                            detailed_msg = self._get_wsl2_docker_cli_guidance()
                            return False, f"Auto-fix failed: {fix_message}\n\nPlease follow these manual steps:\n\n{detailed_msg}"
                        elif status_code == DOCKER_DAEMON_NOT_ACCESSIBLE_WSL2:
                            detailed_msg = self._get_wsl2_daemon_guidance()
                            return False, f"Auto-fix failed: {fix_message}\n\nPlease follow these manual steps:\n\n{detailed_msg}"
                        else:
                            return False, f"Auto-fix failed: {fix_message}"
                else:
                    report_progress(f"Docker not accessible: {error_msg}")
                    return False, error_msg
            
            if docker_status['ready']:
                report_progress("Docker services are already running")
                return True, "Docker services are running"
            else:
                report_progress("Docker services not running, attempting to start them...")
                
                # Get the appropriate compose command for WSL2
                compose_cmd_parts = get_compose_command_windows_wsl2()
                if not compose_cmd_parts:
                    detailed_msg = self._get_compose_v2_guidance()
                    report_progress("Docker Compose V2 not available")
                    return False, detailed_msg
                
                # Try to start Docker services
                try:
                    result = subprocess.run(
                        compose_cmd_parts + ['up', '-d'],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        shell=True  # shell=True for Windows
                    )
                    
                    if result.returncode == 0:
                        report_progress("Docker services started successfully")
                        
                        # Wait a moment for services to initialize
                        report_progress("Waiting for services to initialize...")
                        time.sleep(5)
                        
                        # Verify services are now running
                        final_status = self._check_docker_services()
                        if final_status['ready']:
                            success_msg = "Docker services started and verified"
                            report_progress(success_msg)
                            return True, success_msg
                        else:
                            error_msg = f"Docker services started but not yet ready: {final_status.get('error', 'Unknown issue')}"
                            report_progress(error_msg)
                            return False, error_msg
                    else:
                        error_msg = f"Failed to start Docker services: {result.stderr}"
                        report_progress(error_msg)
                        return False, error_msg
                        
                except subprocess.TimeoutExpired:
                    error_msg = "Docker startup timed out"
                    report_progress(error_msg)
                    return False, error_msg
                except Exception as e:
                    error_msg = f"Error starting Docker services: {str(e)}"
                    report_progress(error_msg)
                    return False, error_msg
                
        except subprocess.TimeoutExpired:
            error_msg = "Docker command timed out"
            report_progress(f"Error: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Docker setup failed: {str(e)}"
            report_progress(f"Error: {e}")
            return False, error_msg

def smart_setup_check() -> Dict[str, Any]:
    """
    Smart setup detection that combines multiple approaches:
    1. Check for setup completion marker (fastest)
    2. Quick infrastructure check if marker exists
    3. Full setup check only if needed
    """
    result = {
        'first_time': is_first_time_setup(),
        'needs_full_setup': False,
        'needs_restart': False,
        'ready': False,
        'docker_issue': False,
        'message': ""
    }
    
    if result['first_time']:
        # First time - definitely need full setup
        result['needs_full_setup'] = True
        result['message'] = "First-time setup required"
        return result
    
    # Not first time - do quick check
    quick_check = quick_infrastructure_check()
    
    # Check for Docker accessibility issues
    if not quick_check.get('docker_accessible', False):
        result['docker_issue'] = True
        result['needs_restart'] = True
        result['message'] = "Docker CLI not accessible or WSL2 daemon not running. Please ensure Docker Engine is properly installed in WSL2."
        return result
    
    if quick_check['docker'] and quick_check['influxdb']:
        # Everything looks good
        result['ready'] = True
        result['message'] = "Infrastructure appears to be running"
        return result
    elif quick_check['files'] and quick_check['docker']:
        # Docker is running but InfluxDB might need a moment
        result['needs_restart'] = True
        result['message'] = "Infrastructure partially running - may need restart"
        return result
    elif quick_check['files']:
        # Files exist but services aren't running
        result['needs_restart'] = True
        result['message'] = "Infrastructure configured but services not running"
        return result
    else:
        # Something is wrong - need full setup
        result['needs_full_setup'] = True
        result['message'] = "Infrastructure appears to be misconfigured"
        return result

def check_setup_status() -> bool:
    """Quick check if infrastructure is set up properly"""
    wizard = InfrastructureSetupWizard()
    status = wizard.check_setup_status()
    return status['overall_ready']

def print_setup_status() -> None:
    """Print current setup status"""
    wizard = InfrastructureSetupWizard()
    status = wizard.check_setup_status()
    wizard.print_setup_status(status)

def run_setup_wizard() -> bool:
    """Run the interactive setup wizard"""
    try:
        wizard = InfrastructureSetupWizard()
        return wizard.run_interactive_setup()
    except KeyboardInterrupt:
        print("\n\n⚠️  Setup wizard interrupted by user. Exiting...")
        return False

def ensure_setup_or_exit() -> None:
    """Ensure infrastructure is set up or exit with helpful message"""
    if check_setup_status():
        log_info(LogSource.SYSTEM, "Infrastructure setup verification passed")
        return
    
    print("\n❌ Infrastructure is not properly set up!")
    print_setup_status()
    
    print("\nOptions:")
    print("1. Run automated setup wizard")
    print("2. Exit and set up manually")
    
    choice = input("\nChoose an option (1/2): ").strip()
    
    if choice == '1':
        if run_setup_wizard():
            print("\n✅ Setup completed successfully!")
            return
        else:
            print("\n❌ Setup failed. Please check the errors above and try again.")
            sys.exit(1)
    else:
        print("\nPlease set up the infrastructure manually and try again.")
        print("You can run the setup wizard anytime with:")
        print("python -c \"from src.utils.setup_wizard import run_setup_wizard; run_setup_wizard()\"")
        sys.exit(1)

def check_wsl2_status() -> Dict[str, Any]:
    """Check WSL2 status and available distributions"""
    try:
        # Check if WSL is available
        wsl_list_result = subprocess.run(
            ['wsl', '--list', '--verbose'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if wsl_list_result.returncode != 0:
            return {
                'wsl_available': False,
                'error': 'WSL not available or not installed',
                'distributions': []
            }
        
        # Parse distributions
        distributions = []
        lines = wsl_list_result.stdout.strip().split('\n')[1:]  # Skip header
        
        for line in lines:
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 3:
                    name = parts[0].replace('*', '').strip()
                    state = parts[1].strip()
                    version = parts[2].strip()
                    distributions.append({
                        'name': name,
                        'state': state,
                        'version': version,
                        'running': state.lower() == 'running'
                    })
        
        return {
            'wsl_available': True,
            'distributions': distributions,
            'has_ubuntu': any(d['name'] in WSL2_DEFAULT_DISTROS for d in distributions),
            'running_distros': [d for d in distributions if d['running']]
        }
        
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        return {
            'wsl_available': False,
            'error': f'WSL check failed: {str(e)}',
            'distributions': []
        }

def automated_wsl2_docker_install(ubuntu_distro_name: str, progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
    """Automatically install Docker Engine in WSL2 Ubuntu distribution"""
    def report_progress(message: str):
        if progress_callback:
            progress_callback("Docker Installation", message)
        else:
            print(f"   {message}")
    
    try:
        report_progress(f"Starting automated Docker Engine installation in {ubuntu_distro_name}...")
        
        # Step 1: Update package lists
        report_progress("Updating package lists...")
        update_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'sudo', 'apt', 'update'],
            capture_output=True,
            text=True,
            timeout=60,
            shell=True
        )
        
        if update_result.returncode != 0:
            return False, f"Failed to update package lists: {update_result.stderr}"
        
        report_progress("Package lists updated successfully")
        
        # Step 2: Download Docker installation script
        report_progress("Downloading Docker installation script...")
        download_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'curl', '-fsSL', 'https://get.docker.com', '-o', 'get-docker.sh'],
            capture_output=True,
            text=True,
            timeout=30,
            shell=True
        )
        
        if download_result.returncode != 0:
            return False, f"Failed to download Docker installation script: {download_result.stderr}"
        
        report_progress("Docker installation script downloaded")
        
        # Step 3: Install Docker Engine
        report_progress("Installing Docker Engine (this may take a few minutes)...")
        install_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'sudo', 'sh', 'get-docker.sh'],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes timeout for installation
            shell=True
        )
        
        if install_result.returncode != 0:
            return False, f"Failed to install Docker Engine: {install_result.stderr}"
        
        report_progress("Docker Engine installed successfully")
        
        # Step 4: Add user to docker group
        report_progress("Adding user to docker group...")
        
        # Get current user
        user_check = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'whoami'],
            capture_output=True,
            text=True,
            timeout=5,
            shell=True
        )
        
        if user_check.returncode != 0:
            return False, f"Failed to get current user: {user_check.stderr}"
        
        username = user_check.stdout.strip()
        
        # Add user to docker group
        usermod_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'sudo', 'usermod', '-aG', 'docker', username],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if usermod_result.returncode != 0:
            return False, f"Failed to add user to docker group: {usermod_result.stderr}"
        
        report_progress(f"User {username} added to docker group")
        
        # Step 5: Start and enable Docker service
        report_progress("Starting Docker service...")
        start_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'sudo', 'systemctl', 'start', 'docker'],
            capture_output=True,
            text=True,
            timeout=DOCKER_SERVICE_START_TIMEOUT,
            shell=True
        )
        
        if start_result.returncode != 0:
            return False, f"Failed to start Docker service: {start_result.stderr}"
        
        report_progress("Docker service started")
        
        # Enable Docker service for auto-start
        enable_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'sudo', 'systemctl', 'enable', 'docker'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if enable_result.returncode != 0:
            report_progress("Warning: Could not enable Docker service for auto-start")
        else:
            report_progress("Docker service enabled for auto-start")
        
        # Step 6: Clean up installation script
        cleanup_result = subprocess.run(
            ['wsl', '-d', ubuntu_distro_name, '--', 'rm', '-f', 'get-docker.sh'],
            capture_output=True,
            text=True,
            timeout=5,
            shell=True
        )
        
        # Step 7: Wait for Docker to fully initialize
        report_progress("Waiting for Docker to fully initialize...")
        import time
        time.sleep(5)
        
        # Step 8: Test Docker installation
        report_progress("Testing Docker installation...")
        test_result = subprocess.run(
            ['docker', 'version'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if test_result.returncode != 0:
            return False, f"Docker installation completed but connectivity test failed: {test_result.stderr}"
        
        report_progress("Docker Engine installation completed successfully!")
        return True, "Docker Engine has been successfully installed and configured in WSL2"
        
    except subprocess.TimeoutExpired:
        return False, "Docker installation timed out. Please try manual installation."
    except Exception as e:
        return False, f"Docker installation failed: {str(e)}"

def automated_wsl2_complete_setup(progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
    """Completely automate WSL2, Ubuntu, and Docker Engine setup on Windows"""
    def report_progress(message: str):
        if progress_callback:
            progress_callback("WSL2 Complete Setup", message)
        else:
            print(f"   {message}")
    
    try:
        report_progress("Starting complete WSL2 and Docker Engine setup...")
        
        # Step 1: Check if WSL2 is available/installed
        report_progress("Checking WSL2 status...")
        wsl_status = check_wsl2_status()
        
        if not wsl_status['wsl_available']:
            report_progress("WSL2 not available. Attempting to install WSL2...")
            
            # Try to install WSL2
            try:
                install_wsl_result = subprocess.run(
                    ['wsl', '--install', '--no-launch'],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    shell=True
                )
                
                if install_wsl_result.returncode != 0:
                    return False, f"Failed to install WSL2. Please run 'wsl --install' in an Administrator PowerShell and restart your computer. Error: {install_wsl_result.stderr}"
                
                report_progress("WSL2 installation initiated. A restart may be required.")
                # Recheck WSL status after installation attempt
                wsl_status = check_wsl2_status()
                if not wsl_status['wsl_available']:
                    return False, "WSL2 installation completed but WSL2 is still not available. Please restart your computer and try again."
                
            except subprocess.TimeoutExpired:
                return False, "WSL2 installation timed out. Please install WSL2 manually using 'wsl --install' in an Administrator PowerShell."
            except Exception as e:
                return False, f"Failed to install WSL2: {str(e)}. Please install WSL2 manually using 'wsl --install' in an Administrator PowerShell."
        
        report_progress("WSL2 is available")
        
        # Step 2: Check for Ubuntu distribution and install if needed
        if not wsl_status['has_ubuntu']:
            report_progress("Ubuntu distribution not found. Installing Ubuntu...")
            
            try:
                install_ubuntu_result = subprocess.run(
                    ['wsl', '--install', '-d', 'Ubuntu', '--no-launch'],
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minutes for Ubuntu installation
                    shell=True
                )
                
                if install_ubuntu_result.returncode != 0:
                    return False, f"Failed to install Ubuntu distribution. Error: {install_ubuntu_result.stderr}"
                
                report_progress("Ubuntu installation initiated successfully")
                
                # Wait a moment for installation to settle
                import time
                time.sleep(5)
                
                # Recheck WSL status to get updated distribution list
                wsl_status = check_wsl2_status()
                if not wsl_status['has_ubuntu']:
                    return False, "Ubuntu installation completed but Ubuntu distribution is not detected. Please check WSL2 status manually."
                
            except subprocess.TimeoutExpired:
                return False, "Ubuntu installation timed out. Please install Ubuntu manually using 'wsl --install -d Ubuntu'."
            except Exception as e:
                return False, f"Failed to install Ubuntu: {str(e)}. Please install Ubuntu manually using 'wsl --install -d Ubuntu'."
        
        # Step 3: Find the Ubuntu distribution to use
        ubuntu_distro = None
        for distro in wsl_status['distributions']:
            if distro['name'] in WSL2_DEFAULT_DISTROS:
                ubuntu_distro = distro
                break
        
        if not ubuntu_distro:
            return False, "No suitable Ubuntu distribution found after installation."
        
        report_progress(f"Found Ubuntu distribution: {ubuntu_distro['name']}")
        
        # Step 4: Ensure Ubuntu distribution is running
        if not ubuntu_distro['running']:
            report_progress("Starting Ubuntu distribution...")
            try:
                startup_result = subprocess.run(
                    ['wsl', '-d', ubuntu_distro['name'], '--', 'echo', 'WSL2 startup test'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    shell=True
                )
                
                if startup_result.returncode != 0:
                    return False, f"Failed to start Ubuntu distribution: {startup_result.stderr}"
                
                report_progress("Ubuntu distribution started successfully")
                
            except subprocess.TimeoutExpired:
                return False, f"Ubuntu distribution {ubuntu_distro['name']} failed to start in time."
        
        # Step 5: Check if Docker is already installed
        report_progress("Checking if Docker is already installed in Ubuntu...")
        docker_check = subprocess.run(
            ['wsl', '-d', ubuntu_distro['name'], '--', 'which', 'docker'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if docker_check.returncode != 0:
            # Docker not installed - proceed with automated installation
            report_progress("Docker not found. Starting automated Docker installation...")
            
            install_success, install_message = automated_wsl2_docker_install(ubuntu_distro['name'], progress_callback)
            
            if not install_success:
                return False, f"Automated Docker installation failed: {install_message}"
            
            report_progress("Docker installation completed successfully!")
        else:
            report_progress("Docker is already installed. Checking if it's running...")
            
            # Docker is installed, make sure it's running
            service_check = subprocess.run(
                ['wsl', '-d', ubuntu_distro['name'], '--', 'sudo', 'systemctl', 'is-active', 'docker'],
                capture_output=True,
                text=True,
                timeout=10,
                shell=True
            )
            
            if service_check.returncode != 0 or 'active' not in service_check.stdout:
                report_progress("Docker service is not running. Starting it...")
                
                start_result = subprocess.run(
                    ['wsl', '-d', ubuntu_distro['name'], '--', 'sudo', 'systemctl', 'start', 'docker'],
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_SERVICE_START_TIMEOUT,
                    shell=True
                )
                
                if start_result.returncode != 0:
                    return False, f"Failed to start Docker service: {start_result.stderr}"
                
                report_progress("Docker service started successfully")
            else:
                report_progress("Docker service is already running")
        
        # Step 6: Final verification
        report_progress("Running final Docker connectivity test...")
        final_test = subprocess.run(
            ['docker', 'version'],
            capture_output=True,
            text=True,
            timeout=15,
            shell=True
        )
        
        if final_test.returncode == 0:
            report_progress("🎉 Complete WSL2 and Docker Engine setup successful!")
            return True, "WSL2, Ubuntu, and Docker Engine have been successfully set up and are working correctly"
        else:
            return False, f"Setup completed but Docker connectivity test failed: {final_test.stderr}"
        
    except subprocess.TimeoutExpired:
        return False, "WSL2 setup timed out. Please try manual setup."
    except Exception as e:
        return False, f"WSL2 complete setup failed: {str(e)}"

def attempt_wsl2_docker_fix(progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[bool, str]:
    """Attempt to automatically fix common WSL2/Docker issues"""
    def report_progress(message: str):
        if progress_callback:
            progress_callback("Docker Auto-Fix", message)
        else:
            print(f"   {message}")
    
    try:
        report_progress("Checking WSL2 status...")
        wsl_status = check_wsl2_status()
        
        # If WSL2 is not available or Ubuntu is not installed, do complete setup
        if not wsl_status['wsl_available'] or not wsl_status['has_ubuntu']:
            report_progress("WSL2 or Ubuntu not properly set up. Running complete automated setup...")
            return automated_wsl2_complete_setup(progress_callback)
        
        # Find a suitable Ubuntu distribution
        ubuntu_distro = None
        for distro in wsl_status['distributions']:
            if distro['name'] in WSL2_DEFAULT_DISTROS:
                ubuntu_distro = distro
                break
        
        if not ubuntu_distro:
            report_progress("No suitable Ubuntu distribution found. Running complete automated setup...")
            return automated_wsl2_complete_setup(progress_callback)
        
        report_progress(f"Found Ubuntu distribution: {ubuntu_distro['name']}")
        
        # Try to start the WSL2 distribution if it's not running
        if not ubuntu_distro['running']:
            report_progress("Starting WSL2 Ubuntu distribution...")
            try:
                subprocess.run(
                    ['wsl', '-d', ubuntu_distro['name'], '--', 'echo', 'WSL2 startup test'],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    shell=True
                )
                report_progress("WSL2 distribution started")
            except subprocess.TimeoutExpired:
                return False, f"WSL2 distribution {ubuntu_distro['name']} failed to start in time."
        
        # Check if Docker is installed in WSL2
        report_progress("Checking if Docker is installed in WSL2...")
        docker_check = subprocess.run(
            ['wsl', '-d', ubuntu_distro['name'], '--', 'which', 'docker'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if docker_check.returncode != 0:
            # Docker is not installed - attempt automated installation
            report_progress(f"Docker not found in {ubuntu_distro['name']}. Starting automated installation...")
            
            install_success, install_message = automated_wsl2_docker_install(ubuntu_distro['name'], progress_callback)
            
            if not install_success:
                return False, f"Automated Docker installation failed: {install_message}"
            
            report_progress("Docker installation completed successfully!")
        else:
            report_progress("Docker is already installed in WSL2")
        
        # Check if Docker service is running
        report_progress("Checking Docker service status in WSL2...")
        service_check = subprocess.run(
            ['wsl', '-d', ubuntu_distro['name'], '--', 'sudo', 'systemctl', 'is-active', 'docker'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if service_check.returncode != 0 or 'active' not in service_check.stdout:
            report_progress("Docker service is not running. Attempting to start it...")
            
            # Try to start Docker service
            start_result = subprocess.run(
                ['wsl', '-d', ubuntu_distro['name'], '--', 'sudo', 'systemctl', 'start', 'docker'],
                capture_output=True,
                text=True,
                timeout=DOCKER_SERVICE_START_TIMEOUT,
                shell=True
            )
            
            if start_result.returncode != 0:
                return False, f"Failed to start Docker service: {start_result.stderr}"
            
            report_progress("Docker service started successfully")
            
            # Wait a moment for service to fully initialize
            import time
            time.sleep(3)
        
        # Check if user is in docker group
        report_progress("Checking Docker group membership...")
        group_check = subprocess.run(
            ['wsl', '-d', ubuntu_distro['name'], '--', 'groups'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if group_check.returncode == 0 and 'docker' not in group_check.stdout:
            report_progress("Adding user to docker group...")
            
            # Get current user
            user_check = subprocess.run(
                ['wsl', '-d', ubuntu_distro['name'], '--', 'whoami'],
                capture_output=True,
                text=True,
                timeout=5,
                shell=True
            )
            
            if user_check.returncode == 0:
                username = user_check.stdout.strip()
                
                # Add user to docker group
                usermod_result = subprocess.run(
                    ['wsl', '-d', ubuntu_distro['name'], '--', 'sudo', 'usermod', '-aG', 'docker', username],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=True
                )
                
                if usermod_result.returncode != 0:
                    report_progress("Warning: Could not add user to docker group automatically")
                else:
                    report_progress("User added to docker group successfully")
        
        # Final verification - test Docker CLI connectivity
        report_progress("Testing Docker CLI connectivity...")
        final_test = subprocess.run(
            ['docker', 'version'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if final_test.returncode == 0:
            report_progress("Docker auto-fix completed successfully!")
            return True, "Docker service started and is now accessible"
        else:
            return False, f"Docker auto-fix partially successful, but CLI test failed: {final_test.stderr}"
            
    except subprocess.TimeoutExpired:
        return False, "Docker auto-fix timed out. Please try manual setup."
    except Exception as e:
        return False, f"Docker auto-fix failed: {str(e)}"

if __name__ == "__main__":
    # Allow running the setup wizard directly
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "status":
            print_setup_status()
        elif sys.argv[1] == "setup":
            run_setup_wizard()
        else:
            print("Usage: python setup_wizard.py [status|setup]")
    else:
        run_setup_wizard() 