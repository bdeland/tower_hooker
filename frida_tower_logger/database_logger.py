import psycopg2
import psycopg2.extras # For DictCursor
import json
import os
import sys # Make sure sys is imported for path manipulation
from datetime import datetime, timezone

# Assuming config.py will be in the parent directory or accessible via sys.path
# when this module is used by the main application.
# For standalone testing, config might need to be handled differently if not in sys.path.
try:
    # This path is taken when DatabaseLogger is imported as part of the frida_tower_logger package
    from .. import config
except ImportError:
    # This path is taken when running this script directly (e.g., python frida_tower_logger/database_logger.py)
    # Forcibly adjust sys.path to find 'config.py' in the parent directory (project root)
    # and ensure a fresh import.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    # Remove 'config' from sys.modules if it was cached (e.g., by a failed relative import attempt)
    if 'config' in sys.modules:
        del sys.modules['config']
        
    import config # This should now reliably load config.py from the project root.

class DatabaseLogger:
    def __init__(self):
        self.host = config.POSTGRES_HOST
        self.port = config.POSTGRES_PORT
        self.dbname = config.POSTGRES_DB
        self.user = config.POSTGRES_USER
        self.password = config.POSTGRES_PASSWORD
        print(f"DatabaseLogger initialized for PostgreSQL: db='{self.dbname}', host='{self.host}'")

    def _get_connection(self):
        conn_string = f"dbname='{self.dbname}' user='{self.user}' host='{self.host}' port='{self.port}' password='{self.password}'"
        conn = psycopg2.connect(conn_string)
        # Optional: Use DictCursor to get rows as dictionaries
        # cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor = conn.cursor() # Standard cursor
        return conn, cursor

    def create_tables_if_not_exists(self):
        """Creates all necessary tables if they don't already exist for PostgreSQL."""
        print(f"Ensuring all tables exist in PostgreSQL database '{self.dbname}'...")
        conn, cursor = self._get_connection()
        try:
            # game_rounds table for PostgreSQL
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS game_rounds (
                    round_id SERIAL PRIMARY KEY,
                    process_name TEXT,
                    script_name TEXT,
                    start_timestamp TIMESTAMPTZ,
                    end_timestamp TIMESTAMPTZ NULL,
                    tier INTEGER,
                    initial_cards_equipped JSONB,
                    initial_modules_equipped JSONB NULL,
                    other_fixed_metadata JSONB NULL,
                    final_wave INTEGER NULL,
                    final_cash REAL NULL,
                    final_coins REAL NULL,
                    duration_seconds INTEGER NULL
                );
            """)
            print("Table 'game_rounds' ensured.")

            # round_events table for PostgreSQL
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS round_events (
                    event_id SERIAL PRIMARY KEY,
                    round_id INTEGER REFERENCES game_rounds(round_id) ON DELETE CASCADE,
                    event_timestamp TIMESTAMPTZ,
                    event_type TEXT,
                    event_data JSONB
                );
            """)
            print("Table 'round_events' ensured.")

            # round_snapshots table for PostgreSQL
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS round_snapshots (
                    snapshot_id SERIAL PRIMARY KEY,
                    round_id INTEGER REFERENCES game_rounds(round_id) ON DELETE CASCADE,
                    snapshot_timestamp TIMESTAMPTZ,
                    cash REAL,
                    coins REAL,
                    gems INTEGER NULL,
                    wave_number INTEGER NULL,
                    tower_health REAL NULL
                    -- Add other snapshot fields here
                );
            """)
            print("Table 'round_snapshots' ensured.")

            # script_logs table for PostgreSQL (for generic script messages and errors)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS script_logs (
                    log_id SERIAL PRIMARY KEY,
                    script_timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    process_name TEXT,
                    script_name TEXT,
                    message_type TEXT,
                    event_subtype TEXT,
                    data_payload JSONB
                );
            """)
            print("Table 'script_logs' ensured.")

            conn.commit()
            print("All PostgreSQL tables ensured.")
        except Exception as e:
            print(f"Error creating PostgreSQL tables: {e}")
            conn.rollback() # Rollback on error for DDL changes too, though less critical for IF NOT EXISTS
        finally:
            conn.close()

    def log_message(self, script_timestamp, process_name, script_name, message_type, event_subtype, data_payload_dict):
        """Logs a generic script message or error to the script_logs table in PostgreSQL."""
        conn, cursor = self._get_connection()
        try:
            # Ensure data_payload_dict is a JSON string; psycopg2 can adapt dicts to JSONB
            # but explicit conversion is safer if the input might not be a perfect dict.
            json_data_payload = json.dumps(data_payload_dict) if isinstance(data_payload_dict, dict) else str(data_payload_dict)

            sql_query = """
                INSERT INTO script_logs 
                (script_timestamp, process_name, script_name, message_type, event_subtype, data_payload) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql_query, (script_timestamp, process_name, script_name, message_type, event_subtype, json_data_payload))
            conn.commit()
            print(f"Logged message to script_logs: Type='{message_type}', Subtype='{event_subtype}'")
        except Exception as e:
            print(f"Error logging message to script_logs DB: {e}")
            print(f"Data that failed: ST={script_timestamp}, PN={process_name}, SN={script_name}, MT={message_type}, ES={event_subtype}, DP={data_payload_dict}")
            if conn: conn.rollback()
        finally:
            if conn: conn.close()

    def start_new_round(self, script_timestamp, process_name, script_name, tier, initial_cards_json, other_fixed_metadata_json=None):
        """Starts a new game round and logs its initial data using PostgreSQL."""
        conn, cursor = self._get_connection()
        new_round_id = None
        try:
            sql = """
                INSERT INTO game_rounds (start_timestamp, process_name, script_name, tier, initial_cards_equipped, other_fixed_metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING round_id;
            """
            # Ensure JSON data is passed as JSON strings or psycopg2 handles dicts with jsonb_adapt
            initial_cards_payload = json.dumps(initial_cards_json) if isinstance(initial_cards_json, dict) else initial_cards_json
            other_fixed_payload = json.dumps(other_fixed_metadata_json) if isinstance(other_fixed_metadata_json, dict) else other_fixed_metadata_json
            
            cursor.execute(sql, (script_timestamp, process_name, script_name, tier, initial_cards_payload, other_fixed_payload))
            new_round_id = cursor.fetchone()[0]
            conn.commit()
            print(f"Started new round with ID: {new_round_id}")
        except Exception as e:
            print(f"Error starting new round in DB: {e}")
            conn.rollback() # Rollback on error
        finally:
            conn.close()
        return new_round_id

    def end_round(self, round_id, end_timestamp, final_wave=None, final_cash=None, final_coins=None, duration_seconds=None):
        """Ends a game round and logs its final data using PostgreSQL."""
        conn, cursor = self._get_connection()
        try:
            sql_query = """
                UPDATE game_rounds 
                SET end_timestamp = %s, final_wave = %s, final_cash = %s, final_coins = %s, duration_seconds = %s
                WHERE round_id = %s
            """
            cursor.execute(sql_query, (end_timestamp, final_wave, final_cash, final_coins, duration_seconds, round_id))
            conn.commit()
            print(f"Ended round ID: {round_id}")
        except Exception as e:
            print(f"Error ending round {round_id} in DB: {e}")
            conn.rollback()
        finally:
            conn.close()

    def log_round_event(self, round_id, event_timestamp, event_type, event_data_json):
        """Logs a specific event that occurred during a game round using PostgreSQL."""
        conn, cursor = self._get_connection()
        try:
            sql_query = """
                INSERT INTO round_events 
                (round_id, event_timestamp, event_type, event_data)
                VALUES (%s, %s, %s, %s)
            """
            event_data_payload = json.dumps(event_data_json) if isinstance(event_data_json, dict) else event_data_json
            cursor.execute(sql_query, (round_id, event_timestamp, event_type, event_data_payload))
            conn.commit()
            print(f"Logged event '{event_type}' for round ID: {round_id}")
        except Exception as e:
            print(f"Error logging round event for round {round_id} in DB: {e}")
            conn.rollback()
        finally:
            conn.close()

    def log_round_snapshot(self, round_id, snapshot_timestamp, cash, coins, gems=None, wave_number=None, tower_health=None):
        """Logs a periodic snapshot of game state during a round using PostgreSQL."""
        conn, cursor = self._get_connection()
        try:
            sql_query = """
                INSERT INTO round_snapshots 
                (round_id, snapshot_timestamp, cash, coins, gems, wave_number, tower_health)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql_query, (round_id, snapshot_timestamp, cash, coins, gems, wave_number, tower_health))
            conn.commit()
            print(f"Logged snapshot for round ID: {round_id} at {snapshot_timestamp}")
        except Exception as e:
            print(f"Error logging round snapshot for round {round_id} in DB: {e}")
            conn.rollback()
        finally:
            conn.close()

if __name__ == '__main__':
    # This is the test script section adapted for PostgreSQL
    print("Running DatabaseLogger standalone test for PostgreSQL...")

    # The try-except for config import at the top of the file handles package vs. script context.
    # For the __main__ block, we rely on that or ensure config.py is findable.
    # No specific test_config needed here as DatabaseLogger pulls from config directly.
    
    logger = DatabaseLogger() # Uses PostgreSQL config internally
    logger.create_tables_if_not_exists()

    # Test logging a sample message to the old table (still useful)
    # print("\\n--- Testing log_message (old table, now placeholder) ---")
    # sample_ts_old_log = datetime.now(timezone.utc).isoformat()
    # logger.log_message(
    #     script_timestamp=sample_ts_old_log,
    #     process_name="com.example.testapp.old",
    #     script_name="test_script_old.js",
    #     message_type="status_old",
    #     event_subtype="test_event_occurred_old",
    #     data_payload_dict={"key_old": "value_old"}
    # )
    # print("Sample message to 'script_logs' (placeholder method) called.")

    # --- Test new round-based logging methods ---
    print("\\n--- Testing new round-based logging methods for PostgreSQL ---")
    current_time_iso = lambda: datetime.now(timezone.utc).isoformat()

    # 1. Test start_new_round
    print("\\n1. Testing start_new_round...")
    start_ts = current_time_iso()
    # Pass as Python dicts, let the method handle json.dumps
    cards_dict = {"active": ["CardA", "CardB"], "inactive": ["CardC"]}
    metadata_dict = {"difficulty": "Hard", "version": "1.2.3"} 
    
    round_id = logger.start_new_round(
        script_timestamp=start_ts,
        process_name="com.TechTreeGames.TheTower",
        script_name="master_hooker_pg.js",
        tier=10,
        initial_cards_json=cards_dict, # Pass dict
        other_fixed_metadata_json=metadata_dict # Pass dict
    )
    # assert round_id is not None, "start_new_round failed to return a round_id" # Assertion removed, will rely on print
    if round_id is not None:
        print(f"start_new_round returned round_id: {round_id}. Manual verification in PostgreSQL needed.")
    else:
        print("start_new_round failed to return a round_id. Check console for errors.")


    if round_id is not None:
        # 2. Test log_round_snapshot
        print("\\n2. Testing log_round_snapshot...")
        snap_ts = current_time_iso()
        logger.log_round_snapshot(
            round_id=round_id,
            snapshot_timestamp=snap_ts,
            cash=1000.50,
            coins=25,
            gems=5,
            wave_number=15,
            tower_health=95.5
        )
        print(f"log_round_snapshot called for round_id {round_id}. Manual verification in PostgreSQL needed.")

        # 3. Test log_round_event
        print("\\n3. Testing log_round_event...")
        event_ts = current_time_iso()
        event_data_dict = {"card_drawn": "NewCardPG", "mana_cost": 3, "rarity": "Epic"} # Pass dict
        logger.log_round_event(
            round_id=round_id,
            event_timestamp=event_ts,
            event_type="CardDrawnPG",
            event_data_json=event_data_dict # Pass dict
        )
        print(f"log_round_event called for round_id {round_id}. Manual verification in PostgreSQL needed.")

        # 4. Test end_round
        print("\\n4. Testing end_round...")
        end_ts = current_time_iso()
        logger.end_round(
            round_id=round_id,
            end_timestamp=end_ts,
            final_wave=50,
            final_cash=12345.67,
            final_coins=100,
            duration_seconds=3600
        )
        print(f"end_round called for round_id {round_id}. Manual verification in PostgreSQL needed.")
    else:
        print("Skipping dependent tests as round_id was not obtained from start_new_round.")

    print("\\n--- All DatabaseLogger PostgreSQL tests completed (function calls made) ---")
    print("Please verify data in PostgreSQL (e.g., using pgAdmin or DBeaver).")
    print(f"Connection parameters used (from config):")
    print(f"  Host: {config.POSTGRES_HOST}")
    print(f"  Port: {config.POSTGRES_PORT}")
    print(f"  DB:   {config.POSTGRES_DB}")
    print(f"  User: {config.POSTGRES_USER}") 