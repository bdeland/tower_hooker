"""
AppController - Thin layer between GUI and AppOrchestrator backend

This class provides a clean interface for GUI components to interact with
the asynchronous AppOrchestrator without directly dealing with threading.
"""

import asyncio
from typing import Any
from PyQt6.QtCore import QObject
from src.managers.unified_logging_definitions import LogSource


class AppController(QObject):
    """
    Thin controller layer that bridges GUI interactions with AppOrchestrator.
    
    This class handles the thread safety concerns of calling async methods
    from the synchronous GUI thread using asyncio.run_coroutine_threadsafe.
    """
    
    def __init__(self, core_orchestrator, backend_event_loop: asyncio.AbstractEventLoop, parent=None):
        """
        Initialize the AppController.
        
        Args:
            core_orchestrator: AppOrchestrator instance
            backend_event_loop: The asyncio event loop running the backend
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self.orchestrator = core_orchestrator
        self.loop = backend_event_loop
        
    def request_backend_shutdown(self):
        """Request graceful shutdown of the backend."""
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.request_graceful_shutdown(), 
            self.loop
        )
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Requesting backend shutdown via AppController"
        )
        
    def connect_bluestacks_via_gui(self):
        """Request BlueStacks connection via GUI."""
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Requesting BlueStacks connection..."
        )
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.connect_bluestacks_wrapper(), 
            self.loop
        )
        
    def refresh_adb_devices_via_gui(self):
        """Refresh available ADB devices via GUI."""
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Requesting ADB device refresh..."
        )
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.list_adb_devices_wrapper(), 
            self.loop
        )
        
    def get_bluestacks_processes_via_gui(self):
        """Get BlueStacks processes via GUI."""
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Requesting BlueStacks processes..."
        )
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.list_bluestacks_processes_wrapper(), 
            self.loop
        )
        
    def attach_frida_via_gui(self, process_identifier: str, script_path: str):
        """Attach Frida to process via GUI."""
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            f"GUI: Requesting Frida attachment to {process_identifier} with script {script_path}"
        )
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.attach_frida_to_process_wrapper(process_identifier, script_path), 
            self.loop
        )
        
    def detach_frida_via_gui(self):
        """Detach Frida via GUI."""
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Requesting Frida detachment..."
        )
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.detach_frida_wrapper(), 
            self.loop
        )
        
    def update_setting_via_gui(self, key_path: str, new_value: Any):
        """Update configuration setting via GUI."""
        self.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            f"GUI: Updating setting {key_path} = {new_value}"
        )
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.update_setting_and_persist(key_path, new_value), 
            self.loop
        ) 