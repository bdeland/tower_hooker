#!/usr/bin/env python3
"""
Tower Hooker Project Reset Utility

This script resets the project to default values for testing purposes by:
- Stopping and removing all Docker containers (InfluxDB, Grafana, Loki, Promtail)
- Removing Docker volumes to ensure clean state
- Cleaning up Docker networks
- Optionally removing the setup completion marker
- Cleaning up unused Docker resources (images, build cache)

Usage:
    python reset_project.py                    # Full reset (containers + volumes)
    python reset_project.py --containers-only  # Only stop/remove containers
    python reset_project.py --deep-clean      # Full reset + cleanup unused resources
    python reset_project.py --list            # List current project resources
    python reset_project.py --help            # Show this help
"""

import subprocess
import sys
import os
import argparse
import shutil
from pathlib import Path
from typing import List, Optional, Tuple
import json

# Import WSL2ServiceManager for better Docker integration
try:
    from src.utils.wsl2_service_manager import WSL2ServiceManager
    from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_debug
    from src.managers.unified_logging_definitions import LogSource
    WSL2_MANAGER_AVAILABLE = True
except ImportError:
    WSL2_MANAGER_AVAILABLE = False
    print("⚠️  WSL2ServiceManager not available, using fallback methods")

# Project configuration
PROJECT_NAME = "tower_hooker"
CONTAINER_NAMES = [
    f"{PROJECT_NAME}_influxdb",
    f"{PROJECT_NAME}_grafana", 
    f"{PROJECT_NAME}_loki",
    f"{PROJECT_NAME}_promtail"
]

VOLUME_NAMES = [
    f"{PROJECT_NAME}_influxdb_data",
    f"{PROJECT_NAME}_influxdb_config",
    f"{PROJECT_NAME}_loki_data", 
    f"{PROJECT_NAME}_grafana_data"
]

NETWORK_NAME = f"{PROJECT_NAME}_network"
SETUP_MARKER_FILE = ".tower_hooker_setup_complete"

class DockerResetManager:
    """Enhanced Docker reset manager using WSL2ServiceManager when available"""
    
    def __init__(self, compose_file_path: str = "docker-compose.yml"):
        self.compose_file_path = compose_file_path
        self.wsl2_manager = None
        
        if WSL2_MANAGER_AVAILABLE:
            try:
                self.wsl2_manager = WSL2ServiceManager(compose_file_path=compose_file_path)
                log_info(LogSource.SYSTEM, "Initialized with WSL2ServiceManager")
            except Exception as e:
                log_warning(LogSource.SYSTEM, f"Failed to initialize WSL2ServiceManager: {e}")
                self.wsl2_manager = None
    
    def detect_environment(self) -> str:
        """Detect the current environment (Windows, WSL, or Linux)"""
        import platform
        
        system = platform.system().lower()
        
        if system == "windows":
            return "windows"
        elif system == "linux":
            # Check if we're in WSL
            try:
                with open("/proc/version", "r") as f:
                    version_info = f.read().lower()
                    if "microsoft" in version_info or "wsl" in version_info:
                        return "wsl"
            except FileNotFoundError:
                pass
            return "linux"
        else:
            return "unknown"

    def get_docker_compose_command(self) -> Optional[List[str]]:
        """Get Docker Compose command, using WSL2ServiceManager if available"""
        if self.wsl2_manager:
            return self.wsl2_manager.get_compose_command()
        
        # Fallback to original detection logic
        if shutil.which("docker"):
            try:
                result = subprocess.run(
                    ["docker", "compose", "version"], 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                if result.returncode == 0:
                    return ["docker", "compose"]
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                pass
        
        if shutil.which("docker-compose"):
            try:
                result = subprocess.run(
                    ["docker-compose", "--version"], 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                if result.returncode == 0:
                    return ["docker-compose"]
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                pass
        
        return None

    def get_docker_startup_instructions(self, environment: str) -> List[str]:
        """Get Docker startup instructions based on the environment"""
        if environment == "windows":
            return [
                "💡 Start Docker Desktop application",
                "💡 Or if using Docker Engine: restart Docker service in Services.msc",
                "💡 In PowerShell as Admin: Restart-Service docker",
                "💡 Check Docker Desktop system tray icon"
            ]
        elif environment == "wsl":
            return [
                "💡 Install Docker Engine in WSL: curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh",
                "💡 Start Docker: sudo service docker start",
                "💡 Or: sudo systemctl start docker",
                "💡 Add user to docker group: sudo usermod -aG docker $USER",
                "💡 Ensure Docker starts on boot: sudo systemctl enable docker"
            ]
        elif environment == "linux":
            return [
                "💡 sudo systemctl start docker",
                "💡 Or: sudo service docker start",
                "💡 Enable auto-start: sudo systemctl enable docker"
            ]
        else:
            return [
                "💡 Start your Docker service/daemon",
                "💡 Check Docker documentation for your platform"
            ]

    def run_command(self, command: str, capture_output: bool = True, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a shell command with improved error handling and timeout support."""
        try:
            result = subprocess.run(
                command, 
                shell=True, 
                capture_output=capture_output, 
                text=True,
                check=check,
                timeout=timeout
            )
            return result
        except subprocess.TimeoutExpired:
            message = f"⏰ Command timed out after {timeout}s: {command}"
            if WSL2_MANAGER_AVAILABLE:
                log_warning(LogSource.SYSTEM, message)
            else:
                print(message)
            return subprocess.CompletedProcess(command, 124, "", f"Command timed out after {timeout}s")
        except subprocess.CalledProcessError as e:
            if not capture_output:
                message = f"❌ Command failed: {command}\nError: {e}"
                if WSL2_MANAGER_AVAILABLE:
                    log_error(LogSource.SYSTEM, message)
                else:
                    print(message)
            return e

    def check_docker(self) -> bool:
        """Check if Docker Engine is available and running"""
        environment = self.detect_environment()
        message = f"🔍 Checking Docker Engine availability... (Environment: {environment.upper()})"
        
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, message)
        else:
            print(message)
        
        # Check if docker command exists
        if not shutil.which("docker"):
            error_msg = "❌ Docker command not found. Please install Docker first."
            if WSL2_MANAGER_AVAILABLE:
                log_error(LogSource.SYSTEM, error_msg)
            else:
                print(error_msg)
            
            if environment == "windows":
                print("📋 Download: https://docs.docker.com/desktop/install/windows/")
            elif environment == "wsl":
                print("📋 Install Docker Engine in WSL: https://docs.docker.com/engine/install/ubuntu/")
                print("📋 Quick install: curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh")
            else:
                print("📋 Installation guide: https://docs.docker.com/engine/install/")
            return False
        
        # Check Docker version
        result = self.run_command("docker --version")
        if result.returncode != 0:
            error_msg = "❌ Docker is not available. Please install Docker first."
            if WSL2_MANAGER_AVAILABLE:
                log_error(LogSource.SYSTEM, error_msg)
            else:
                print(error_msg)
            return False
        
        version_msg = f"✅ Docker version: {result.stdout.strip()}"
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, version_msg)
        else:
            print(version_msg)
        
        # Check if Docker daemon is running
        result = self.run_command("docker info", timeout=10)
        if result.returncode != 0:
            error_msg = "❌ Docker daemon is not running."
            if WSL2_MANAGER_AVAILABLE:
                log_error(LogSource.SYSTEM, error_msg)
            else:
                print(error_msg)
            
            instructions = self.get_docker_startup_instructions(environment)
            for instruction in instructions:
                print(instruction)
            
            # Additional environment-specific guidance
            if environment == "windows":
                print("\n🔧 Windows-specific troubleshooting:")
                print("   • Check if Docker Desktop is installed and running")
                print("   • Look for Docker whale icon in system tray")
                print("   • Try restarting Docker Desktop")
            elif environment == "wsl":
                print("\n🔧 WSL-specific troubleshooting:")
                print("   • Install Docker Engine directly in WSL (recommended)")
                print("   • Avoid Docker Desktop for better performance")
                print("   • Make sure Docker service is started: sudo service docker start")
                print("   • Add your user to docker group: sudo usermod -aG docker $USER")
            
            return False
        
        # Check Docker Compose availability
        compose_cmd = self.get_docker_compose_command()
        if compose_cmd:
            compose_version_result = self.run_command(" ".join(compose_cmd + ["version"]))
            if compose_version_result.returncode == 0:
                version_output = compose_version_result.stdout.strip()
                version_parts = version_output.split()
                version = version_parts[2] if len(version_parts) > 2 else "available"
                success_msg = f"✅ Docker Compose: {version}"
                if WSL2_MANAGER_AVAILABLE:
                    log_info(LogSource.SYSTEM, success_msg)
                else:
                    print(success_msg)
            else:
                warning_msg = "⚠️  Docker Compose not available, will use individual container commands"
                if WSL2_MANAGER_AVAILABLE:
                    log_warning(LogSource.SYSTEM, warning_msg)
                else:
                    print(warning_msg)
        else:
            warning_msg = "⚠️  Docker Compose not found, will use individual container commands"
            if WSL2_MANAGER_AVAILABLE:
                log_warning(LogSource.SYSTEM, warning_msg)
            else:
                print(warning_msg)
        
        success_msg = "✅ Docker Engine is available and running"
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, success_msg)
        else:
            print(success_msg)
        return True

    def stop_containers(self) -> bool:
        """Stop all project containers using WSL2ServiceManager or fallback methods"""
        message = "\n🛑 Stopping containers..."
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, message)
        else:
            print(message)
        
        success = False
        
        # Try WSL2ServiceManager first if available
        if self.wsl2_manager:
            try:
                if self.wsl2_manager.stop_docker_services():
                    log_info(LogSource.SYSTEM, "✅ Docker services stopped via WSL2ServiceManager")
                    return True
                else:
                    log_warning(LogSource.SYSTEM, "WSL2ServiceManager stop failed, trying fallback methods")
            except Exception as e:
                log_warning(LogSource.SYSTEM, f"WSL2ServiceManager stop failed: {e}, trying fallback methods")
        
        # Fallback to original logic
        if os.path.exists(self.compose_file_path):
            compose_cmd = self.get_docker_compose_command()
            if compose_cmd:
                print("📋 Using Docker Compose to stop services...")
                command = " ".join(compose_cmd + ["down"])
                result = self.run_command(command, capture_output=False, timeout=60)
                if result.returncode == 0:
                    print("✅ Docker Compose services stopped successfully")
                    return True
                else:
                    print("⚠️  Docker Compose stop failed, trying individual containers...")
            else:
                print("⚠️  Docker Compose not available, using individual containers...")
        else:
            print("⚠️  docker-compose.yml not found, using individual containers...")
        
        # Stop individual containers as fallback
        stopped_count = 0
        for container in CONTAINER_NAMES:
            print(f"🛑 Stopping container: {container}")
            result = self.run_command(f"docker stop {container}", timeout=30)
            if result.returncode == 0:
                print(f"✅ Stopped: {container}")
                stopped_count += 1
                success = True
            else:
                print(f"⚠️  Container {container} was not running or doesn't exist")
        
        if stopped_count > 0:
            print(f"✅ Stopped {stopped_count} container(s)")
        
        return success

    def remove_containers(self) -> bool:
        """Remove all project containers"""
        print("\n🗑️  Removing containers...")
        removed_count = 0
        
        for container in CONTAINER_NAMES:
            print(f"🗑️  Removing container: {container}")
            result = self.run_command(f"docker rm -f {container}")
            if result.returncode == 0:
                print(f"✅ Removed: {container}")
                removed_count += 1
            else:
                print(f"⚠️  Container {container} doesn't exist or already removed")
        
        if removed_count > 0:
            success_msg = f"✅ Removed {removed_count} container(s)"
            if WSL2_MANAGER_AVAILABLE:
                log_info(LogSource.SYSTEM, success_msg)
            else:
                print(success_msg)
            return True
        return False

    def remove_volumes(self) -> bool:
        """Remove all project volumes"""
        print("\n🗑️  Removing volumes...")
        removed_count = 0
        
        for volume in VOLUME_NAMES:
            print(f"🗑️  Removing volume: {volume}")
            result = self.run_command(f"docker volume rm {volume}")
            if result.returncode == 0:
                print(f"✅ Removed: {volume}")
                removed_count += 1
            else:
                print(f"⚠️  Volume {volume} doesn't exist or already removed")
        
        if removed_count > 0:
            success_msg = f"✅ Removed {removed_count} volume(s)"
            if WSL2_MANAGER_AVAILABLE:
                log_info(LogSource.SYSTEM, success_msg)
            else:
                print(success_msg)
            return True
        return False

    def remove_network(self) -> bool:
        """Remove project network"""
        print(f"\n🌐 Removing network: {NETWORK_NAME}")
        result = self.run_command(f"docker network rm {NETWORK_NAME}")
        if result.returncode == 0:
            success_msg = f"✅ Removed network: {NETWORK_NAME}"
            if WSL2_MANAGER_AVAILABLE:
                log_info(LogSource.SYSTEM, success_msg)
            else:
                print(success_msg)
            return True
        else:
            print(f"⚠️  Network {NETWORK_NAME} doesn't exist or already removed")
            return False

    def cleanup_unused_resources(self) -> None:
        """Clean up unused Docker resources (images, build cache, etc.)"""
        message = "\n🧹 Cleaning up unused Docker resources..."
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, message)
        else:
            print(message)
        
        cleanup_commands = [
            ("🗑️  Removing unused images...", "docker image prune -f", 60),
            ("🗑️  Removing build cache...", "docker builder prune -f", 60),
            ("🗑️  Removing unused volumes...", "docker volume prune -f", 30),
            ("🗑️  Removing unused networks...", "docker network prune -f", 30)
        ]
        
        for description, command, timeout in cleanup_commands:
            print(description)
            result = self.run_command(command, timeout=timeout)
            if result.returncode == 0:
                print(f"✅ {description.replace('🗑️  Removing', 'Cleaned up').replace('...', '')}")
            else:
                print(f"⚠️  Failed to {description.lower().replace('🗑️  removing', 'clean up').replace('...', '')}")

    def remove_setup_marker(self) -> bool:
        """Remove the setup completion marker file"""
        marker_path = Path(SETUP_MARKER_FILE)
        if marker_path.exists():
            print(f"\n🗑️  Removing setup marker: {SETUP_MARKER_FILE}")
            try:
                marker_path.unlink()
                success_msg = "✅ Setup marker removed"
                if WSL2_MANAGER_AVAILABLE:
                    log_info(LogSource.SYSTEM, success_msg)
                else:
                    print(success_msg)
                return True
            except OSError as e:
                error_msg = f"❌ Failed to remove setup marker: {e}"
                if WSL2_MANAGER_AVAILABLE:
                    log_error(LogSource.SYSTEM, error_msg)
                else:
                    print(error_msg)
                return False
        else:
            print(f"\n⚠️  Setup marker {SETUP_MARKER_FILE} doesn't exist")
            return False

    def get_container_status(self, container_name: str) -> Tuple[bool, str]:
        """Get the status of a specific container"""
        result = self.run_command(f"docker ps -a --filter name={container_name} --format '{{{{.Status}}}}'")
        if result.returncode == 0 and result.stdout.strip():
            return True, result.stdout.strip()
        return False, "Not found"

    def list_project_resources(self) -> None:
        """List current project Docker resources with enhanced formatting"""
        message = "\n📋 Current project Docker resources:"
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, message)
        else:
            print(message)
        
        # Use WSL2ServiceManager for enhanced status if available
        if self.wsl2_manager:
            try:
                status_info = self.wsl2_manager.get_service_status()
                if "services" in status_info and status_info["services"]:
                    print("\n🐳 Services (via WSL2ServiceManager):")
                    services = status_info["services"]
                    if isinstance(services, list):
                        for service in services:
                            if isinstance(service, dict):
                                name = service.get("Name", "Unknown")
                                state = service.get("State", "Unknown")
                                status = service.get("Status", "Unknown")
                                status_icon = "🟢" if state == "running" else "🔴" if "exited" in state.lower() else "🟡"
                                print(f"  {status_icon} {name}: {state} ({status})")
                    else:
                        print("  📋 Service status format not recognized")
                elif "error" in status_info:
                    print(f"  ⚠️  Service status error: {status_info['error']}")
                    # Fall back to manual container checking
                    self._list_containers_manual()
                else:
                    print("  📭 No services found via WSL2ServiceManager")
                    self._list_containers_manual()
            except Exception as e:
                if WSL2_MANAGER_AVAILABLE:
                    log_warning(LogSource.SYSTEM, f"WSL2ServiceManager status check failed: {e}")
                print(f"  ⚠️  WSL2ServiceManager failed: {e}")
                self._list_containers_manual()
        else:
            self._list_containers_manual()
        
        # Enhanced volume listing
        print("\n💾 Volumes:")
        volumes_found = False
        for volume in VOLUME_NAMES:
            result = self.run_command(f"docker volume inspect {volume} --format '{{{{.Driver}}}}'")
            if result.returncode == 0 and result.stdout.strip():
                volumes_found = True
                driver = result.stdout.strip()
                # Get volume size if possible
                size_result = self.run_command(f"docker system df -v | grep {volume}")
                size_info = ""
                if size_result.returncode == 0 and size_result.stdout.strip():
                    parts = size_result.stdout.strip().split()
                    if len(parts) > 1:
                        size_info = f" ({parts[1]})"
                print(f"  📦 {volume}: {driver}{size_info}")
            else:
                print(f"  📭 {volume}: Not found")
        
        if not volumes_found:
            print("  📭 No project volumes found")
        
        # Enhanced network listing
        print(f"\n🌐 Network:")
        result = self.run_command(f"docker network inspect {NETWORK_NAME} --format '{{{{.Driver}}}}'")
        if result.returncode == 0 and result.stdout.strip():
            driver = result.stdout.strip()
            print(f"  🔗 {NETWORK_NAME}: {driver}")
        else:
            print(f"  📭 {NETWORK_NAME}: Not found")
        
        # Show disk usage
        print(f"\n💽 Docker disk usage:")
        result = self.run_command("docker system df")
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip():
                    print(f"  {line}")

    def _list_containers_manual(self):
        """Manual container listing fallback"""
        print("\n🐳 Containers:")
        containers_found = False
        for container in CONTAINER_NAMES:
            exists, status = self.get_container_status(container)
            if exists:
                containers_found = True
                status_icon = "🟢" if "Up" in status else "🔴" if "Exited" in status else "🟡"
                print(f"  {status_icon} {container}: {status}")
            else:
                print(f"  ⚪ {container}: Not found")
        
        if not containers_found:
            print("  📭 No project containers found")

def main():
    parser = argparse.ArgumentParser(
        description="Reset Tower Hooker project for testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--containers-only", 
        action="store_true",
        help="Only stop and remove containers, keep volumes and networks"
    )
    parser.add_argument(
        "--deep-clean",
        action="store_true", 
        help="Full reset plus cleanup of unused Docker resources (images, cache, etc.)"
    )
    parser.add_argument(
        "--list", 
        action="store_true",
        help="List current project Docker resources and exit"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt and proceed with reset"
    )
    
    args = parser.parse_args()
    
    # Initialize reset manager
    reset_manager = DockerResetManager()
    
    print("🔄 Tower Hooker Project Reset Utility")
    print("=" * 50)
    if WSL2_MANAGER_AVAILABLE:
        print("🐳 Enhanced with WSL2ServiceManager")
    else:
        print("🐳 Basic Docker Engine integration")
    print("=" * 50)
    
    if not reset_manager.check_docker():
        sys.exit(1)
    
    if args.list:
        reset_manager.list_project_resources()
        return
    
    # Show current state
    reset_manager.list_project_resources()
    
    # Determine reset scope
    if args.deep_clean:
        reset_description = "⚠️  DEEP CLEAN: This will completely reset the project AND clean up all unused Docker resources"
    elif args.containers_only:
        reset_description = "⚠️  This will stop and remove all project containers (keeping volumes and networks)"
    else:
        reset_description = "⚠️  This will completely reset the project (containers + volumes + networks)"
    
    print(f"\n{reset_description}")
    
    # Confirm reset action
    if not args.force:
        try:
            confirm = input("\nDo you want to continue? (y/N): ").strip().lower()
            if confirm not in ['y', 'yes']:
                message = "❌ Reset cancelled"
                if WSL2_MANAGER_AVAILABLE:
                    log_info(LogSource.SYSTEM, message)
                else:
                    print(message)
                return
        except KeyboardInterrupt:
            message = "\n❌ Reset cancelled"
            if WSL2_MANAGER_AVAILABLE:
                log_info(LogSource.SYSTEM, message)
            else:
                print(message)
            return
    
    # Perform reset
    start_message = "\n🚀 Starting project reset..."
    if WSL2_MANAGER_AVAILABLE:
        log_info(LogSource.SYSTEM, start_message)
    else:
        print(start_message)
    
    reset_success = True
    
    # Always stop and remove containers
    if not reset_manager.stop_containers():
        reset_success = False
    
    if not reset_manager.remove_containers():
        reset_success = False
    
    # Remove network and volumes unless containers-only
    if not args.containers_only:
        if not reset_manager.remove_network():
            reset_success = False
        
        if not reset_manager.remove_volumes():
            reset_success = False
        
        if not reset_manager.remove_setup_marker():
            reset_success = False
    
    # Deep clean if requested
    if args.deep_clean:
        reset_manager.cleanup_unused_resources()
    
    # Final status
    if reset_success:
        final_message = "\n✅ Project reset completed successfully!"
        if WSL2_MANAGER_AVAILABLE:
            log_info(LogSource.SYSTEM, final_message)
        else:
            print(final_message)
    else:
        final_message = "\n⚠️  Project reset completed with some warnings"
        if WSL2_MANAGER_AVAILABLE:
            log_warning(LogSource.SYSTEM, final_message)
        else:
            print(final_message)
    
    print("\n📝 Next steps:")
    if args.containers_only:
        print("  1. Run 'docker compose up -d' to restart services")
        print("  2. Or run 'python setup_infrastructure.py' to reconfigure")
    else:
        print("  1. Run 'python setup_infrastructure.py' to set up infrastructure")
        print("  2. Or run 'docker compose up -d' to start services manually")
    print("  3. Test your application's setup capabilities")
    
    if args.deep_clean:
        print("\n🧹 Deep clean completed - all unused Docker resources have been removed")

if __name__ == "__main__":
    main() 