import os
import argparse
import duckdb
import time
from datetime import datetime, timedelta

# Import unified logging for when used as a module
try:
    from ..managers.unified_logging_manager_v2 import log_info, log_error, log_warning, LogSource
    _use_unified_logging = True
except ImportError:
    # Fallback for standalone script usage
    import structlog
    logger = structlog.get_logger("db_maintenance")
    _use_unified_logging = False

def _log_info(message, **kwargs):
    """Log info message using unified logging or structlog fallback."""
    if _use_unified_logging:
        log_info(LogSource.SYSTEM, message, **kwargs)
    else:
        logger.info(message, **kwargs)

def _log_error(message, **kwargs):
    """Log error message using unified logging or structlog fallback."""
    if _use_unified_logging:
        log_error(LogSource.SYSTEM, message, **kwargs)
    else:
        logger.error(message, **kwargs)

def get_default_db_path():
    """Get the default path to the logs database."""
    # Get the project root directory (3 levels up from this file)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db_dir = os.path.join(project_root, "db")
    return os.path.join(db_dir, "logs.duckdb")

def cleanup_old_logs(db_path=None, days=7, table_name="logs", dry_run=False):
    """
    Delete logs older than the specified number of days.
    
    Args:
        db_path: Path to the DuckDB database file. If None, uses the default path.
        days: Delete logs older than this many days.
        table_name: Name of the logs table.
        dry_run: If True, only show how many logs would be deleted without actually deleting them.
        
    Returns:
        int: Number of deleted log entries
    """
    if db_path is None:
        db_path = get_default_db_path()
    
    if not os.path.exists(db_path):
        _log_error(f"Database file not found: {db_path}")
        return 0
    
    try:
        # Calculate the cutoff date
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        
        # Connect to DuckDB
        conn = duckdb.connect(db_path)
        
        # Get the count of logs to be deleted
        count_query = f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < '{cutoff_date}'"
        count_result = conn.execute(count_query).fetchone()
        count = count_result[0] if count_result else 0
        
        if dry_run:
            _log_info(f"Would delete {count} logs older than {cutoff_date} (dry run)")
        else:
            if count > 0:
                # Delete old logs
                delete_query = f"DELETE FROM {table_name} WHERE timestamp < '{cutoff_date}'"
                conn.execute(delete_query)
                _log_info(f"Deleted {count} logs older than {cutoff_date}")
            else:
                _log_info(f"No logs found older than {cutoff_date}")
        
        conn.close()
        return count
    except Exception as e:
        _log_error(f"Error cleaning up old logs: {e}")
        return 0

def vacuum_database(db_path=None, table_name="logs"):
    """
    Vacuum the database to reclaim space after deleting logs.
    
    Args:
        db_path: Path to the DuckDB database file. If None, uses the default path.
        table_name: Name of the logs table.
        
    Returns:
        bool: True if successful, False otherwise
    """
    if db_path is None:
        db_path = get_default_db_path()
    
    if not os.path.exists(db_path):
        _log_error(f"Database file not found: {db_path}")
        return False
    
    try:
        # Connect to DuckDB
        conn = duckdb.connect(db_path)
        
        # Get database size before vacuum
        size_before = os.path.getsize(db_path)
        
        # Vacuum the database
        _log_info(f"Vacuuming database: {db_path}")
        conn.execute("VACUUM")
        conn.close()
        
        # Get database size after vacuum
        size_after = os.path.getsize(db_path)
        
        # Calculate size difference
        size_diff = size_before - size_after
        size_diff_mb = size_diff / (1024 * 1024)
        
        if size_diff > 0:
            _log_info(f"Database vacuum complete. Recovered {size_diff_mb:.2f} MB of space.")
        else:
            _log_info("Database vacuum complete. No space recovered.")
        
        return True
    except Exception as e:
        _log_error(f"Error vacuuming database: {e}")
        return False

def get_db_stats(db_path=None, table_name="logs"):
    """
    Get statistics about the logs database.
    
    Args:
        db_path: Path to the DuckDB database file. If None, uses the default path.
        table_name: Name of the logs table.
        
    Returns:
        dict: Database statistics
    """
    if db_path is None:
        db_path = get_default_db_path()
    
    if not os.path.exists(db_path):
        _log_error(f"Database file not found: {db_path}")
        return {}
    
    stats = {
        "db_path": db_path,
        "db_size_mb": os.path.getsize(db_path) / (1024 * 1024),
        "table_name": table_name
    }
    
    try:
        # Connect to DuckDB
        conn = duckdb.connect(db_path)
        
        # Get total log count
        total_query = f"SELECT COUNT(*) FROM {table_name}"
        total_result = conn.execute(total_query).fetchone()
        stats["total_logs"] = total_result[0] if total_result else 0
        
        # Get log count by source
        source_query = f"SELECT source, COUNT(*) FROM {table_name} GROUP BY source ORDER BY COUNT(*) DESC"
        source_results = conn.execute(source_query).fetchall()
        stats["logs_by_source"] = {source: count for source, count in source_results}
        
        # Get log count by level
        level_query = f"SELECT level, COUNT(*) FROM {table_name} GROUP BY level ORDER BY COUNT(*) DESC"
        level_results = conn.execute(level_query).fetchall()
        stats["logs_by_level"] = {level: count for level, count in level_results}
        
        # Get date range
        range_query = f"SELECT MIN(timestamp), MAX(timestamp) FROM {table_name}"
        range_result = conn.execute(range_query).fetchone()
        if range_result and range_result[0] and range_result[1]:
            stats["oldest_log"] = range_result[0]
            stats["newest_log"] = range_result[1]
        
        conn.close()
    except Exception as e:
        _log_error(f"Error getting database stats: {e}")
    
    return stats

if __name__ == "__main__":
    # Setup argument parser
    parser = argparse.ArgumentParser(description="DuckDB Log Maintenance Utility")
    parser.add_argument("--db-path", help="Path to the DuckDB database file")
    parser.add_argument("--table", default="logs", help="Name of the logs table")
    parser.add_argument("--days", type=int, default=7, help="Delete logs older than this many days")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without actually deleting")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--vacuum", action="store_true", help="Vacuum the database after cleanup")
    
    args = parser.parse_args()
    
    # Initialize structlog for console output
    import structlog
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    db_path = args.db_path or get_default_db_path()
    
    if args.stats:
        stats = get_db_stats(db_path, args.table)
        _log_info("Database Statistics", **stats)
    
    if args.dry_run or not args.stats:
        cleanup_old_logs(db_path, args.days, args.table, args.dry_run)
    
    if args.vacuum and not args.dry_run:
        vacuum_database(db_path, args.table) 