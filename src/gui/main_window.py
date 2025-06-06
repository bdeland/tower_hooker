"""
MainWindow - Main GUI window for Tower Hooker Control Panel

This class provides the main user interface for the Tower Hooker application,
including status display, controls, and embedded views.
"""

import os
import time
import webbrowser
from PyQt6.QtWidgets import (QMainWindow, QVBoxLayout, QWidget, QLabel, 
                             QStatusBar, QMessageBox, QTextEdit, QSplitter,
                             QGroupBox, QHBoxLayout, QPushButton, QComboBox,
                             QLineEdit, QFileDialog, QSystemTrayIcon, QMenu,
                             QCheckBox)
from PyQt6.QtGui import QCloseEvent, QFont, QIcon, QAction
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtCore import QUrl, Qt
from src.managers.unified_logging_definitions import LogSource
from .app_controller import AppController


class MainWindow(QMainWindow):
    """
    Main window for the Tower Hooker Control Panel GUI.
    
    Provides the primary interface for users to interact with the application,
    displaying status information and connecting to backend operations via AppController.
    """
    
    def __init__(self, app_controller: AppController, parent=None):
        """
        Initialize the main window.
        
        Args:
            app_controller: AppController instance for backend communication
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.controller = app_controller
        
        # Set window properties
        self.setWindowTitle("Tower Hooker Control Panel")
        self.resize(1280, 768)
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Create a splitter to separate Grafana view from controls and log view
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(main_splitter)
        
        # Create and setup Grafana web view with persistent profile
        self.web_view = QWebEngineView()
        
        # Create persistent profile for Grafana login/session persistence
        self.web_engine_profile = QWebEngineProfile("TowerHookerGrafanaProfile", self.web_view)
        
        # Set persistent storage path for web engine profile
        profile_path = os.path.join(os.getcwd(), "qt_webengine_profile")
        os.makedirs(profile_path, exist_ok=True)
        self.web_engine_profile.setPersistentStoragePath(profile_path)
        self.web_engine_profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.web_engine_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        
        # Create and set the web page with our persistent profile
        web_page = QWebEnginePage(self.web_engine_profile, self.web_view)
        self.web_view.setPage(web_page)
        
        # Initially load a blank page
        self.web_view.setUrl(QUrl("about:blank"))
        
        # Add web view to top part of splitter (takes up most space)
        main_splitter.addWidget(self.web_view)
        
        # Create BlueStacks & Frida Control Panel
        self._create_control_panel()
        main_splitter.addWidget(self.control_panel_widget)
        
        # Initialize process data storage for filtering
        self.all_processes = []  # Store full process list for filtering
        
        # Create GUI log view widget
        self.gui_log_view = QTextEdit()
        self.gui_log_view.setReadOnly(True)
        self.gui_log_view.setFont(QFont("Courier New", 9))  # Monospaced font for logs
        self.gui_log_view.setMaximumHeight(200)  # Reasonable fixed height
        
        # Add log view to bottom part of splitter
        main_splitter.addWidget(self.gui_log_view)
        
        # Set splitter proportions (Grafana gets most space, controls get medium, logs get fixed portion)
        main_splitter.setSizes([500, 250, 200])  # Approximate ratio
        
        # Create status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # Add update check controls to the status bar
        self.btn_check_updates = QPushButton("Check for Updates")
        self.btn_check_updates.clicked.connect(self.on_check_updates_clicked)
        self.lbl_update_status = QLabel("")
        
        # Add minimize to tray button to the status bar
        self.btn_minimize_to_tray = QPushButton("Minimize to Tray")
        self.btn_minimize_to_tray.clicked.connect(self.minimize_to_tray)
        
        # Add update check widgets to status bar (left side)
        self.status_bar.addWidget(self.btn_check_updates)
        self.status_bar.addWidget(self.lbl_update_status)
        
        # Add minimize to tray button to permanent section (right side)
        self.status_bar.addPermanentWidget(self.btn_minimize_to_tray)
        
        # Connect core signals from AppOrchestrator
        self.controller.orchestrator.initialization_status.connect(self.update_status_bar)
        self.controller.orchestrator.fatal_error_occurred.connect(self.handle_fatal_error)
        
        # Connect services_configured signal for Grafana URL updates
        self.controller.orchestrator.services_configured.connect(self.update_grafana_url)
        
        # Connect GUI log feed signal for log view
        self.controller.orchestrator.gui_log_feed.connect(self.append_gui_log)
        
        # Connect BlueStacks & Frida signals
        self.controller.orchestrator.bluestacks_connection_status.connect(self.on_bluestacks_connection_status)
        self.controller.orchestrator.available_adb_devices.connect(self.on_available_adb_devices)
        self.controller.orchestrator.bluestacks_processes_updated.connect(self.on_bluestacks_processes_updated)
        self.controller.orchestrator.frida_server_status.connect(self.on_frida_server_status)
        self.controller.orchestrator.frida_attachment_status.connect(self.on_frida_attachment_status)
        
        # Create system tray icon for minimize to tray functionality
        self._create_system_tray()
        
        # Log that the GUI has been initialized
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "Main GUI window initialized with Grafana web view, control panel, log display, and system tray"
        )
        
    def _create_control_panel(self):
        """Create the BlueStacks & Frida Control Panel section."""
        # Create main control panel widget
        self.control_panel_widget = QWidget()
        control_layout = QHBoxLayout(self.control_panel_widget)
        
        # BlueStacks Controls Section
        bluestacks_group = QGroupBox("BlueStacks Controls")
        bs_layout = QVBoxLayout(bluestacks_group)
        
        # BlueStacks connection controls
        self.btn_connect_bs = QPushButton("Connect BlueStacks")
        self.lbl_bs_status = QLabel("BlueStacks Status: Disconnected")
        
        # ADB devices controls
        devices_layout = QHBoxLayout()
        self.combo_adb_devices = QComboBox()
        self.combo_adb_devices.setEnabled(False)  # Disabled initially
        self.btn_refresh_adb_devices = QPushButton("Refresh Devices")
        devices_layout.addWidget(QLabel("ADB Devices:"))
        devices_layout.addWidget(self.combo_adb_devices)
        devices_layout.addWidget(self.btn_refresh_adb_devices)
        
        # Add BlueStacks widgets to layout
        bs_layout.addWidget(self.btn_connect_bs)
        bs_layout.addWidget(self.lbl_bs_status)
        bs_layout.addLayout(devices_layout)
        
        # Frida Controls Section
        frida_group = QGroupBox("Frida Controls")
        frida_layout = QVBoxLayout(frida_group)
        
        # Process selection controls with inline third-party filter
        processes_layout = QHBoxLayout()
        self.combo_bs_processes = QComboBox()
        self.combo_bs_processes.setEnabled(False)  # Disabled initially
        self.combo_bs_processes.setEditable(True)  # Make it searchable/editable
        self.combo_bs_processes.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # Don't allow inserting new items
        
        # Third party processes filter (inline with dropdown)
        self.checkbox_third_party_only = QCheckBox("Third party only")
        self.checkbox_third_party_only.setChecked(True)  # Enabled by default
        self.checkbox_third_party_only.stateChanged.connect(self.on_third_party_filter_changed)
        
        self.btn_get_bs_processes = QPushButton("Get Processes")
        self.btn_get_bs_processes.setEnabled(False)  # Disabled until BS connected
        
        processes_layout.addWidget(QLabel("Processes:"))
        processes_layout.addWidget(self.combo_bs_processes)
        processes_layout.addWidget(self.checkbox_third_party_only)
        processes_layout.addWidget(self.btn_get_bs_processes)
        
        # Hook script controls
        script_layout = QHBoxLayout()
        self.lbl_hook_script_path = QLineEdit("src/scripts/default_hook.js")
        self.lbl_hook_script_path.setReadOnly(True)
        self.btn_change_hook_script = QPushButton("Change Script...")
        script_layout.addWidget(QLabel("Hook Script:"))
        script_layout.addWidget(self.lbl_hook_script_path)
        script_layout.addWidget(self.btn_change_hook_script)
        
        # Frida attachment controls
        attach_layout = QHBoxLayout()
        self.btn_attach_frida = QPushButton("Attach Frida")
        self.btn_attach_frida.setEnabled(False)  # Disabled initially
        self.btn_detach_frida = QPushButton("Detach Frida")
        self.btn_detach_frida.setEnabled(False)  # Disabled initially
        attach_layout.addWidget(self.btn_attach_frida)
        attach_layout.addWidget(self.btn_detach_frida)
        
        # Frida status
        self.lbl_frida_status = QLabel("Frida Status: Not Attached")
        
        # Add Frida widgets to layout
        frida_layout.addLayout(processes_layout)
        frida_layout.addLayout(script_layout)
        frida_layout.addLayout(attach_layout)
        frida_layout.addWidget(self.lbl_frida_status)
        
        # Add group boxes to main control layout
        control_layout.addWidget(bluestacks_group)
        control_layout.addWidget(frida_group)
        
        # Connect button signals to controller methods
        self.btn_connect_bs.clicked.connect(self.controller.connect_bluestacks_via_gui)
        self.btn_refresh_adb_devices.clicked.connect(self.controller.refresh_adb_devices_via_gui)
        self.btn_get_bs_processes.clicked.connect(self.controller.get_bluestacks_processes_via_gui)
        self.btn_attach_frida.clicked.connect(self.on_attach_frida_clicked)
        self.btn_detach_frida.clicked.connect(self.controller.detach_frida_via_gui)
        self.btn_change_hook_script.clicked.connect(self.on_change_hook_script_clicked)
        
    def _create_system_tray(self):
        """Create and configure the system tray icon and menu."""
        # Create system tray icon
        self.tray_icon = QSystemTrayIcon(self)
        
        # Set the app icon - use relative path that works from app run location
        icon_path = os.path.join("src", "gui", "assets", "app_icon.png")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            # Fallback to a default system icon if our icon is not found
            self.tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
            self.controller.orchestrator.log_warning_via_ulm(
                LogSource.MAIN_APP, 
                f"App icon not found at {icon_path}, using default system icon"
            )
        
        # Create context menu for tray icon
        tray_menu = QMenu()
        
        # Add "Show/Hide" action
        show_action = QAction("Show/Hide", self)
        show_action.triggered.connect(self.toggle_window_visibility)
        tray_menu.addAction(show_action)
        
        # Add separator
        tray_menu.addSeparator()
        
        # Add "Quit" action
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        # Set the context menu
        self.tray_icon.setContextMenu(tray_menu)
        
        # Connect tray icon activation (left click)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        
        # Show the tray icon
        self.tray_icon.show()
        
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "System tray icon created and activated"
        )
        
    def update_status_bar(self, message: str):
        """
        Update the status bar with a new message.
        
        Args:
            message: Status message to display
        """
        self.status_bar.showMessage(message, 5000)  # Show for 5 seconds
        
    def handle_fatal_error(self, error_message: str):
        """
        Handle fatal backend errors by displaying a critical error dialog.
        
        Args:
            error_message: Error message from the backend
        """
        QMessageBox.critical(
            self, 
            "Fatal Backend Error", 
            f"A critical error occurred in the backend:\n\n{error_message}\n\n"
            "The application may be unstable and might need to restart."
        )
        
    def append_gui_log(self, level_str: str, source_str: str, message_str: str):
        """
        Append a formatted log message to the GUI log view.
        
        Args:
            level_str: Log level (INFO, WARNING, ERROR, etc.)
            source_str: Log source identifier
            message_str: The log message content
        """
        # Format the log entry with timestamp
        timestamp = time.strftime('%H:%M:%S')
        formatted_log = f"[{timestamp}] [{level_str}] [{source_str}] {message_str}"
        
        # Append to the log view
        self.gui_log_view.append(formatted_log)
        
        # Auto-scroll to the latest message
        scrollbar = self.gui_log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
    def update_grafana_url(self, services_info_dict: dict):
        """
        Update the Grafana web view URL based on services configuration.
        
        Args:
            services_info_dict: Dictionary containing service URLs from AppOrchestrator
        """
        grafana_url = services_info_dict.get("grafana_url")
        
        if grafana_url:
            # Load the Grafana dashboard
            self.web_view.setUrl(QUrl(grafana_url))
            self.update_status_bar(f"Grafana dashboard loading from: {grafana_url}")
            self.controller.orchestrator.log_info_via_ulm(
                LogSource.MAIN_APP, 
                f"Loading Grafana dashboard from: {grafana_url}"
            )
        else:
            # Grafana URL not available
            self.update_status_bar("Grafana URL not available. Check service status.")
            self.web_view.setHtml(
                "<html><body><h1>Grafana Not Available</h1>"
                "<p>Please ensure Grafana service is running and configured.</p>"
                "</body></html>"
            )
            self.controller.orchestrator.log_warning_via_ulm(
                LogSource.MAIN_APP, 
                "Grafana URL not provided in services configuration"
            )
    
    # BlueStacks & Frida Control Panel Signal Handlers
    
    def on_bluestacks_connection_status(self, is_connected: bool, message: str):
        """
        Handle BlueStacks connection status updates.
        
        Args:
            is_connected: Whether BlueStacks is connected
            message: Status message or device serial
        """
        if is_connected:
            self.lbl_bs_status.setText(f"BlueStacks Status: Connected ({message})")
            self.btn_get_bs_processes.setEnabled(True)
            self.combo_adb_devices.setEnabled(True)
            # Automatically refresh devices when connected
            self.controller.refresh_adb_devices_via_gui()
        else:
            self.lbl_bs_status.setText(f"BlueStacks Status: Disconnected ({message})")
            self.btn_get_bs_processes.setEnabled(False)
            self.combo_adb_devices.setEnabled(False)
            self.combo_adb_devices.clear()
            self.combo_bs_processes.setEnabled(False)
            self.combo_bs_processes.clear()
            self.btn_attach_frida.setEnabled(False)
            
    def on_available_adb_devices(self, devices: list):
        """
        Handle available ADB devices list updates.
        
        Args:
            devices: List of available device serials
        """
        self.combo_adb_devices.clear()
        for device in devices:
            self.combo_adb_devices.addItem(device)
            
    def on_bluestacks_processes_updated(self, processes: list):
        """
        Handle BlueStacks processes list updates.
        
        Args:
            processes: List of process dictionaries with name and PID
        """
        # Store all processes for filtering
        self.all_processes = processes
        
        # Apply current filtering
        self._update_processes_display()
            
    def _update_processes_display(self):
        """Update the processes combo box based on current filter settings."""
        self.combo_bs_processes.clear()
        self.combo_bs_processes.setEnabled(True)
        
        # Apply third party filter if enabled
        filtered_processes = self._filter_processes(self.all_processes)
        
        for process in filtered_processes:
            if isinstance(process, dict):
                # Extract process name and clean it for display
                process_name = process.get('name', 'Unknown')
                
                # Extract clean package/app name for display (no PID, no path)
                display_name = self._extract_package_name(process_name)
                
                # Store the full process info as item data for later use
                self.combo_bs_processes.addItem(display_name, process)
            else:
                # Fallback for simple string process identifiers
                self.combo_bs_processes.addItem(str(process), process)
                
        # Enable attach button if we have processes
        if filtered_processes:
            self.btn_attach_frida.setEnabled(True)
        else:
            self.btn_attach_frida.setEnabled(False)
            
    def _extract_package_name(self, process_name: str) -> str:
        """
        Extract app name from process name.
        
        Args:
            process_name: Full process name
            
        Returns:
            Cleaned app name for display
        """
        if not process_name or process_name == 'Unknown':
            return process_name
            
        # If it's a path, extract the last part
        if '/' in process_name:
            return process_name.split('/')[-1]
            
        # If it looks like a full package name (contains dots), return the full package name
        # This is better for uniqueness and clarity
        if '.' in process_name and not process_name.startswith('/'):
            return process_name
            
        # Otherwise return as is
        return process_name
        
    def _filter_processes(self, processes: list) -> list:
        """
        Filter processes based on current filter settings.
        
        Args:
            processes: List of all processes
            
        Returns:
            Filtered list of processes
        """
        if not self.checkbox_third_party_only.isChecked():
            return processes
            
        # Define system/built-in process patterns to exclude (more comprehensive)
        system_patterns = [
            # Core Android system
            'com.android.',
            'android.',
            '/system/',
            'system_server',
            'zygote',
            'kernel',
            'init',
            'surfaceflinger',
            'servicemanager',
            'vold',
            'netd',
            'rild',
            'mediaserver',
            'installd',
            'adbd',
            'logd',
            'lmkd',
            'ueventd',
            'healthd',
            
            # Google services and apps
            'com.google.android.',
            'com.google.process.',
            'com.android.providers.',
            'com.android.systemui',
            'com.android.phone',
            'com.android.settings',
            'com.android.bluetooth',
            'com.android.nfc',
            'com.android.keychain',
            'com.android.launcher',
            'com.android.inputmethod',
            'com.android.calendar',
            'com.android.camera',
            'com.android.contacts',
            'com.android.deskclock',
            'com.android.dialer',
            'com.android.email',
            'com.android.gallery',
            'com.android.mms',
            'com.android.music',
            'com.android.calculator',
            'com.android.chrome',
            'com.android.webview',
            
            # Samsung/OEM system apps
            'com.sec.android.',
            'com.samsung.android.',
            'com.samsung.knox.',
            'com.lge.android.',
            'com.htc.android.',
            'com.sonyericsson.',
            'com.motorola.',
            
            # BlueStacks system processes
            'com.bluestacks.',
            'bstservice',
            'hd-agent',
            'bstkeymap',
            
            # Generic system processes
            'dex2oat',
            'app_process',
            'dalvikvm',
            'artd',
            'perfprofd',
            'gatekeeperd',
            'keystore',
            'fingerprintd',
            'thermal-engine',
            'mm-qcamera-daemon',
            'sensors.qcom',
            'wpa_supplicant',
            'dhcpcd',
        ]
        
        # Define patterns that indicate third-party apps (these should be included)
        third_party_indicators = [
            # Common third-party app package patterns
            'com.facebook.',
            'com.twitter.',
            'com.instagram.',
            'com.whatsapp',
            'com.spotify.',
            'com.netflix.',
            'com.youtube.',
            'com.snapchat.',
            'com.tiktok.',
            'com.discord.',
            'com.telegram.',
            'com.uber.',
            'com.airbnb.',
            'com.amazon.',
            'com.ebay.',
            'com.paypal.',
            'com.microsoft.',
            'com.adobe.',
            'com.dropbox.',
            'com.skype.',
            'com.viber.',
            'com.linkedin.',
            'com.pinterest.',
            'com.reddit.',
            'com.tumblr.',
            # Games often have these patterns
            'com.king.',
            'com.supercell.',
            'com.rovio.',
            'com.mojang.',
            'com.ea.',
            'com.activision.',
            'com.ubisoft.',
            # Common developer patterns
            'org.mozilla.',
            'org.telegram.',
            'tv.twitch.',
            'app.revanced.',
        ]
        
        filtered = []
        for process in processes:
            if isinstance(process, dict):
                process_name = process.get('name', '').lower()
                
                # Skip empty or unknown processes
                if not process_name or process_name == 'unknown':
                    continue
                
                # Check if it's explicitly a third-party app
                is_third_party = any(indicator in process_name for indicator in third_party_indicators)
                
                # Check if it matches system patterns
                is_system = any(pattern in process_name for pattern in system_patterns)
                
                # Include if:
                # 1. It's explicitly identified as third-party, OR
                # 2. It doesn't match system patterns AND looks like a package name (has dots but not system paths)
                if is_third_party or (not is_system and '.' in process_name and not process_name.startswith('/')):
                    # Additional check: skip if it looks like a system service or process
                    if not any(sys_word in process_name for sys_word in ['system', 'service', 'daemon', 'provider', 'process']):
                        filtered.append(process)
                elif not is_system and not '.' in process_name:
                    # For processes without dots (like executables), be more selective
                    # Only include if they don't look like system binaries
                    if not any(sys_word in process_name for sys_word in ['bin/', 'sbin/', 'usr/', 'vendor/', 'system/']):
                        filtered.append(process)
            else:
                # For non-dict processes, apply basic filtering
                process_str = str(process).lower()
                is_system = any(pattern in process_str for pattern in system_patterns)
                if not is_system:
                    filtered.append(process)
                
        return filtered
        
    def on_third_party_filter_changed(self, state):
        """Handle third party filter checkbox state change."""
        # Refresh the display with current filter settings
        self._update_processes_display()
        
        filter_status = "enabled" if self.checkbox_third_party_only.isChecked() else "disabled"
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            f"Third party processes filter {filter_status}"
        )
            
    def on_frida_server_status(self, is_running: bool, message: str):
        """
        Handle Frida server status updates.
        
        Args:
            is_running: Whether Frida server is running on device
            message: Status message
        """
        # Update status bar or add a dedicated Frida server status label if needed
        status_msg = f"Frida Server: {'Running' if is_running else 'Not Running'} - {message}"
        self.update_status_bar(status_msg)
        
    def on_frida_attachment_status(self, is_attached: bool, process_name: str, message: str):
        """
        Handle Frida attachment status updates.
        
        Args:
            is_attached: Whether Frida is attached to a process
            process_name: Name of the attached process
            message: Status message
        """
        if is_attached:
            self.lbl_frida_status.setText(f"Frida Status: Attached to {process_name}")
            self.btn_attach_frida.setEnabled(False)
            self.btn_detach_frida.setEnabled(True)
        else:
            self.lbl_frida_status.setText("Frida Status: Not Attached")
            self.btn_attach_frida.setEnabled(True)
            self.btn_detach_frida.setEnabled(False)
            
    def on_attach_frida_clicked(self):
        """Handle Frida attach button click."""
        # Get current text from combo box (works for both selected and typed entries)
        current_text = self.combo_bs_processes.currentText().strip()
        current_index = self.combo_bs_processes.currentIndex()
        
        if not current_text:
            QMessageBox.warning(self, "No Process Selected", 
                              "Please select or type a process name to attach Frida to.")
            return
        
        # Try to get process data from selection first
        process_data = None
        if current_index >= 0:
            process_data = self.combo_bs_processes.currentData()
            
        # If we have process data from selection, use the actual process name for attachment
        if isinstance(process_data, dict):
            # Use the actual process name from the data, not the display name
            process_identifier = process_data.get('name', current_text)
            process_name = process_data.get('name', current_text)
        else:
            # User typed a custom name - try to find matching process
            process_identifier = None
            process_name = current_text
            
            # Search for a process with matching display name in our stored processes
            for process in self.all_processes:
                if isinstance(process, dict):
                    stored_name = self._extract_package_name(process.get('name', ''))
                    if stored_name.lower() == current_text.lower():
                        process_identifier = process.get('name', current_text)
                        process_name = process.get('name', current_text)
                        break
            
            # If still no match, try partial matching on actual process names
            if not process_identifier:
                for process in self.all_processes:
                    if isinstance(process, dict):
                        actual_name = process.get('name', '')
                        if current_text.lower() in actual_name.lower():
                            process_identifier = actual_name
                            process_name = actual_name
                            break
            
            # If still no process found, use the typed name as-is (might be valid)
            if not process_identifier:
                process_identifier = current_text
                process_name = current_text
            
        # Get script path
        script_path = self.lbl_hook_script_path.text().strip()
        
        # Validate inputs
        if not process_identifier:
            QMessageBox.warning(self, "Invalid Process", 
                              "Selected process does not have a valid identifier.")
            return
            
        if not script_path or not os.path.exists(script_path):
            QMessageBox.warning(self, "Invalid Script Path", 
                              f"Hook script path is invalid or file does not exist:\n{script_path}")
            return
            
        # Log the attach attempt
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            f"Attempting to attach Frida to process: {process_name} (PID: {process_identifier})"
        )
            
        # Call controller to attach Frida
        self.controller.attach_frida_via_gui(process_identifier, script_path)
        
    def on_change_hook_script_clicked(self):
        """Handle hook script change button click."""
        # Open file dialog to select a JavaScript file
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "Select Hook Script", 
            ".", 
            "JavaScript Files (*.js);;All Files (*)"
        )
        
        if file_path:
            # Update the script path display
            self.lbl_hook_script_path.setText(file_path)
            
            # Persist this as the new default script path
            self.controller.update_setting_via_gui("frida.default_script_path", file_path)
            
            # Update status bar
            self.update_status_bar(f"Hook script updated: {os.path.basename(file_path)}")
    
    # System Tray Functionality
    
    def minimize_to_tray(self):
        """Minimize the window to the system tray."""
        self.hide()
        
        # Show tray notification
        self.tray_icon.showMessage(
            "Tower Hooker", 
            "Application minimized to tray.", 
            QSystemTrayIcon.MessageIcon.Information, 
            2000  # 2 seconds
        )
        
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Window minimized to system tray via button"
        )
    
    def toggle_window_visibility(self):
        """Toggle window visibility between hidden and shown."""
        if self.isVisible():
            self.minimize_to_tray()
        else:
            self.showNormal()
            self.activateWindow()
            self.controller.orchestrator.log_info_via_ulm(
                LogSource.MAIN_APP, 
                "Window restored from system tray"
            )
            
    def on_tray_icon_activated(self, reason):
        """
        Handle system tray icon activation.
        
        Args:
            reason: The activation reason (left click, double click, etc.)
        """
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left click
            self.toggle_window_visibility()
            
    def quit_application(self):
        """Handle quit action from system tray menu."""
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Quit action triggered from system tray, requesting backend shutdown"
        )
        
        # Properly shut down the backend
        self.controller.request_backend_shutdown()
        
        # Hide the tray icon
        self.tray_icon.hide()
        
        # Actually close the application
        import sys
        sys.exit(0)
        
    def on_check_updates_clicked(self):
        """Handle the check for updates button click."""
        try:
            # Update the status label immediately
            self.lbl_update_status.setText("Checking... (Opens browser)")
            
            # Schedule the update check on the backend loop
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self.controller.orchestrator.check_for_updates_simple(), 
                self.controller.loop
            )
            
            # Open the browser to the update URL
            if hasattr(self.controller.orchestrator, 'UPDATE_INFO_URL'):
                webbrowser.open_new_tab(self.controller.orchestrator.UPDATE_INFO_URL)
                self.lbl_update_status.setText("Browser opened for update check")
            else:
                self.lbl_update_status.setText("Update URL not available")
                
        except Exception as e:
            error_msg = f"Failed to check for updates: {str(e)}"
            self.lbl_update_status.setText("Update check failed")
            self.controller.orchestrator.log_error_via_ulm(
                LogSource.MAIN_APP, 
                error_msg, 
                error=str(e)
            )

    def closeEvent(self, event: QCloseEvent):
        """
        Handle window close events by requesting graceful backend shutdown.
        
        Args:
            event: The close event
        """
        self.controller.orchestrator.log_info_via_ulm(
            LogSource.MAIN_APP, 
            "GUI: Close event received, requesting backend shutdown."
        )
        
        # Hide the tray icon
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        
        # Request graceful backend shutdown
        self.controller.request_backend_shutdown()
        event.accept() 