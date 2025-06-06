import os
import json
import threading
import time
import logging
import structlog
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Assuming DatabaseManager is in src.managers.database_manager
# Adjust import path if necessary based on your project structure
try:
    from ..managers.database_manager import DatabaseManager, InfluxMeasurement
except ImportError:
    # Fallback for cases where the script might be run in a different context
    # or for easier testing if paths are tricky.
    # This often happens if utils/ is not part of a package recognized by Python's import system.
    # For a robust solution, ensure your project is structured as a package.
    logger = logging.getLogger("influxdb_logging_handler")
    logger.warning("Could not import DatabaseManager using relative path. Attempting fallback.")
    # This is a common pattern but might need adjustment based on how you run your project.
    # It assumes 'src' is in PYTHONPATH or the script is run from project root.
    import sys
    _CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
    _SRC_DIR = os.path.dirname(_CURRENT_FILE_DIR) # Up to 'utils', then up to 'src'
    if _SRC_DIR not in sys.path:
        sys.path.insert(0, _SRC_DIR)
    try:
        from managers.database_manager import DatabaseManager, InfluxMeasurement
    except ImportError as e:
        _log_with_context_local(logger.critical, "Failed to import DatabaseManager even with sys.path modification.", error=e, exc_info=True)
        raise ImportError("Could not import DatabaseManager. Ensure src/ is in PYTHONPATH or project structure is correct.") from e

logger = logging.getLogger("influxdb_logging_handler")

def _log_with_context_local(logger_method, message: str, **context_kwargs):
    """
    Local helper function to conditionally log with context.
    This avoids circular import with logging_manager.
    Since we're using standard logging to prevent recursion, we'll format the context into the message.
    """
    if context_kwargs:
        # Format context as key=value pairs
        context_str = " ".join(f"{k}={v}" for k, v in context_kwargs.items())
        formatted_message = f"{message} {context_str}"
    else:
        formatted_message = message
    
    # Use standard logging without keyword arguments
    logger_method(formatted_message)

class InfluxDBLoggingHandler(logging.Handler):
    """
    A logging.Handler subclass that forwards structlog logs to InfluxDB via the DatabaseManager.
    This handler is thread-safe and batches logs for efficient writing.
    """
    
    def __init__(self, 
                 db_manager: DatabaseManager, 
                 batch_size: int = 50, 
                 flush_interval: float = 5.0):
        """
        Initialize the InfluxDB log handler.
        
        Args:
            db_manager: An instance of the configured DatabaseManager for InfluxDB.
            batch_size: Number of logs to batch before writing to DB.
            flush_interval: Maximum time (seconds) between writes to DB.
        """
        super().__init__()
        if not isinstance(db_manager, DatabaseManager):
            raise TypeError("db_manager must be an instance of DatabaseManager")
            
        self.db_manager = db_manager
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        
        self.log_queue: List[Dict[str, Any]] = []
        self.queue_lock = threading.Lock()
        self.last_flush_time = time.monotonic()
        
        # Start background thread for periodic flushing
        self.stop_event = threading.Event()
        self.flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self.flush_thread.start()
        _log_with_context_local(logger.info, "InfluxDBLoggingHandler initialized.", batch_size=batch_size, flush_interval=flush_interval)
    
    def emit(self, record: logging.LogRecord):
        """
        Emit a record.

        If a formatter is specified, it is used to format the record.
        The record is then written to the stream with a trailing newline.
        If exception information is present, it is formatted using
        traceback.format_exception and appended to the stream.
        If the stream has an encoding set, it is used to encode the record.
        """
        try:
            # Our custom InfluxDBFormatter stores the processed event_dict in _structlog_event_dict
            event_dict = None
            
            # Method 1: Check if our custom formatter processed the record
            if hasattr(record, '_structlog_event_dict'):
                event_dict = record._structlog_event_dict
            
            # Method 2: Apply formatter if available to trigger processing
            elif self.formatter:
                try:
                    # Apply the formatter to trigger processing and store event_dict
                    self.formatter.format(record)
                    # Check if the formatter stored the event_dict
                    if hasattr(record, '_structlog_event_dict'):
                        event_dict = record._structlog_event_dict
                except Exception as format_error:
                    # If formatting fails, we'll create a fallback event_dict below
                    pass
            
            # Method 3: Check legacy locations for backward compatibility
            if event_dict is None:
                if hasattr(record, 'event_dict') and isinstance(record.event_dict, dict):
                    event_dict = record.event_dict
                elif hasattr(record, 'msg') and isinstance(record.msg, dict):
                    event_dict = record.msg
            
            # Method 4: Fallback - create basic event_dict from LogRecord
            if event_dict is None:
                event_dict = {
                    'event': record.getMessage(),
                    'level': record.levelname,
                    'logger': record.name,
                    'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
                }
                if record.exc_info:
                    event_dict['exception'] = self.formatException(record.exc_info)

            # Ensure 'timestamp' is in ISO format and UTC, as process_log might expect it.
            # If structlog's TimeStamper(fmt="iso", utc=True) is used, it should already be correct.
            if 'timestamp' not in event_dict or not isinstance(event_dict['timestamp'], str):
                event_dict['timestamp'] = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

            self.process_log(event_dict.copy()) # Use a copy
        except Exception:
            self.handleError(record)

    def process_log(self, log_entry: Dict[str, Any]):
        """
        Process a log entry and add it to the queue for batch insertion.
        log_entry is expected to be a dictionary from structlog.
        """
        # Add to queue
        with self.queue_lock:
            self.log_queue.append(log_entry)
            should_flush_immediately = len(self.log_queue) >= self.batch_size
        
        if should_flush_immediately:
            # If batch size reached, attempt to flush. 
            # This call is made outside the lock to prevent _flush_logs from blocking other log producers for too long if DB write is slow.
            self._trigger_flush()

    def _trigger_flush(self):
        """Triggers a log flush. Can be called internally or externally if needed."""
        # This method can be expanded if we want to make it callable from other threads, e.g. via a ThreadPoolExecutor
        # For now, it directly calls _flush_logs.
        self._flush_logs()

    def _flush_logs(self):
        """
        Flush logs from the queue to InfluxDB using DatabaseManager.
        This method extracts logs from the queue and calls db_manager.write_log_batch_sync.
        """
        records_to_flush: List[Dict[str, Any]] = []
        with self.queue_lock:
            if not self.log_queue:
                self.last_flush_time = time.monotonic() # Update flush time even if nothing to flush
                return
            
            # Swap out the current queue contents
            records_to_flush.extend(self.log_queue)
            self.log_queue.clear()
            self.last_flush_time = time.monotonic()

        if not records_to_flush:
            return

        # Remove debug logging to prevent infinite recursion
        # logger.debug(f"Flushing {len(records_to_flush)} log entries to InfluxDB...")
        try:
            # The db_manager.write_log_batch_sync method is responsible for 
            # formatting these records into InfluxDB Points and writing them.
            # It should handle its own error logging for DB write failures.
            
            # During shutdown, the database connection might be unstable
            # so we add a timeout check to prevent hanging
            start_time = time.monotonic()
            self.db_manager.write_log_batch_sync(records_to_flush)
            
            # Check if the operation took too long (potential indicator of connection issues)
            elapsed_time = time.monotonic() - start_time
            if elapsed_time > 10.0:  # If it takes more than 10 seconds, log a warning
                print(f"WARNING: InfluxDB write took {elapsed_time:.2f} seconds for {len(records_to_flush)} entries")
                
            # logger.debug(f"Successfully flushed {len(records_to_flush)} log entries.")
        except Exception as e:
            # This exception would typically be from _get_write_api if InfluxDB is down, 
            # or other unexpected errors in the sync batch write call itself.
            # Use standard logging to prevent recursion
            error_msg = f"Failed to flush {len(records_to_flush)} log entries to InfluxDB: {e}"
            print(f"ERROR: {error_msg}")
            
            # During shutdown, if the stop_event is set, don't try to re-queue logs
            # as it could cause issues during cleanup
            if hasattr(self, 'stop_event') and self.stop_event.is_set():
                print(f"INFO: Dropping {len(records_to_flush)} log entries due to shutdown in progress")
                return
                
            # Basic retry or dead-letter queue for logs could be implemented here if needed.
            # For now, logs are dropped if db_manager can't handle them.
            # Re-queueing them naively could lead to infinite loops if DB is persistently down.
            # Consider adding them back to the queue with a retry limit or to a separate fallback.
            # Example: Re-add to queue if some condition is met (not shown here to keep it simple)
            # with self.queue_lock:
            #     self.log_queue.extend(records_to_flush) # Potentially problematic
            pass

    def _periodic_flush(self):
        """Periodically flush logs based on flush_interval."""
        try:
            while not self.stop_event.is_set():
                try:
                    # Wait for the flush interval or until stop_event is set
                    # Check more frequently than flush_interval to be responsive to stop_event
                    # For example, check every 1 second or min(1.0, self.flush_interval)
                    wait_time = min(1.0, self.flush_interval)
                    if self.stop_event.wait(timeout=wait_time):
                        break # Stop event was set

                    # Check if it's time to flush based on interval
                    # No lock needed for reading last_flush_time here as it's for a heuristic check
                    if (time.monotonic() - self.last_flush_time) >= self.flush_interval:
                        with self.queue_lock: # Lock only when deciding to flush and accessing queue
                            if self.log_queue: # Only flush if there's something in the queue
                                should_flush_due_to_interval = True
                            else:
                                should_flush_due_to_interval = False
                                self.last_flush_time = time.monotonic() # Reset timer if queue is empty
                        
                        if should_flush_due_to_interval:
                            self._flush_logs()

                except Exception as e:
                    _log_with_context_local(logger.error, f"Error in periodic_flush thread: {e}", exc_info=True)
                    # Avoid busy-looping on persistent errors
                    time.sleep(self.flush_interval if self.flush_interval > 0 else 5.0)
            
            logger.info("InfluxDBLoggingHandler periodic flush thread stopped.")
            
        except Exception as e:
            _log_with_context_local(logger.error, f"Fatal error in periodic_flush thread: {e}", exc_info=True)
        finally:
            # Final flush before exiting - with additional error handling
            try:
                self._flush_logs()
            except Exception as e:
                _log_with_context_local(logger.error, f"Error during final flush in periodic_flush thread: {e}", exc_info=True)

    def close(self):
        """Signal the flush thread to stop and perform a final flush."""
        logger.info("Closing InfluxDBLoggingHandler...")
        self.stop_event.set()
        
        if self.flush_thread.is_alive():
            # Wait longer for the thread to terminate - especially important during shutdown
            # when database operations might be slower
            timeout_duration = max(10.0, self.flush_interval * 2)  # At least 10 seconds or 2x flush interval
            self.flush_thread.join(timeout=timeout_duration)
            
            if self.flush_thread.is_alive():
                logger.warning("Flush thread did not terminate gracefully.")
        
        # Perform a final flush of any remaining logs with error handling
        try:
            self._flush_logs()
        except Exception as e:
            _log_with_context_local(logger.error, f"Error during final flush in close method: {e}", exc_info=True)
        
        logger.info("InfluxDBLoggingHandler closed.")


# Example of how this might be integrated with structlog configuration (in logging_config.py)
# This is illustrative and would replace DuckDBLogHandler usage.

# In your logging_config.py, you would initialize DatabaseManager first:
# from src.managers.database_manager import DatabaseManager
# from src.utils.db_logging_handler import InfluxDBLoggingHandler # This file
# import src.config as app_config

# db_manager_instance = DatabaseManager(
#     influx_url=app_config.INFLUXDB_URL,
#     influx_token=app_config.INFLUXDB_TOKEN,
#     influx_org=app_config.INFLUXDB_ORG,
#     default_bucket_logs=app_config.INFLUXDB_BUCKET_LOGS
# )
# # It's good practice to call initialize_schema once at startup
# db_manager_instance.initialize_schema()

# influx_log_handler = InfluxDBLoggingHandler(db_manager=db_manager_instance)

# Then, in structlog.configure, you'd add it to processors:
# shared_processors = [
#     structlog.stdlib.add_logger_name,
#     structlog.stdlib.add_log_level,
#     structlog.stdlib.ProcessorFormatter.wrap_for_formatter, # If using stdlib formatter later
#     influx_log_handler, # Add the handler here
# ]

# structlog.configure(
#     processors=shared_processors,
#     logger_factory=structlog.stdlib.LoggerFactory(),
#     wrapper_class=structlog.stdlib.BoundLogger,
#     cache_logger_on_first_use=True,
# )

# And ensure to close it on application shutdown:
# import atexit
# atexit.register(influx_log_handler.close)
# atexit.register(db_manager_instance.close_connection) 