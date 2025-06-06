"""
Standardized timestamp formatters using epoch milliseconds for consistent logging.
All logs in the application should use these formatters to ensure consistent timestamp handling.
"""

import logging
import json
from datetime import datetime, timezone
from typing import Dict, Any
from pythonjsonlogger import jsonlogger


def get_epoch_millis() -> int:
    """Get current timestamp as epoch milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def epoch_millis_to_human(epoch_millis: int) -> str:
    """Convert epoch milliseconds to human-readable format."""
    dt = datetime.fromtimestamp(epoch_millis / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"  # Trim to milliseconds


class EpochMillisFormatter(logging.Formatter):
    """
    Custom formatter that uses epoch milliseconds for all timestamps.
    Provides consistent timestamp format across the entire application.
    """
    
    def __init__(self, fmt: str = None, include_human_readable: bool = True):
        super().__init__()
        self.fmt = fmt or "[{timestamp_millis}] {timestamp_human} - {name} - {levelname} - {message}"
        self.include_human_readable = include_human_readable
    
    def format(self, record: logging.LogRecord) -> str:
        # Get epoch milliseconds for this log record
        timestamp_millis = int(record.created * 1000)
        
        # Add timestamp fields to record
        record.timestamp_millis = timestamp_millis
        if self.include_human_readable:
            record.timestamp_human = epoch_millis_to_human(timestamp_millis)
        
        # Format the message
        try:
            # Create format dictionary, avoiding conflicts with record.__dict__
            format_dict = {
                'timestamp_millis': timestamp_millis,
                'timestamp_human': getattr(record, 'timestamp_human', ''),
                'name': record.name,
                'levelname': record.levelname,
                'message': record.getMessage(),
            }
            
            # Add other record attributes that don't conflict
            for key, value in record.__dict__.items():
                if key not in format_dict:
                    format_dict[key] = value
            
            formatted = self.fmt.format(**format_dict)
            return formatted
        except (KeyError, ValueError) as e:
            # Fallback to basic format if custom format fails
            return f"[{timestamp_millis}] {record.levelname} - {record.getMessage()}"


class EpochMillisJsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter that uses epoch milliseconds for timestamps.
    Ensures all JSON logs have consistent timestamp format.
    """
    
    def __init__(self, *args, **kwargs):
        # Remove datefmt from kwargs since we handle timestamps manually
        kwargs.pop('datefmt', None)
        super().__init__(*args, **kwargs)
    
    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]):
        super().add_fields(log_record, record, message_dict)
        
        # Override timestamp with epoch milliseconds
        timestamp_millis = int(record.created * 1000)
        log_record['timestamp_millis'] = timestamp_millis
        log_record['timestamp_human'] = epoch_millis_to_human(timestamp_millis)
        
        # Remove the default 'asctime' field if present
        log_record.pop('asctime', None)
        
        # Ensure required fields are present
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['message'] = record.getMessage()


class FallbackEpochFormatter(logging.Formatter):
    """
    Simple fallback formatter using epoch milliseconds.
    Used when other formatters fail or for emergency logging.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp_millis = int(record.created * 1000)
        return f"[{timestamp_millis}] {record.levelname} - {record.getMessage()}"


def create_console_formatter(include_human_readable: bool = True) -> EpochMillisFormatter:
    """Create a console formatter with epoch milliseconds."""
    if include_human_readable:
        fmt = "[{timestamp_millis}] {timestamp_human} - [{name}] {levelname} - {message}"
    else:
        fmt = "[{timestamp_millis}] - [{name}] {levelname} - {message}"
    
    return EpochMillisFormatter(fmt=fmt, include_human_readable=include_human_readable)


def create_json_formatter() -> EpochMillisJsonFormatter:
    """Create a JSON formatter with epoch milliseconds."""
    return EpochMillisJsonFormatter(
        '%(timestamp_millis)s %(timestamp_human)s %(level)s %(logger)s %(message)s'
    )


def create_fallback_formatter() -> FallbackEpochFormatter:
    """Create a fallback formatter with epoch milliseconds."""
    return FallbackEpochFormatter() 