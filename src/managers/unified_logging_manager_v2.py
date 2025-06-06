import sys
import logging
import logging.handlers
import threading
import time
import asyncio
import json
import structlog
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Any
from pythonjsonlogger import jsonlogger
from .unified_logging_definitions import LogLevel, LogSource, LogEntry


class FallbackLogger:
    def __init__(self, emergency_file_path: Optional[str] = None, max_bytes: int = 5*1024*1024, backup_count: int = 2):
        self._file_handler = None
        
        # Set up stderr handler (existing functionality)
        self._stderr_handler = logging.StreamHandler(sys.stderr)
        # Use epoch milliseconds formatter for consistency
        from src.utils.epoch_timestamp_formatter import create_fallback_formatter
        fallback_formatter = create_fallback_formatter()
        # Override format to include emergency prefix
        class EmergencyFallbackFormatter(logging.Formatter):
            def format(self, record):
                timestamp_millis = int(record.created * 1000)
                return f"EMERGENCY_LOG_FALLBACK: [{timestamp_millis}] {record.levelname} - {record.getMessage()}"
        
        self._stderr_handler.setFormatter(EmergencyFallbackFormatter())
        
        # Set up file handler if emergency_file_path is provided
        if emergency_file_path:
            try:
                self._file_handler = logging.handlers.RotatingFileHandler(
                    emergency_file_path, 
                    mode='a', 
                    maxBytes=max_bytes, 
                    backupCount=backup_count, 
                    encoding='utf-8'
                )
                # Use standardized JSON formatter with epoch milliseconds
                from src.utils.epoch_timestamp_formatter import create_json_formatter
                formatter = create_json_formatter()
                self._file_handler.setFormatter(formatter)
            except Exception as e:
                # If file handler setup fails, continue with stderr only
                print(f"CRITICAL_FALLBACK_FAILURE: Could not create emergency file handler for {emergency_file_path}: {e}", file=sys.stderr)
                self._file_handler = None
        
        # Create a logger instance for this fallback system
        self._logger = logging.getLogger("FallbackLoggerInternal")
        self._logger.addHandler(self._stderr_handler)
        if self._file_handler:
            self._logger.addHandler(self._file_handler)
        self._logger.setLevel(logging.WARNING)  # Log warnings and above
        self._logger.propagate = False  # Prevent double logging if root logger is configured

    def _log(self, level: int, message: str, **kwargs):
        try:
            # For stderr handler, format the message including kwargs
            full_message = message
            if kwargs:
                # Convert kwargs to a string representation for stderr
                extra_info = ", ".join(f"{k}={v}" for k, v in kwargs.items())
                full_message = f"{message} ({extra_info})"
            
            # If we have both stderr and file handlers, we need to handle them differently
            if self._file_handler and len(self._logger.handlers) > 1:
                # Handle stderr with formatted message
                stderr_record = self._logger.makeRecord(
                    name=self._logger.name,
                    level=level,
                    fn="",
                    lno=0,
                    msg=full_message,  # Formatted message for stderr
                    args=(),
                    exc_info=None,
                    func=""
                )
                self._stderr_handler.emit(stderr_record)
                
                # Handle file with original message and extra fields
                file_record = self._logger.makeRecord(
                    name=self._logger.name,
                    level=level,
                    fn="",
                    lno=0,
                    msg=message,  # Original message for JSON
                    args=(),
                    exc_info=None,
                    func="",
                    extra=kwargs  # Pass kwargs as extra fields for JSON
                )
                self._file_handler.emit(file_record)
            else:
                # Only one handler, use normal logging
                if kwargs:
                    self._logger.log(level, message, extra=kwargs)
                else:
                    self._logger.log(level, full_message)
                    
        except Exception as e:
            # Ultimate fallback if logger itself fails - use epoch milliseconds
            from src.managers.unified_logging_definitions import get_epoch_millis
            timestamp_millis = get_epoch_millis()
            print(f"CRITICAL_FALLBACK_FAILURE: [{timestamp_millis}] {message} - Error: {e}", file=sys.stderr)

    def warning(self, message: str, **kwargs):
        self._log(logging.WARNING, message, **kwargs)

    def info(self, message: str, **kwargs):
        self._log(logging.INFO, message, **kwargs)

    def error(self, message: str, **kwargs):
        self._log(logging.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs):
        self._log(logging.CRITICAL, message, **kwargs)
    
    def close(self):
        """Close and cleanup handlers"""
        try:
            if self._file_handler:
                self._file_handler.close()
                self._logger.removeHandler(self._file_handler)
                self._file_handler = None
        except Exception as e:
            print(f"CRITICAL_FALLBACK_FAILURE: Error closing file handler: {e}", file=sys.stderr)


class FrequencyController:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_log_times: Dict[LogSource, float] = {}
        self._intervals: Dict[LogSource, float] = {}
        self._default_intervals: Dict[LogSource, float] = {}
        
        # Set initial default intervals
        self.set_default_intervals()
    
    def set_default_intervals(self):
        """Set the default logging intervals for each source"""
        self._default_intervals = {
            LogSource.MAIN_APP: 0.0,  # No throttling
            LogSource.FRIDA: 0.1,     # 100ms
            LogSource.BLUESTACKS: 0.1,  # 100ms
            LogSource.PSLIST: 1.0,    # 1 second
            LogSource.LOGCAT: 0.5,    # 500ms
            LogSource.DATABASE: 0.0,  # No throttling
            LogSource.SYSTEM: 0.0,    # No throttling
            LogSource.FALLBACK_SYSTEM: 0.0  # No throttling
        }
        
        # Apply defaults to current intervals
        with self._lock:
            self._intervals = self._default_intervals.copy()
    
    def set_interval(self, source: LogSource, interval_seconds: float):
        """Set custom interval for a specific log source"""
        with self._lock:
            self._intervals[source] = interval_seconds
    
    def reset_to_default(self, source: LogSource):
        """Reset a specific source to its default interval"""
        with self._lock:
            if source in self._default_intervals:
                self._intervals[source] = self._default_intervals[source]
    
    def get_default_interval(self, source: LogSource) -> float:
        """Get the default interval for a source"""
        return self._default_intervals.get(source, 0.0)
    
    def should_log(self, source: LogSource) -> bool:
        """Check if enough time has passed since last log for this source"""
        current_time = time.time()
        
        with self._lock:
            # Get the interval for this source (default to 0 if not set)
            interval = self._intervals.get(source, 0.0)
            
            # If no throttling (interval is 0), always log
            if interval <= 0:
                self._last_log_times[source] = current_time
                return True
            
            # Check last log time
            last_log_time = self._last_log_times.get(source, 0.0)
            
            # If enough time has passed, allow logging
            if current_time - last_log_time >= interval:
                self._last_log_times[source] = current_time
                return True
            
            return False


class ConsoleFilter:
    def __init__(self):
        self._enabled_sources: Set[LogSource] = set()
        self._min_level: LogLevel = LogLevel.INFO  # Default minimum level
        self._level_order = {  # Define numerical order for levels
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.WARNING: 2,
            LogLevel.ERROR: 3,
            LogLevel.CRITICAL: 4
        }
        # Default: enable all sources for console initially
        self.enable_all_sources()

    def enable_source(self, source: LogSource):
        self._enabled_sources.add(source)

    def disable_source(self, source: LogSource):
        self._enabled_sources.discard(source)

    def enable_all_sources(self):
        self._enabled_sources = set(LogSource)  # Add all defined LogSource members

    def set_min_level(self, level: LogLevel):
        self._min_level = level

    def should_show_in_console(self, entry: LogEntry) -> bool:
        # Always show CRITICAL, ERROR, WARNING regardless of source filter or min_level
        if entry.level in [LogLevel.CRITICAL, LogLevel.ERROR, LogLevel.WARNING]:
            return True

        # Check if source is enabled
        if entry.source not in self._enabled_sources:
            return False

        # Check minimum level for other logs
        entry_level_value = self._level_order.get(entry.level, -1)
        min_level_value = self._level_order.get(self._min_level, self._level_order[LogLevel.INFO])

        return entry_level_value >= min_level_value 


class AsyncLogQueue:
    def __init__(self, max_size: int = 1000, put_timeout: float = 0.1):
        self.max_size = max_size
        self.put_timeout = put_timeout
        self.queue: Optional[asyncio.Queue] = None  # Defer creation to backend event loop
        self._dropped_count = 0
        self._shutdown = False
        self._last_drop_warning_time = 0.0
        self._drop_warning_interval_seconds = 10.0  # Warning every 10 seconds when dropping
    
    def initialize_queue(self, loop: asyncio.AbstractEventLoop):
        """Initialize the queue in the context of the provided event loop"""
        if self.queue is None:
            # Create queue in the current event loop context
            self.queue = asyncio.Queue(maxsize=self.max_size)
            # Note: Using print here since this is internal queue initialization
            # and occurs before the full logging system is available
            pass
        else:
            # Queue already initialized - this is fine
            pass
    
    async def put(self, entry: LogEntry):
        """Put a log entry into the queue with timeout handling"""
        assert self.queue is not None, "AsyncLogQueue.queue was not initialized before use!"
        try:
            await asyncio.wait_for(self.queue.put(entry), timeout=self.put_timeout)
        except asyncio.TimeoutError:
            # Queue is full, increment dropped count
            self._dropped_count += 1
            current_time = time.time()
            
            # Periodically warn about dropped logs
            if (current_time - self._last_drop_warning_time) >= self._drop_warning_interval_seconds:
                self._last_drop_warning_time = current_time
                try:
                    # Create a warning log entry about dropped logs
                    warning_entry = LogEntry(
                        source=LogSource.SYSTEM,
                        level=LogLevel.WARNING,
                        message=f"Log queue full - dropped {self._dropped_count} log entries",
                        extra_data={"dropped_count": self._dropped_count, "queue_size": self.queue.qsize()}
                    )
                    # Try to put the warning with a very short timeout to avoid blocking
                    await asyncio.wait_for(self.queue.put(warning_entry), timeout=0.01)
                except asyncio.TimeoutError:
                    # If we can't even queue the warning, that's fine - don't block
                    pass
    
    async def get(self) -> Optional[LogEntry]:
        """Get a log entry from the queue with timeout"""
        assert self.queue is not None, "AsyncLogQueue.queue was not initialized before use!"
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None
    
    def get_stats(self) -> Dict[str, int]:
        """Get queue statistics"""
        return {
            "current_size": self.queue.qsize() if self.queue is not None else 0,
            "max_size": self.max_size,
            "dropped_count": self._dropped_count
        }
    
    def shutdown(self):
        """Signal shutdown"""
        self._shutdown = True


class UnifiedLoggingManager:
    def __init__(self, enable_console: bool = True, 
                 console_min_level_str: str = "INFO",
                 console_filters_config: Optional[Dict[str, bool]] = None,
                 fallback_logger_config: Optional[Dict[str, Any]] = None,
                 loki_failure_fallback_config: Optional[Dict[str, Any]] = None,
                 enable_loki: bool = False,
                 loki_url: Optional[str] = None,
                 loki_default_labels: Optional[Dict[str, str]] = None,
                 influx_config: Optional[dict] = None,
                 enable_influxdb: bool = False,
                 gui_signal_emitter: Optional[Any] = None):
        # Store configuration
        self.enable_console = enable_console
        self.console_min_level_str = console_min_level_str
        self.console_filters_config = console_filters_config or {}
        
        # Extract fallback logger config
        self.fallback_logger_config = fallback_logger_config or {}
        fallback_emergency_path = self.fallback_logger_config.get('emergency_file_path')
        fallback_max_bytes = self.fallback_logger_config.get('max_bytes', 5*1024*1024)
        fallback_backup_count = self.fallback_logger_config.get('backup_count', 2)
        
        # Extract loki failure fallback config
        self.loki_failure_fallback_config = loki_failure_fallback_config or {}
        self.loki_failure_fallback_file_path = self.loki_failure_fallback_config.get('file_path')
        self.loki_failure_fallback_max_bytes = self.loki_failure_fallback_config.get('max_bytes', 5*1024*1024)
        self.loki_failure_fallback_backup_count = self.loki_failure_fallback_config.get('backup_count', 2)
        
        self.enable_loki = enable_loki
        self.loki_url = loki_url or self._get_default_loki_url()
        self.loki_default_labels = loki_default_labels or {
            "job": "tower_hooker_unified",
            "environment": "development"
        }
        
        self.enable_influxdb = enable_influxdb
        self.influx_config = influx_config
        
        # Store GUI signal emitter
        self.gui_signal_emitter = gui_signal_emitter
        
        # Initialize core components
        self.fallback_logger = FallbackLogger(
            emergency_file_path=fallback_emergency_path,
            max_bytes=fallback_max_bytes,
            backup_count=fallback_backup_count
        )
        self.frequency_controller = FrequencyController()
        self.console_filter = ConsoleFilter()
        self.log_queue = AsyncLogQueue()  # Queue creation deferred to backend event loop
        self._log_queue_initialized = False  # Track queue initialization status
        
        # Configure console filter
        self._configure_console_filter()
        
        # Initialize handlers
        self.console_handler: Optional[logging.Handler] = None
        self.loki_handler: Optional[logging.Handler] = None
        self.loki_failure_fallback_file_handler: Optional[logging.Handler] = None
        self.influx_handler: Optional[logging.Handler] = None
        self.qt_gui_handler: Optional[logging.Handler] = None
        
        # Loki failure tracking
        self._loki_is_failing: bool = False
        self._loki_consecutive_failures: int = 0
        self._loki_failure_threshold: int = 3
        
        # Initialize async processing
        self._log_processor_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        # Setup handlers and structlog
        self._setup_handlers()
        self._setup_structlog()
    
    def _get_default_loki_url(self) -> str:
        """Get default Loki URL from configuration or fallback"""
        try:
            from src.utils.config import get_loki_url
            return get_loki_url()
        except Exception:
            return "http://localhost:3100/loki/api/v1/push"
    
    def _configure_console_filter(self):
        """Configure console filter based on console_filters_config and min level"""
        from .unified_logging_definitions import LogLevel, LogSource
        
        # Set minimum level from string
        try:
            min_level = LogLevel(self.console_min_level_str.upper())
            self.console_filter.set_min_level(min_level)
        except ValueError:
            self.fallback_logger.warning(f"Invalid console min level: {self.console_min_level_str}, using INFO")
            self.console_filter.set_min_level(LogLevel.INFO)
        
        # Configure source filters
        for source_name, enabled in self.console_filters_config.items():
            try:
                # Get LogSource by member name (e.g., "PSLIST" -> LogSource.PSLIST)
                source = getattr(LogSource, source_name.upper())
                if enabled:
                    self.console_filter.enable_source(source)
                else:
                    self.console_filter.disable_source(source)
            except AttributeError:
                self.fallback_logger.warning(f"Unknown log source in config: {source_name}")
    
    def _setup_handlers(self):
        """Setup all log handlers (Console, Loki, and Fallback File)"""
        try:
            # Console handler setup with epoch milliseconds formatter
            if self.enable_console:
                self.console_handler = logging.StreamHandler(sys.stdout)
                self.console_handler.setLevel(logging.DEBUG)  # Allow all, ConsoleFilter will do fine-grained filtering
                from src.utils.epoch_timestamp_formatter import create_console_formatter
                console_formatter = create_console_formatter(include_human_readable=True)
                self.console_handler.setFormatter(console_formatter)
            
            # Loki handler setup
            if self.enable_loki:
                try:
                    from src.utils.loki_logging_handler import create_loki_handler
                    self.loki_handler = create_loki_handler(self.loki_url, self.loki_default_labels)
                    if self.loki_handler:
                        self.loki_handler.setLevel(logging.DEBUG)  # Allow all levels for Loki
                        self.fallback_logger.info("Loki handler initialized successfully", 
                                                loki_url=self.loki_url)
                    else:
                        self.fallback_logger.warning("Loki handler creation failed - library not available")
                        self.enable_loki = False  # Disable Loki if handler creation failed
                except Exception as e:
                    self.fallback_logger.error("Failed to create Loki handler", 
                                             error=str(e), loki_url=self.loki_url)
                    self.enable_loki = False  # Disable Loki on error
            
            # Loki failure fallback file handler setup
            if self.loki_failure_fallback_file_path:
                try:
                    self.loki_failure_fallback_file_handler = logging.handlers.RotatingFileHandler(
                        filename=self.loki_failure_fallback_file_path,
                        maxBytes=self.loki_failure_fallback_max_bytes,
                        backupCount=self.loki_failure_fallback_backup_count
                    )
                    self.loki_failure_fallback_file_handler.setLevel(logging.DEBUG)
                    
                    # Use standardized JSON formatter with epoch milliseconds
                    from src.utils.epoch_timestamp_formatter import create_json_formatter
                    json_formatter = create_json_formatter()
                    self.loki_failure_fallback_file_handler.setFormatter(json_formatter)
                    
                    # Give it a distinctive name for identification
                    self.loki_failure_fallback_file_handler.set_name("LokiFailureFallbackFileHandler")
                    
                except Exception as e:
                    self.fallback_logger.error("Failed to create Loki failure fallback file handler", 
                                             error=str(e), file_path=self.loki_failure_fallback_file_path)
                    self.loki_failure_fallback_file_handler = None
            
            # InfluxDB handler setup
            if self.enable_influxdb and self.influx_config:
                try:
                    from src.utils.simple_influxdb_handler import InfluxDBLoggingHandler
                    self.influx_handler = InfluxDBLoggingHandler(**self.influx_config)
                    self.influx_handler.setLevel(logging.DEBUG)  # Allow all levels for InfluxDB
                    self.fallback_logger.info("InfluxDB handler initialized successfully", 
                                            url=self.influx_config.get('url'))
                except Exception as e:
                    self.fallback_logger.error("Failed to create InfluxDB handler", 
                                             error=str(e), config=str(self.influx_config))
                    self.influx_handler = None
                    self.enable_influxdb = False  # Disable InfluxDB on error
                    
            # Qt GUI handler setup
            if self.gui_signal_emitter is not None:
                try:
                    from src.utils.qt_logging_handler import QtSignalLogHandler
                    self.qt_gui_handler = QtSignalLogHandler(self.gui_signal_emitter)
                    # Set level to INFO to filter what goes to GUI (can be made configurable)
                    self.qt_gui_handler.setLevel(logging.INFO)
                    # Set a simple formatter
                    formatter = logging.Formatter('%(message)s')
                    self.qt_gui_handler.setFormatter(formatter)
                    self.fallback_logger.info("Qt GUI signal handler initialized successfully")
                except Exception as e:
                    self.fallback_logger.error("Failed to create Qt GUI signal handler", error=str(e))
                    self.qt_gui_handler = None
        
        except Exception as e:
            self.fallback_logger.error("Critical error in handler setup", error=str(e))
    
    def _setup_structlog(self):
        """Configure structlog with processors"""
        try:
            processors = [
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),  # Handles non-ASCII chars
                structlog.processors.JSONRenderer(serializer=lambda **kwargs: json.dumps(kwargs, default=str))  # For robust JSON
            ]
            structlog.configure(
                processors=processors,
                logger_factory=structlog.stdlib.LoggerFactory(),
                wrapper_class=structlog.stdlib.BoundLogger,
                cache_logger_on_first_use=True,
            )
        except Exception as e:
            self.fallback_logger.error("Failed to setup structlog", error=str(e))
    
    async def start_log_processor(self):
        """Start background log processor AND initialize the queue in the current event loop."""
        if not self._log_queue_initialized:
            try:
                current_loop = asyncio.get_running_loop()
                self.log_queue.initialize_queue(current_loop)  # Pass the current (backend) loop
                self._log_queue_initialized = True
                self.fallback_logger.info("AsyncLogQueue initialized successfully in backend loop.")
            except RuntimeError:  # No running loop (should not happen if called from async context)
                self.fallback_logger.critical("CRITICAL: Could not get running loop to initialize AsyncLogQueue.")
                self._shutdown_event.set()  # Prevent processor from starting if queue fails
                return
            except Exception as e:
                self.fallback_logger.critical(f"CRITICAL: Failed to initialize AsyncLogQueue: {e}")
                self._shutdown_event.set()
                return

        if self._log_processor_task is None and not self._shutdown_event.is_set():
            self._log_processor_task = asyncio.create_task(self._process_logs())
            self.fallback_logger.info("Log processor task created.")
        elif self._log_processor_task is not None:
            self.fallback_logger.info("Log processor task already exists or shutdown initiated.")
    
    def _should_send_to_influx(self, entry: LogEntry) -> bool:
        """
        Determine if a log entry should be sent to InfluxDB.
        Returns True if the entry contains measurement and fields data.
        """
        if not entry.extra_data:
            return False
        
        # Check if the log entry has InfluxDB-specific fields
        has_measurement = "measurement" in entry.extra_data
        has_fields = "fields" in entry.extra_data
        
        return has_measurement and has_fields
        
    def _should_send_to_gui(self, entry: LogEntry) -> bool:
        """
        Determine if a log entry should be sent to the GUI.
        Returns True for INFO and above from specific sources.
        """
        from .unified_logging_definitions import LogLevel, LogSource
        
        # Only send INFO and above to GUI
        if entry.level.value not in ['INFO', 'WARNING', 'ERROR', 'CRITICAL']:
            return False
            
        # Send logs from these sources to GUI
        gui_sources = [
            LogSource.MAIN_APP, 
            LogSource.BLUESTACKS, 
            LogSource.FRIDA, 
            LogSource.SYSTEM, 
            LogSource.FALLBACK_SYSTEM
        ]
        
        return entry.source in gui_sources

    async def _process_logs(self):
        """Main log processing loop (async)"""
        try:
            while not self._shutdown_event.is_set():
                try:
                    entry: Optional[LogEntry] = await self.log_queue.get()
                    if entry is None:  # Queue timeout, but not shutdown
                        continue
                    if entry is not None:
                        await self._write_log_entry(entry)
                except Exception as e:
                    self.fallback_logger.error("Error in log processor loop", error=str(e))
            
            # After the while loop (when _shutdown_event is set), process any remaining items in the queue
            self.fallback_logger.info("Log processor shutting down. Processing remaining queue items.")
            if self.log_queue.queue is not None:
                while not self.log_queue.queue.empty():
                    entry = await self.log_queue.get()  # This should get immediately if not empty
                    if entry:
                        await self._write_log_entry(entry)
                    else:  # Should not happen if queue not empty, but as a guard
                        break
            self.fallback_logger.info("Log processor shutdown complete.")
        except Exception as e:
            self.fallback_logger.error("Critical error in log processor", error=str(e))
    
    async def _write_log_entry(self, entry: LogEntry):
        """Write a log entry to configured outputs (Console and Loki with fallback)"""
        
        # Loki handler integration (real implementation)
        loki_send_successful = False
        
        # Check if we have a special flag in extra_data to simulate Loki failure for testing
        simulate_loki_failure = entry.extra_data.get('_simulate_loki_failure', False) if entry.extra_data else False
        
        # Try Loki if enabled and handler is available
        # When _loki_is_failing is True, we skip Loki and go directly to fallback
        if self.enable_loki and self.loki_handler and not self._loki_is_failing:
            try:
                # Create a standard LogRecord for the Loki handler
                loki_log_record = logging.LogRecord(
                    name=entry.source.value,
                    level=getattr(logging, entry.level.value.upper()),
                    pathname="",
                    lineno=0,
                    msg=entry.message,
                    args=(),
                    exc_info=None,
                    func=""
                )
                
                # Add structured data to the record for Loki label extraction
                if entry.extra_data:
                    for k, v in entry.extra_data.items():
                        # Skip internal simulation flags 
                        if k.startswith('_simulate_'):
                            continue
                        # Add extra data as record attributes for the Loki handler to process
                        setattr(loki_log_record, k, v)
                
                # Add source as an attribute for Loki labeling
                setattr(loki_log_record, 'source', entry.source.value)
                
                # Test simulation override
                if simulate_loki_failure:
                    raise ConnectionError("Simulated Loki connection error")
                
                # Send to Loki
                self.loki_handler.emit(loki_log_record)
                
                # If we get here, Loki send was successful
                loki_send_successful = True
                # Reset failure tracking on success
                self._loki_consecutive_failures = 0
                if self._loki_is_failing:
                    self.fallback_logger.info("Loki connection restored after previous failures")
                    self._loki_is_failing = False
                    
            except Exception as e:
                self._loki_consecutive_failures += 1
                self.fallback_logger.error("Loki handler error", 
                                         error=str(e), 
                                         attempt=self._loki_consecutive_failures,
                                         source=entry.source.value)
                
                if self._loki_consecutive_failures >= self._loki_failure_threshold:
                    if not self._loki_is_failing:
                        self.fallback_logger.critical("Loki is considered failing after multiple errors. Switching to file fallback.",
                                                     consecutive_failures=self._loki_consecutive_failures)
                    self._loki_is_failing = True
        
        # Loki failure fallback file output (if Loki failed to send OR if Loki is in failing state)
        if (not loki_send_successful or self._loki_is_failing) and self.loki_failure_fallback_file_handler:
            try:
                # Create a standard LogRecord for the file handler with fallback indicator in name
                file_log_record = logging.LogRecord(
                    name=f"{entry.source.value}.loki_fallback",  # Indicate it's a fallback
                    level=getattr(logging, entry.level.value.upper()),
                    pathname="",
                    lineno=0,
                    msg=entry.message,
                    args=(),
                    exc_info=None,
                    func=""
                )
                
                # Add extra_data for JSON output
                if entry.extra_data:
                    for k, v in entry.extra_data.items():
                        # Skip internal simulation flags and avoid overwriting standard LogRecord attributes
                        if k.startswith('_simulate_') or hasattr(file_log_record, k):
                            continue
                        setattr(file_log_record, k, v)
                
                self.loki_failure_fallback_file_handler.emit(file_log_record)
            except Exception as e_file:
                self.fallback_logger.error("Loki failure fallback file_handler error", 
                                         error=str(e_file), source=entry.source.value)
        
        # Loki recovery test - periodically test if Loki is working again
        # Only test recovery occasionally to avoid spam
        if (self._loki_is_failing and self.enable_loki and self.loki_handler and 
            self._loki_consecutive_failures % 10 == 0):  # Test every 10th failure
            try:
                # Create a simple test log record
                test_record = logging.LogRecord(
                    name="recovery_test",
                    level=logging.INFO,
                    pathname="",
                    lineno=0,
                    msg="Loki recovery test",
                    args=(),
                    exc_info=None,
                    func=""
                )
                
                # Try to send to Loki
                self.loki_handler.emit(test_record)
                
                # If successful, reset failure state
                self._loki_consecutive_failures = 0
                self._loki_is_failing = False
                self.fallback_logger.info("Loki connection restored after recovery test")
                
            except Exception:
                # Recovery test failed, increment counter and continue using fallback
                self._loki_consecutive_failures += 1
        
        # InfluxDB output (for metric-like logs)
        if self.influx_handler and self._should_send_to_influx(entry):
            try:
                # Prepare data for InfluxDBLoggingHandler
                # The handler expects 'measurement', 'tags', 'fields', 'time_ns' in extra_influx_fields
                influx_payload = {
                    "measurement": entry.extra_data.get("measurement"),
                    "tags": entry.extra_data.get("tags", {}),
                    "fields": entry.extra_data.get("fields", {}),
                    "time_ns": int(entry.timestamp_millis * 1e6)  # Convert millis to nanoseconds
                }
                
                # Filter out None measurement, or empty fields
                if not influx_payload["measurement"] or not influx_payload["fields"]:
                    self.fallback_logger.warning("Skipping InfluxDB log due to missing measurement or fields", 
                                                entry_message=entry.message)
                else:
                    # Create a standard LogRecord
                    influx_std_log_record = logging.LogRecord(
                        name=f"{entry.source.value}.influx",
                        level=getattr(logging, entry.level.value.upper()),
                        pathname="", lineno=0, msg=entry.message, args=(), exc_info=None, func=''
                    )
                    # Attach the payload for the InfluxDB handler
                    influx_std_log_record.extra_influx_fields = influx_payload
                    self.influx_handler.handle(influx_std_log_record)
            except Exception as e:
                self.fallback_logger.error("InfluxDB handler error during emit", error=str(e))
        
        # Console output
        if self.console_handler and self.console_filter.should_show_in_console(entry):
            try:
                # Create a standard logging LogRecord from our LogEntry
                std_log_record = logging.LogRecord(
                    name=entry.source.value,
                    level=getattr(logging, entry.level.value.upper()),  # e.g., logging.INFO
                    pathname="",
                    lineno=0,
                    msg=entry.message,
                    args=(),
                    exc_info=None,
                    func=""
                )
                
                # Add extra_data to the record so formatters can use it
                if entry.extra_data:
                    for k, v in entry.extra_data.items():
                        # Skip internal simulation flags and avoid overwriting standard LogRecord attributes
                        if k.startswith('_simulate_') or hasattr(std_log_record, k):
                            continue
                        setattr(std_log_record, k, v)
                
                # Emit the log record
                self.console_handler.emit(std_log_record)
            except Exception as e:
                self.fallback_logger.error("Console handler error", error=str(e))
                
        # Qt GUI handler output (send specific logs to GUI view)
        if self.qt_gui_handler and self._should_send_to_gui(entry):
            try:
                # Create a standard logging LogRecord from our LogEntry
                gui_std_log_record = logging.LogRecord(
                    name=entry.source.value,
                    level=getattr(logging, entry.level.value.upper()),  # e.g., logging.INFO
                    pathname="",
                    lineno=0,
                    msg=entry.message,
                    args=(),
                    exc_info=None,
                    func=""
                )
                
                # Add extra_data to the record so formatters can use it
                if entry.extra_data:
                    for k, v in entry.extra_data.items():
                        # Skip internal simulation flags and avoid overwriting standard LogRecord attributes
                        if k.startswith('_simulate_') or hasattr(gui_std_log_record, k):
                            continue
                        setattr(gui_std_log_record, k, v)
                
                # Emit the log record to GUI
                self.qt_gui_handler.handle(gui_std_log_record)
            except Exception as e:
                self.fallback_logger.error("Qt GUI handler error", error=str(e))
    
    async def log_async(self, source: LogSource, level: LogLevel, message: str, **extra_data):
        """Log a message asynchronously"""
        if not self._log_queue_initialized:
            self.fallback_logger.error("Attempted to log_async before log queue was initialized.", 
                                     source=source.value, original_message=message)
            return
            
        if not self.frequency_controller.should_log(source):
            return
        
        entry = LogEntry(source, level, message, extra_data)
        await self.log_queue.put(entry)
    
    def log_info(self, source: LogSource, message: str, **extra_data):
        """Log an info message (sync convenience method)"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.log_async(source, LogLevel.INFO, message, **extra_data))
        except RuntimeError:  # No loop
            self.fallback_logger.warning(f"No async loop for INFO log [{source.value}] {message}")
    
    def log_warning(self, source: LogSource, message: str, **extra_data):
        """Log a warning message (sync convenience method)"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.log_async(source, LogLevel.WARNING, message, **extra_data))
        except RuntimeError:  # No loop
            self.fallback_logger.warning(f"WARNING [{source.value}] {message}", **extra_data)
    
    def log_error(self, source: LogSource, message: str, **extra_data):
        """Log an error message (sync convenience method)"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.log_async(source, LogLevel.ERROR, message, **extra_data))
        except RuntimeError:  # No loop
            self.fallback_logger.error(f"ERROR [{source.value}] {message}", **extra_data)
    
    async def shutdown(self):
        """Shutdown the logging manager gracefully"""
        # Signal shutdown to the processor
        self._shutdown_event.set()
        
        # Wait for log processor to finish
        if self._log_processor_task:
            try:
                await asyncio.wait_for(self._log_processor_task, timeout=5.0)  # Wait for graceful finish
            except asyncio.TimeoutError:
                self.fallback_logger.warning("Log processor task timed out during shutdown.")
                # Force cancel if it didn't finish gracefully
                self._log_processor_task.cancel()
                try:
                    await self._log_processor_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                # Expected if task was cancelled elsewhere
                pass
        
        # Close handlers
        if self.console_handler:
            try:
                self.console_handler.close()
            except Exception as e:
                self.fallback_logger.error("Error closing console handler", error=str(e))
        
        if self.loki_handler:
            try:
                self.loki_handler.close()
            except Exception as e:
                self.fallback_logger.error("Error closing Loki handler", error=str(e))
        
        if self.loki_failure_fallback_file_handler:
            try:
                self.loki_failure_fallback_file_handler.close()
            except Exception as e:
                self.fallback_logger.error("Error closing Loki failure fallback file handler", error=str(e))
        
        if self.influx_handler:
            try:
                self.influx_handler.close()
            except Exception as e:
                self.fallback_logger.error("Error closing InfluxDB handler", error=str(e))
        
        # Close Qt GUI handler
        if self.qt_gui_handler:
            try:
                self.qt_gui_handler.close()
                # Clear the reference to prevent further attempts to emit
                self.qt_gui_handler = None
            except Exception as e:
                self.fallback_logger.error("Error closing Qt GUI handler", error=str(e))
        
        # Close the fallback logger itself
        try:
            self.fallback_logger.close()
        except Exception as e:
            print(f"Error closing fallback logger: {e}", file=sys.stderr)


# Global logging manager instance
_logging_manager: Optional[UnifiedLoggingManager] = None


def get_logging_manager() -> Optional[UnifiedLoggingManager]:
    """Get the global logging manager instance"""
    return _logging_manager


def set_logging_manager(manager: UnifiedLoggingManager) -> None:
    """Set the global logging manager instance"""
    global _logging_manager
    _logging_manager = manager


def log_info(source: LogSource, message: str, **extra_data):
    """Global convenience function for logging info messages"""
    manager = get_logging_manager()
    if manager:
        manager.log_info(source, message, **extra_data)
    else:
        print(f"INFO [{source.value}] {message}")


def log_warning(source: LogSource, message: str, **extra_data):
    """Global convenience function for logging warning messages"""
    manager = get_logging_manager()
    if manager:
        manager.log_warning(source, message, **extra_data)
    else:
        print(f"WARNING [{source.value}] {message}")


def log_error(source: LogSource, message: str, **extra_data):
    """Global convenience function for logging error messages"""
    manager = get_logging_manager()
    if manager:
        manager.log_error(source, message, **extra_data)
    else:
        print(f"ERROR [{source.value}] {message}")


def log_critical(source: LogSource, message: str, **extra_data):
    """Global convenience function for logging critical messages"""
    manager = get_logging_manager()
    if manager:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(manager.log_async(source, LogLevel.CRITICAL, message, **extra_data))
        except RuntimeError:  # No loop
            manager.fallback_logger.critical(f"CRITICAL [{source.value}] {message}", **extra_data)
    else:
        print(f"CRITICAL [{source.value}] {message}")


def log_debug(source: LogSource, message: str, **extra_data):
    """Global convenience function for logging debug messages"""
    manager = get_logging_manager()
    if manager:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(manager.log_async(source, LogLevel.DEBUG, message, **extra_data))
        except RuntimeError:  # No loop
            manager.fallback_logger.info(f"DEBUG [{source.value}] {message}", **extra_data)
    else:
        print(f"DEBUG [{source.value}] {message}")


async def log_async(source: LogSource, level: LogLevel, message: str, **extra_data):
    """Global convenience function for async logging"""
    manager = get_logging_manager()
    if manager:
        await manager.log_async(source, level, message, **extra_data)
    else:
        print(f"{level.value.upper()} [{source.value}] {message}") 