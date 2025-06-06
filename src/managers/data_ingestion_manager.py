"""
Unified Data Ingestion Manager for Tower Hooker

This manager handles all types of data ingestion according to the database schema:
- System logs (application, infrastructure, logcat, pslist, etc.)
- Game metrics (periodic data like cash, coins, gems)
- Game events (method hooks, round events, wave events)
- Round metadata (round info, tier, game version)

The manager routes data to appropriate destinations:
- InfluxDB (for metrics and structured data)
- Loki (for logs and events via structured logging)
- Files (for backup/debugging when enabled)
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Union
from enum import Enum
import uuid
import logging

from .database_manager import DatabaseManager
from ..utils.config import get_config_value
from .unified_logging_manager_v2 import log_info, log_error, log_warning, log_debug, log_critical, LogSource


class DataType(Enum):
    """Types of data that can be ingested"""
    SYSTEM_LOG = "system_log"
    GAME_METRIC = "game_metric"
    GAME_EVENT = "game_event"
    ROUND_METADATA = "round_metadata"


class DataIngestionManager:
    """
    Unified manager for all data ingestion in Tower Hooker.
    Routes data to appropriate destinations based on type and configuration.
    """
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        
        # Load simplified configuration - only basic enable/disable flags
        self.game_metrics_enabled = get_config_value("DATA_INGESTION", {}).get("GAME_METRICS", {}).get("ENABLED", True)
        self.game_events_enabled = get_config_value("DATA_INGESTION", {}).get("GAME_EVENTS", {}).get("ENABLED", True)
        self.round_metadata_enabled = get_config_value("DATA_INGESTION", {}).get("ROUND_METADATA", {}).get("ENABLED", True)
        self.metrics_collection_interval = get_config_value("DATA_INGESTION", {}).get("GAME_METRICS", {}).get("COLLECTION_INTERVAL_MS", 1000)
        
        # Current round tracking
        self.current_round_id: Optional[str] = None
        self.current_round_start: Optional[datetime] = None
        
        log_info(
            LogSource.SYSTEM,
            "Data Ingestion Manager initialized",
            game_metrics_enabled=self.game_metrics_enabled,
            game_events_enabled=self.game_events_enabled,
            round_metadata_enabled=self.round_metadata_enabled,
            metrics_interval_ms=self.metrics_collection_interval
        )
    
    # --- System Logs ---
    
    async def ingest_system_log(self, source: str, message: str, level: str = "INFO", 
                              extra_data: Optional[Dict[str, Any]] = None,
                              timestamp: Optional[datetime] = None):
        """
        Ingest system log - HARDCODED: Always goes directly to Loki
        Design intent: Logs → Loki, Structured Data → InfluxDB
        
        Args:
            source: Log source (th_main_app, logcat, pslist, docker, etc.)
            message: Log message
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            extra_data: Additional structured data
            timestamp: Log timestamp (defaults to now)
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
            
        # HARDCODED: Always send to Loki via structured logging
        log_data = {
            "message": message,
            "source": source,
            "level": level,
            "timestamp": timestamp.isoformat(),
            "round_id": self.current_round_id
        }
        
        if extra_data:
            log_data.update(extra_data)
        
        # Route to Loki via TowerHookerLokiHandler with the specific source
        # Use appropriate log level method and pass source-specific structured data
        try:
            if level == "DEBUG":
                log_debug(LogSource.SYSTEM, message, **log_data)
            elif level == "WARNING":
                log_warning(LogSource.SYSTEM, message, **log_data)
            elif level == "ERROR":
                log_error(LogSource.SYSTEM, message, **log_data)
            elif level == "CRITICAL":
                log_critical(LogSource.SYSTEM, message, **log_data)
            else:
                log_info(LogSource.SYSTEM, message, **log_data)
        except Exception as e:
            # Fallback to basic logging if structured logging fails
            log_error(
                LogSource.SYSTEM,
                "Failed to send structured log to Loki",
                error=str(e),
                original_message=message,
                original_source=source
            )
    
    # --- Game Metrics ---
    
    async def ingest_game_metrics(self, round_id: str, cash: int, coins: int, gems: int,
                                timestamp: Optional[datetime] = None):
        """
        Ingest periodic game metrics (cash, coins, gems).
        
        Args:
            round_id: Current round ID
            cash: Current cash amount
            coins: Current coins amount  
            gems: Current gems amount
            timestamp: Metric timestamp (defaults to now)
        """
        if not self.game_metrics_enabled:
            return
            
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
        
        # HARDCODED: Always send to InfluxDB (design intent: structured data → InfluxDB)
        try:
            await self.db_manager.write_round_metrics_periodic_async(
                round_id=round_id,
                cash=cash,
                coins=coins,
                gems=gems,
                timestamp=timestamp
            )
        except Exception as e:
            log_error(
                LogSource.SYSTEM,
                "Failed to write game metrics to database",
                round_id=round_id,
                error=str(e)
            )
    
    # --- Game Events ---
    
    async def ingest_game_event(self, event_type: str, round_id: str, 
                              event_data: Dict[str, Any],
                              timestamp: Optional[datetime] = None):
        """
        Ingest a game event (wave start, method hook, etc.).
        
        Args:
            event_type: Type of event (round_start, wave_start, method_hook, etc.)
            round_id: Current round ID
            event_data: Event-specific data
            timestamp: Event timestamp (defaults to now)
        """
        if not self.game_events_enabled:
            return
            
        if not timestamp:
            timestamp = datetime.now(timezone.utc)
        
        # Handle wave start events specifically (they go to round_events_wave measurement)
        # HARDCODED: Always send to InfluxDB (design intent: structured data → InfluxDB)
        if event_type == "wave_start":
            try:
                await self.db_manager.write_round_events_wave_async(
                    round_id=round_id,
                    wave=event_data.get("wave", 0),
                    cash=event_data.get("cash", 0),
                    coins=event_data.get("coins", 0),
                    gems=event_data.get("gems", 0),
                    timestamp=timestamp
                )
            except Exception as e:
                log_error(
                    LogSource.SYSTEM,
                    "Failed to write wave event to database",
                    round_id=round_id,
                    error=str(e)
                )
        
        # HARDCODED: Always log event to Loki for monitoring
        # Prefix event data keys to avoid conflicts with unified logging parameters
        safe_event_data = {f"event_{k}": v for k, v in event_data.items()}
        
        log_info(
            LogSource.SYSTEM,
            "Game event",
            event_type=event_type,
            round_id=round_id,
            timestamp=timestamp.isoformat(),
            source="game_events",
            **safe_event_data
        )
    
    # --- Round Metadata ---
    
    async def ingest_round_metadata(self, round_id: str, tier: str, game_version: str,
                                  timestamp_start_round: datetime,
                                  timestamp_end_round: Optional[datetime] = None):
        """
        Ingest round metadata.
        
        Args:
            round_id: Round ID
            tier: Game tier
            game_version: Game version
            timestamp_start_round: Round start timestamp
            timestamp_end_round: Round end timestamp (optional)
        """
        if not self.round_metadata_enabled:
            return
        
        # HARDCODED: Always send to InfluxDB (design intent: structured data → InfluxDB)
        try:
            await self.db_manager.write_round_metadata_async(
                round_id=round_id,
                tier=tier,
                game_version=game_version,
                timestamp_start_round=timestamp_start_round,
                timestamp_end_round=timestamp_end_round
            )
        except Exception as e:
            log_error(
                LogSource.SYSTEM,
                "Failed to write round metadata to database",
                round_id=round_id,
                error=str(e)
            )
        
        # HARDCODED: Always log metadata to Loki for monitoring
        log_info(
            LogSource.SYSTEM,
            "Round metadata",
            round_id=round_id,
            tier=tier,
            game_version=game_version,
            timestamp_start_round=timestamp_start_round.isoformat(),
            timestamp_end_round=timestamp_end_round.isoformat() if timestamp_end_round else None,
            source="round_metadata"
        )
    
    # --- Round Management ---
    
    async def start_round(self, tier: str, game_version: str) -> str:
        """
        Start a new round and return the round ID.
        
        Args:
            tier: Game tier
            game_version: Game version
            
        Returns:
            Generated round ID
        """
        round_id = str(uuid.uuid4())
        timestamp_start = datetime.now(timezone.utc)
        
        self.current_round_id = round_id
        self.current_round_start = timestamp_start
        
        await self.ingest_round_metadata(
            round_id=round_id,
            tier=tier,
            game_version=game_version,
            timestamp_start_round=timestamp_start
        )
        
        await self.ingest_game_event(
            event_type="round_start",
            round_id=round_id,
            event_data={"tier": tier, "game_version": game_version},
            timestamp=timestamp_start
        )
        
        await self.ingest_system_log(
            source="th_main_app",
            message=f"Round started: {round_id}",
            level="INFO",
            extra_data={"tier": tier, "game_version": game_version}
        )
        
        return round_id
    
    async def end_round(self, round_id: Optional[str] = None) -> None:
        """
        End the current round.
        
        Args:
            round_id: Round ID to end (defaults to current round)
        """
        if not round_id:
            round_id = self.current_round_id
            
        if not round_id:
            log_warning(LogSource.SYSTEM, "No round to end")
            return
        
        timestamp_end = datetime.now(timezone.utc)
        
        # Update round metadata with end time
        if self.current_round_start:
            await self.ingest_round_metadata(
                round_id=round_id,
                tier="unknown",  # We don't have this info here
                game_version="unknown",  # We don't have this info here
                timestamp_start_round=self.current_round_start,
                timestamp_end_round=timestamp_end
            )
        
        await self.ingest_game_event(
            event_type="round_end",
            round_id=round_id,
            event_data={},
            timestamp=timestamp_end
        )
        
        await self.ingest_system_log(
            source="th_main_app",
            message=f"Round ended: {round_id}",
            level="INFO"
        )
        
        self.current_round_id = None
        self.current_round_start = None
    
    # --- Convenience Methods ---
    
    async def log_method_hook(self, method_name: str, args: List[Any] = None, 
                            result: Any = None, timestamp: Optional[datetime] = None):
        """
        Log a method hook event.
        
        Args:
            method_name: Name of the hooked method
            args: Method arguments
            result: Method result
            timestamp: Hook timestamp
        """
        if not self.current_round_id:
            log_warning(LogSource.SYSTEM, "Method hook logged without active round", method=method_name)
            return
        
        event_data = {
            "method_name": method_name,
            "args": args or [],
            "result": result
        }
        
        await self.ingest_game_event(
            event_type="method_hook",
            round_id=self.current_round_id,
            event_data=event_data,
            timestamp=timestamp
        )
    
    async def log_wave_start(self, wave: int, cash: int, coins: int, gems: int,
                           timestamp: Optional[datetime] = None):
        """
        Log a wave start event.
        
        Args:
            wave: Wave number
            cash: Current cash
            coins: Current coins
            gems: Current gems
            timestamp: Wave start timestamp
        """
        if not self.current_round_id:
            log_warning(LogSource.SYSTEM, "Wave start logged without active round", wave=wave)
            return
        
        event_data = {
            "wave": wave,
            "cash": cash,
            "coins": coins,
            "gems": gems
        }
        
        await self.ingest_game_event(
            event_type="wave_start",
            round_id=self.current_round_id,
            event_data=event_data,
            timestamp=timestamp
        )
    
    # --- Infrastructure Logging Helpers ---
    
    async def log_docker_health(self, service: str, status: str, details: Dict[str, Any] = None):
        """Log Docker service health status."""
        await self.ingest_system_log(
            source="docker",
            message=f"Service {service}: {status}",
            level="INFO" if status == "healthy" else "WARNING",
            extra_data={"service": service, "status": status, **(details or {})}
        )
    
    async def log_database_operation(self, operation: str, success: bool, 
                                   details: Dict[str, Any] = None):
        """Log database operation result."""
        await self.ingest_system_log(
            source="th_database",
            message=f"Database {operation}: {'success' if success else 'failed'}",
            level="INFO" if success else "ERROR",
            extra_data={"operation": operation, "success": success, **(details or {})}
        )
    
    async def log_schema_validation(self, measurement: str, success: bool, 
                                  errors: List[str] = None, warnings: List[str] = None):
        """Log schema validation result."""
        await self.ingest_system_log(
            source="th_schema",
            message=f"Schema validation for {measurement}: {'passed' if success else 'failed'}",
            level="INFO" if success else "ERROR",
            extra_data={
                "measurement": measurement,
                "success": success,
                "errors": errors or [],
                "warnings": warnings or []
            }
        ) 