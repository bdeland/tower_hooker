import logging
import time
from typing import Dict, Optional, Any
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point, WriteOptions
from influxdb_client.client.write_api import SYNCHRONOUS


class InfluxDBLoggingHandler(logging.Handler):
    """
    A simplified logging.Handler for InfluxDB that works directly with influxdb-client.
    This handler is designed for use with UnifiedLoggingManager.
    """
    
    def __init__(self, url: str, token: str, org: str, bucket: str, 
                 batch_size: int = 100, flush_interval: int = 5000):
        """
        Initialize the InfluxDB log handler.
        
        Args:
            url: InfluxDB server URL
            token: InfluxDB authentication token
            org: InfluxDB organization name
            bucket: InfluxDB bucket name
            batch_size: Number of points to batch before writing
            flush_interval: Flush interval in milliseconds
        """
        super().__init__()
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        
        try:
            # Initialize InfluxDB client with SYNCHRONOUS writes for simplicity
            self.influx_client = InfluxDBClient(url=url, token=token, org=org)
            self.write_api = self.influx_client.write_api(
                write_options=WriteOptions(
                    batch_size=batch_size,
                    flush_interval=flush_interval,
                    write_type=SYNCHRONOUS
                )
            )
        except Exception as e:
            # Re-raise with more context
            raise ConnectionError(f"Failed to initialize InfluxDB client: {e}")
    
    def emit(self, record: logging.LogRecord):
        """
        Emit a log record to InfluxDB.
        
        Expects record.extra_influx_fields to contain:
        - measurement: str
        - tags: dict
        - fields: dict  
        - time_ns: Optional[int]
        """
        try:
            # Extract InfluxDB-specific data from the log record
            if not hasattr(record, 'extra_influx_fields'):
                # Skip records that don't have InfluxDB data
                return
            
            influx_data = record.extra_influx_fields
            
            # Validate required fields
            measurement = influx_data.get('measurement')
            fields = influx_data.get('fields', {})
            
            if not measurement or not fields:
                return  # Skip invalid records
            
            tags = influx_data.get('tags', {})
            time_ns = influx_data.get('time_ns')
            
            # Create InfluxDB Point
            point = Point(measurement)
            
            # Add tags
            for tag_key, tag_value in tags.items():
                point = point.tag(tag_key, str(tag_value))
            
            # Add fields
            for field_key, field_value in fields.items():
                # Handle different field types
                if isinstance(field_value, (int, float)):
                    point = point.field(field_key, field_value)
                else:
                    point = point.field(field_key, str(field_value))
            
            # Set timestamp if provided
            if time_ns:
                point = point.time(time_ns)
            
            # Write to InfluxDB
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            
        except Exception as e:
            # Use handleError to follow logging conventions
            self.handleError(record)
    
    def close(self):
        """Close the InfluxDB client and write API"""
        try:
            if hasattr(self, 'write_api') and self.write_api:
                self.write_api.close()
        except Exception:
            pass
        
        try:
            if hasattr(self, 'influx_client') and self.influx_client:
                self.influx_client.close()
        except Exception:
            pass 