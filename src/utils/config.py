"""
Centralized configuration management for tower_hooker.
This module provides a single configuration class with proper validation and error handling.
"""

import os
import yaml
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""
    pass


class LogLevel(str, Enum):
    """Valid logging levels - standardized across entire application."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    
    def __str__(self) -> str:
        return self.value


class TimestampFormat(str, Enum):
    """Valid timestamp formats."""
    LOCAL = "local"
    UTC = "utc"
    ISO = "iso"
    EPOCH = "epoch"
    EPOCH_MILLIS = "epoch_millis"  # Epoch milliseconds - standard for all logs
    NONE = "none"
    
    def __str__(self) -> str:
        return self.value


class Timezone(str, Enum):
    """Valid timezone settings."""
    LOCAL = "local"
    UTC = "utc"
    
    def __str__(self) -> str:
        return self.value


@dataclass
class TowerHookerConfig:
    """Centralized configuration for tower_hooker application."""
    
    # File Paths
    hook_script_path: str = ""
    frida_server_dir: str = ""
    bluestacks_adb_path: str = ""
    log_file_path: str = "logs/tower_hooker.log"
    db_schema_file: str = ""
    
    # Target Application
    default_target_package: str = "com.TechTreeGames.TheTower"
    
    # Frida Settings
    frida_server_version: str = "16.7.19"
    frida_server_arch: str = "x86_64"
    frida_server_remote_path: str = "/data/local/tmp/frida-server"
    
    # Logging Settings - Always use epoch milliseconds for consistency
    log_level: LogLevel = LogLevel.INFO
    log_bind_context: bool = True
    log_console_timestamp_format: TimestampFormat = TimestampFormat.EPOCH_MILLIS  # Standard for all logs
    log_structured_timestamp_format: TimestampFormat = TimestampFormat.EPOCH_MILLIS  # Standard for all logs
    log_console_timezone: Timezone = Timezone.UTC  # UTC for consistency with epoch
    log_structured_timezone: Timezone = Timezone.UTC
    
    # Logging Output Controls
    log_to_console: bool = True
    log_to_file: bool = False
    log_to_database: bool = True
    log_file_max_size_mb: int = 10
    log_file_backup_count: int = 5
    
    # Service URLs
    influxdb_url: str = "http://localhost:8086"
    influxdb_token: Optional[str] = None
    influxdb_org: str = "tower_hooker"
    loki_url: str = "http://localhost:3100/loki/api/v1/push"
    
    # Database Schema
    db_schema_validation_enabled: bool = True
    db_schema_strict_mode: bool = False
    
    # Rich Context Logging
    log_rich_context: bool = False
    log_show_logger_names: bool = False
    log_capture_locals: bool = False
    log_capture_function_args: bool = False
    log_capture_stack_trace: bool = False
    log_max_local_vars: int = 10
    log_max_string_length: int = 200
    log_max_stack_frames: int = 10
    
    # Background Data Collection
    enable_logcat_logging: bool = True
    enable_pslist_logging: bool = True
    
    # Console Log Filters
    console_log_filters: Dict[str, bool] = field(default_factory=lambda: {
        'PSLIST_PROCESSES': False,
        'PSLIST_SUMMARY': True,
        'INFRASTRUCTURE_SETUP': True,
        'DATABASE_OPERATIONS': True,
        'APPLICATION_STARTUP': True,
        'APPLICATION_SHUTDOWN': True,
        'BLUESTACKS_CONNECTION': True,
        'FRIDA_OPERATIONS': True,
        'MONITORING_STATUS': True,
        'ERRORS_AND_WARNINGS': True,
        'DEVELOPMENT_DEBUG': False
    })
    
    @classmethod
    def from_env_and_yaml(cls, yaml_path: Optional[str] = None) -> 'TowerHookerConfig':
        """
        Load configuration from environment variables and YAML file.
        
        Args:
            yaml_path: Optional path to YAML config file
            
        Returns:
            Configured TowerHookerConfig instance
            
        Raises:
            ConfigurationError: If configuration is invalid
        """
        config = cls()
        
        try:
            # Load from YAML first
            if yaml_path:
                config._load_from_yaml(yaml_path)
            else:
                config._load_from_default_yaml()
            
            # Override with environment variables
            config._load_from_environment()
            
            # Resolve and validate paths
            config._resolve_paths()
            config._validate()
            
            logger.info("Configuration loaded successfully")
            return config
            
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration: {e}") from e
    
    def _load_from_yaml(self, yaml_path: str) -> None:
        """Load configuration from YAML file."""
        if not os.path.exists(yaml_path):
            logger.warning(f"YAML config file not found: {yaml_path}")
            return
        
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f) or {}
            
            # Map YAML values to dataclass fields
            self._apply_yaml_config(yaml_config)
            
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML syntax in {yaml_path}: {e}")
        except Exception as e:
            raise ConfigurationError(f"Error reading {yaml_path}: {e}")
    
    def _load_from_default_yaml(self) -> None:
        """Load from default YAML configuration path."""
        project_root = self._get_project_root()
        default_path = os.path.join(project_root, "config", "main_config.yaml")
        self._load_from_yaml(default_path)
    
    def _apply_yaml_config(self, yaml_config: Dict[str, Any]) -> None:
        """Apply YAML configuration to dataclass fields."""
        # Direct mappings
        mappings = {
            'HOOK_SCRIPT_PATH': 'hook_script_path',
            'LOG_BIND_CONTEXT': 'log_bind_context',
            'LOG_CONSOLE_TIMESTAMP_FORMAT': 'log_console_timestamp_format',
            'LOG_STRUCTURED_TIMESTAMP_FORMAT': 'log_structured_timestamp_format',
            'LOG_CONSOLE_TIMEZONE': 'log_console_timezone',
            'LOG_STRUCTURED_TIMEZONE': 'log_structured_timezone',
            'LOG_TO_CONSOLE': 'log_to_console',
            'LOG_TO_FILE': 'log_to_file',
            'LOG_TO_DATABASE': 'log_to_database',
            'LOG_FILE_MAX_SIZE_MB': 'log_file_max_size_mb',
            'LOG_FILE_BACKUP_COUNT': 'log_file_backup_count',
            'DB_SCHEMA_FILE': 'db_schema_file',
            'LOG_RICH_CONTEXT': 'log_rich_context',
            'LOG_SHOW_LOGGER_NAMES': 'log_show_logger_names',
            'LOG_CAPTURE_LOCALS': 'log_capture_locals',
            'LOG_CAPTURE_FUNCTION_ARGS': 'log_capture_function_args',
            'LOG_CAPTURE_STACK_TRACE': 'log_capture_stack_trace',
            'LOG_MAX_LOCAL_VARS': 'log_max_local_vars',
            'LOG_MAX_STRING_LENGTH': 'log_max_string_length',
            'LOG_MAX_STACK_FRAMES': 'log_max_stack_frames',
            'CONSOLE_LOG_FILTERS': 'console_log_filters'
        }
        
        for yaml_key, attr_name in mappings.items():
            if yaml_key in yaml_config:
                setattr(self, attr_name, yaml_config[yaml_key])
        
        # Handle nested logging configuration
        logging_config = yaml_config.get('logging', {})
        if logging_config:
            background_collection = logging_config.get('background_collection', {})
            self.enable_logcat_logging = background_collection.get('enable_logcat', True)
            self.enable_pslist_logging = background_collection.get('enable_pslist', True)
    
    def _load_from_environment(self) -> None:
        """Load configuration from environment variables."""
        env_mappings = [
            ('LOG_LEVEL', 'log_level', lambda x: LogLevel(x.upper())),
            ('BLUESTACKS_ADB_PATH', 'bluestacks_adb_path', str),
            ('FRIDA_SERVER_VERSION', 'frida_server_version', str),
            ('FRIDA_SERVER_ARCH', 'frida_server_arch', str),
            ('INFLUXDB_TOKEN', 'influxdb_token', str),
            ('INFLUXDB_ORG', 'influxdb_org', str),
            ('DB_SCHEMA_VALIDATION_ENABLED', 'db_schema_validation_enabled', 
             lambda x: x.lower() == 'true'),
            ('DB_SCHEMA_STRICT_MODE', 'db_schema_strict_mode', 
             lambda x: x.lower() == 'true'),
        ]
        
        for env_key, attr_name, converter in env_mappings:
            env_value = os.environ.get(env_key)
            if env_value is not None:
                try:
                    setattr(self, attr_name, converter(env_value))
                except (ValueError, KeyError) as e:
                    logger.warning(f"Invalid environment value for {env_key}: {env_value}")
    
    def _resolve_paths(self) -> None:
        """Resolve relative paths to absolute paths."""
        project_root = self._get_project_root()
        
        # Default paths if not set
        if not self.hook_script_path:
            self.hook_script_path = os.path.join(project_root, 'src/scripts/test_hook_script.js')
        elif not os.path.isabs(self.hook_script_path):
            self.hook_script_path = os.path.join(project_root, self.hook_script_path)
        
        if not self.frida_server_dir:
            self.frida_server_dir = os.path.join(project_root, 'resources/frida-server')
        elif not os.path.isabs(self.frida_server_dir):
            self.frida_server_dir = os.path.join(project_root, self.frida_server_dir)
        
        if not self.bluestacks_adb_path:
            default_path = os.path.join(
                os.environ.get("ProgramFiles", "C:\\Program Files"),
                "BlueStacks_nxt", "HD-Adb.exe"
            )
            self.bluestacks_adb_path = default_path
        elif not os.path.isabs(self.bluestacks_adb_path):
            self.bluestacks_adb_path = os.path.join(project_root, self.bluestacks_adb_path)
        
        if self.db_schema_file and not os.path.isabs(self.db_schema_file):
            self.db_schema_file = os.path.join(project_root, self.db_schema_file)
        elif not self.db_schema_file:
            self.db_schema_file = os.path.join(project_root, 'config/db_schema/db_schema_minimal.yaml')
    
    def _validate(self) -> None:
        """Validate configuration values."""
        # Critical file validation
        if not os.path.exists(self.hook_script_path):
            raise ConfigurationError(f"Hook script not found: {self.hook_script_path}")
        
        # Numeric validations
        assert self.log_file_max_size_mb > 0, "Log file size must be positive"
        assert self.log_file_backup_count >= 0, "Backup count cannot be negative"
        assert self.log_max_local_vars > 0, "Max local vars must be positive"
        assert self.log_max_string_length > 0, "Max string length must be positive"
        assert self.log_max_stack_frames > 0, "Max stack frames must be positive"
        
        # Required environment variables
        if not self.influxdb_token:
            logger.warning("INFLUXDB_TOKEN not set - InfluxDB features may not work")
        
        # Configuration consistency checks
        self._validate_logging_consistency()
        
        logger.info("Configuration validation complete")
    
    def _validate_logging_consistency(self) -> None:
        """Check for logical conflicts in logging configuration."""
        if not self.enable_pslist_logging and self.console_log_filters.get('PSLIST_PROCESSES', False):
            logger.warning(
                "Config conflict: PSLIST collection disabled but console filter enabled. "
                "No PSLIST logs will be generated."
            )
        
        if not self.enable_logcat_logging and self.console_log_filters.get('LOGCAT', False):
            logger.warning(
                "Config conflict: LOGCAT collection disabled but console filter enabled. "
                "No LOGCAT logs will be generated."
            )
    
    def _get_project_root(self) -> str:
        """Get the project root directory."""
        current_dir = Path(__file__).parent
        
        # Walk up until we find main.py
        while current_dir != current_dir.parent:
            if (current_dir / "main.py").exists():
                return str(current_dir)
            current_dir = current_dir.parent
        
        # Fallback: assume we're in src/utils
        return str(Path(__file__).parent.parent.parent)
    
    def should_show_in_console(self, filter_type: str) -> bool:
        """Check if a log type should be shown in console."""
        return self.console_log_filters.get(filter_type, True)
    
    def get_attribute(self, attr_name: str, default: Any = None) -> Any:
        """Generic getter for any configuration attribute."""
        return getattr(self, attr_name, default)
    
    def get_logging_standards(self) -> Dict[str, Any]:
        """
        Get standardized logging configuration for the entire application.
        
        Returns:
            Dictionary with standardized logging settings
        """
        return {
            'timestamp_format': 'epoch_millis',
            'valid_levels': [level.value for level in LogLevel],
            'default_level': self.log_level.value,
            'timezone': 'UTC',
            'epoch_millis_example': get_epoch_millis(),
            'human_readable_example': epoch_millis_to_human(get_epoch_millis())
        }
    
    def validate_log_level(self, level: str) -> LogLevel:
        """
        Validate and convert a log level string to LogLevel enum.
        
        Args:
            level: Log level string to validate
            
        Returns:
            LogLevel enum value
            
        Raises:
            ConfigurationError: If log level is invalid
        """
        try:
            return LogLevel(level.upper())
        except ValueError:
            valid_levels = [level.value for level in LogLevel]
            raise ConfigurationError(
                f"Invalid log level '{level}'. Must be one of: {valid_levels}"
            )
    
    # Backward compatibility methods
    def get_influxdb_config(self) -> Dict[str, Any]:
        """Get InfluxDB configuration for backward compatibility."""
        from .config_loader import load_influxdb_config
        influxdb_config = load_influxdb_config()
        
        return {
            'url': influxdb_config.get('url', self.influxdb_url),
            'token': influxdb_config.get('token', self.influxdb_token),
            'org': influxdb_config.get('org', self.influxdb_org),
            'bucket_data': influxdb_config.get('bucket', 'metrics'),
            'enabled': influxdb_config.get('enabled', True),
        }
    
    def get_loki_config(self) -> Dict[str, Any]:
        """Get Loki configuration for backward compatibility."""
        from .config_loader import load_loki_config
        loki_config = load_loki_config()
        
        return {
            'url': loki_config.get('url', self.loki_url),
            'enabled': loki_config.get('enabled', True),
            'default_labels': loki_config.get('default_labels', {}),
        }


# Global configuration instance
_config: Optional[TowerHookerConfig] = None


def initialize_config(yaml_path: Optional[str] = None) -> TowerHookerConfig:
    """
    Initialize global configuration.
    
    Args:
        yaml_path: Optional path to YAML config file
        
    Returns:
        Configured TowerHookerConfig instance
    """
    global _config
    _config = TowerHookerConfig.from_env_and_yaml(yaml_path)
    return _config


def get_config() -> TowerHookerConfig:
    """
    Get the global configuration instance.
    
    Returns:
        TowerHookerConfig instance
        
    Raises:
        ConfigurationError: If configuration not initialized
    """
    if _config is None:
        raise ConfigurationError("Configuration not initialized. Call initialize_config() first.")
    return _config


def ensure_initialized() -> TowerHookerConfig:
    """Ensure configuration is initialized, initialize if needed."""
    global _config
    if _config is None:
        _config = TowerHookerConfig.from_env_and_yaml()
    return _config


# Convenience functions for common access patterns
def get_project_root() -> str:
    """Get the project root directory."""
    return ensure_initialized()._get_project_root()


def reload_config(yaml_path: Optional[str] = None) -> TowerHookerConfig:
    """Force reload configuration."""
    global _config
    _config = None
    return initialize_config(yaml_path)


def get_epoch_millis() -> int:
    """Get current timestamp as epoch milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def epoch_millis_to_human(epoch_millis: int, format_str: str = "%Y-%m-%d %H:%M:%S.%f UTC") -> str:
    """
    Convert epoch milliseconds to human-readable format.
    
    Args:
        epoch_millis: Timestamp in epoch milliseconds
        format_str: Format string for datetime formatting
        
    Returns:
        Human-readable timestamp string
    """
    dt = datetime.fromtimestamp(epoch_millis / 1000.0, tz=timezone.utc)
    return dt.strftime(format_str)[:-3] + " UTC"  # Trim microseconds to milliseconds


def epoch_millis_to_iso(epoch_millis: int) -> str:
    """Convert epoch milliseconds to ISO format."""
    dt = datetime.fromtimestamp(epoch_millis / 1000.0, tz=timezone.utc)
    return dt.isoformat()


def epoch_millis_to_local(epoch_millis: int) -> str:
    """Convert epoch milliseconds to local timezone format."""
    dt = datetime.fromtimestamp(epoch_millis / 1000.0)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Trim to milliseconds