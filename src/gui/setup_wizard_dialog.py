"""
Tower Hooker Infrastructure Setup Wizard Dialog

This module provides a PyQt6 dialog for managing the infrastructure setup wizard.
It provides a GUI interface for the setup process with progress tracking and user feedback.
"""

import sys
import asyncio
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, 
    QProgressBar, QPushButton, QWidget, QFrame
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QFont, QPalette


class SetupWizardDialog(QDialog):
    """
    Dialog for managing Tower Hooker infrastructure setup wizard.
    
    This dialog provides a user interface for running the infrastructure setup
    with progress tracking, status updates, and user controls.
    """
    
    def __init__(self, app_orchestrator_ref, parent=None):
        """
        Initialize the Setup Wizard Dialog.
        
        Args:
            app_orchestrator_ref: Reference to the AppOrchestrator instance
            parent: Parent widget (optional)
        """
        super().__init__(parent)
        self.app_orchestrator_ref = app_orchestrator_ref
        self._setup_complete = False
        self._degraded_mode = False
        self._setup_task = None  # Track the setup task for proper cleanup
        
        # Breathing animation variables
        self._breathing_opacity = 1.0
        self._breathing_direction = -1  # -1 for fading out, 1 for fading in
        self._breathing_timer = QTimer()
        self._breathing_timer.timeout.connect(self._update_breathing)
        
        self._init_ui()
        self._setup_connections()
    
    def _init_ui(self):
        """Initialize the user interface components."""
        # Set window properties
        self.setWindowTitle("Tower Hooker - Infrastructure Setup Wizard")
        self.setModal(True)
        self.setMinimumSize(600, 400)
        self.resize(700, 450)
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title and description
        self._create_header(layout)
        
        # Progress section
        self._create_progress_section(layout)
        
        # Button section
        self._create_button_section(layout)
        
    def _create_header(self, layout: QVBoxLayout):
        """Create the header section with title and description."""
        # Title
        title_label = QLabel("Infrastructure Setup Wizard")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # Description
        desc_label = QLabel(
            "This wizard will verify and set up the required infrastructure components:\n"
            "• Docker services\n"
            "• InfluxDB database\n"
            "• Grafana dashboard\n"
            "• Loki log aggregation"
        )
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet("color: #666; margin: 10px;")
        layout.addWidget(desc_label)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)
    
    def _create_progress_section(self, layout: QVBoxLayout):
        """Create the progress tracking section."""
        # Initial explanation (shown before setup starts)
        self.explanation_label = QLabel(
            "This wizard will automatically set up the complete infrastructure for Tower Hooker:\n\n"
            "🖥️  WSL2 - Windows Subsystem for Linux 2 (if not already installed)\n"
            "🐧 Ubuntu - Linux distribution in WSL2 (if not already installed)\n"
            "🐳 Docker Engine - Container runtime in Ubuntu WSL2 (automated installation)\n"
            "📊 InfluxDB database - Time-series data storage\n"
            "📈 Grafana dashboard - Monitoring and visualization\n"
            "📝 Loki log aggregation - Centralized logging\n\n"
            "The wizard will automatically install and configure everything needed. "
            "This includes WSL2, Ubuntu, and Docker Engine installation if they're not already present. "
            "Click 'Start Automated Setup' to begin the fully automated process."
        )
        self.explanation_label.setWordWrap(True)
        self.explanation_label.setStyleSheet("color: #888; margin: 20px 0px; line-height: 1.4;")
        layout.addWidget(self.explanation_label)
        
        # Progress elements (initially hidden)
        self.progress_container = QWidget()
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        
        # Status label
        self.status_label = QLabel("Initializing setup...")
        status_font = QFont()
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        progress_layout.addWidget(self.status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self._update_progress_bar_style()
        progress_layout.addWidget(self.progress_bar)
        
        # Current step label
        self.current_step_label = QLabel("")
        self.current_step_label.setWordWrap(True)  # Allow text wrapping for longer error messages
        self.current_step_label.setMinimumHeight(80)  # Ensure enough space for multi-line text
        self.current_step_label.setAlignment(Qt.AlignmentFlag.AlignTop)  # Align text to top
        self._update_current_step_styling(is_error=False)  # Set initial styling
        progress_layout.addWidget(self.current_step_label)
        
        # Initially hide the progress container
        self.progress_container.setVisible(False)
        layout.addWidget(self.progress_container)
    
    def _create_button_section(self, layout: QVBoxLayout):
        """Create the button section."""
        # Button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()  # Push buttons to the right
        
        # Start setup button
        self.btn_start_setup = QPushButton("Start Automated Setup")
        self.btn_start_setup.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:disabled { background-color: #cccccc; color: #666666; }"
        )
        button_layout.addWidget(self.btn_start_setup)
        
        # Continue anyway button (initially hidden)
        self.btn_continue_anyway = QPushButton("Continue Anyway (Degraded Mode)")
        self.btn_continue_anyway.setStyleSheet(
            "QPushButton { background-color: #ff9800; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #e68900; }"
            "QPushButton:disabled { background-color: #cccccc; color: #666666; }"
        )
        self.btn_continue_anyway.setVisible(False)  # Initially hidden
        button_layout.addWidget(self.btn_continue_anyway)
        
        # Close/Finish button (initially hidden)
        self.btn_close_wizard = QPushButton("Close")
        self.btn_close_wizard.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:disabled { background-color: #cccccc; color: #666666; }"
        )
        self.btn_close_wizard.setVisible(False)  # Initially hidden
        button_layout.addWidget(self.btn_close_wizard)
        
        layout.addLayout(button_layout)
    
    def _setup_connections(self):
        """Set up signal-slot connections."""
        self.btn_start_setup.clicked.connect(self.run_setup_sequence)
        self.btn_continue_anyway.clicked.connect(self._continue_anyway)
        self.btn_close_wizard.clicked.connect(self.accept)
    
    def _update_breathing(self):
        """Update the breathing effect on the progress bar."""
        self._breathing_opacity += self._breathing_direction * 0.03
        
        if self._breathing_opacity <= 0.5:
            self._breathing_opacity = 0.5
            self._breathing_direction = 1
        elif self._breathing_opacity >= 1.0:
            self._breathing_opacity = 1.0
            self._breathing_direction = -1
            
        self._update_progress_bar_style()
    
    def _update_progress_bar_style(self):
        """Update the progress bar style with current breathing opacity."""
        # Calculate color intensity based on breathing opacity
        base_color = 76, 175, 80  # Green color
        breathing_color = tuple(int(c * self._breathing_opacity) for c in base_color)
        
        style = f"""
            QProgressBar {{
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: #f0f0f0;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: rgb({breathing_color[0]}, {breathing_color[1]}, {breathing_color[2]});
                border-radius: 4px;
            }}
        """
        self.progress_bar.setStyleSheet(style)
    
    def _start_breathing(self):
        """Start the breathing animation on the progress bar."""
        self._breathing_timer.start(50)  # Update every 50ms
        
    def _stop_breathing(self):
        """Stop the breathing animation and reset to normal color."""
        self._breathing_timer.stop()
        self._breathing_opacity = 1.0
        self._update_progress_bar_style()
    
    def _update_current_step_styling(self, is_error=False):
        """Update the styling of the current step label based on content type."""
        if is_error:
            # Error styling - red background, bold text
            style = (
                "color: #d32f2f; font-weight: bold; margin-top: 5px; padding: 12px; "
                "background-color: #ffebee; border: 2px solid #ffcdd2; border-radius: 5px; "
                "font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.4;"
            )
        else:
            # Normal styling - gray background, regular text
            style = (
                "color: #666; font-style: italic; margin-top: 5px; padding: 10px; "
                "background-color: #f9f9f9; border-radius: 5px; "
                "font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.4;"
            )
        self.current_step_label.setStyleSheet(style)
    
    def run_setup_sequence(self):
        """
        Start the setup sequence by triggering the orchestrator's wizard.
        This is connected to the "Start Automated Setup / Check" button.
        """
        # Hide explanation and show progress elements
        self.explanation_label.setVisible(False)
        self.progress_container.setVisible(True)
        
        # Start breathing animation
        self._start_breathing()
        
        # Disable buttons during setup
        self.btn_start_setup.setEnabled(False)
        self.btn_continue_anyway.setVisible(False)
        self.btn_close_wizard.setVisible(False)
        
        # Clear progress display
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting infrastructure setup...")
        self.current_step_label.setText("Initializing setup sequence...")
        
        # Schedule the wizard on the backend loop
        try:
            if hasattr(self.app_orchestrator_ref, 'schedule_on_backend_loop'):
                async def do_setup():
                    try:
                        await self.app_orchestrator_ref.run_infrastructure_setup_wizard_async_steps()
                    except asyncio.CancelledError:
                        # Setup was cancelled, this is expected during shutdown
                        return
                    except Exception as e:
                        # Log the error and emit a failure signal
                        from src.managers.unified_logging_definitions import LogSource
                        self.app_orchestrator_ref.log_error_via_ulm(
                            LogSource.MAIN_APP, 
                            f"Setup wizard failed: {str(e)}"
                        )
                
                # Store the future for later cancellation
                self._setup_task = self.app_orchestrator_ref.schedule_on_backend_loop(do_setup())
            else:
                # Fallback - this should not happen if AppOrchestrator is properly implemented
                self.update_step_status("Error", False, "Cannot schedule wizard on backend loop from dialog.")
                self.setup_sequence_complete(False)
        except Exception as e:
            self.update_step_status("Error", False, f"Failed to start setup sequence: {str(e)}")
            self.setup_sequence_complete(False)
    
    def _continue_anyway(self):
        """Handle the 'Continue Anyway' button click (degraded mode)."""
        self._degraded_mode = True
        self.accept()
    
    # Slots to receive progress updates from AppOrchestrator
    
    @pyqtSlot(str, str)
    def update_progress_text(self, step_name: str, message: str):
        """
        Update the current step display with a new message.
        
        Args:
            step_name: Name of the setup step
            message: Progress message
        """
        self.current_step_label.setText(f"{step_name}: {message}")
    
    @pyqtSlot(str, bool, str)
    def update_step_status(self, step_name: str, success: bool, error_message: str = ""):
        """
        Update the status based on a completed setup step.
        
        Args:
            step_name: Name of the completed step
            success: Whether the step succeeded
            error_message: Error message if step failed (optional)
        """
        if success:
            self.status_label.setText(f"✅ {step_name} completed")
            self.current_step_label.setText(f"Successfully completed {step_name}")
        else:
            self.status_label.setText(f"❌ {step_name} failed")
            if error_message:
                self.current_step_label.setText(f"Failed: {error_message}")
            else:
                self.current_step_label.setText(f"Failed to complete {step_name}")
            
            # Show "Continue Anyway" button if any step fails
            self.btn_continue_anyway.setVisible(True)
    
    @pyqtSlot(int)
    def update_progress_bar(self, value: int):
        """
        Update the progress bar value.
        
        Args:
            value: Progress value (0-100)
        """
        self.progress_bar.setValue(max(0, min(100, value)))
    
    @pyqtSlot(bool)
    def setup_sequence_complete(self, overall_success: bool):
        """
        Handle completion of the setup sequence.
        
        Args:
            overall_success: Whether the overall setup was successful
        """
        self._setup_complete = True
        
        # Stop breathing animation
        self._stop_breathing()
        
        if overall_success:
            self.status_label.setText("✅ Setup Complete!")
            self.current_step_label.setText("Infrastructure setup completed successfully. You can now proceed to use Tower Hooker.")
            
            # Update buttons for successful completion
            self.btn_start_setup.setVisible(False)
            self.btn_continue_anyway.setVisible(False)
            self.btn_close_wizard.setText("Finish")
            self.btn_close_wizard.setVisible(True)
            self.btn_close_wizard.setEnabled(True)
            
            # Set progress to 100%
            self.progress_bar.setValue(100)
        else:
            self.status_label.setText("❌ Setup Incomplete")
            self.current_step_label.setText("Setup completed with errors. You can continue in degraded mode or close and fix the issues.")
            
            # Update buttons for failed completion
            self.btn_start_setup.setEnabled(True)  # Allow retry
            self.btn_continue_anyway.setVisible(True)
            self.btn_close_wizard.setText("Close")
            self.btn_close_wizard.setVisible(True)
            self.btn_close_wizard.setEnabled(True)
    
    @pyqtSlot(bool, str, list)
    def setup_sequence_detailed_complete(self, overall_success: bool, summary_message: str, failed_steps: list):
        """
        Handle detailed completion of the setup sequence with specific error information.
        
        Args:
            overall_success: Whether the overall setup was successful
            summary_message: Detailed summary message
            failed_steps: List of failed steps with details
        """
        self._setup_complete = True
        
        # Stop breathing animation
        self._stop_breathing()
        
        if overall_success:
            self.status_label.setText("✅ Setup Complete!")
            self.current_step_label.setText(summary_message)
            self._update_current_step_styling(is_error=False)
            
            # Update buttons for successful completion
            self.btn_start_setup.setVisible(False)
            self.btn_continue_anyway.setVisible(False)
            self.btn_close_wizard.setText("Finish")
            self.btn_close_wizard.setVisible(True)
            self.btn_close_wizard.setEnabled(True)
            
            # Set progress to 100%
            self.progress_bar.setValue(100)
        else:
            self.status_label.setText("❌ Setup Failed")
            
            # Create detailed error message for user
            if failed_steps:
                error_details = []
                for failure in failed_steps:
                    step_name = failure.get('step', 'Unknown Step')
                    error_msg = failure.get('error', 'Unknown error')
                    impact = failure.get('impact', '')
                    
                    detail = f"• {step_name}: {error_msg}"
                    if impact:
                        detail += f"\n  Impact: {impact}"
                    error_details.append(detail)
                
                detailed_message = f"{summary_message}\n\nDetails:\n" + "\n".join(error_details[:3])  # Show max 3 failures
                if len(failed_steps) > 3:
                    detailed_message += f"\n... and {len(failed_steps) - 3} more issues"
            else:
                detailed_message = summary_message
            
            self.current_step_label.setText(detailed_message)
            self._update_current_step_styling(is_error=True)
            
            # Update buttons for failed completion
            self.btn_start_setup.setEnabled(True)  # Allow retry
            self.btn_start_setup.setText("Retry Setup")
            self.btn_continue_anyway.setVisible(True)
            self.btn_close_wizard.setText("Close")
            self.btn_close_wizard.setVisible(True)
            self.btn_close_wizard.setEnabled(True)
    
    def is_degraded_mode(self) -> bool:
        """Check if user chose to continue in degraded mode."""
        return self._degraded_mode
    
    def is_setup_complete(self) -> bool:
        """Check if setup completed successfully."""
        return self._setup_complete
    
    def closeEvent(self, event):
        """Handle dialog close event with proper cleanup."""
        # Cancel the setup future if it's running
        if self._setup_task and not self._setup_task.done():
            try:
                self._setup_task.cancel()
            except Exception as e:
                print(f"Error cancelling setup task: {e}")
        
        # Stop any running animations
        self._stop_breathing()
        
        # Accept the close event
        event.accept()
    
    def reject(self):
        """Handle dialog rejection with proper cleanup."""
        # Cancel the setup future if it's running
        if self._setup_task and not self._setup_task.done():
            try:
                self._setup_task.cancel()
            except Exception as e:
                print(f"Error cancelling setup task: {e}")
        
        # Stop any running animations
        self._stop_breathing()
        
        # Call parent reject
        super().reject() 