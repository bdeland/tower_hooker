"""
Configuration loaders for external service configurations.
Handles loading InfluxDB, Loki, and environment configurations with proper error handling.
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ConfigLoaderError(Exception):
    """Raised when configuration loading fails."""
    pass


def load_influxdb_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load InfluxDB configuration from dedicated influxdb-config.yaml file.
    
    Args:
        config_path: Optional path to influxdb-config.yaml. 
                    Defaults to config/influxdb/influxdb-config.yaml.
        
    Returns:
        Dictionary containing InfluxDB configuration.
        
    Raises:
        ConfigLoaderError: If configuration cannot be loaded
    """
    if config_path is None:
        config_path = "config/influxdb/influxdb-config.yaml"
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file) or {}
                
            # Extract connection settings if they exist
            connection_config = config.get('connection', {})
            
            return {
                'enabled': connection_config.get('enabled', True),
                'bucket': connection_config.get('bucket', 'metrics'),
                'org': connection_config.get('org', 'tower_hooker'),
                'url': os.getenv("INFLUXDB_URL", "http://localhost:8086"),
                'token': os.getenv("INFLUXDB_TOKEN"),
            }
        else:
            logger.info(f"InfluxDB config file not found at {config_path}, using environment defaults")
            return _get_default_influxdb_config()
            
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in InfluxDB config {config_path}: {e}")
        raise ConfigLoaderError(f"Invalid YAML syntax in {config_path}: {e}")
    except Exception as e:
        logger.error(f"Error loading InfluxDB config from {config_path}: {e}")
        return _get_default_influxdb_config()


def _get_default_influxdb_config() -> Dict[str, Any]:
    """Get default InfluxDB configuration from environment variables."""
    return {
        'enabled': True,
        'bucket': os.getenv("INFLUXDB_BUCKET", "metrics"),
        'org': os.getenv("INFLUXDB_ORG", "tower_hooker"),
        'url': os.getenv("INFLUXDB_URL", "http://localhost:8086"),
        'token': os.getenv("INFLUXDB_TOKEN"),
    }


def load_loki_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load Loki configuration from dedicated loki-config.yml file.
    
    Args:
        config_path: Optional path to loki-config.yml. 
                    Defaults to config/loki/loki-config.yml.
        
    Returns:
        Dictionary containing Loki configuration.
        
    Raises:
        ConfigLoaderError: If configuration cannot be loaded
    """
    if config_path is None:
        config_path = "config/loki/loki-config.yml"
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file) or {}
            
            # Extract server settings
            server_config = config.get('server', {})
            http_port = server_config.get('http_listen_port', 3100)
            
            # Build Loki URL from config
            loki_url = f"http://localhost:{http_port}/loki/api/v1/push"
            
            # Override with environment variable if set
            env_loki_url = os.getenv("LOKI_URL")
            if env_loki_url:
                loki_url = env_loki_url
            
            return {
                'enabled': True,
                'url': loki_url,
                'default_labels': _get_default_loki_labels()
            }
        else:
            logger.info(f"Loki config file not found at {config_path}, using environment defaults")
            return _get_default_loki_config()
            
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in Loki config {config_path}: {e}")
        raise ConfigLoaderError(f"Invalid YAML syntax in {config_path}: {e}")
    except Exception as e:
        logger.error(f"Error loading Loki config from {config_path}: {e}")
        return _get_default_loki_config()


def _get_default_loki_config() -> Dict[str, Any]:
    """Get default Loki configuration from environment variables."""
    return {
        'enabled': True,
        'url': os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push"),
        'default_labels': _get_default_loki_labels()
    }


def _get_default_loki_labels() -> Dict[str, str]:
    """Get default labels for Loki logging."""
    return {
        'application': 'tower_hooker',
        'environment': 'development',
        'job': 'tower_hooker'
    }


def load_env_config(env_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load environment variables from a .env file and return relevant configuration values.
    
    Args:
        env_path: Optional path to .env file. Defaults to ".env" in current directory.
        
    Returns:
        Dictionary containing configuration values from environment variables.
    """
    try:
        # Load the .env file - override=True ensures file values take precedence
        if env_path:
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)
        
        # Extract relevant environment variables
        config = {}
        
        # Environment variable mappings with type conversion
        env_mappings = [
            ("LOG_LEVEL", "LOG_LEVEL", lambda x: x.upper()),
            ("ENABLE_CONSOLE_LOGGING", "ENABLE_CONSOLE_LOGGING", _parse_bool),
            ("TEST_LOKI_URL", "TEST_LOKI_URL", str),
            ("APP_LOG_LEVEL", "APP_LOG_LEVEL", lambda x: x.upper()),
        ]
        
        for env_key, config_key, converter in env_mappings:
            env_value = os.getenv(env_key)
            if env_value is not None:
                try:
                    config[config_key] = converter(env_value)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid environment value for {env_key}: {env_value}")
        
        return config
        
    except Exception as e:
        logger.error(f"Error loading environment configuration: {e}")
        return {}


def _parse_bool(value: str) -> bool:
    """Parse string value to boolean."""
    return value.lower() in ("true", "1", "yes", "on")


def load_yaml_config(filepath: str) -> Dict[str, Any]:
    """
    Load configuration from a YAML file with proper error handling.
    
    Args:
        filepath: Path to the YAML configuration file.
        
    Returns:
        Dictionary containing the parsed YAML configuration.
        
    Raises:
        ConfigLoaderError: If the YAML file doesn't exist or is invalid.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
            return config if config is not None else {}
    except FileNotFoundError:
        raise ConfigLoaderError(f"YAML configuration file not found: {filepath}")
    except yaml.YAMLError as e:
        raise ConfigLoaderError(f"Invalid YAML in configuration file {filepath}: {e}")
    except Exception as e:
        raise ConfigLoaderError(f"Error reading configuration file {filepath}: {e}")


def load_app_config(yaml_path: str, env_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load and merge application configuration from YAML and environment files.
    
    Environment variables take precedence over YAML values when there are conflicts.
    
    Args:
        yaml_path: Path to the YAML configuration file.
        env_path: Optional path to .env file.
        
    Returns:
        Dictionary containing merged configuration values formatted for UnifiedLoggingManager.
        
    Raises:
        ConfigLoaderError: If configuration cannot be loaded
    """
    try:
        # Load base configuration from YAML
        yaml_config = load_yaml_config(yaml_path)
        
        # Load environment configuration
        env_config = load_env_config(env_path)
        
        # Load dedicated service configurations
        influxdb_config = load_influxdb_config()
        loki_config = load_loki_config()
        
        # Merge configurations with precedence: env > yaml > defaults
        merged_config = _merge_configurations(yaml_config, env_config, influxdb_config, loki_config)
        
        # Transform to UnifiedLoggingManager format
        ulm_config = _transform_to_ulm_format(merged_config, env_config)
        
        logger.info("Application configuration loaded and merged successfully")
        return ulm_config
        
    except Exception as e:
        logger.error(f"Failed to load application configuration: {e}")
        raise ConfigLoaderError(f"Failed to load application configuration: {e}") from e


def _merge_configurations(
    yaml_config: Dict[str, Any], 
    env_config: Dict[str, Any],
    influxdb_config: Dict[str, Any], 
    loki_config: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge all configuration sources with proper precedence."""
    merged_config = yaml_config.copy()
    
    # Apply environment variable overrides with specific mapping
    if "TEST_LOKI_URL" in env_config:
        merged_config["loki_url"] = env_config["TEST_LOKI_URL"]
    else:
        merged_config["loki_url"] = loki_config.get("url")
        
    if "APP_LOG_LEVEL" in env_config:
        merged_config["log_level"] = env_config["APP_LOG_LEVEL"]
    elif "LOG_LEVEL" in env_config:
        merged_config["log_level"] = env_config["LOG_LEVEL"]
        
    if "ENABLE_CONSOLE_LOGGING" in env_config:
        merged_config["console_enabled"] = env_config["ENABLE_CONSOLE_LOGGING"]
    
    # Apply service configurations
    merged_config.update({
        "influxdb_url": influxdb_config.get("url"),
        "influxdb_token": influxdb_config.get("token"),
        "influxdb_org": influxdb_config.get("org"),
        "influxdb_bucket": influxdb_config.get("bucket"),
        "_env_vars": env_config,
        "_influxdb_config": influxdb_config,
        "_loki_config": loki_config,
    })
    
    return merged_config


def _transform_to_ulm_format(merged_config: Dict[str, Any], env_config: Dict[str, Any]) -> Dict[str, Any]:
    """Transform merged config into UnifiedLoggingManager format."""
    ulm_config = {}
    
    # Extract console settings
    console_config = merged_config.get("logging", {}).get("console", {})
    ulm_config["logging_console_enabled"] = console_config.get("enabled", True)
    
    # Priority order for console min level
    console_min_level = (
        env_config.get("APP_LOG_LEVEL") or 
        console_config.get("log_level") or 
        env_config.get("LOG_LEVEL") or 
        "INFO"
    )
    ulm_config["logging_console_min_level_str"] = console_min_level
    ulm_config["logging_console_filters"] = console_config.get("filters", {})
    
    # File fallback settings
    file_fallback_config = merged_config.get("logging", {}).get("file_fallback", {})
    ulm_config.update({
        "logging_file_fallback_emergency_log_path": file_fallback_config.get("emergency_log_path"),
        "logging_file_fallback_loki_failure_log_path": file_fallback_config.get("loki_failure_log_path"),
        "logging_file_fallback_max_bytes": int(file_fallback_config.get("max_size_mb", 5) * 1024 * 1024),
        "logging_file_fallback_backup_count": file_fallback_config.get("backup_count", 2),
    })
    
    # Service settings
    loki_config = merged_config["_loki_config"]
    influxdb_config = merged_config["_influxdb_config"]
    
    ulm_config.update({
        "enable_loki": loki_config.get("enabled", True),
        "loki_url": merged_config.get("loki_url"),
        "loki_default_labels": loki_config.get("default_labels", {}),
        "enable_influxdb": influxdb_config.get("enabled", True),
        "influxdb_url": merged_config.get("influxdb_url"),
        "influxdb_token": merged_config.get("influxdb_token"),
        "influxdb_org": merged_config.get("influxdb_org"),
        "influxdb_bucket": merged_config.get("influxdb_bucket"),
    })
    
    return ulm_config 