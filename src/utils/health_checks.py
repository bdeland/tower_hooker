import requests
import time
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_debug, log_critical, LogSource
from typing import Dict, List, Tuple, Optional
import subprocess
import sys
import os

# Import WSL2-compatible compose command function
from .setup_wizard import get_compose_command_windows_wsl2

class HealthCheckError(Exception):
    """Custom exception for health check failures"""
    pass

class InfrastructureHealthChecker:
    """Health checker for all required infrastructure services"""
    
    def __init__(self):
        self.services = {
            'influxdb': {
                'url': 'http://localhost:8086/health',
                'name': 'InfluxDB',
                'required': True,
                'timeout': 10
            },
            'loki': {
                'url': 'http://localhost:3100/ready',
                'name': 'Loki',
                'required': True,
                'timeout': 15  # Loki takes longer to be ready
            },
            'grafana': {
                'url': 'http://localhost:3000/api/health',
                'name': 'Grafana',
                'required': False,  # Optional for core functionality
                'timeout': 10
            }
        }
    
    def check_service_health(self, service_key: str) -> Tuple[bool, str]:
        """Check health of a specific service"""
        service = self.services[service_key]
        
        try:
            log_debug(LogSource.SYSTEM, f"Checking {service['name']} health", url=service['url'])
            response = requests.get(
                service['url'], 
                timeout=service['timeout'],
                headers={'Accept': 'application/json'}
            )
            
            if response.status_code == 200:
                log_info(LogSource.SYSTEM, f"{service['name']} is healthy", status_code=response.status_code)
                return True, f"{service['name']} is healthy"
            else:
                error_msg = f"{service['name']} returned status {response.status_code}"
                log_warning(LogSource.SYSTEM, error_msg, status_code=response.status_code)
                return False, error_msg
                
        except requests.exceptions.ConnectionError:
            error_msg = f"{service['name']} is not accessible (connection refused)"
            log_error(LogSource.SYSTEM, error_msg, url=service['url'])
            return False, error_msg
        except requests.exceptions.Timeout:
            error_msg = f"{service['name']} health check timed out"
            log_error(LogSource.SYSTEM, error_msg, timeout=service['timeout'])
            return False, error_msg
        except Exception as e:
            error_msg = f"{service['name']} health check failed: {str(e)}"
            log_error(LogSource.SYSTEM, error_msg, error=str(e))
            return False, error_msg
    
    def check_docker_services(self) -> Tuple[bool, List[str]]:
        """Check if Docker services are running via docker compose (WSL2-compatible)"""
        try:
            log_debug(LogSource.SYSTEM, "Checking Docker Compose services status")
            
            # Get the appropriate compose command for WSL2
            compose_cmd_parts = get_compose_command_windows_wsl2()
            if not compose_cmd_parts:
                error_msg = "Docker Compose not found. Please ensure Docker Engine is correctly installed in WSL2 and 'docker.exe' is in your Windows PATH."
                log_error(LogSource.SYSTEM, error_msg)
                return False, [error_msg]
            
            result = subprocess.run(
                compose_cmd_parts + ['ps', '--format', 'json'],
                capture_output=True,
                text=True,
                timeout=10,
                shell=True  # shell=True for Windows CLI
            )
            
            if result.returncode != 0:
                error_msg = f"Docker Compose check failed: {result.stderr}"
                log_error(LogSource.SYSTEM, error_msg)
                return False, [error_msg]
            
            # Parse the output to check service status
            import json
            services_status = []
            running_services = 0
            total_expected_services = 4  # influxdb, loki, promtail, grafana
            
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    try:
                        service_info = json.loads(line)
                        service_name = service_info.get('Service', 'Unknown')
                        status = service_info.get('State', 'Unknown')
                        
                        if status.lower() == 'running':
                            services_status.append(f"✓ {service_name}: {status}")
                            running_services += 1
                            log_debug(LogSource.SYSTEM, f"Docker service {service_name} is running")
                        else:
                            services_status.append(f"✗ {service_name}: {status}")
                            log_warning(LogSource.SYSTEM, f"Docker service {service_name} is not running", status=status)
                    except json.JSONDecodeError:
                        continue
            else:
                # No services running
                services_status.append("✗ No Docker services are currently running")
                log_warning(LogSource.SYSTEM, "No Docker services detected")
            
            # Consider services healthy if we have the expected number running
            services_healthy = running_services >= total_expected_services
            
            if not services_healthy:
                log_warning(LogSource.SYSTEM, f"Only {running_services}/{total_expected_services} expected services are running")
            
            return services_healthy, services_status
            
        except subprocess.TimeoutExpired:
            error_msg = "Docker Compose status check timed out"
            log_error(LogSource.SYSTEM, error_msg)
            return False, [error_msg]
        except FileNotFoundError:
            error_msg = "Docker Compose not found. Please ensure Docker Engine is installed in WSL2 and docker.exe is available in Windows PATH"
            log_error(LogSource.SYSTEM, error_msg)
            return False, [error_msg]
        except Exception as e:
            error_msg = f"Docker Compose check failed: {str(e)}"
            log_error(LogSource.SYSTEM, error_msg, error=str(e))
            return False, [error_msg]
    
    def start_docker_services(self) -> bool:
        """Attempt to start Docker services if they're not running (WSL2-compatible)"""
        try:
            log_info(LogSource.SYSTEM, "Attempting to start Docker Compose services...")
            
            # Get the appropriate compose command for WSL2
            compose_cmd_parts = get_compose_command_windows_wsl2()
            if not compose_cmd_parts:
                log_error(LogSource.SYSTEM, "Docker Compose not found. Please ensure Docker Engine is correctly installed in WSL2.")
                return False
            
            result = subprocess.run(
                compose_cmd_parts + ['up', '-d'],
                capture_output=True,
                text=True,
                timeout=60,
                shell=True  # shell=True for Windows CLI
            )
            
            if result.returncode == 0:
                log_info(LogSource.SYSTEM, "Docker Compose services started successfully")
                return True
            else:
                log_error(LogSource.SYSTEM, "Failed to start Docker Compose services", stderr=result.stderr)
                return False
                
        except subprocess.TimeoutExpired:
            log_error(LogSource.SYSTEM, "Docker Compose startup timed out")
            return False
        except Exception as e:
            log_error(LogSource.SYSTEM, "Failed to start Docker Compose services", error=str(e))
            return False
    
    def wait_for_service_ready(self, service_key: str, max_wait: int = 60) -> bool:
        """Wait for a service to become ready with retries"""
        service = self.services[service_key]
        log_info(LogSource.SYSTEM, f"Waiting for {service['name']} to become ready...", max_wait=max_wait)
        
        start_time = time.time()
        while time.time() - start_time < max_wait:
            is_healthy, message = self.check_service_health(service_key)
            if is_healthy:
                log_info(LogSource.SYSTEM, f"{service['name']} is ready", wait_time=f"{time.time() - start_time:.1f}s")
                return True
            
            log_debug(LogSource.SYSTEM, f"Waiting for {service['name']}...", message=message)
            time.sleep(2)
        
        log_error(LogSource.SYSTEM, f"{service['name']} did not become ready within {max_wait}s")
        return False
    
    def perform_full_health_check(self, auto_start: bool = True) -> Dict[str, any]:
        """Perform comprehensive health check of all infrastructure"""
        log_info(LogSource.SYSTEM, "Starting infrastructure health check...")
        
        results = {
            'overall_healthy': True,
            'services': {},
            'docker_status': {},
            'errors': [],
            'warnings': []
        }
        
        # Check Docker services first
        docker_ok, docker_status = self.check_docker_services()
        results['docker_status'] = {
            'running': docker_ok,
            'services': docker_status
        }
        
        if not docker_ok and auto_start:
            log_info(LogSource.SYSTEM, "Docker services not running. Attempting to start them...")
            if self.start_docker_services():
                log_info(LogSource.SYSTEM, "Docker services started. Waiting for them to be ready...")
                time.sleep(5)  # Give services time to start
                docker_ok, docker_status = self.check_docker_services()
                results['docker_status'] = {
                    'running': docker_ok,
                    'services': docker_status
                }
            else:
                results['errors'].append("Failed to start Docker services")
                results['overall_healthy'] = False
        
        # Check individual service health
        for service_key, service_config in self.services.items():
            log_debug(LogSource.SYSTEM, f"Checking {service_config['name']} health...")
            
            # Wait for service to be ready if Docker was just started
            if auto_start and not docker_ok:
                is_ready = self.wait_for_service_ready(service_key, max_wait=30)
            else:
                is_ready, message = self.check_service_health(service_key)
            
            if not is_ready and auto_start:
                # Try waiting a bit more
                is_ready = self.wait_for_service_ready(service_key, max_wait=30)
            
            results['services'][service_key] = {
                'healthy': is_ready,
                'name': service_config['name'],
                'required': service_config['required'],
                'url': service_config['url']
            }
            
            if not is_ready:
                if service_config['required']:
                    results['errors'].append(f"Required service {service_config['name']} is not healthy")
                    results['overall_healthy'] = False
                else:
                    results['warnings'].append(f"Optional service {service_config['name']} is not healthy")
        
        # Log summary
        if results['overall_healthy']:
            log_info(LogSource.SYSTEM, "Infrastructure health check passed", 
                       healthy_services=len([s for s in results['services'].values() if s['healthy']]),
                       total_services=len(results['services']))
        else:
            log_error(LogSource.SYSTEM, "Infrastructure health check failed", 
                        errors=results['errors'],
                        warnings=results['warnings'])
        
        return results
    
    def print_health_report(self, results: Dict[str, any]) -> None:
        """Print a formatted health check report"""
        print("\n" + "="*60)
        print("INFRASTRUCTURE HEALTH CHECK REPORT")
        print("="*60)
        
        # Docker status
        print(f"\nDocker Services: {'✓ Running' if results['docker_status']['running'] else '✗ Not Running'}")
        for service_status in results['docker_status']['services']:
            print(f"  {service_status}")
        
        # Service health
        print(f"\nService Health:")
        for service_key, service_info in results['services'].items():
            status_icon = "✓" if service_info['healthy'] else "✗"
            required_text = "(Required)" if service_info['required'] else "(Optional)"
            print(f"  {status_icon} {service_info['name']} {required_text}")
            print(f"    URL: {service_info['url']}")
        
        # Errors and warnings
        if results['errors']:
            print(f"\nErrors:")
            for error in results['errors']:
                print(f"  ✗ {error}")
        
        if results['warnings']:
            print(f"\nWarnings:")
            for warning in results['warnings']:
                print(f"  ⚠ {warning}")
        
        # Overall status
        overall_status = "HEALTHY" if results['overall_healthy'] else "UNHEALTHY"
        status_icon = "✓" if results['overall_healthy'] else "✗"
        print(f"\nOverall Status: {status_icon} {overall_status}")
        print("="*60 + "\n")

def check_infrastructure_health(auto_start: bool = True, print_report: bool = True) -> bool:
    """Convenience function to check infrastructure health"""
    checker = InfrastructureHealthChecker()
    results = checker.perform_full_health_check(auto_start=auto_start)
    
    if print_report:
        checker.print_health_report(results)
    
    return results['overall_healthy']

def ensure_infrastructure_ready() -> None:
    """Ensure infrastructure is ready or exit with error"""
    log_info(LogSource.SYSTEM, "Verifying infrastructure readiness...")
    
    if not check_infrastructure_health(auto_start=True, print_report=True):
        log_critical(LogSource.SYSTEM, "Infrastructure health check failed. Cannot proceed.")
        print("\nPlease ensure all required services are running before starting the application.")
        print("You can manually start services with: docker compose up -d")
        sys.exit(1)
    
    log_info(LogSource.SYSTEM, "Infrastructure health check passed. Application can proceed.") 