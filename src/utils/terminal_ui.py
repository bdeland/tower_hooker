"""
Sleek Terminal UI for Tower Hooker Setup

This module provides beautiful terminal output using Rich library for:
- Status displays with colored indicators
- Progress bars for setup operations
- Formatted panels and tables
- Interactive prompts with styling
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich.align import Align
from rich import box
from typing import Dict, Any, Optional, List
import time
import threading

class TowerHookerUI:
    """Sleek terminal UI for Tower Hooker setup and operations"""
    
    def __init__(self):
        self.console = Console()
        self.progress = None
        
    def print_welcome_banner(self):
        """Display a beautiful welcome banner"""
        banner_text = Text()
        banner_text.append("🎉 Welcome to ", style="bold white")
        banner_text.append("Tower Hooker", style="bold cyan")
        banner_text.append("!", style="bold white")
        
        welcome_panel = Panel(
            Align.center(banner_text),
            title="[bold blue]Infrastructure Setup[/bold blue]",
            border_style="blue",
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(welcome_panel)
        self.console.print()
    
    def print_setup_status(self, status: Dict[str, Any]) -> None:
        """Display setup status in a beautiful formatted table"""
        
        # Create main status table
        table = Table(
            title="[bold cyan]Infrastructure Status[/bold cyan]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta"
        )
        
        table.add_column("Component", style="bold white", width=15)
        table.add_column("Status", width=12)
        table.add_column("Details", style="dim")
        
        # Docker Services
        docker = status['docker_services']
        docker_status = "[green]✓ Ready[/green]" if docker['ready'] else "[red]✗ Not Ready[/red]"
        docker_details = ""
        
        if 'services' in docker and docker['services']:
            running = sum(1 for s in docker['services'] if s['running'])
            total = len(docker['services'])
            docker_details = f"{running}/{total} services running"
        elif 'error' in docker:
            docker_details = f"[red]{docker['error'][:50]}...[/red]" if len(docker['error']) > 50 else f"[red]{docker['error']}[/red]"
        
        table.add_row("🐳 Docker", docker_status, docker_details)
        
        # InfluxDB
        influx = status['influxdb']
        influx_status = "[green]✓ Ready[/green]" if influx['ready'] else "[red]✗ Not Ready[/red]"
        influx_details = ""
        
        if influx.get('auth_valid'):
            bucket_count = len(influx.get('existing_buckets', []))
            influx_details = f"Auth valid, {bucket_count} buckets"
        elif 'error' in influx:
            influx_details = f"[red]{influx['error'][:50]}...[/red]" if len(influx['error']) > 50 else f"[red]{influx['error']}[/red]"
        else:
            influx_details = "[yellow]Authentication failed[/yellow]"
        
        table.add_row("📊 InfluxDB", influx_status, influx_details)
        
        # Grafana
        grafana = status['grafana']
        grafana_status = "[green]✓ Ready[/green]" if grafana['ready'] else "[red]✗ Not Ready[/red]"
        grafana_details = ""
        
        if grafana.get('accessible'):
            grafana_details = "Service accessible"
            if grafana.get('auth_valid'):
                grafana_details += ", auth valid"
        elif 'error' in grafana:
            grafana_details = f"[red]{grafana['error'][:50]}...[/red]" if len(grafana['error']) > 50 else f"[red]{grafana['error']}[/red]"
        else:
            grafana_details = "[red]Service not accessible[/red]"
        
        table.add_row("📈 Grafana", grafana_status, grafana_details)
        
        # Loki
        loki = status['loki']
        loki_status = "[green]✓ Ready[/green]" if loki['ready'] else "[red]✗ Not Ready[/red]"
        loki_details = ""
        
        if loki.get('accessible'):
            loki_details = "Service accessible and ready"
        elif 'error' in loki:
            loki_details = f"[red]{loki['error'][:50]}...[/red]" if len(loki['error']) > 50 else f"[red]{loki['error']}[/red]"
        else:
            loki_details = "[red]Service not accessible[/red]"
        
        table.add_row("📝 Loki", loki_status, loki_details)
        
        # Display the table
        self.console.print()
        self.console.print(table)
        
        # Overall status panel
        overall_ready = status['overall_ready']
        if overall_ready:
            status_text = Text("🎯 All systems ready!", style="bold green")
            panel_style = "green"
        else:
            status_text = Text("⚠️  Setup required", style="bold yellow")
            panel_style = "yellow"
        
        status_panel = Panel(
            Align.center(status_text),
            border_style=panel_style,
            padding=(0, 2)
        )
        
        self.console.print()
        self.console.print(status_panel)
        self.console.print()
    
    def confirm_setup(self) -> bool:
        """Ask user to confirm setup with styled prompt"""
        try:
            return Confirm.ask(
                "[bold cyan]Would you like to run the automated setup?[/bold cyan]",
                default=False
            )
        except (EOFError, KeyboardInterrupt):
            # If input is not available or interrupted, use default (False)
            self.console.print("[yellow]Input not available, skipping automated setup[/yellow]")
            return False
    
    def confirm_start_services(self) -> bool:
        """Ask user to confirm starting services"""
        try:
            return Confirm.ask(
                "[bold yellow]Would you like to start the services?[/bold yellow]",
                default=True
            )
        except (EOFError, KeyboardInterrupt):
            # If input is not available or interrupted, use default (True)
            self.console.print("[yellow]Input not available, proceeding with service startup[/yellow]")
            return True
    
    def get_target_package(self, default_package: str, default_pid: Optional[int] = None) -> str:
        """Get target package with styled prompt"""
        if default_pid:
            self.console.print(f"[green]✓[/green] Default target '[cyan]{default_package}[/cyan]' is running (PID: {default_pid})")
            
            try:
                use_default = Confirm.ask(
                    f"[bold]Use default target '[cyan]{default_package}[/cyan]'?[/bold]",
                    default=True
                )
            except (EOFError, KeyboardInterrupt):
                # If input is not available or interrupted, use default
                self.console.print(f"[yellow]Input not available, using default target: {default_package}[/yellow]")
                return default_package
            
            if use_default:
                return default_package
        
        try:
            return Prompt.ask(
                f"[bold]Enter target package name[/bold]",
                default=default_package if not default_pid else ""
            )
        except (EOFError, KeyboardInterrupt):
            # If input is not available or interrupted, use default
            self.console.print(f"[yellow]Input not available, using default target: {default_package}[/yellow]")
            return default_package
    
    def show_setup_wizard_header(self):
        """Display setup wizard header"""
        header_text = Text()
        header_text.append("🔧 ", style="bold yellow")
        header_text.append("Infrastructure Setup Wizard", style="bold white")
        
        header_panel = Panel(
            Align.center(header_text),
            subtitle="[dim]Checking and configuring Docker, InfluxDB, Grafana, and Loki[/dim]",
            border_style="yellow",
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(header_panel)
        self.console.print()
    
    def create_setup_progress(self) -> Progress:
        """Create a progress bar for setup operations"""
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
            transient=True
        )
    
    def show_setup_step(self, step_name: str, description: str):
        """Show current setup step"""
        step_text = Text()
        step_text.append(f"🔄 {step_name}", style="bold cyan")
        step_text.append(f"\n{description}", style="dim white")
        
        step_panel = Panel(
            step_text,
            border_style="cyan",
            padding=(0, 1)
        )
        
        self.console.print(step_panel)
    
    def show_success(self, message: str):
        """Show success message"""
        success_text = Text(f"✅ {message}", style="bold green")
        self.console.print(success_text)
    
    def show_error(self, message: str):
        """Show error message"""
        error_text = Text(f"❌ {message}", style="bold red")
        self.console.print(error_text)
    
    def show_warning(self, message: str):
        """Show warning message"""
        warning_text = Text(f"⚠️  {message}", style="bold yellow")
        self.console.print(warning_text)
    
    def show_info(self, message: str):
        """Show info message"""
        info_text = Text(f"ℹ️  {message}", style="bold blue")
        self.console.print(info_text)
    
    def show_completion_banner(self, success: bool = True):
        """Show setup completion banner"""
        if success:
            completion_text = Text()
            completion_text.append("🎉 Setup Complete!", style="bold green")
            completion_text.append("\nAll infrastructure components are ready.", style="green")
            
            panel_style = "green"
            title = "[bold green]Success![/bold green]"
        else:
            completion_text = Text()
            completion_text.append("⚠️  Setup Incomplete", style="bold yellow")
            completion_text.append("\nSome issues remain. Check the status above.", style="yellow")
            
            panel_style = "yellow"
            title = "[bold yellow]Partial Success[/bold yellow]"
        
        completion_panel = Panel(
            Align.center(completion_text),
            title=title,
            border_style=panel_style,
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(completion_panel)
        self.console.print()
    
    def show_application_running(self):
        """Show application running status"""
        running_text = Text()
        running_text.append("🚀 Tower Hooker is running", style="bold green")
        running_text.append("\nPress Ctrl+C to stop", style="dim white")
        
        running_panel = Panel(
            Align.center(running_text),
            title="[bold green]Application Status[/bold green]",
            border_style="green",
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(running_panel)
    
    def show_shutdown_message(self):
        """Show shutdown message"""
        shutdown_text = Text("🛑 Shutting down gracefully...", style="bold yellow")
        self.console.print()
        self.console.print(shutdown_text)
    
    def print_separator(self):
        """Print a visual separator"""
        self.console.print()
    
    def with_progress_context(self, description: str):
        """Context manager for progress operations"""
        class ProgressContext:
            def __init__(self, ui, desc):
                self.ui = ui
                self.description = desc
                self.progress = None
                self.task = None
            
            def __enter__(self):
                self.progress = self.ui.create_setup_progress()
                self.progress.start()
                self.task = self.progress.add_task(self.description, total=None)
                return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.progress:
                    self.progress.stop()
            
            def update(self, description: str):
                if self.progress and self.task:
                    self.progress.update(self.task, description=description)
        
        return ProgressContext(self, description)
    
    def show_docker_not_running_error(self):
        """Show Docker Desktop not running error with guidance"""
        error_text = Text()
        error_text.append("🐳 Docker Desktop Not Running", style="bold red")
        error_text.append("\n\nDocker Desktop needs to be running to start the infrastructure services.", style="white")
        error_text.append("\n\nPlease:", style="white")
        error_text.append("\n1. Start Docker Desktop", style="cyan")
        error_text.append("\n2. Wait for it to fully initialize", style="cyan")
        error_text.append("\n3. Try running Tower Hooker again", style="cyan")
        
        error_panel = Panel(
            error_text,
            title="[bold red]Docker Error[/bold red]",
            border_style="red",
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(error_panel)
        self.console.print()
    
    def show_docker_permission_error(self):
        """Show Docker permission error with guidance"""
        error_text = Text()
        error_text.append("🔒 Docker Permission Error", style="bold red")
        error_text.append("\n\nDocker Desktop may not have proper permissions or isn't accessible.", style="white")
        error_text.append("\n\nTry:", style="white")
        error_text.append("\n1. Run as Administrator (if on Windows)", style="cyan")
        error_text.append("\n2. Check Docker Desktop is running", style="cyan")
        error_text.append("\n3. Restart Docker Desktop", style="cyan")
        
        error_panel = Panel(
            error_text,
            title="[bold red]Permission Error[/bold red]",
            border_style="red",
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(error_panel)
        self.console.print()
    
    def show_docker_status_check(self):
        """Show Docker status checking message"""
        status_text = Text("🔍 Checking Docker Desktop status...", style="bold blue")
        self.console.print(status_text)
    
    def confirm_continue_without_docker(self) -> bool:
        """Ask user if they want to continue without Docker services"""
        try:
            return Confirm.ask(
                "[bold yellow]Docker services unavailable. Continue in monitoring-only mode?[/bold yellow]",
                default=False
            )
        except (EOFError, KeyboardInterrupt):
            # If input is not available or interrupted, use default (False)
            self.console.print("[yellow]Input not available, not continuing without Docker[/yellow]")
            return False
    
    def show_monitoring_only_mode(self):
        """Show monitoring-only mode message"""
        mode_text = Text()
        mode_text.append("📊 Running in Monitoring-Only Mode", style="bold yellow")
        mode_text.append("\n\nSome features will be limited:", style="white")
        mode_text.append("\n• No database logging to InfluxDB", style="dim white")
        mode_text.append("\n• No Grafana dashboards", style="dim white")
        mode_text.append("\n• No centralized log aggregation", style="dim white")
        mode_text.append("\n\nFrida hooking and console logging will still work.", style="green")
        
        mode_panel = Panel(
            mode_text,
            title="[bold yellow]Limited Mode[/bold yellow]",
            border_style="yellow",
            padding=(1, 2)
        )
        
        self.console.print()
        self.console.print(mode_panel)
        self.console.print()

# Global UI instance
ui = TowerHookerUI() 