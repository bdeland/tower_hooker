"""
Simplified structlog setup for Tower Hooker with automatic context binding.
Uses epoch milliseconds internally with local time for human-readable display.
"""

import logging
import structlog
from datetime import datetime
from typing import Any, Dict

from src.managers.unified_logging_definitions import get_epoch_millis, epoch_millis_to_human


def add_epoch_timestamp(logger, method_name, event_dict):
    """Add epoch milliseconds timestamp to log events."""
    event_dict['timestamp_millis'] = get_epoch_millis()
    event_dict['timestamp_human'] = epoch_millis_to_human(event_dict['timestamp_millis'])
    return event_dict


def add_context(logger, method_name, event_dict):
    """Add automatic context information."""
    import inspect
    
    # Get calling frame info
    frame = inspect.currentframe()
    try:
        # Go up the stack to find the actual caller (skip structlog internals)
        for _ in range(10):  # Max 10 levels to avoid infinite loop
            frame = frame.f_back
            if frame is None:
                break
            
            filename = frame.f_code.co_filename
            function_name = frame.f_code.co_name
            
            # Skip structlog internal frames
            if 'structlog' not in filename and '_log' not in function_name:
                module_name = frame.f_globals.get('__name__', 'unknown')
                event_dict['module'] = module_name.split('.')[-1]  # Just the module name
                event_dict['function'] = function_name
                event_dict['line'] = frame.f_lineno
                break
    finally:
        del frame
    
    return event_dict


class LocalTimeConsoleRenderer:
    """Console renderer that shows local time for human readability."""
    
    def __call__(self, logger, method_name, event_dict):
        timestamp_human = event_dict.pop('timestamp_human', '')
        timestamp_millis = event_dict.pop('timestamp_millis', '')
        module = event_dict.pop('module', '')
        function = event_dict.pop('function', '')
        line = event_dict.pop('line', '')
        level = method_name.upper()
        
        # Get the main event message
        event = event_dict.pop('event', '')
        
        # Format context
        context = f"{module}.{function}:{line}" if module and function else ""
        
        # Format extra data
        extra = ""
        if event_dict:
            extra_parts = [f"{k}={v}" for k, v in event_dict.items()]
            extra = " | " + " ".join(extra_parts)
        
        # Main log line - clean format
        log_line = f"[{timestamp_millis}] {timestamp_human} - [{context}] {level} - {event}{extra}"
        
        return log_line


class EpochMillisJSONRenderer:
    """JSON renderer that includes epoch milliseconds."""
    
    def __call__(self, logger, method_name, event_dict):
        # Ensure we have standard fields
        if 'timestamp_millis' not in event_dict:
            event_dict['timestamp_millis'] = get_epoch_millis()
        
        event_dict['level'] = method_name.upper()
        
        import json
        return json.dumps(event_dict)


def setup_structlog(console_output: bool = True, json_output: bool = False):
    """
    Setup structlog with automatic context binding and epoch milliseconds.
    
    Args:
        console_output: Enable human-readable console output
        json_output: Enable JSON structured output
    """
    processors = [
        # Add context automatically
        add_context,
        # Add epoch timestamps
        add_epoch_timestamp,
        # Standard structlog processors
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if console_output:
        processors.append(LocalTimeConsoleRenderer())
    elif json_output:
        processors.append(EpochMillisJSONRenderer())
    else:
        # Default to console
        processors.append(LocalTimeConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # Setup standard logging
    logging.basicConfig(
        format="%(message)s",
        level=logging.DEBUG,
    )


# Convenience functions for getting loggers
def get_logger(name: str = None):
    """Get a structlog logger with automatic context binding."""
    if name is None:
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get('__name__', 'unknown')
    
    return structlog.get_logger(name)


# Quick setup function
def quick_setup():
    """Quickly setup structlog for simple usage."""
    setup_structlog(console_output=True)
    return get_logger() 