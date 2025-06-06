"""
Loki Logging Handler for Tower Hooker

This module provides a logging handler that sends logs directly to Grafana Loki
using the python-logging-loki library. It integrates with our existing structured
logging system and provides proper source tagging for Grafana filtering.
"""

import logging
import structlog
from typing import Dict, Any, Optional
from datetime import datetime, timezone

try:
    import logging_loki
    LOKI_AVAILABLE = True
except ImportError:
    LOKI_AVAILABLE = False
    logging_loki = None


class TowerHookerLokiHandler(logging.Handler):
    """
    A custom Loki handler that integrates with Tower Hooker's structured logging.
    
    This handler extracts structured data from log records and sends them to Loki
    with proper labels and formatting for Grafana visualization.
    """
    
    def __init__(self, loki_url: str, default_labels: Optional[Dict[str, str]] = None):
        """
        Initialize the Loki handler.
        
        Args:
            loki_url: The URL of the Loki push endpoint
            default_labels: Default labels to attach to all log messages
        """
        super().__init__()
        
        if not LOKI_AVAILABLE:
            raise ImportError("python-logging-loki is required but not installed")
        
        self.loki_url = loki_url
        self.default_labels = default_labels or {}
        
        # Configure the underlying Loki handler
        # Set level_tag to "level" for Grafana compatibility
        logging_loki.emitter.LokiEmitter.level_tag = "level"
        
        # Create the underlying Loki handler
        self.loki_handler = logging_loki.LokiHandler(
            url=loki_url,
            version="1"
        )
        
        # Set up a logger specifically for Loki
        self.loki_logger = logging.getLogger("tower_hooker_loki")
        self.loki_logger.addHandler(self.loki_handler)
        self.loki_logger.setLevel(logging.DEBUG)
        
        # Prevent propagation to avoid loops
        self.loki_logger.propagate = False
    
    def emit(self, record: logging.LogRecord):
        """
        Emit a log record to Loki.
        
        This method extracts structured data from the log record and sends it
        to Loki with appropriate labels.
        """
        try:
            # Extract structured data from the record
            log_data = self._extract_log_data(record)
            
            # Build labels for Loki
            labels = self._build_labels(log_data)
            
            # Get the message
            message = log_data.get('event', record.getMessage())
            
            # Create a new LogRecord for the underlying Loki handler
            # This ensures proper timestamp and formatting
            loki_record = logging.LogRecord(
                name=record.name,
                level=record.levelno,
                pathname=record.pathname,
                lineno=record.lineno,
                msg=message,
                args=(),
                exc_info=record.exc_info,
                func=record.funcName
            )
            
            # Add the tags as extra data for the underlying handler
            loki_record.tags = labels
            
            # Send directly to the underlying Loki handler
            self.loki_handler.emit(loki_record)
                
        except Exception as e:
            # Handle errors gracefully to avoid breaking the application
            if not getattr(self, '_shutting_down', False):
                self.handleError(record)
    
    def _extract_log_data(self, record: logging.LogRecord) -> Dict[str, Any]:
        """
        Extract structured data from a log record.
        
        This method looks for structured data in various places where
        structlog might have stored it.
        """
        log_data = {}
        
        # Method 1: Check if our custom formatter processed the record
        if hasattr(record, '_structlog_event_dict'):
            log_data = record._structlog_event_dict.copy()
        
        # Method 2: Check for event_dict attribute
        elif hasattr(record, 'event_dict') and isinstance(record.event_dict, dict):
            log_data = record.event_dict.copy()
        
        # Method 3: Check if the message is a dict (from structlog)
        elif hasattr(record, 'msg') and isinstance(record.msg, dict):
            log_data = record.msg.copy()
        
        # Method 4: Fallback - create basic log data from LogRecord
        else:
            log_data = {
                'event': record.getMessage(),
                'level': record.levelname,
                'logger': record.name,
                'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
            }
            
            if record.exc_info:
                log_data['exception'] = self.formatException(record.exc_info)
        
        # Extract any custom attributes that were added to the LogRecord
        # This handles attributes added via setattr() in UnifiedLoggingManager
        standard_attrs = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
            'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
            'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
            'processName', 'process', 'getMessage', 'extra', 'tags'
        }
        
        for attr_name in dir(record):
            if (not attr_name.startswith('_') and 
                attr_name not in standard_attrs and
                not callable(getattr(record, attr_name, None))):
                try:
                    attr_value = getattr(record, attr_name)
                    # Only include serializable values
                    if isinstance(attr_value, (str, int, float, bool, list, dict, type(None))):
                        log_data[attr_name] = attr_value
                except (AttributeError, TypeError):
                    # Skip attributes that can't be accessed or serialized
                    pass
        
        # Ensure we have basic required fields
        if 'timestamp' not in log_data:
            log_data['timestamp'] = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        if 'level' not in log_data:
            log_data['level'] = record.levelname
        if 'logger' not in log_data:
            log_data['logger'] = record.name
        
        return log_data
    
    def _build_labels(self, log_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Build Loki labels from log data.
        
        This method extracts relevant fields from the log data to use as
        Loki labels for filtering in Grafana.
        """
        labels = self.default_labels.copy()
        
        # Add source label (most important for filtering)
        if 'source' in log_data:
            labels['source'] = str(log_data['source'])
        
        # Add level label
        if 'level' in log_data:
            labels['level'] = str(log_data['level'])
        
        # Add logger name
        if 'logger' in log_data:
            labels['logger'] = str(log_data['logger'])
        
        # Add component if available
        if 'component' in log_data:
            labels['component'] = str(log_data['component'])
        
        # Add job label for consistency with Promtail (only if not already set)
        if 'job' not in labels:
            labels['job'] = 'tower_hooker'
        
        # Add any additional labels from the log data
        # Look for common label fields
        label_fields = ['device', 'tag', 'priority', 'round_id', 'phase']
        for field in label_fields:
            if field in log_data and log_data[field] is not None:
                # Convert to string and limit length for Loki
                label_value = str(log_data[field])[:100]  # Limit label length
                labels[field] = label_value
        
        return labels
    
    def close(self):
        """Close the handler and clean up resources with timeout protection."""
        # Mark as shutting down to prevent blocking
        self._shutting_down = True
        
        try:
            if hasattr(self, 'loki_handler'):
                # Close with timeout protection
                import threading
                
                def _close_handler():
                    try:
                        self.loki_handler.close()
                    except Exception:
                        pass
                
                close_thread = threading.Thread(target=_close_handler, daemon=True)
                close_thread.start()
                close_thread.join(timeout=2.0)  # 2 second timeout
                
        except Exception:
            pass
        super().close()


def create_loki_handler(loki_url: str, default_labels: Optional[Dict[str, str]] = None) -> Optional[TowerHookerLokiHandler]:
    """
    Create a Loki handler if the library is available.
    
    Args:
        loki_url: The URL of the Loki push endpoint
        default_labels: Default labels to attach to all log messages
        
    Returns:
        TowerHookerLokiHandler instance or None if library not available
    """
    if not LOKI_AVAILABLE:
        return None
    
    try:
        return TowerHookerLokiHandler(loki_url, default_labels)
    except Exception as e:
        # Log the error but don't fail the application
        logger = structlog.get_logger("loki_handler")
        logger.error("Failed to create Loki handler", error=str(e))
        return None 