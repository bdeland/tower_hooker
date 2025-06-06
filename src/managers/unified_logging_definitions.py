from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional


class LogSource(Enum):
    MAIN_APP = "th_main_app"
    FRIDA = "th_frida"
    BLUESTACKS = "th_bluestacks"  # Kept for backward compatibility
    EMULATOR = "th_emulator"  # Generic emulator source
    PSLIST = "th_pslist"
    LOGCAT = "th_logcat"
    DATABASE = "th_database"
    SYSTEM = "th_system"  # For internal logging system messages
    FALLBACK_SYSTEM = "th_fallback_system"  # For FallbackLogger messages


class LogLevel(Enum):
    """Standardized logging levels for entire application."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def get_epoch_millis() -> int:
    """Get current timestamp as epoch milliseconds - standard for all logs."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def epoch_millis_to_human(epoch_millis: int, format_str: str = "%Y-%m-%d %H:%M:%S.%f") -> str:
    """
    Convert epoch milliseconds to human-readable local time for display.
    
    Args:
        epoch_millis: Timestamp in epoch milliseconds
        format_str: Format string for datetime formatting
        
    Returns:
        Human-readable timestamp string in local time
    """
    dt = datetime.fromtimestamp(epoch_millis / 1000.0)  # Local time, not UTC
    formatted = dt.strftime(format_str)
    # Trim microseconds to milliseconds if %f is in format
    if "%f" in format_str:
        formatted = formatted[:-3]  # Remove last 3 digits for milliseconds
    return formatted


def epoch_millis_to_iso(epoch_millis: int) -> str:
    """Convert epoch milliseconds to ISO format."""
    dt = datetime.fromtimestamp(epoch_millis / 1000.0, tz=timezone.utc)
    return dt.isoformat()


def epoch_millis_to_local(epoch_millis: int) -> str:
    """Convert epoch milliseconds to local timezone format (same as epoch_millis_to_human)."""
    return epoch_millis_to_human(epoch_millis)


@dataclass
class LogEntry:
    """
    Simplified log entry for tower_hooker application with structlog context binding.
    Always uses epoch milliseconds for consistent timestamp handling.
    Context (module, function, etc.) is automatically bound by structlog.
    """
    level: LogLevel
    message: str
    extra_data: Dict[str, Any] = field(default_factory=dict)
    timestamp_millis: int = field(default_factory=get_epoch_millis)
    
    @property
    def timestamp(self) -> datetime:
        """Convert timestamp_millis to datetime for backward compatibility."""
        return datetime.fromtimestamp(self.timestamp_millis / 1000.0, tz=timezone.utc)
    
    @property
    def timestamp_human(self) -> str:
        """Get human-readable timestamp for display."""
        return epoch_millis_to_human(self.timestamp_millis)
    
    @property
    def timestamp_iso(self) -> str:
        """Get ISO format timestamp."""
        return epoch_millis_to_iso(self.timestamp_millis) 