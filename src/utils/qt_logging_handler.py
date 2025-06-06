import logging
import weakref

class QtSignalLogHandler(logging.Handler):
    def __init__(self, signal_emitter_object_with_gui_log_feed_signal):
        super().__init__()
        # This emitter must have a signal like:
        #   gui_log_feed = pyqtSignal(str, str, str) # level_str, source_str, message_str
        # Use weak reference to avoid keeping the object alive and handle deletion gracefully
        self._signal_emitter_ref = weakref.ref(signal_emitter_object_with_gui_log_feed_signal)

    def emit(self, record: logging.LogRecord):
        try:
            # Get the signal emitter object from weak reference
            signal_emitter = self._signal_emitter_ref()
            
            # Check if the object still exists
            if signal_emitter is None:
                # Object has been deleted, do nothing
                return
                
            # Format the message (or ULM can pre-format it before creating LogEntry)
            # For simplicity, we'll just pass raw components.
            # AppOrchestrator's slot can then format it for display.
            level_str = record.levelname
            source_str = record.name # ULM sets record.name to LogSource.value
            message_str = self.format(record) # Get the formatted message

            # Double-check the object and signal still exist before emitting
            if hasattr(signal_emitter, 'gui_log_feed') and \
               hasattr(signal_emitter.gui_log_feed, 'emit'):
                try:
                    signal_emitter.gui_log_feed.emit(level_str, source_str, message_str)
                except RuntimeError as e:
                    # Handle case where object was deleted between checks
                    if "wrapped C/C++ object" in str(e) or "has been deleted" in str(e):
                        # Object was deleted, clear the weak reference and stop trying
                        self._signal_emitter_ref = lambda: None
                        return
                    else:
                        # Some other runtime error, re-raise
                        raise
        except Exception:
            self.handleError(record) # Default error handling 