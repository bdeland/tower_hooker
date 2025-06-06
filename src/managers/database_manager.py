from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, ASYNCHRONOUS
import os
import json
import yaml
from typing import Any, List, Dict, Optional, Union
from fastapi.concurrency import run_in_threadpool # For async context
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from dateutil.parser import parse as dateutil_parse # Added import
from src.utils.config import get_influxdb_config, get_schema_config
from src.managers.unified_logging_manager_v2 import log_info, log_error, log_warning, log_debug, log_critical, LogSource

class SchemaLoader:
    """
    Loads and parses the database schema from YAML files.
    Provides methods to access schema information for database initialization.
    """
    
    def __init__(self, schema_file_path: Optional[str] = None):
        """
        Initialize the schema loader.
        
        Args:
            schema_file_path: Path to the schema YAML file. If None, uses default location.
        """
        if schema_file_path is None:
            # Default to config/db_schema/db_schema_minimal.yaml relative to project root
            project_root = Path(__file__).parent.parent.parent  # Go up from src/managers/
            schema_file_path = project_root / "config" / "db_schema" / "db_schema_minimal.yaml"
        
        self.schema_file_path = Path(schema_file_path)
        self.schema_data: Optional[Dict[str, Any]] = None
        self._load_schema()
    
    def _load_schema(self):
        """Load the schema from the YAML file."""
        try:
            if not self.schema_file_path.exists():
                raise FileNotFoundError(f"Schema file not found: {self.schema_file_path}")
            
            with open(self.schema_file_path, 'r', encoding='utf-8') as file:
                self.schema_data = yaml.safe_load(file)
            
            _log_with_context_local("info", 
                f"Successfully loaded database schema from {self.schema_file_path}",
                schema_version=self.schema_data.get('schema_version'),
                measurements_count=len(self.schema_data.get('measurements', {}))
            )
            
        except Exception as e:
            _log_with_context_local("error", 
                f"Failed to load schema from {self.schema_file_path}: {e}"
            )
            raise
    
    def get_measurements(self) -> Dict[str, Any]:
        """Get all measurements defined in the schema."""
        if not self.schema_data:
            return {}
        return self.schema_data.get('measurements', {})
    
    def get_measurement_names(self) -> List[str]:
        """Get list of all measurement names defined in the schema."""
        return list(self.get_measurements().keys())
    
    def get_measurement_info(self, measurement_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific measurement."""
        measurements = self.get_measurements()
        return measurements.get(measurement_name)
    
    def get_measurement_tags(self, measurement_name: str) -> Dict[str, Any]:
        """Get tag definitions for a specific measurement."""
        measurement_info = self.get_measurement_info(measurement_name)
        if not measurement_info:
            return {}
        return measurement_info.get('tags', {})
    
    def get_measurement_fields(self, measurement_name: str) -> Dict[str, Any]:
        """Get field definitions for a specific measurement."""
        measurement_info = self.get_measurement_info(measurement_name)
        if not measurement_info:
            return {}
        return measurement_info.get('fields', {})
    
    def validate_data_point(self, measurement_name: str, tags: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        Validate a data point against the schema.
        
        Returns:
            Dict with 'errors' and 'warnings' lists
        """
        errors = []
        warnings = []
        
        measurement_info = self.get_measurement_info(measurement_name)
        if not measurement_info:
            errors.append(f"Measurement '{measurement_name}' not defined in schema")
            return {'errors': errors, 'warnings': warnings}
        
        schema_tags = self.get_measurement_tags(measurement_name)
        schema_fields = self.get_measurement_fields(measurement_name)
        
        # Check required tags
        for tag_name, tag_def in schema_tags.items():
            if not tag_def.get('nullable', True) and tag_name not in tags:
                errors.append(f"Required tag '{tag_name}' missing for measurement '{measurement_name}'")
        
        # Check required fields
        for field_name, field_def in schema_fields.items():
            if not field_def.get('nullable', True) and field_name not in fields:
                errors.append(f"Required field '{field_name}' missing for measurement '{measurement_name}'")
        
        # Check for unexpected tags/fields
        for tag_name in tags:
            if tag_name not in schema_tags:
                warnings.append(f"Tag '{tag_name}' not defined in schema for measurement '{measurement_name}'")
        
        for field_name in fields:
            if field_name not in schema_fields:
                warnings.append(f"Field '{field_name}' not defined in schema for measurement '{measurement_name}'")
        
        return {'errors': errors, 'warnings': warnings}
    
    def get_schema_info(self) -> Dict[str, Any]:
        """Get general schema information."""
        if not self.schema_data:
            return {}
        
        return {
            'schema_version': self.schema_data.get('schema_version'),
            'schema_release_date': self.schema_data.get('schema_release_date'),
            'description': self.schema_data.get('description'),
            'influxdb_version_compatibility': self.schema_data.get('influxdb_version_compatibility'),
            'measurements': list(self.get_measurements().keys())
        }

def _log_with_context_local(log_level_str: str, message: str, **context_kwargs):
    """
    Local helper function to log using unified logging system.
    Maps old logger method calls to unified logging calls.
    """
    try:
        # Map the log level to appropriate unified logging function
        if log_level_str == "info":
            log_info(LogSource.DATABASE, message, **context_kwargs)
        elif log_level_str == "error":
            log_error(LogSource.DATABASE, message, **context_kwargs)
        elif log_level_str == "warning":
            log_warning(LogSource.DATABASE, message, **context_kwargs)
        elif log_level_str == "debug":
            log_debug(LogSource.DATABASE, message, **context_kwargs)
        elif log_level_str == "critical":
            log_critical(LogSource.DATABASE, message, **context_kwargs)
        else:
            # Default to info if unknown level
            log_info(LogSource.DATABASE, message, **context_kwargs)
    except Exception as e:
        # Fallback to basic print if unified logging fails
        print(f"[{log_level_str.upper()}] {message} (context: {context_kwargs})")
        print(f"Logging error: {e}")

class DynamicMeasurement:
    """
    Dynamic measurement class that replaces the hardcoded enum.
    Measurements are loaded from the schema file.
    """
    
    def __init__(self, name: str):
        self.value = name
        self.name = name.upper()
    
    def __str__(self):
        return self.value
    
    def __repr__(self):
        return f"DynamicMeasurement('{self.value}')"

class InfluxMeasurement:
    """
    Dynamic measurement container that loads measurements from schema.
    Provides both schema-based and legacy measurements for backward compatibility.
    """
    
    # Legacy measurements (for backward compatibility)
    GAME_ROUNDS = DynamicMeasurement("game_rounds")
    ROUND_EVENTS = DynamicMeasurement("round_events")
    ROUND_SNAPSHOTS = DynamicMeasurement("round_snapshots")
    SCRIPT_LOGS = DynamicMeasurement("script_logs")
    AWX_EVENTS = DynamicMeasurement("awx_events")
    GENERAL_LOGS = DynamicMeasurement("general_logs")  # For db_logging_handler
    LOGCAT_LOGS = DynamicMeasurement("logcat_logs")    # For Android logcat
    PSLIST_LOGS = DynamicMeasurement("pslist_logs")    # For process list snapshots
    
    def __init__(self, schema_loader: Optional[SchemaLoader] = None):
        """Initialize with schema-based measurements."""
        self._schema_measurements = {}
        if schema_loader:
            self._load_schema_measurements(schema_loader)
    
    def _load_schema_measurements(self, schema_loader: SchemaLoader):
        """Load measurements from schema and create dynamic measurement objects."""
        measurement_names = schema_loader.get_measurement_names()
        for name in measurement_names:
            measurement_obj = DynamicMeasurement(name)
            self._schema_measurements[name.upper()] = measurement_obj
            # Also set as attribute for easy access
            setattr(self, name.upper(), measurement_obj)
    
    def get_measurement(self, name: str) -> Optional[DynamicMeasurement]:
        """Get a measurement by name (case-insensitive)."""
        # Check schema measurements first
        upper_name = name.upper()
        if upper_name in self._schema_measurements:
            return self._schema_measurements[upper_name]
        
        # Check legacy measurements
        if hasattr(self, upper_name):
            return getattr(self, upper_name)
        
        return None
    
    def get_all_measurements(self) -> Dict[str, DynamicMeasurement]:
        """Get all available measurements (schema + legacy)."""
        all_measurements = {}
        
        # Add schema measurements
        all_measurements.update(self._schema_measurements)
        
        # Add legacy measurements
        legacy_attrs = ['GAME_ROUNDS', 'ROUND_EVENTS', 'ROUND_SNAPSHOTS', 'SCRIPT_LOGS', 
                       'AWX_EVENTS', 'GENERAL_LOGS', 'LOGCAT_LOGS', 'PSLIST_LOGS']
        for attr in legacy_attrs:
            if hasattr(self, attr):
                measurement = getattr(self, attr)
                all_measurements[attr] = measurement
        
        return all_measurements
    
    def is_schema_measurement(self, measurement_name: str) -> bool:
        """Check if a measurement is defined in the schema."""
        return measurement_name.upper() in self._schema_measurements

class DatabaseManager:
    _influx_client: Optional[InfluxDBClient] = None
    _write_api = None

    def __init__(self, 
                 influx_url: Optional[str] = None, 
                 influx_token: Optional[str] = None, 
                 influx_org: Optional[str] = None,
                 default_bucket_data: Optional[str] = None,
                 default_bucket_logs: Optional[str] = None,
                 write_option=ASYNCHRONOUS, # Use ASYNCHRONOUS for performance
                 schema_file_path: Optional[str] = None,
                 enable_schema_validation: Optional[bool] = None):
        
        # Get config with fallbacks
        influx_config = get_influxdb_config()
        schema_config = get_schema_config()
        
        self.influx_url = influx_url or influx_config['url']
        self.influx_token = influx_token or influx_config['token']
        self.influx_org = influx_org or influx_config['org']
        self.bucket_data = default_bucket_data or influx_config.get('bucket_data', 'tower_data')
        self.bucket_logs = default_bucket_logs or influx_config.get('bucket_logs', 'logs')
        self._write_option = write_option
        
        # Schema configuration with config file fallbacks
        self.schema_file_path = schema_file_path or schema_config.get('schema_file')
        if enable_schema_validation is None:
            self.enable_schema_validation = schema_config.get('validation_enabled', True)
        else:
            self.enable_schema_validation = enable_schema_validation
        
        self.schema_strict_mode = schema_config.get('strict_mode', False)
        
        # Initialize schema loader
        try:
            self.schema_loader = SchemaLoader(self.schema_file_path)
            schema_info = self.schema_loader.get_schema_info()
            _log_with_context_local("info",
                "Schema initialized successfully.",
                schema_version=schema_info.get('schema_version'),
                measurements=schema_info.get('measurements'),
                schema_file=self.schema_file_path,
                validation_enabled=self.enable_schema_validation,
                strict_mode=self.schema_strict_mode
            )
        except Exception as e:
            _log_with_context_local("warning",
                f"Failed to initialize schema, database schema validation will be disabled: {e}",
                schema_file=self.schema_file_path,
                exc_info=True
            )
            self.schema_loader = None
            self.enable_schema_validation = False

        # Initialize dynamic measurements
        self.measurements = InfluxMeasurement(self.schema_loader)
        
        # Create dynamic bucket mapping based on schema
        self._bucket_mapping = self._create_bucket_mapping()

        _log_with_context_local("info",
            "DatabaseManager initialized for InfluxDB.",
            url=self.influx_url,
            org=self.influx_org,
            data_bucket=self.bucket_data,
            logs_bucket=self.bucket_logs,
            schema_validation=self.enable_schema_validation,
            schema_measurements=len(self.measurements._schema_measurements) if self.schema_loader else 0
        )
        # Connection and write_api will be established on first use via _get_write_api

    def _create_bucket_mapping(self) -> Dict[str, str]:
        """Create dynamic bucket mapping based on schema and measurement characteristics."""
        bucket_mapping = {}
        
        if not self.schema_loader:
            # Fallback mapping for legacy measurements
            return {
                'game_rounds': self.bucket_data,
                'round_events': self.bucket_data,
                'round_snapshots': self.bucket_data,
                'script_logs': self.bucket_logs,
                'awx_events': self.bucket_logs,
                'general_logs': self.bucket_logs,
                'logcat_logs': self.bucket_logs,
                'pslist_logs': self.bucket_logs,
            }
        
        # Create mapping based on schema measurements
        measurements = self.schema_loader.get_measurements()
        for measurement_name, measurement_def in measurements.items():
            # Determine bucket based on measurement characteristics
            description = measurement_def.get('description', '').lower()
            
            # Default logic: logs go to logs bucket, everything else to data bucket
            if any(keyword in description for keyword in ['log', 'debug', 'error', 'warning']):
                bucket_mapping[measurement_name] = self.bucket_logs
            elif any(keyword in description for keyword in ['event', 'snapshot', 'metric', 'data']):
                bucket_mapping[measurement_name] = self.bucket_data
            else:
                # Default to data bucket
                bucket_mapping[measurement_name] = self.bucket_data
        
        # Add legacy measurements
        legacy_mapping = {
            'game_rounds': self.bucket_data,
            'round_events': self.bucket_data,
            'round_snapshots': self.bucket_data,
            'script_logs': self.bucket_logs,
            'awx_events': self.bucket_logs,
            'general_logs': self.bucket_logs,
            'logcat_logs': self.bucket_logs,
            'pslist_logs': self.bucket_logs,
        }
        bucket_mapping.update(legacy_mapping)
        
        return bucket_mapping
    
    def get_bucket_for_measurement(self, measurement_name: str) -> str:
        """Get the appropriate bucket for a measurement."""
        return self._bucket_mapping.get(measurement_name, self.bucket_data)

    def _get_write_api(self):
        if self._write_api is None:
            try:
                _log_with_context_local("info", f"Connecting to InfluxDB at {self.influx_url} for org {self.influx_org}...")
                self._influx_client = InfluxDBClient(url=self.influx_url, token=self.influx_token, org=self.influx_org)
                self._write_api = self._influx_client.write_api(write_options=self._write_option)
                
                # Verify connection by trying to list buckets (or a more lightweight ping if available)
                try:
                    buckets_api = self._influx_client.buckets_api()
                    buckets_api.find_buckets() # This will raise an exception if connection fails
                    _log_with_context_local("info",
                        f"Successfully connected to InfluxDB and write_api initialized (mode: {'ASYNCHRONOUS' if self._write_option == ASYNCHRONOUS else 'SYNCHRONOUS'})."
                    )
                except Exception as conn_err:
                    _log_with_context_local("error", f"InfluxDB connection verification failed: {conn_err}")
                    self._influx_client = None # Reset client if verification fails
                    self._write_api = None
                    raise conn_err # Re-raise the error to indicate connection failure

            except Exception as e:
                _log_with_context_local("error", f"Failed to connect to InfluxDB or initialize write_api: {e}")
                # Ensure client and api are None if setup fails
                self._influx_client = None
                self._write_api = None
                raise
        return self._write_api

    def close_connection(self):
        """Close InfluxDB connections with timeout protection to prevent hanging during shutdown."""
        import threading
        import time
        
        def _close_write_api():
            """Helper function to close write_api with timeout protection."""
            if self._write_api:
                try:
                    self._write_api.close()  # Flushes pending writes and closes
                    self._write_api = None
                    _log_with_context_local("info", "InfluxDB write_api closed.")
                except Exception as e:
                    _log_with_context_local("error", f"Error closing InfluxDB write_api: {e}")
                    self._write_api = None  # Ensure it's set to None even if close fails
        
        def _close_client():
            """Helper function to close client with timeout protection."""
            if self._influx_client:
                try:
                    self._influx_client.close()
                    self._influx_client = None
                    _log_with_context_local("info", "InfluxDB client closed.")
                except Exception as e:
                    _log_with_context_local("error", f"Error closing InfluxDB client: {e}")
                    self._influx_client = None  # Ensure it's set to None even if close fails
        
        # Close write_api with timeout protection
        if self._write_api:
            write_api_thread = threading.Thread(target=_close_write_api, daemon=True)
            write_api_thread.start()
            write_api_thread.join(timeout=5.0)  # Wait max 5 seconds for write_api to close
            
            if write_api_thread.is_alive():
                _log_with_context_local("warning", "InfluxDB write_api close operation timed out, forcing cleanup.")
                self._write_api = None  # Force cleanup even if close didn't complete
        
        # Close client with timeout protection
        if self._influx_client:
            client_thread = threading.Thread(target=_close_client, daemon=True)
            client_thread.start()
            client_thread.join(timeout=3.0)  # Wait max 3 seconds for client to close
            
            if client_thread.is_alive():
                _log_with_context_local("warning", "InfluxDB client close operation timed out, forcing cleanup.")
                self._influx_client = None  # Force cleanup even if close didn't complete

    def _convert_timestamp(self, ts_str: str) -> Optional[datetime]:
        if not ts_str:
            return None
        
        try:
            dt_obj = dateutil_parse(ts_str)
            
            # Ensure it's timezone-aware and in UTC
            if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
                # Parsed as naive. Logcat timestamps are typically local.
                # To accurately convert to UTC, we'd ideally use the original device's timezone.
                # For now, we'll treat naive timestamps as UTC. This is a common simplification
                # but may lead to inaccuracies if the source time was significantly offset from UTC
                # and that offset matters.
                # log_info(LogSource.DATABASE, f"Timestamp \'{ts_str}\' parsed as naive. Assuming UTC.", input_ts=ts_str)
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)
            else:
                # It's already timezone-aware, convert to UTC
                dt_obj = dt_obj.astimezone(timezone.utc)
            return dt_obj
        except (ValueError, TypeError) as e: # dateutil_parse can raise TypeError (e.g. for None) or ValueError
            _log_with_context_local("warning",
                f"Could not parse timestamp string \'{ts_str}\' with dateutil.parser. Error: {e}. Falling back to current UTC time.", 
                exc_info=True
            )
            return datetime.now(timezone.utc)

    def _prepare_data_for_influx(self, data: Any) -> Any:
        if isinstance(data, (dict, list)):
            return json.dumps(data) # Serialize complex types to JSON strings
        if isinstance(data, bool): # Ensure booleans are passed as bools, not strings
            return data
        if data is None:
            return "" # Represent None as empty string or handle as needed
        if isinstance(data, (int, float)): # Ensure numbers are passed as numbers
             return data
        return str(data) # Default to string conversion for other types

    async def write_point_async(self, bucket: str, measurement: Union[DynamicMeasurement, str], tags: Dict[str, str], fields: Dict[str, Any], timestamp: Optional[datetime] = None):
        write_api = self._get_write_api()
        if not timestamp:
            timestamp = datetime.now(timezone.utc)

        # Get measurement name
        measurement_name = measurement.value if isinstance(measurement, DynamicMeasurement) else measurement

        # Schema validation if enabled
        if self.enable_schema_validation and self.schema_loader:
            validation_result = self.schema_loader.validate_data_point(measurement_name, tags, fields)
            
            if validation_result['errors']:
                error_msg = f"Schema validation errors for measurement '{measurement_name}'"
                _log_with_context_local("error",
                    error_msg,
                    errors=validation_result['errors'],
                    tags=tags,
                    fields=list(fields.keys())
                )
                
                # In strict mode, raise an exception to prevent writing invalid data
                if self.schema_strict_mode:
                    raise ValueError(f"{error_msg}: {', '.join(validation_result['errors'])}")
            
            if validation_result['warnings']:
                _log_with_context_local("warning",
                    f"Schema validation warnings for measurement '{measurement_name}'",
                    warnings=validation_result['warnings'],
                    tags=tags,
                    fields=list(fields.keys())
                )

        # Prepare data for InfluxDB
        prepared_tags = {k: str(v) for k, v in tags.items() if v is not None}
        prepared_fields = {k: self._prepare_data_for_influx(v) for k, v in fields.items() if v is not None}

        if not prepared_fields:
            _log_with_context_local("warning", 
                f"No valid fields to write for measurement '{measurement_name}'. Skipping write.",
                tags=prepared_tags
            )
            return

        try:
            point = Point(measurement_name) \
                .time(timestamp, WritePrecision.NS)

            for tag_key, tag_value in prepared_tags.items():
                point = point.tag(tag_key, tag_value)

            for field_key, field_value in prepared_fields.items():
                point = point.field(field_key, field_value)

            await run_in_threadpool(write_api.write, bucket=bucket, record=point)
            
            _log_with_context_local("debug", 
                f"Successfully wrote point to InfluxDB.",
                measurement=measurement_name,
                bucket=bucket,
                tags=list(prepared_tags.keys()),
                fields=list(prepared_fields.keys())
            )

        except Exception as e:
            _log_with_context_local("error", 
                f"Failed to write point to InfluxDB for measurement '{measurement_name}': {e}",
                bucket=bucket,
                tags=prepared_tags,
                fields=list(prepared_fields.keys()),
                exc_info=True
            )
            raise

    def get_schema_info(self) -> Optional[Dict[str, Any]]:
        """Get schema information if schema loader is available."""
        if self.schema_loader:
            return self.schema_loader.get_schema_info()
        return None

    def validate_measurement_data(self, measurement_name: str, tags: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        Validate data against the schema for a specific measurement.
        
        Returns:
            Dict with 'errors' and 'warnings' lists
        """
        if not self.schema_loader:
            return {'errors': ['Schema loader not available'], 'warnings': []}
        
        return self.schema_loader.validate_data_point(measurement_name, tags, fields)

    def initialize_schema(self):
        """
        Initialize the database schema based on the loaded YAML schema file.
        This includes:
        1. Ensuring required buckets exist
        2. Validating schema compatibility
        3. Logging schema information
        4. Setting up any schema-specific configurations
        """
        _log_with_context_local("info", "Initializing database schema from YAML configuration...")
        
        # Ensure InfluxDB client is available
        if not self._influx_client:
            try:
                self._get_write_api() 
            except Exception as e:
                _log_with_context_local("error", 
                    f"Failed to initialize InfluxDB client for schema setup: {e}", 
                    exc_info=True
                )
                return

        if not self._influx_client:
            _log_with_context_local("error", "InfluxDB client not available, cannot initialize schema.")
            return

        # Load and validate schema
        if not self.schema_loader:
            _log_with_context_local("warning", 
                "No schema loader available, falling back to basic bucket initialization."
            )
            self._initialize_basic_buckets()
            return

        try:
            # Get schema information
            schema_info = self.schema_loader.get_schema_info()
            measurements = self.schema_loader.get_measurements()
            
            _log_with_context_local("info",
                "Initializing schema based on YAML configuration.",
                schema_version=schema_info.get('schema_version'),
                schema_date=schema_info.get('schema_release_date'),
                influxdb_compatibility=schema_info.get('influxdb_version_compatibility'),
                measurement_count=len(measurements)
            )

            # 1. Initialize buckets
            self._initialize_buckets_from_schema(measurements)
            
            # 2. Validate measurements against schema
            self._validate_schema_measurements(measurements)
            
            # 3. Log schema details
            self._log_schema_details(measurements)
            
            _log_with_context_local("info", 
                "Database schema initialization completed successfully.",
                measurements_initialized=list(measurements.keys())
            )
            
        except Exception as e:
            _log_with_context_local("error", 
                f"Error during schema initialization: {e}", 
                exc_info=True
            )
            # Fall back to basic initialization
            _log_with_context_local("info", "Falling back to basic bucket initialization...")
            self._initialize_basic_buckets()

    def _initialize_basic_buckets(self):
        """Fallback method for basic bucket initialization without schema."""
        buckets_api = self._influx_client.buckets_api()
        required_buckets = {self.bucket_data, self.bucket_logs}

        for bucket_name in required_buckets:
            if not bucket_name:
                _log_with_context_local("warning", 
                    "Attempted to initialize undefined bucket name. Skipping."
                )
                continue
            
            try:
                bucket = buckets_api.find_bucket_by_name(bucket_name)
                if bucket:
                    _log_with_context_local("info", 
                        f"InfluxDB bucket '{bucket_name}' already exists (ID: {bucket.id})."
                    )
                else:
                    new_bucket = buckets_api.create_bucket(bucket_name=bucket_name, org=self.influx_org)
                    _log_with_context_local("info", 
                        f"Successfully created InfluxDB bucket '{bucket_name}' (ID: {new_bucket.id})."
                    )
            except Exception as e:
                _log_with_context_local("error", 
                    f"Error with InfluxDB bucket '{bucket_name}': {e}", 
                    exc_info=True
                )

    def _initialize_buckets_from_schema(self, measurements: Dict[str, Any]):
        """Initialize buckets based on schema measurements."""
        buckets_api = self._influx_client.buckets_api()
        
        # Get all required buckets from the dynamic mapping
        required_buckets = set()
        
        # Add configured buckets
        if self.bucket_data:
            required_buckets.add(self.bucket_data)
        if self.bucket_logs:
            required_buckets.add(self.bucket_logs)
        
        # Add buckets needed for schema measurements
        for measurement_name in measurements.keys():
            bucket = self.get_bucket_for_measurement(measurement_name)
            if bucket:
                required_buckets.add(bucket)

        # Create/verify buckets
        for bucket_name in required_buckets:
            try:
                bucket = buckets_api.find_bucket_by_name(bucket_name)
                if bucket:
                    _log_with_context_local("info", 
                        f"Schema bucket '{bucket_name}' already exists (ID: {bucket.id})."
                    )
                else:
                    new_bucket = buckets_api.create_bucket(bucket_name=bucket_name, org=self.influx_org)
                    _log_with_context_local("info", 
                        f"Created schema bucket '{bucket_name}' (ID: {new_bucket.id})."
                    )
            except Exception as e:
                _log_with_context_local("error", 
                    f"Error with schema bucket '{bucket_name}': {e}", 
                    exc_info=True
                )

    def _validate_schema_measurements(self, measurements: Dict[str, Any]):
        """Validate that schema measurements are properly defined."""
        validation_results = []
        
        for measurement_name, measurement_def in measurements.items():
            result = {
                'measurement': measurement_name,
                'valid': True,
                'issues': []
            }
            
            # Check required sections
            if 'description' not in measurement_def:
                result['issues'].append("Missing description")
            
            if 'tags' not in measurement_def:
                result['issues'].append("Missing tags section")
            
            if 'fields' not in measurement_def:
                result['issues'].append("Missing fields section")
            
            # Validate tags
            tags = measurement_def.get('tags', {})
            for tag_name, tag_def in tags.items():
                if 'data_type' not in tag_def:
                    result['issues'].append(f"Tag '{tag_name}' missing data_type")
                if 'description' not in tag_def:
                    result['issues'].append(f"Tag '{tag_name}' missing description")
                if 'display_name' not in tag_def:
                    result['issues'].append(f"Tag '{tag_name}' missing display_name")
            
            # Validate fields
            fields = measurement_def.get('fields', {})
            for field_name, field_def in fields.items():
                if 'data_type' not in field_def:
                    result['issues'].append(f"Field '{field_name}' missing data_type")
                if 'description' not in field_def:
                    result['issues'].append(f"Field '{field_name}' missing description")
                if 'display_name' not in field_def:
                    result['issues'].append(f"Field '{field_name}' missing display_name")
            
            if result['issues']:
                result['valid'] = False
            
            validation_results.append(result)
        
        # Log validation results
        valid_measurements = [r for r in validation_results if r['valid']]
        invalid_measurements = [r for r in validation_results if not r['valid']]
        
        _log_with_context_local("info",
            f"Schema validation completed: {len(valid_measurements)} valid, {len(invalid_measurements)} invalid measurements."
        )
        
        for result in invalid_measurements:
            _log_with_context_local("warning",
                f"Schema validation issues for measurement '{result['measurement']}'",
                issues=result['issues']
            )

    def _log_schema_details(self, measurements: Dict[str, Any]):
        """Log detailed schema information for debugging and verification."""
        for measurement_name, measurement_def in measurements.items():
            tags = measurement_def.get('tags', {})
            fields = measurement_def.get('fields', {})
            
            _log_with_context_local("debug",
                f"Schema measurement '{measurement_name}' loaded",
                description=measurement_def.get('description', 'No description'),
                tag_count=len(tags),
                field_count=len(fields),
                tags=list(tags.keys()),
                fields=list(fields.keys())
            )

    async def log_message_async(self, script_timestamp_str: str, process_name: str, script_name: str, 
                                message_type: str, event_subtype: Optional[str], 
                                data_payload: Union[Dict, List, str]):
        ts = self._convert_timestamp(script_timestamp_str) or datetime.now(timezone.utc)
        
        tags = {
            "process_name": process_name,
            "script_name": script_name,
            "message_type": message_type,
        }
        if event_subtype:
            tags["event_subtype"] = event_subtype

        fields = {
            # Ensure data_payload is correctly prepared for InfluxDB
            "data_payload": self._prepare_data_for_influx(data_payload)
        }
        # This specific method seems to be for SCRIPT_LOGS, usually to bucket_data.
        # If it were for general app logs, it would go to bucket_logs and GENERAL_LOGS.
        await self.write_point_async(self.bucket_data, InfluxMeasurement.SCRIPT_LOGS, tags, fields, ts)
        _log_with_context_local("info", "Script message logged to InfluxDB.", **tags, payload_type=type(data_payload).__name__)

    async def start_new_round_async(self, script_timestamp_str: str, process_name: str, script_name: str, 
                                    tier: int, initial_cards_equipped: Union[Dict, List, str], 
                                    initial_modules_equipped: Union[Dict, List, str],
                                    other_fixed_metadata: Optional[Union[Dict, List, str]] = None) -> str:
        # For InfluxDB, round_id will be a unique identifier based on timestamp and tags, or a generated one.
        # We can use the start timestamp combined with process_name as a practical unique ID for a round.
        # Alternatively, use a UUID if strict global uniqueness is needed, and store it as a tag/field.
        start_ts = self._convert_timestamp(script_timestamp_str) or datetime.now(timezone.utc)
        
        # For simplicity, we\'ll use the timestamp as the primary identifier in time-series context.
        # If you need a separate round_id for linking, generate one (e.g., UUID) and add as a tag.
        # Let\'s assume the combination of tags and timestamp makes it unique enough for querying a "round".
        # We will return the ISO format of the start_ts as a "round_id" for conceptual linking.
        round_id_conceptual = start_ts.isoformat()

        tags = {
            "process_name": process_name,
            "script_name": script_name,
            "tier": str(tier), # Tags are strings
            "round_id_conceptual": round_id_conceptual # Store conceptual ID if needed
        }
        fields = {
            "initial_cards_equipped": self._prepare_data_for_influx(initial_cards_equipped),
            "initial_modules_equipped": self._prepare_data_for_influx(initial_modules_equipped),
            "other_fixed_metadata": self._prepare_data_for_influx(other_fixed_metadata) if other_fixed_metadata else "",
            "status": "started" # Indicates the round has started
        }
        await self.write_point_async(self.bucket_data, InfluxMeasurement.GAME_ROUNDS, tags, fields, start_ts)
        _log_with_context_local("info", f"New game round started and logged to InfluxDB.", conceptual_round_id=round_id_conceptual, **tags)
        return round_id_conceptual # Return the conceptual ID

    async def end_round_async(self, round_id_conceptual: str, end_timestamp_str: str, 
                              process_name: str, script_name: str, tier: int, # Moved and made non-optional
                              final_wave: Optional[int] = None, final_cash: Optional[float] = None, 
                              final_coins: Optional[float] = None, duration_seconds: Optional[int] = None):
        end_ts = self._convert_timestamp(end_timestamp_str) or datetime.now(timezone.utc)

        # In InfluxDB, you typically write a new point for the "end" event or update status.
        # We will write a point with the final details.
        # The query to get a "full round" would involve selecting points related to `round_id_conceptual`.
        
        tags = {
            "process_name": process_name,
            "script_name": script_name,
            "tier": str(tier),
            "round_id_conceptual": round_id_conceptual 
        }
        fields = {
            "status": "ended", # Mark the round as ended
        }
        if final_wave is not None:
            fields["final_wave"] = final_wave
        if final_cash is not None:
            fields["final_cash"] = float(final_cash)
        if final_coins is not None:
            fields["final_coins"] = float(final_coins)
        if duration_seconds is not None:
            fields["duration_seconds"] = int(duration_seconds)

        # We use the END timestamp for this specific point representing the end of the round.
        await self.write_point_async(self.bucket_data, InfluxMeasurement.GAME_ROUNDS, tags, fields, end_ts)
        _log_with_context_local("info", f"Game round ended and logged to InfluxDB.", conceptual_round_id=round_id_conceptual, **tags)

    async def log_round_event_async(self, round_id_conceptual: str, event_timestamp_str: str, 
                                    event_type: str, event_data: Union[Dict, List, str],
                                    # We might need some parent round tags if not included in round_id_conceptual
                                    process_name: Optional[str] = None, script_name: Optional[str] = None):
        event_ts = self._convert_timestamp(event_timestamp_str) or datetime.now(timezone.utc)
        
        tags = {
            "round_id_conceptual": round_id_conceptual,
            "event_type": event_type,
        }
        if process_name: tags["process_name"] = process_name
        if script_name: tags["script_name"] = script_name

        fields = {
            "event_data": self._prepare_data_for_influx(event_data)
        }
        await self.write_point_async(self.bucket_data, InfluxMeasurement.ROUND_EVENTS, tags, fields, event_ts)
        _log_with_context_local("debug", "Round event logged to InfluxDB.", conceptual_round_id=round_id_conceptual, event_type=event_type)

    async def log_round_snapshot_async(self, round_id_conceptual: str, snapshot_timestamp_str: str, 
                                       cash: float, coins: float, gems: Optional[int] = None, 
                                       wave_number: Optional[int] = None, tower_health: Optional[float] = None,
                                       process_name: Optional[str] = None, script_name: Optional[str] = None):
        snapshot_ts = self._convert_timestamp(snapshot_timestamp_str) or datetime.now(timezone.utc)
        
        tags = {
            "round_id_conceptual": round_id_conceptual,
        }
        if process_name: tags["process_name"] = process_name
        if script_name: tags["script_name"] = script_name

        fields = {
            "cash": float(cash),
            "coins": float(coins),
        }
        if gems is not None:
            fields["gems"] = int(gems)
        if wave_number is not None:
            fields["wave_number"] = int(wave_number)
        if tower_health is not None:
            fields["tower_health"] = float(tower_health)
            
        await self.write_point_async(self.bucket_data, InfluxMeasurement.ROUND_SNAPSHOTS, tags, fields, snapshot_ts)
        _log_with_context_local("debug", "Round snapshot logged to InfluxDB.", conceptual_round_id=round_id_conceptual)

    async def log_awx_event_async(self, event_data: Dict[str, Any]) -> None:
        """
        Logs an AWX (Ansible Semaphore) event to InfluxDB.
        The event_data dictionary is expected to contain all necessary fields and tags.
        Timestamp should be part of event_data, e.g., as 'timestamp_override'.
        """
        
        # Extract or generate timestamp
        ts_str = event_data.pop('timestamp_override', None) # Allow overriding timestamp
        timestamp = self._convert_timestamp(ts_str) if ts_str else datetime.now(timezone.utc)

        # Define default tags or extract them from event_data
        tags = {
            "source": event_data.pop("source", "awx"), # Default source
            "project_id": str(event_data.pop("project_id", "unknown")),
            "task_id": str(event_data.pop("task_id", "unknown")),
            "status": event_data.pop("status", "unknown")
        }
        
        # Any remaining items in event_data can be considered fields
        # Filter out None values for fields and ensure complex types are serialized
        fields = {k: self._prepare_data_for_influx(v) for k, v in event_data.items() if v is not None}

        if not fields: # Don't write if there are no fields
            _log_with_context_local("warning", "AWX event logging skipped: no fields to write after processing event_data.", original_data=event_data, tags=tags)
            return

        # AWX events go to the general logs bucket but with a specific measurement
        await self.write_point_async(self.bucket_logs, InfluxMeasurement.AWX_EVENTS, tags, fields, timestamp)
        _log_with_context_local("info", "AWX event logged to InfluxDB.", awx_task_id=tags.get("task_id"), status=tags.get("status"))

    # --- Methods for InfluxDBLoggingHandler ---

    def _create_point_from_log_entry(self, record: Dict[str, Any], default_measurement: str) -> Optional[Point]:
        """
        Creates an InfluxDB Point from a structured log record.
        The record is expected to be a dictionary, typically from structlog.
        """
        
        # Determine measurement: use _target_measurement if present, else default
        measurement_name = record.pop("_target_measurement", default_measurement)

        # Handle timestamp
        timestamp_str = record.pop("timestamp", None)
        if isinstance(timestamp_str, datetime): # Already a datetime object
            timestamp = timestamp_str
            if timestamp.tzinfo is None: # Ensure timezone aware (UTC)
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            elif timestamp.tzinfo.utcoffset(timestamp) is not None: # If aware, convert to UTC
                 timestamp = timestamp.astimezone(timezone.utc)
        elif isinstance(timestamp_str, str):
            timestamp = self._convert_timestamp(timestamp_str)
        else: # Fallback if no valid timestamp string
            timestamp = datetime.now(timezone.utc)
        
        if not timestamp: # Should not happen with fallback, but defensive
            timestamp = datetime.now(timezone.utc)

        # Check if the 'event' field contains a dictionary with structured data
        # This happens when the InfluxDBFormatter processes structlog records
        if "event" in record and isinstance(record["event"], dict):
            # The event field contains the full event_dict from structlog
            event_dict = record.pop("event")
            
            # Merge the event_dict into the record, giving priority to existing record fields
            for key, value in event_dict.items():
                if key not in record:  # Don't overwrite existing fields
                    record[key] = value

        # Separate tags and fields. Common log attributes can be tags.
        # Customize this based on your data model and querying needs.
        # Attributes that are frequently filtered on or grouped by are good candidates for tags.
        # Attributes with high cardinality (many unique values) are better as fields.
        
        tags = {}
        fields = {}

        # Standard log attributes to consider as tags
        # We pop them from the record so they don't also become fields.
        common_tags = ["level", "logger", "log_event", "app", "mode", "device", "package_name", "script_name", "process_name", "source"]
        for tag_key in common_tags:
            if tag_key in record:
                tag_value = record.pop(tag_key)
                if tag_value is not None and tag_value != "": # Tags cannot be empty
                    tags[tag_key] = str(tag_value) # Ensure tags are strings

        # The 'event' field (main log message) is usually a field.
        if "event" in record:
            fields["event"] = self._prepare_data_for_influx(record.pop("event"))
        
        # All remaining items in the record are treated as fields.
        for key, value in record.items():
            # Skip None values for fields, InfluxDB doesn't store None fields.
            # Skip empty strings too for cleanliness, unless explicitly needed.
            if value is not None and value != "": 
                fields[key] = self._prepare_data_for_influx(value)
        
        # If there are no fields, InfluxDB might reject the point or it's not useful.
        if not fields:
            # log_debug(LogSource.DATABASE, f"Skipping log entry for InfluxDB: no fields. Record: {record}, Tags: {tags}")
            return None

        point = Point(measurement_name).time(timestamp, WritePrecision.NS)
        for key, value in tags.items():
            point.tag(key, value)
        for key, value in fields.items():
            point.field(key, value)
            
        return point

    def write_log_batch_sync(self, records: List[Dict[str, Any]]):
        """
        Writes a batch of log records synchronously to InfluxDB.
        Each record is a dictionary, typically from structlog.
        It now uses _create_point_from_log_entry which can determine measurement.
        """
        write_api = self._get_write_api()
        if not write_api:
            _log_with_context_local("error", "InfluxDB write_api not available. Cannot write log batch.")
            return

        points_to_write: List[Point] = []
        for record in records:
            try:
                # Use system_logs measurement from schema instead of legacy GENERAL_LOGS
                default_measurement = "system_logs"
                point = self._create_point_from_log_entry(record.copy(), default_measurement)
                if point:
                    points_to_write.append(point)
            except Exception as e:
                _log_with_context_local("error", f"Error creating InfluxDB point from log record: {e}", record_sample=record)
                # Optionally, log the problematic record to a fallback or skip it
                continue # Skip this record and proceed with others

        if not points_to_write:
            # log_debug(LogSource.DATABASE, "No valid points to write in the current log batch.")
            return

        try:
            write_api.write(bucket=self.bucket_logs, org=self.influx_org, record=points_to_write)
            # log_debug(LogSource.DATABASE, f"Successfully wrote batch of {len(points_to_write)} log entries to InfluxDB bucket '{self.bucket_logs}'.")
        except Exception as e:
            _log_with_context_local("error",
                f"Error writing batch of {len(points_to_write)} log entries to InfluxDB bucket '{self.bucket_logs}': {e}", 
                exc_info=True,
                # sample_point_tags=[p.tags for p in points_to_write[:2]], # Log sample tags
                # sample_point_fields=[p.fields for p in points_to_write[:2]] # Log sample fields
            )
            # Handle batch write failure, e.g., retry logic, dead-letter queue.
            # For now, logs in the failed batch are dropped.

    # --- New dedicated methods for Logcat and PSList ---

    def _write_structured_data_sync(self, data: Dict[str, Any], measurement: InfluxMeasurement, bucket: str, default_timestamp_field: str = "timestamp"):
        """
        Helper to write a single structured data entry (like logcat or pslist) synchronously.
        """
        write_api = self._get_write_api()
        if not write_api:
            _log_with_context_local("error", f"InfluxDB write_api not available. Cannot write {measurement.value} data.")
            return

        record_copy = data.copy() # Work with a copy

        # Handle timestamp
        timestamp_str = record_copy.pop(default_timestamp_field, None)
        if isinstance(timestamp_str, datetime):
            timestamp = timestamp_str
            if timestamp.tzinfo is None: timestamp = timestamp.replace(tzinfo=timezone.utc)
            elif timestamp.tzinfo.utcoffset(timestamp) is not None: timestamp = timestamp.astimezone(timezone.utc)
        elif isinstance(timestamp_str, str):
            timestamp = self._convert_timestamp(timestamp_str)
        else:
            timestamp = datetime.now(timezone.utc)
        
        if not timestamp: timestamp = datetime.now(timezone.utc)

        # Define tags - customize as needed for logcat/pslist
        tags = {}
        # Example tags that might be relevant for logcat/pslist
        potential_tags = ["device", "package_name", "pid", "tid", "tag", "priority", "type"] # Add more as needed
        for tag_key in potential_tags:
            if tag_key in record_copy:
                tag_value = record_copy.pop(tag_key)
                if tag_value is not None and tag_value != "":
                    tags[tag_key] = str(tag_value)
        
        # Remaining items are fields
        fields = {k: self._prepare_data_for_influx(v) for k, v in record_copy.items() if v is not None and v != ""}

        if not fields:
            # log_debug(LogSource.DATABASE, f"Skipping {measurement.value} entry: no fields. Original data: {data}")
            return

        point = Point(measurement.value).time(timestamp, WritePrecision.NS)
        for key, value in tags.items(): point.tag(key, value)
        for key, value in fields.items(): point.field(key, value)
        
        try:
            write_api.write(bucket=bucket, org=self.influx_org, record=point)
            # log_debug(LogSource.DATABASE, f"Successfully wrote {measurement.value} entry to InfluxDB bucket '{bucket}'.")
        except Exception as e:
            _log_with_context_local("error", f"Error writing {measurement.value} entry to InfluxDB bucket '{bucket}': {e}", data_sample=data)

    async def _write_structured_data_async(self, data: Dict[str, Any], measurement: InfluxMeasurement, bucket: str, default_timestamp_field: str = "timestamp"):
        """
        Helper to write a single structured data entry (like logcat or pslist) asynchronously.
        """
        await run_in_threadpool(self._write_structured_data_sync, data=data, measurement=measurement, bucket=bucket, default_timestamp_field=default_timestamp_field)

    # Logcat specific
    def write_logcat_entry_sync(self, record: Dict[str, Any]):
        """Writes a single parsed logcat entry synchronously."""
        self._write_structured_data_sync(record, InfluxMeasurement.LOGCAT_LOGS, self.bucket_logs, default_timestamp_field="timestamp")

    async def write_logcat_entry_async(self, record: Dict[str, Any]):
        """Writes a single parsed logcat entry asynchronously."""
        await self._write_structured_data_async(record, InfluxMeasurement.LOGCAT_LOGS, self.bucket_logs, default_timestamp_field="timestamp")

    def write_logcat_batch_sync(self, records: List[Dict[str, Any]]):
        """Writes a batch of parsed logcat entries synchronously."""
        # This can be optimized like write_log_batch_sync if needed, for now, it iterates.
        for record in records:
            self.write_logcat_entry_sync(record)
    
    async def write_logcat_batch_async(self, records: List[Dict[str, Any]]):
        """Writes a batch of parsed logcat entries asynchronously."""
        for record in records: # Can be parallelized with asyncio.gather if performance is critical
            await self.write_logcat_entry_async(record)

    # PSList specific
    def write_pslist_entry_sync(self, record: Dict[str, Any]):
        """Writes a single parsed pslist entry (or a summary) synchronously."""
        # pslist data might have a different timestamp field, e.g., 'capture_time'
        self._write_structured_data_sync(record, InfluxMeasurement.PSLIST_LOGS, self.bucket_logs, default_timestamp_field="timestamp")

    async def write_pslist_entry_async(self, record: Dict[str, Any]):
        """Writes a single parsed pslist entry (or a summary) asynchronously."""
        await self._write_structured_data_async(record, InfluxMeasurement.PSLIST_LOGS, self.bucket_logs, default_timestamp_field="timestamp")
    
    def write_pslist_batch_sync(self, records: List[Dict[str, Any]]):
        """Writes a batch of parsed pslist entries synchronously."""
        for record in records:
            self.write_pslist_entry_sync(record)

    async def write_pslist_batch_async(self, records: List[Dict[str, Any]]):
        """Writes a batch of parsed pslist entries asynchronously."""
        for record in records:
            await self.write_pslist_entry_async(record)

    # --- Schema-based methods for new measurements ---

    async def write_round_metadata_async(self, round_id: str, tier: str, game_version: str, 
                                       timestamp_start_round: datetime, timestamp_end_round: Optional[datetime] = None):
        """Write round metadata according to the schema."""
        measurement = self.measurements.get_measurement("round_metadata")
        if not measurement:
            raise ValueError("round_metadata measurement not found in schema")
            
        tags = {
            "round_id": round_id,
            "tier": str(tier),
            "game_version": game_version
        }
        
        fields = {
            "timestamp_start_round": int(timestamp_start_round.timestamp() * 1_000_000_000),  # nanoseconds
        }
        
        if timestamp_end_round:
            fields["timestamp_end_round"] = int(timestamp_end_round.timestamp() * 1_000_000_000)
        
        bucket = self.get_bucket_for_measurement("round_metadata")
        await self.write_point_async(
            bucket=bucket, 
            measurement=measurement, 
            tags=tags, 
            fields=fields, 
            timestamp=timestamp_start_round
        )
        
        _log_with_context_local("info", "Round metadata written to InfluxDB.", round_id=round_id, tier=tier)

    async def write_round_metrics_periodic_async(self, round_id: str, cash: int, coins: int, gems: int, 
                                               timestamp: Optional[datetime] = None):
        """Write periodic round metrics according to the schema."""
        measurement = self.measurements.get_measurement("round_metrics_periodic")
        if not measurement:
            raise ValueError("round_metrics_periodic measurement not found in schema")
            
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
            
        tags = {
            "round_id": round_id
        }
        
        fields = {
            "cash": cash,
            "coins": coins,
            "gems": gems
        }
        
        bucket = self.get_bucket_for_measurement("round_metrics_periodic")
        await self.write_point_async(
            bucket=bucket, 
            measurement=measurement, 
            tags=tags, 
            fields=fields, 
            timestamp=timestamp
        )
        
        _log_with_context_local("debug", "Round metrics written to InfluxDB.", round_id=round_id)

    async def write_round_events_wave_async(self, round_id: str, wave: int, cash: int, coins: int, gems: int,
                                          timestamp: Optional[datetime] = None):
        """Write wave start events according to the schema."""
        measurement = self.measurements.get_measurement("round_events_wave")
        if not measurement:
            raise ValueError("round_events_wave measurement not found in schema")
            
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
            
        tags = {
            "round_id": round_id
        }
        
        fields = {
            "wave": wave,
            "cash": cash,
            "coins": coins,
            "gems": gems
        }
        
        bucket = self.get_bucket_for_measurement("round_events_wave")
        await self.write_point_async(
            bucket=bucket, 
            measurement=measurement, 
            tags=tags, 
            fields=fields, 
            timestamp=timestamp
        )
        
        _log_with_context_local("info", "Wave event written to InfluxDB.", round_id=round_id, wave=wave)

    async def write_schema_measurement_async(self, measurement_name: str, tags: Dict[str, Any], 
                                           fields: Dict[str, Any], timestamp: Optional[datetime] = None):
        """
        Generic method to write any schema-defined measurement.
        This allows writing to any measurement defined in the schema without hardcoded methods.
        """
        measurement = self.measurements.get_measurement(measurement_name)
        if not measurement:
            raise ValueError(f"Measurement '{measurement_name}' not found in schema")
        
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
        
        bucket = self.get_bucket_for_measurement(measurement_name)
        await self.write_point_async(
            bucket=bucket,
            measurement=measurement,
            tags=tags,
            fields=fields,
            timestamp=timestamp
        )
        
        _log_with_context_local("debug", 
            f"Schema measurement '{measurement_name}' written to InfluxDB.",
            measurement=measurement_name,
            bucket=bucket
        )
    
    def get_available_measurements(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all available measurements with their schema information.
        Returns both schema-defined and legacy measurements.
        """
        available = {}
        
        # Add schema measurements with their definitions
        if self.schema_loader:
            schema_measurements = self.schema_loader.get_measurements()
            for name, definition in schema_measurements.items():
                available[name] = {
                    'type': 'schema',
                    'definition': definition,
                    'bucket': self.get_bucket_for_measurement(name)
                }
        
        # Add legacy measurements
        legacy_measurements = ['game_rounds', 'round_events', 'round_snapshots', 'script_logs',
                              'awx_events', 'general_logs', 'logcat_logs', 'pslist_logs']
        for name in legacy_measurements:
            available[name] = {
                'type': 'legacy',
                'definition': None,
                'bucket': self.get_bucket_for_measurement(name)
            }
        
        return available
    
    def validate_measurement_exists(self, measurement_name: str) -> bool:
        """Check if a measurement exists in either schema or legacy measurements."""
        return self.measurements.get_measurement(measurement_name) is not None

# --- Main Test (Example Usage) ---
async def main_test():
    # This test assumes InfluxDB is running and accessible as per config.
    # And that app_config has INFLUXDB_URL, TOKEN, ORG, BUCKET_DATA, BUCKET_LOGS defined.

    log_info(LogSource.DATABASE, "Starting InfluxDB DatabaseManager test with schema support...")

    db_manager = DatabaseManager(write_option=SYNCHRONOUS, enable_schema_validation=True) # Use SYNCHRONOUS for immediate feedback in test

    try:
        # 0. Initialize schema (this will now use the YAML schema)
        log_info(LogSource.DATABASE, "Running schema initialization from YAML...")
        db_manager.initialize_schema()
        
        # Display schema info
        schema_info = db_manager.get_schema_info()
        if schema_info:
            log_info(LogSource.DATABASE, "Schema information loaded:", **schema_info)

        # 1. Test schema-based round metadata
        log_info(LogSource.DATABASE, "Testing schema-based round metadata...")
        round_id = "test-round-12345"
        start_time = datetime.now(timezone.utc)
        await db_manager.write_round_metadata_async(
            round_id=round_id,
            tier="5",
            game_version="26.2.28",
            timestamp_start_round=start_time
        )

        # 2. Test periodic metrics
        log_info(LogSource.DATABASE, "Testing schema-based periodic metrics...")
        await db_manager.write_round_metrics_periodic_async(
            round_id=round_id,
            cash=1000,
            coins=50,
            gems=10
        )

        # 3. Test wave events
        log_info(LogSource.DATABASE, "Testing schema-based wave events...")
        await db_manager.write_round_events_wave_async(
            round_id=round_id,
            wave=1,
            cash=950,
            coins=55,
            gems=10
        )

        # 4. Test schema validation
        log_info(LogSource.DATABASE, "Testing schema validation...")
        validation_result = db_manager.validate_measurement_data(
            "round_metadata",
            {"round_id": "test", "tier": "5"},  # Missing game_version
            {"timestamp_start_round": 1234567890}
        )
        log_info(LogSource.DATABASE, "Validation result:", **validation_result)

        # 5. Display available measurements
        log_info(LogSource.DATABASE, "Available measurements:")
        available = db_manager.get_available_measurements()
        for name, info in available.items():
            log_info(LogSource.DATABASE, f"  {name}: {info['type']} -> {info['bucket']}")

        # 6. Test legacy methods (should still work)
        log_info(LogSource.DATABASE, "Testing legacy methods...")
        conceptual_round_id = await db_manager.start_new_round_async(
            script_timestamp_str=datetime.now(timezone.utc).isoformat(),
            process_name="TheTower.exe",
            script_name="master_hooker.js",
            tier=5,
            initial_cards_equipped=["CardA", "CardB"],
            initial_modules_equipped={"ModuleX": {"level": 2}},
            other_fixed_metadata={"player_id": "test_player_123"}
        )
        log_info(LogSource.DATABASE, f"Legacy round started with ID: {conceptual_round_id}")

        log_info(LogSource.DATABASE, "Schema-based InfluxDB DatabaseManager test completed successfully.")

    except Exception as e:
        _log_with_context_local("error", f"Error during InfluxDB test: {e}")
    finally:
        log_info(LogSource.DATABASE, "Closing InfluxDB connection...")
        db_manager.close_connection() # Important to close to flush async writes if any are pending

if __name__ == "__main__":
    import asyncio
    from datetime import timedelta # Added for AWX event test

    # To run this test:
    # 1. Ensure InfluxDB is running and accessible.
    # 2. Create a .env file in the project root (or `src/` if that\'s where config.py is run from)
    #    with your InfluxDB details:
    #    INFLUXDB_URL=http://localhost:8086
    #    INFLUXDB_TOKEN=yourinfluxdbtoken
    #    INFLUXDB_ORG=yourinfluxdborg
    #    INFLUXDB_BUCKET_DATA=tower_data_test 
    #    INFLUXDB_BUCKET_LOGS=logs_test
    # 3. Make sure `python-dotenv` is installed (`pip install python-dotenv`).
    #    The config.py should load these.
    # 4. Run from the project root: `python src/managers/database_manager.py`
    
    # Load .env file for the test if it exists in project root
    # This is typically handled by application startup, but for direct script run:
    try:
        from dotenv import load_dotenv
        # Determine project root relative to this file (src/managers/database_manager.py)
        # Project root is two levels up from src/managers/
        project_root_for_dotenv = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(project_root_for_dotenv, '.env')
        
        loaded_env = False
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path=dotenv_path)
            log_info(LogSource.DATABASE, f"Loaded .env file from: {dotenv_path}")
            loaded_env = True
        else:
            # Try .env in src directory if running from src or similar context
            # src directory is one level up from src/managers/
            src_dir_for_dotenv = os.path.dirname(os.path.abspath(__file__))
            src_dotenv_path = os.path.join(src_dir_for_dotenv, '.env')
            if os.path.exists(src_dotenv_path):
                 load_dotenv(dotenv_path=src_dotenv_path)
                 log_info(LogSource.DATABASE, f"Loaded .env file from: {src_dotenv_path}")
                 loaded_env = True
        
        if not loaded_env:
            log_warning(LogSource.DATABASE, ".env file not found in project root or current directory. Relying on environment variables or config defaults.")
            
    except ImportError:
        log_warning(LogSource.DATABASE, "python-dotenv not installed. Relying on environment variables or config defaults.")
    except Exception as e:
        log_error(LogSource.DATABASE, f"Error loading .env: {e}")

    asyncio.run(main_test()) 