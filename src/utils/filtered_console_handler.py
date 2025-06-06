"""
Filtered console logging handler for tower_hooker.
This handler allows granular control over what types of logs appear in the terminal
while preserving all logging to Loki and InfluxDB.
"""

import logging
import structlog
import sys
from typing import Dict, Any, Optional
from src.utils.config import should_show_in_console

class FilteredConsoleHandler(logging.Handler):
    """
    Custom console handler that filters logs based on configuration.
    Only affects console output - Loki and InfluxDB logging remains unchanged.
    """
    
    def __init__(self, stream=None, formatter=None):
        super().__init__()
        self.stream = stream or sys.stdout
        if formatter:
            self.setFormatter(formatter)
    
    def emit(self, record):
        """
        Emit a log record if it passes the console filters.
        """
        try:
            # Check if this log should be shown in console
            if not self._should_show_in_console(record):
                return
            
            # Format and write the message
            msg = self.format(record)
            self.stream.write(msg + '\n')
            self.stream.flush()
        except Exception:
            self.handleError(record)
    
    def _should_show_in_console(self, record) -> bool:
        """
        Determine if a log record should be shown in the console based on filters.
        
        Args:
            record: The logging record to check
            
        Returns:
            True if the log should be shown in console, False otherwise
        """
        # Always show errors and warnings if that filter is enabled
        if record.levelname.upper() in ['ERROR', 'CRITICAL', 'WARNING']:
            return should_show_in_console('ERRORS_AND_WARNINGS')
        
        # Get the log message and any structured data
        message = ""
        if hasattr(record, 'msg'):
            if isinstance(record.msg, str):
                message = record.msg
                if hasattr(record, 'args') and record.args:
                    try:
                        message = message % record.args
                    except:
                        pass
            elif isinstance(record.msg, dict):
                # Handle structured logging where msg is a dict
                message = record.msg.get('event', record.msg.get('message', str(record.msg)))
        
        # Check for structured logging data in record dict
        extra_data = getattr(record, '__dict__', {})
        
        # Categorize the log based on content and context
        category = self._categorize_log(message, extra_data)
        
        # Return filter decision
        return should_show_in_console(category)
    
    def _categorize_log(self, message: str, extra_data: Dict[str, Any]) -> str:
        """
        Categorize a log message to determine which filter applies.
        
        Args:
            message: The log message text
            extra_data: Any extra data/context from the log record
            
        Returns:
            The category key that corresponds to a console filter
        """
        # Handle cases where message might not be a string
        if not isinstance(message, str):
            message = str(message) if message is not None else ""
        
        message_lower = message.lower()
        
        # Check for pslist process entries (the verbose ones)
        if ('process ' in message_lower and 'pid:' in message_lower and 'rss:' in message_lower and 'kb' in message_lower):
            return 'PSLIST_PROCESSES'
        
        # Check for pslist summary messages
        if any(keyword in message_lower for keyword in [
            'starting periodic ps', 'process list logging', 'pslist', 'ps -a'
        ]):
            return 'PSLIST_SUMMARY'
        
        # Check for infrastructure/setup messages
        if any(keyword in message_lower for keyword in [
            'infrastructure setup', 'docker', 'starting docker', 'setup wizard', 
            'welcome to tower', 'setup complete', 'influxdb', 'grafana', 'loki'
        ]):
            return 'INFRASTRUCTURE_SETUP'
        
        # Check for database operations
        if any(keyword in message_lower for keyword in [
            'database', 'bucket', 'schema', 'connecting to influxdb', 'write_api',
            'db_manager', 'influxdb', 'data ingestion manager'
        ]):
            return 'DATABASE_OPERATIONS'
        
        # Check for application startup
        if any(keyword in message_lower for keyword in [
            'main application starting', 'importing application modules', 'phase 1:', 'phase 2:', 'phase 3:',
            'starting frida tower logger', 'initializing dependencies', 'app orchestrator',
            'application startup', 'tower hooker application starting'
        ]):
            return 'APPLICATION_STARTUP'
        
        # Check for application shutdown
        if any(keyword in message_lower for keyword in [
            'shutdown', 'cleanup', 'closing', 'stopping', 'detaching', 'exiting',
            'application cleanup', 'graceful shutdown'
        ]):
            return 'APPLICATION_SHUTDOWN'
        
        # Check for BlueStacks connection messages
        if any(keyword in message_lower for keyword in [
            'bluestacks', 'emulator', 'connected to:', 'adb', 'device', 'rooted'
        ]):
            return 'BLUESTACKS_CONNECTION'
        
        # Check for Frida operations
        if any(keyword in message_lower for keyword in [
            'frida', 'injector', 'hook', 'script', 'frida server', 'frida device'
        ]):
            return 'FRIDA_OPERATIONS'
        
        # Check for monitoring status
        if any(keyword in message_lower for keyword in [
            'monitoring', 'keep alive', 'running', 'application running'
        ]):
            return 'MONITORING_STATUS'
        
        # Check for development/debug messages
        if any(keyword in message_lower for keyword in [
            'debug', 'context binding', 'logging system', 'structlog'
        ]):
            return 'DEVELOPMENT_DEBUG'
        
        # Default to allowing the message (conservative approach)
        return 'APPLICATION_STARTUP'

class StructlogFilteredConsoleProcessor:
    """
    Structlog processor that filters messages before they reach the console renderer.
    This integrates with structlog's pipeline to provide filtering at the structlog level.
    """
    
    def __init__(self):
        pass
    
    def __call__(self, logger, method_name, event_dict):
        """
        Process a structlog event and determine if it should be rendered for console.
        
        Args:
            logger: The structlog logger instance
            method_name: The method name (info, error, etc.)
            event_dict: The event dictionary containing message and context
            
        Returns:
            The event_dict if it should be shown, or None to suppress it
        """
        # Check if this should be shown in console
        if not self._should_show_in_console(method_name, event_dict):
            # Add a marker to indicate this should be suppressed in console
            event_dict['_suppress_console'] = True
        
        return event_dict
    
    def _should_show_in_console(self, method_name: str, event_dict: Dict[str, Any]) -> bool:
        """
        Determine if a structlog event should be shown in the console.
        
        Args:
            method_name: The logging method name (info, error, etc.)
            event_dict: The event dictionary
            
        Returns:
            True if the event should be shown in console, False otherwise
        """
        # Always show errors and warnings if that filter is enabled
        if method_name.upper() in ['ERROR', 'CRITICAL', 'WARNING']:
            return should_show_in_console('ERRORS_AND_WARNINGS')
        
        # Get the message
        message = event_dict.get('event', '')
        
        # Categorize and check filter
        category = self._categorize_log(message, event_dict)
        return should_show_in_console(category)
    
    def _categorize_log(self, message: str, event_dict: Dict[str, Any]) -> str:
        """
        Categorize a structlog message to determine which filter applies.
        Same logic as the standard handler.
        """
        # Handle cases where message might not be a string
        if not isinstance(message, str):
            message = str(message) if message is not None else ""
        
        message_lower = message.lower()
        
        # Check for pslist process entries (the verbose ones)
        if ('process ' in message_lower and 'pid:' in message_lower and 'rss:' in message_lower and 'kb' in message_lower):
            return 'PSLIST_PROCESSES'
        
        # Check for pslist summary messages  
        if any(keyword in message_lower for keyword in [
            'starting periodic ps', 'process list logging', 'pslist', 'ps -a'
        ]):
            return 'PSLIST_SUMMARY'
        
        # Check for infrastructure/setup messages
        if any(keyword in message_lower for keyword in [
            'infrastructure setup', 'docker', 'starting docker', 'setup wizard', 
            'welcome to tower', 'setup complete', 'influxdb', 'grafana', 'loki'
        ]):
            return 'INFRASTRUCTURE_SETUP'
        
        # Check for database operations
        if any(keyword in message_lower for keyword in [
            'database', 'bucket', 'schema', 'connecting to influxdb', 'write_api',
            'db_manager', 'influxdb', 'data ingestion manager'
        ]):
            return 'DATABASE_OPERATIONS'
        
        # Check for application startup
        if any(keyword in message_lower for keyword in [
            'main application starting', 'importing application modules', 'phase 1:', 'phase 2:', 'phase 3:',
            'starting frida tower logger', 'initializing dependencies', 'app orchestrator',
            'application startup', 'tower hooker application starting'
        ]):
            return 'APPLICATION_STARTUP'
        
        # Check for application shutdown
        if any(keyword in message_lower for keyword in [
            'shutdown', 'cleanup', 'closing', 'stopping', 'detaching', 'exiting',
            'application cleanup', 'graceful shutdown'
        ]):
            return 'APPLICATION_SHUTDOWN'
        
        # Check for BlueStacks connection messages
        if any(keyword in message_lower for keyword in [
            'bluestacks', 'emulator', 'connected to:', 'adb', 'device', 'rooted'
        ]):
            return 'BLUESTACKS_CONNECTION'
        
        # Check for Frida operations
        if any(keyword in message_lower for keyword in [
            'frida', 'injector', 'hook', 'script', 'frida server', 'frida device'
        ]):
            return 'FRIDA_OPERATIONS'
        
        # Check for monitoring status
        if any(keyword in message_lower for keyword in [
            'monitoring', 'keep alive', 'running', 'application running'
        ]):
            return 'MONITORING_STATUS'
        
        # Check for development/debug messages
        if any(keyword in message_lower for keyword in [
            'debug', 'context binding', 'logging system', 'structlog'
        ]):
            return 'DEVELOPMENT_DEBUG'
        
        # Default to allowing the message (conservative approach)
        return 'APPLICATION_STARTUP'

def create_filtered_console_renderer(base_renderer):
    """
    Create a console renderer that respects the console filters.
    
    Args:
        base_renderer: The base renderer function to wrap
        
    Returns:
        A filtered renderer function
    """
    def filtered_renderer(logger, method_name, event_dict):
        # Check if this message should be suppressed in console
        if event_dict.get('_suppress_console', False):
            return ''  # Return empty string to suppress output
        
        # Remove the suppression marker before rendering
        event_dict.pop('_suppress_console', None)
        
        # Call the base renderer
        return base_renderer(logger, method_name, event_dict)
    
    return filtered_renderer 