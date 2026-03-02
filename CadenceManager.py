import sqlite3
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from CadenceEditor import CadenceEditor

# Add ml_experiments to path for semantic search
sys.path.insert(0, str(Path(__file__).parent / "ml_experiments"))
try:
    from semantic_search import find_similar_semantic
    SEMANTIC_AVAILABLE = True
except ImportError:
    SEMANTIC_AVAILABLE = False

class CadenceManager:
    """
    CadenceManager manages chore data stored in a SQLite database.
    It supports adding chores (with optional parents, URLs, and description),
    logging completions (with recursive parent logging), frequency adjustments,
    deletion, updates, and resetting overdue chores.
    """

    def __init__(self, db_path='data/chore_data.db'):
        self.db_path = db_path
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create tables if they don't exist based on the new schema."""
        cur = self.connection.cursor()
        # Table for chores
        cur.execute("""
        CREATE TABLE IF NOT EXISTS chores (
            name TEXT PRIMARY KEY,
            active INTEGER,
            created_at TEXT,
            frequency_in_days REAL,
            description TEXT,
            adjust_frequency INTEGER DEFAULT 1
        )
        """)
        # Table for urls
        cur.execute("""
        CREATE TABLE IF NOT EXISTS urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chore_name TEXT,
            url TEXT
        )
        """)
        # Table for parent_chores
        cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_chores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chore_name TEXT,
            parent_chore TEXT
        )
        """)
        # Create notes table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chore_name TEXT,
                note TEXT,
                created_at TEXT,
                FOREIGN KEY (chore_name) REFERENCES chores(name)
            )
        """)
        # Table for logs – adding the is_genuine column to denote if a log is genuine (1) or not (0)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chore_name TEXT,
            logged_at TEXT,
            complete_by TEXT,
            is_genuine INTEGER DEFAULT 1
        )
        """)
        # Table for weights (these may be unrelated to chores)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            weight REAL,
            date TEXT
        )
        """)

        # Add adjust_frequency column to existing databases if it doesn't exist
        cur.execute("PRAGMA table_info(chores)")
        columns = [column[1] for column in cur.fetchall()]
        if 'adjust_frequency' not in columns:
            cur.execute("ALTER TABLE chores ADD COLUMN adjust_frequency INTEGER DEFAULT 1")
            # Set all existing chores to have adjust_frequency = 1 (True)
            cur.execute("UPDATE chores SET adjust_frequency = 1 WHERE adjust_frequency IS NULL")

        self.connection.commit()

    def dynamic_threshold(self, freq, base=1, power=1.1, scaling=66):
        """
        Returns a dynamic threshold multiplier using an inverse power function.

        Parameters:
            freq: The frequency in days
            base: Base multiplier (default: 1)
            power: Controls the steepness of the curve (default: 1.1)
            scaling: Scaling factor (default: 66)
        """
        # Prevent division by zero
        if freq <= 0:
            freq = 0.01

        return base + scaling / (freq ** power)

    def dynamic_offset(self, freq, ratio=0.618, base=1, power=1.1, scaling=66):
        """
        Returns a dynamic offset multiplier as a proportion of the threshold value.

        Parameters:
            freq: The frequency in days
            ratio: Multiplier to apply to the threshold (default: 0.618)
            base, power, scaling: Parameters passed to dynamic_threshold function
        """
        threshold = self.dynamic_threshold(freq, base, power, scaling)
        return threshold * ratio

    def adjust_chore_frequency(self, name, lower_bound_multiplier=1.382,
                            upper_bound_divider=1.382, adjust_without_parent=False,
                            threshold_func=None, offset_func=None,
                            base=1, power=1.1, scaling=66, ratio=0.618,
                            min_days_between_adjustments=0, lower_bound_tightness=0.5):
        """
        Adjusts a specific chore's frequency dynamically based on a computed threshold.
        Includes logic to prevent repeated adjustments within a short time period.

        Parameters:
            lower_bound_tightness: Multiplier for the lower bound (default: 0.25).
                                  Lower values = tighter bound (closer to 0) = less frequent adjustments.
                                  For example, 0.25 means the lower bound will be 1/4 of its normal value.
        """
        if threshold_func is None:
            threshold_func = lambda freq: self.dynamic_threshold(freq, base, power, scaling)
        if offset_func is None:
            offset_func = lambda freq: self.dynamic_offset(freq, ratio, base, power, scaling)

        cur = self.connection.cursor()
        cur.execute("SELECT * FROM chores WHERE name = ?", (name,))
        chore = cur.fetchone()
        if not chore:
            print(f"Chore '{name}' not found.")
            return False

        # Check if frequency adjustment is enabled for this chore
        try:
            adjust_freq_enabled = chore['adjust_frequency']
        except (KeyError, IndexError):
            adjust_freq_enabled = 1  # Default to 1 (True) if column doesn't exist

        if not adjust_freq_enabled:
            return False

        # Check if there are parent chores associated.
        cur.execute("SELECT COUNT(*) as cnt FROM parent_chores WHERE chore_name = ?", (name,))
        res = cur.fetchone()
        has_parents = res["cnt"] > 0
        if not has_parents and not adjust_without_parent:
            # Silently skip adjustment for chores without parents (this is normal)
            return False

        # Check if this chore has been adjusted recently
        now = datetime.now()
        cur.execute("""
            SELECT logged_at FROM logs
            WHERE chore_name = ? AND is_genuine = 0
            ORDER BY logged_at DESC LIMIT 1
        """, (name,))
        last_adjustment = cur.fetchone()

        if last_adjustment and min_days_between_adjustments > 0:
            last_adjustment_time = datetime.fromisoformat(last_adjustment["logged_at"])
            days_since_last_adjustment = (now - last_adjustment_time).total_seconds() / (3600 * 24)

            # Skip adjustment if it was adjusted recently
            if days_since_last_adjustment < min_days_between_adjustments:
                return False

        # Continue with regular adjustment logic
        original_frequency = float(chore["frequency_in_days"])
        cur.execute("SELECT complete_by FROM logs WHERE chore_name = ? ORDER BY logged_at DESC LIMIT 1", (name,))
        last_log = cur.fetchone()
        if last_log:
            next_due = datetime.fromisoformat(last_log["complete_by"])
        else:
            next_due = datetime.fromisoformat(chore["created_at"]) + timedelta(days=original_frequency)

        days_until_due = (next_due - now).total_seconds() / (3600 * 24)

        # Calculate original bounds and tau
        original_tau = base + scaling / (original_frequency ** power)
        lower_bound = (original_frequency / original_tau) * lower_bound_tightness
        upper_bound = original_frequency * original_tau

        # Store original values for comparison
        original_days_until_due = days_until_due
        original_due_date = next_due

        adjusted = False
        new_frequency = original_frequency

        if days_until_due < lower_bound:
            new_frequency = original_frequency * lower_bound_multiplier
            adjusted = True
        elif days_until_due > upper_bound:
            new_frequency = original_frequency / upper_bound_divider
            adjusted = True

        if adjusted:
            cur.execute("UPDATE chores SET frequency_in_days = ? WHERE name = ?", (new_frequency, name))

            # Calculate new bounds
            new_tau = base + scaling / (new_frequency ** power)
            new_lower_bound = (new_frequency / new_tau) * lower_bound_tightness
            new_upper_bound = new_frequency * new_tau

            # Calculate the safe range and position within it
            safe_range = new_upper_bound - new_lower_bound
            due_offset_days = new_lower_bound + (safe_range * 0.618)
            new_next_due = now + timedelta(days=due_offset_days)

            cur.execute("""
                INSERT INTO logs (chore_name, logged_at, complete_by, is_genuine)
                VALUES (?, ?, ?, ?)
            """, (name, now.isoformat(), new_next_due.isoformat(), 0))
            self.connection.commit()

            # Check if this is being called from log_chore (which already printed the chore name)
            # or standalone (from adjust_all_frequencies)
            import sys
            import io

            # If stdout is a StringIO (from log_chore's capture), we're in a log context
            # Otherwise, we're being called standalone and need to print the chore name
            if isinstance(sys.stdout, io.StringIO):
                # Called from log_chore - just append to the existing line
                print(f" [adjusted: {original_frequency:.2f}->{new_frequency:.2f}d, new due: {new_next_due.strftime('%Y-%m-%d %H:%M')}]")
            else:
                # Called standalone - print full info with chore name
                print(f"Adjusted {name}: {original_frequency:.2f}->{new_frequency:.2f}d, new due: {new_next_due.strftime('%Y-%m-%d %H:%M')}")
            print()  # Newline after adjustment

        return adjusted

    def adjust_all_frequencies(self, lower_bound_multiplier=1.382,
                            upper_bound_divider=1.382, adjust_without_parent=False,
                            threshold_func=None, offset_func=None,
                            base=1, power=1.1, scaling=66, ratio=0.618,
                            lower_bound_tightness=0.5):
        """
        Iterates over all active chores and adjusts their frequencies.
        """
        cur = self.connection.cursor()
        cur.execute("SELECT name FROM chores WHERE active = 1")
        chores = cur.fetchall()
        adjustments = {}
        for row in chores:
            name = row["name"]
            if self.adjust_chore_frequency(name, lower_bound_multiplier, upper_bound_divider,
                                        adjust_without_parent, threshold_func, offset_func,
                                        base, power, scaling, ratio,
                                        min_days_between_adjustments=0,
                                        lower_bound_tightness=lower_bound_tightness):
                cur.execute("SELECT frequency_in_days FROM chores WHERE name = ?", (name,))
                new_val = cur.fetchone()["frequency_in_days"]
                adjustments[name] = new_val
        self.connection.commit()
        return adjustments

    def add_chore(self, name, active=1, created_at=None, frequency_in_days=None,
                  description=None, urls=None, parent_chores=None, adjust_frequency=1):
        """
        Adds a new chore with optional URLs, parent chores, and a description.
        If frequency_in_days is not provided, it uses the average frequency:
          - For chores that share any of the given parents (if provided),
          - Otherwise the average for chores without parents.
        If no average is found, defaults to 1.0.

        Parameters:
            adjust_frequency: Whether to enable automatic frequency adjustment (default: 1/True)
        """
        if created_at is None:
            created_at = datetime.now().isoformat()

        # Determine frequency_in_days if not provided.
        if frequency_in_days is None:
            cur = self.connection.cursor()
            if parent_chores:
                placeholders = ",".join("?" for _ in parent_chores)
                sql = f"""
                SELECT AVG(c.frequency_in_days) as avg_freq FROM chores c
                INNER JOIN parent_chores pc ON c.name = pc.chore_name
                WHERE pc.parent_chore IN ({placeholders})
                """
                cur.execute(sql, parent_chores)
                row = cur.fetchone()
                avg_freq = row["avg_freq"]
                frequency_in_days = avg_freq if avg_freq is not None else 1.0
            else:
                sql = """
                SELECT AVG(frequency_in_days) as avg_freq FROM chores
                WHERE name NOT IN (SELECT chore_name FROM parent_chores)
                """
                cur.execute(sql)
                row = cur.fetchone()
                avg_freq = row["avg_freq"]
                frequency_in_days = avg_freq if avg_freq is not None else 1.0

        cur = self.connection.cursor()
        cur.execute("""
            INSERT INTO chores (name, active, created_at, frequency_in_days, description, adjust_frequency)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, active, created_at, frequency_in_days, description, adjust_frequency))

        if urls:
            for url in urls:
                cur.execute("INSERT INTO urls (chore_name, url) VALUES (?, ?)", (name, url))
        if parent_chores:
            for parent in parent_chores:
                cur.execute("INSERT INTO parent_chores (chore_name, parent_chore) VALUES (?, ?)", (name, parent))
        self.connection.commit()
        return True

    def delete_chore(self, name, cascade=True):
        """
        Deletes a chore by name.
        If cascade is True (the default), it also deletes associated records
        in urls, logs, notes, and parent_chores.
        """
        cur = self.connection.cursor()
        cur.execute("DELETE FROM chores WHERE name = ?", (name,))
        if cascade:
            cur.execute("DELETE FROM urls WHERE chore_name = ?", (name,))
            cur.execute("DELETE FROM logs WHERE chore_name = ?", (name,))
            cur.execute("DELETE FROM notes WHERE chore_name = ?", (name,))
            # Delete parent links where this chore is either the child or the parent.
            cur.execute("DELETE FROM parent_chores WHERE chore_name = ? OR parent_chore = ?", (name, name))
        self.connection.commit()
        return True

    def add_parent(self, chore_name, parent_chore):
        """
        Adds a parent relationship between a chore and its parent.

        Parameters:
            chore_name (str): The name of the child chore
            parent_chore (str): The name of the parent chore

        Returns:
            bool: True if successful, False otherwise
            str: Error message if unsuccessful
        """
        try:
            cur = self.connection.cursor()

            # Check if child chore exists
            cur.execute("SELECT 1 FROM chores WHERE name = ?", (chore_name,))
            if cur.fetchone() is None:
                return False, f"Child chore '{chore_name}' doesn't exist. Please create it first."

            # Check if parent chore exists
            cur.execute("SELECT 1 FROM chores WHERE name = ?", (parent_chore,))
            if cur.fetchone() is None:
                return False, f"Parent chore '{parent_chore}' doesn't exist. Please create it first."

            # Check if the relationship already exists
            cur.execute(
                "SELECT 1 FROM parent_chores WHERE chore_name = ? AND parent_chore = ?",
                (chore_name, parent_chore)
            )

            if cur.fetchone() is None:  # Only insert if the relationship doesn't exist
                cur.execute(
                    "INSERT INTO parent_chores (chore_name, parent_chore) VALUES (?, ?)",
                    (chore_name, parent_chore)
                )
                self.connection.commit()
                print(f"Added parent relationship: {chore_name} -> {parent_chore}")
                return True, f"Added parent relationship: {chore_name} -> {parent_chore}"
            else:
                print(f"Relationship already exists: {chore_name} -> {parent_chore}")
                return True, f"Relationship already exists: {chore_name} -> {parent_chore}"

        except sqlite3.Error as e:
            error_msg = f"Error adding parent relationship: {e}"
            print(error_msg)
            return False, error_msg

    def remove_parent(self, chore_name, parent_name):
        """
        Removes a parent relationship between a chore and its parent.

        Parameters:
            chore_name (str): The name of the child chore
            parent_name (str): The name of the parent chore to remove

        Returns:
            bool: True if successful, False otherwise
            str: Success or error message
        """
        try:
            cur = self.connection.cursor()

            # Check if child chore exists
            cur.execute("SELECT 1 FROM chores WHERE name = ?", (chore_name,))
            if cur.fetchone() is None:
                return False, f"Child chore '{chore_name}' doesn't exist"

            # Check if the relationship exists
            cur.execute(
                "SELECT 1 FROM parent_chores WHERE chore_name = ? AND parent_chore = ?",
                (chore_name, parent_name)
            )

            if cur.fetchone() is None:
                return False, f"Relationship doesn't exist: {chore_name} -> {parent_name}"

            # Remove the specific parent relationship
            cur.execute("""
                DELETE FROM parent_chores
                WHERE chore_name = ? AND parent_chore = ?
            """, (chore_name, parent_name))

            self.connection.commit()
            message = f"Removed parent relationship: {chore_name} -> {parent_name}"
            print(message)
            return True, message

        except sqlite3.Error as e:
            error_msg = f"Error removing parent relationship: {e}"
            print(error_msg)
            return False, error_msg

    def rename_chore(self, old_name, new_name):
        """
        Renames a chore and updates all references in related tables.

        Parameters:
            old_name: Current name of the chore
            new_name: New name for the chore

        Returns:
            bool: True if successful, False otherwise
        """
        if not old_name or not new_name:
            print("Both old and new names must be provided.")
            return False

        if old_name == new_name:
            print("Old and new names are the same.")
            return False

        cur = self.connection.cursor()

        # Start a transaction
        cur.execute("BEGIN TRANSACTION")

        try:
            # Check if the old chore exists
            cur.execute("SELECT COUNT(*) as cnt FROM chores WHERE name = ?", (old_name,))
            if cur.fetchone()['cnt'] == 0:
                print(f"Chore '{old_name}' does not exist.")
                cur.execute("ROLLBACK")
                return False

            # Check if the new name already exists
            cur.execute("SELECT COUNT(*) as cnt FROM chores WHERE name = ?", (new_name,))
            if cur.fetchone()['cnt'] > 0:
                print(f"A chore with name '{new_name}' already exists.")
                cur.execute("ROLLBACK")
                return False

            # Update the chore name in all tables
            cur.execute("UPDATE chores SET name = ? WHERE name = ?", (new_name, old_name))
            cur.execute("UPDATE logs SET chore_name = ? WHERE chore_name = ?", (new_name, old_name))
            cur.execute("UPDATE notes SET chore_name = ? WHERE chore_name = ?", (new_name, old_name))
            cur.execute("UPDATE urls SET chore_name = ? WHERE chore_name = ?", (new_name, old_name))
            cur.execute("UPDATE parent_chores SET chore_name = ? WHERE chore_name = ?", (new_name, old_name))
            cur.execute("UPDATE parent_chores SET parent_chore = ? WHERE parent_chore = ?", (new_name, old_name))

            # Commit the transaction
            self.connection.commit()
            print(f"Successfully renamed '{old_name}' to '{new_name}'")
            return True

        except Exception as e:
            # Rollback on error
            cur.execute("ROLLBACK")
            print(f"Error renaming chore: {str(e)}")
            return False

    def update_chore_attributes(self, name, updates: dict):
        """
        Updates the specified attributes for a chore.
        Valid fields include: active, frequency_in_days, description, adjust_frequency, created_at, name.
        If 'name' is provided in updates, it will rename the chore.
        """
        # Handle name change separately
        if 'name' in updates:
            new_name = updates['name']
            if not self.rename_chore(name, new_name):
                return False
            # Remove 'name' from updates and update the name for subsequent operations
            updates = {k: v for k, v in updates.items() if k != 'name'}
            name = new_name  # Use new name for other attribute updates

        # Handle other attribute updates
        columns = []
        values = []
        for key, value in updates.items():
            if key in ["active", "frequency_in_days", "description", "adjust_frequency", "created_at"]:
                columns.append(f"{key} = ?")
                values.append(value)

        if columns:
            values.append(name)
            sql = f"UPDATE chores SET {', '.join(columns)} WHERE name = ?"
            cur = self.connection.cursor()
            cur.execute(sql, values)
            self.connection.commit()

        return True

    def add_note(self, chore_name: str, note: str) -> tuple[bool, str]:
        """Add a note to a chore. Returns (success, message)."""
        cur = self.connection.cursor()
        cur.execute("SELECT 1 FROM chores WHERE name = ?", (chore_name,))
        if not cur.fetchone():
            return False, f"Chore '{chore_name}' not found."

        cur.execute(
            "INSERT INTO notes (chore_name, note, created_at) VALUES (?, ?, ?)",
            (chore_name, note, datetime.now().isoformat())
        )
        self.connection.commit()
        return True, f"Added note to '{chore_name}'"

    def delete_note(self, note_id: int) -> tuple[bool, str]:
        """Delete a note by ID. Returns (success, message)."""
        cur = self.connection.cursor()
        cur.execute("SELECT chore_name FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if not row:
            return False, f"Note with ID {note_id} not found."

        chore_name = row[0]
        cur.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.connection.commit()
        return True, f"Deleted note {note_id} from '{chore_name}'"

    def delete_log(self, log_id: int) -> tuple[bool, str, dict]:
        """
        Delete a log entry by ID and recalculate the chore's due date.

        Returns (success, message, details_dict).
        The details_dict includes chore_name, old_due, new_due for confirmation.
        """
        cur = self.connection.cursor()

        # Find the log entry
        cur.execute("SELECT chore_name, logged_at, complete_by FROM logs WHERE id = ?", (log_id,))
        log_row = cur.fetchone()
        if not log_row:
            return False, f"Log with ID {log_id} not found.", {}

        chore_name = log_row["chore_name"]
        deleted_logged_at = log_row["logged_at"]
        deleted_complete_by = log_row["complete_by"]

        # Delete the log entry
        cur.execute("DELETE FROM logs WHERE id = ?", (log_id,))

        # Find the most recent remaining log for this chore
        cur.execute("""
            SELECT complete_by FROM logs
            WHERE chore_name = ?
            ORDER BY logged_at DESC LIMIT 1
        """, (chore_name,))
        prev_log = cur.fetchone()

        if prev_log:
            # Use the previous log's complete_by as the new due date
            new_due = prev_log["complete_by"]
        else:
            # No previous logs - calculate based on created_at + frequency
            cur.execute("SELECT created_at, frequency_in_days FROM chores WHERE name = ?", (chore_name,))
            chore = cur.fetchone()
            if chore:
                created_at = datetime.fromisoformat(chore["created_at"])
                freq = float(chore["frequency_in_days"])
                new_due = (created_at + timedelta(days=freq)).isoformat()
            else:
                # Chore not found - just commit the delete
                self.connection.commit()
                return True, f"Deleted log {log_id} for '{chore_name}' (chore no longer exists)", {
                    "chore_name": chore_name,
                    "deleted_logged_at": deleted_logged_at
                }

        self.connection.commit()

        return True, f"Deleted log {log_id} for '{chore_name}'", {
            "chore_name": chore_name,
            "deleted_logged_at": deleted_logged_at,
            "old_due": deleted_complete_by,
            "new_due": new_due
        }

    def find_similar(self, query: str, limit: int = 5) -> list:
        """
        Find chores with names similar to the query using semantic search.

        Uses sentence-transformers for semantic similarity matching.
        Falls back to string-based matching if semantic search unavailable.

        Returns list of (name, match_type, score) tuples sorted by relevance.
        """
        # Try semantic search first (best quality)
        if SEMANTIC_AVAILABLE:
            try:
                results = find_similar_semantic(query, limit=limit, db_path=self.db_path)
                # Convert to expected format: (name, match_type, score)
                # Scale similarity (0-1) to score (0-100)
                return [(name, "semantic", score * 100) for name, score in results]
            except Exception as e:
                print(f"Semantic search failed, falling back to string matching: {e}")

        # Fallback to string-based matching
        return self._find_similar_string(query, limit)

    def _find_similar_string(self, query: str, limit: int = 5) -> list:
        """String-based fallback for find_similar when semantic search unavailable."""
        cur = self.connection.cursor()
        query_lower = query.lower().strip()
        query_words = set(query_lower.split())

        cur.execute("SELECT name FROM chores WHERE active = 1")
        all_chores = [row[0] for row in cur.fetchall()]

        matches = []

        for chore in all_chores:
            chore_lower = chore.lower()

            # Case-insensitive exact match
            if chore_lower == query_lower:
                matches.append((chore, "exact", 100))
                continue

            # Query is substring of chore name
            if query_lower in chore_lower:
                score = len(query_lower) / len(chore_lower) * 80
                matches.append((chore, "substring", score))
                continue

            # Chore name is substring of query
            if chore_lower in query_lower:
                score = len(chore_lower) / len(query_lower) * 70
                matches.append((chore, "reverse_substring", score))
                continue

            # Word overlap
            chore_words = set(chore_lower.replace('-', ' ').replace('+', ' ').split())
            common_words = query_words & chore_words
            if common_words:
                score = len(common_words) / max(len(query_words), len(chore_words)) * 60
                matches.append((chore, "word_match", score))
                continue

            # Partial word matching
            partial_matches = 0
            for qw in query_words:
                for cw in chore_words:
                    if len(qw) >= 3 and len(cw) >= 3:
                        if qw.startswith(cw[:3]) or cw.startswith(qw[:3]):
                            partial_matches += 1
                            break
            if partial_matches > 0:
                score = partial_matches / max(len(query_words), len(chore_words)) * 50
                matches.append((chore, "partial_word", score))

        matches.sort(key=lambda x: x[2], reverse=True)
        return matches[:limit]

    def log_chore(self, name, visited=None, logged_chores=None):
        """
        Logs the completion of a chore.
        Prior to logging, it checks whether the chore's frequency needs to be adjusted.
        By default, frequency adjustments are only applied if the chore has parent chores.

        After logging the completion (using the current timestamp for logged_at), the method
        computes the next due date based on the chore's frequency and recursively logs all
        parent chores (ensuring each is logged only once per action).

        Returns:
            A tuple of (next_due_date, list_of_logged_chore_names)
        """
        # Initialize logged_chores list on first call
        if logged_chores is None:
            logged_chores = []

        if visited is None:
            visited = set()
        if name in visited:
            return None, logged_chores
        visited.add(name)

        cur = self.connection.cursor()
        cur.execute("SELECT * FROM chores WHERE name = ? AND active = 1", (name,))
        chore = cur.fetchone()
        if not chore:
            print(f"Chore '{name}' not found or not active.")
            return None, logged_chores

        # Retrieve the last log entry to determine the base for next due date.
        cur.execute("SELECT complete_by FROM logs WHERE chore_name = ? ORDER BY logged_at DESC LIMIT 1", (name,))
        last_log = cur.fetchone()
        now = datetime.now()

        if last_log:
            last_due = datetime.fromisoformat(last_log["complete_by"])
            base_date = now if last_due < now else last_due
        else:
            # Use created_at date as reference when no previous log exists
            created_at = datetime.fromisoformat(chore["created_at"])
            freq = float(chore["frequency_in_days"])
            expected_due = created_at + timedelta(days=freq)
            base_date = now if expected_due < now else expected_due

        freq = float(chore["frequency_in_days"])
        next_due = base_date + timedelta(days=freq)

        # Insert a log record with is_genuine set to 1 (genuine log)
        cur.execute("""
            INSERT INTO logs (chore_name, logged_at, complete_by, is_genuine)
            VALUES (?, ?, ?, ?)
        """, (name, now.isoformat(), next_due.isoformat(), 1))
        self.connection.commit()

        # Print compact logging information (single line)
        print(f"[OK] {name} | Logged: {now.strftime('%Y-%m-%d %H:%M')} | Next due: {next_due.strftime('%Y-%m-%d %H:%M')} | Freq: {freq:.2f}d", end="")

        # Add to logged chores list
        logged_chores.append(name)

        # Adjust frequency AFTER logging (by default this will only adjust if the chore has parents;
        # pass adjust_without_parent=True to force adjustments on chores without parents)
        adjusted = self.adjust_chore_frequency(name, adjust_without_parent=False, lower_bound_tightness=0.5)

        # Print newline to complete the log line
        if not adjusted:
            print()  # Just add newline if no adjustment happened

        # Recursively log all parent chores.
        cur.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (name,))
        parents = cur.fetchall()
        for parent in parents:
            parent_name = parent["parent_chore"]
            self.log_chore(parent_name, visited=visited, logged_chores=logged_chores)

        return next_due, logged_chores

    def log_weight_chore(self, chore_name="Weigh In", weights_dict=None):
        """
        Logs a weight-tracking chore and records weights for one or more people.

        Parameters:
            chore_name: The name of the chore to log (default: "Weigh In")
            weights_dict: Dictionary mapping person names to their weights
                        e.g., {"Shane": 162.5, "Alex": 145.2}

        Returns:
            The next due date for the chore (from log_chore)
        """
        # First, log the chore as usual
        next_due = self.log_chore(chore_name)

        # If no weights provided, just return the next due date
        if not weights_dict:
            return next_due

        # Record each person's weight in the weights table
        cur = self.connection.cursor()
        now = datetime.now().isoformat()

        for person, weight in weights_dict.items():
            cur.execute("""
                INSERT INTO weights (name, weight, date)
                VALUES (?, ?, ?)
            """, (person, weight, now))

        self.connection.commit()

        # Provide feedback
        people = list(weights_dict.keys())
        if len(people) == 1:
            print(f"Recorded weight of {weights_dict[people[0]]} for {people[0]}")
        else:
            print(f"Recorded weights for {', '.join(people)}")

        return next_due

    def reset_overdue_chores(self):
        """
        Resets any chores whose next due date is in the past.
        The reset consists of inserting a new log, with is_genuine set to 0 to indicate
        that the log wasn't a genuine (manual) log.
        """
        cur = self.connection.cursor()
        cur.execute("SELECT name, created_at, frequency_in_days FROM chores")
        chores = cur.fetchall()
        now = datetime.now()
        for chore in chores:
            name = chore["name"]
            freq = float(chore["frequency_in_days"])
            created_at = datetime.fromisoformat(chore["created_at"])
            cur.execute("SELECT complete_by FROM logs WHERE chore_name = ? ORDER BY logged_at DESC LIMIT 1", (name,))
            last_log = cur.fetchone()
            if last_log:
                next_due = datetime.fromisoformat(last_log["complete_by"])
            else:
                next_due = created_at + timedelta(days=freq)
            if now > next_due:
                new_due = now + timedelta(days=freq)
                cur.execute("""
                    INSERT INTO logs (chore_name, logged_at, complete_by, is_genuine)
                    VALUES (?, ?, ?, ?)
                """, (name, now.isoformat(), new_due.isoformat(), 0))
                print(f"Resetting chore: {name} to now. Next due: {new_due}")
        self.connection.commit()

    def ensure_naive_datetime(self, dt_or_str):
        """Convert any datetime or ISO string to a naive datetime object"""
        if dt_or_str is None:
            return None

        # If it's already a datetime object
        if isinstance(dt_or_str, datetime):
            # If it has timezone info, remove it by replacing it with a naive datetime
            if dt_or_str.tzinfo is not None:
                return datetime(dt_or_str.year, dt_or_str.month, dt_or_str.day,
                            dt_or_str.hour, dt_or_str.minute, dt_or_str.second,
                            dt_or_str.microsecond)
            return dt_or_str

        # If it's a string, parse it carefully
        try:
            # Handle string with timezone (split at + or Z)
            if isinstance(dt_or_str, str):
                # Split at '+' to remove timezone offset
                if '+' in dt_or_str:
                    dt_or_str = dt_or_str.split('+')[0]
                # Remove 'Z' timezone indicator
                if dt_or_str.endswith('Z'):
                    dt_or_str = dt_or_str[:-1]
                # Handle case with timezone info after T
                if 'T' in dt_or_str:
                    parts = dt_or_str.split('T')
                    if len(parts) == 2:
                        date_part = parts[0]
                        time_part = parts[1]
                        # Strip any remaining timezone info from time part
                        if '-' in time_part:
                            time_part = time_part.split('-')[0]
                        dt_or_str = f"{date_part}T{time_part}"

                # Parse as ISO format
                dt = datetime.fromisoformat(dt_or_str)

                # Make sure the result is timezone-naive
                if dt.tzinfo is not None:
                    return datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond)
                return dt
        except (ValueError, TypeError) as e:
            print(f"Warning: Could not parse datetime: {dt_or_str}, error: {e}")
            return datetime.now()  # Fallback

        # If all else fails
        return datetime.now()

    def get_sorted_due_chores(self, limit=None, offset=None, filter_name=None, filter_parent=None,
                               include_overdue_only=False, leaf_only=True, sort_by="cycle_progress"):
        """Returns a sorted list of active chores with optional pagination and filtering.

        Parameters:
            limit: Maximum number of results to return
            offset: Number of results to skip (for pagination)
            filter_name: Filter chores by name (case-insensitive, partial match)
            filter_parent: Filter to chores under this parent category
            include_overdue_only: If True, only return overdue chores (default: False)
            leaf_only: If True, exclude category nodes (chores that have children) (default: True)
            sort_by: Sort order - "cycle_progress" (default) or "days_until_due"
                     cycle_progress = days_since_last_log / frequency (higher = more urgent)
                     days_until_due = absolute days until due date (lower = more urgent)

        Returns:
            List of dicts with: name, days_until_due, frequency_in_days, time_since_last_log,
            next_due, last_logged, cycle_progress, description
        """
        # Build the query with optional filters
        query = """
        SELECT c.name, c.frequency_in_days, c.created_at, c.description,
            l.logged_at, l.complete_by
        FROM chores c
        LEFT JOIN (
            SELECT chore_name, logged_at, complete_by
            FROM logs
            WHERE (chore_name, logged_at) IN (
                SELECT chore_name, MAX(logged_at)
                FROM logs
                GROUP BY chore_name
            )
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        """

        params = []

        # Exclude category nodes (chores that have children) if leaf_only
        if leaf_only:
            query += " AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)"

        # Add name filter if provided
        if filter_name:
            query += " AND c.name LIKE ?"
            params.append(f"%{filter_name}%")

        # Add parent filter if provided - uses recursive CTE to find all descendants
        if filter_parent:
            query += """
            AND EXISTS (
                WITH RECURSIVE ancestors AS (
                    SELECT parent_chore as name FROM parent_chores WHERE chore_name = c.name
                    UNION ALL
                    SELECT pc.parent_chore FROM parent_chores pc
                    JOIN ancestors a ON pc.chore_name = a.name
                )
                SELECT 1 FROM ancestors WHERE name = ?
            )
            """
            params.append(filter_parent)

        # Note: We apply custom sorting in Python because the calculation is complex

        cur = self.connection.cursor()
        cur.execute(query, params)
        chores = cur.fetchall()

        # Calculate derived values
        now = self.ensure_naive_datetime(datetime.now())
        result = []

        for chore in chores:
            name = chore["name"]
            freq = float(chore["frequency_in_days"])
            created_at = self.ensure_naive_datetime(chore["created_at"])
            description = chore["description"]

            if chore["complete_by"]:
                next_due = self.ensure_naive_datetime(chore["complete_by"])
                logged_at = self.ensure_naive_datetime(chore["logged_at"])
            else:
                next_due = created_at + timedelta(days=freq)
                logged_at = None

            days_until_due = (next_due - now).total_seconds() / (3600 * 24)

            # Skip non-overdue if include_overdue_only is True
            if include_overdue_only and days_until_due >= 0:
                continue

            time_since_last_log = (now - logged_at).total_seconds() / (3600 * 24) if logged_at else None

            # Calculate cycle_progress: how far through the cycle are we?
            # cycle_progress = days_since_last_log / frequency
            cycle_progress = None
            if time_since_last_log is not None and freq > 0:
                cycle_progress = round(time_since_last_log / freq, 2)

            result.append({
                "name": name,
                "days_until_due": days_until_due,
                "frequency_in_days": freq,
                "time_since_last_log": time_since_last_log,
                "next_due": next_due.isoformat(),
                "last_logged": logged_at.isoformat() if logged_at else None,
                "cycle_progress": cycle_progress,
                "description": description
            })

        # Sort based on sort_by parameter
        if sort_by == "cycle_progress":
            # Higher cycle_progress = more urgent (descending)
            # Items with None cycle_progress go to the end
            result.sort(key=lambda x: (x["cycle_progress"] is None, -(x["cycle_progress"] or 0)))
        else:
            # Lower days_until_due = more urgent (ascending)
            result.sort(key=lambda x: x["days_until_due"])

        # Apply pagination if requested
        if limit is not None:
            start = offset if offset is not None else 0
            return result[start:start + limit]

        return result

    def batch_load_chore_data(self, chore_names):
        """Load multiple chores' data in a single batch of queries"""
        if not chore_names:
            return {}, {}, {}

        # Data dictionaries to populate
        children_data = {name: False for name in chore_names}
        note_counts = {name: 0 for name in chore_names}
        last_logs = {}

        # Build placeholders for IN clause
        placeholders = ','.join('?' for _ in chore_names)

        # Query 1: Get chores with children
        cur = self.connection.cursor()
        cur.execute(f"""
            SELECT DISTINCT parent_chore
            FROM parent_chores
            WHERE parent_chore IN ({placeholders})
        """, chore_names)

        for row in cur.fetchall():
            children_data[row['parent_chore']] = True

        # Query 2: Get note counts
        cur.execute(f"""
            SELECT chore_name, COUNT(*) as note_count
            FROM notes
            WHERE chore_name IN ({placeholders})
            GROUP BY chore_name
        """, chore_names)

        for row in cur.fetchall():
            note_counts[row['chore_name']] = row['note_count']

        # Query 3: Get last log data
        cur.execute(f"""
            SELECT l.chore_name, l.logged_at, l.complete_by
            FROM logs l
            INNER JOIN (
                SELECT chore_name, MAX(logged_at) as max_logged_at
                FROM logs
                WHERE chore_name IN ({placeholders})
                GROUP BY chore_name
            ) m ON l.chore_name = m.chore_name AND l.logged_at = m.max_logged_at
        """, chore_names)

        for row in cur.fetchall():
            last_logs[row['chore_name']] = {
                'logged_at': row['logged_at'],
                'complete_by': row['complete_by']
            }

        return children_data, note_counts, last_logs

    def deactivate_chore_tree(self, chore_name, visited=None):
        """
        Recursively deactivates a chore and all its children.

        Parameters:
            chore_name: The name of the chore to deactivate
            visited: Set of already visited chores (to prevent cycles)

        Returns:
            A list of all deactivated chore names
        """
        if visited is None:
            visited = set()

        if chore_name in visited:
            return []  # Prevent cycles

        visited.add(chore_name)
        deactivated = [chore_name]

        # Deactivate the current chore
        cur = self.connection.cursor()
        cur.execute("UPDATE chores SET active = 0 WHERE name = ?", (chore_name,))

        # Find all children
        cur.execute("""
            SELECT chore_name
            FROM parent_chores
            WHERE parent_chore = ?
        """, (chore_name,))

        children = [row['chore_name'] for row in cur.fetchall()]

        # Recursively deactivate all children
        for child in children:
            child_deactivated = self.deactivate_chore_tree(child, visited)
            deactivated.extend(child_deactivated)

        self.connection.commit()
        return deactivated

    def get_leaf_chores(self, parent_name, active_only=True, visited=None, processed_leaves=None):
        """
        Recursively finds all leaf chores (chores with no children) under a parent chore.
        """
        if visited is None:
            visited = set()

        if processed_leaves is None:
            processed_leaves = set()

        if parent_name in visited:
            return []  # Prevent cycles

        visited.add(parent_name)
        leaf_chores = []

        # Find all children of this parent
        cur = self.connection.cursor()
        active_clause = "AND c.active = 1" if active_only else ""
        cur.execute(f"""
            SELECT c.name, c.frequency_in_days, c.active, c.created_at, c.description
            FROM chores c
            JOIN parent_chores pc ON c.name = pc.chore_name
            WHERE pc.parent_chore = ? {active_clause}
        """, (parent_name,))

        children = [dict(row) for row in cur.fetchall()]

        # For each child, check if it has children of its own
        for child in children:
            child_name = child['name']

            # Check if this child has any children
            cur.execute("""
                SELECT COUNT(*) as count
                FROM parent_chores
                WHERE parent_chore = ?
            """, (child_name,))

            has_children = cur.fetchone()['count'] > 0

            if has_children:
                # If it has children, recursively get its leaf chores
                child_leaves = self.get_leaf_chores(child_name, active_only, visited.copy(), processed_leaves)
                leaf_chores.extend(child_leaves)
            else:
                # If it has no children, it's a leaf chore
                # Skip if we've already processed this leaf chore
                if child_name in processed_leaves:
                    continue

                processed_leaves.add(child_name)

                # Add additional useful information
                cur.execute("""
                    SELECT logged_at, complete_by
                    FROM logs
                    WHERE chore_name = ?
                    ORDER BY logged_at DESC LIMIT 1
                """, (child_name,))

                log_entry = cur.fetchone()
                now = self.ensure_naive_datetime(datetime.now())  # Make sure 'now' is naive

                if log_entry:
                    child['last_logged'] = log_entry['logged_at']
                    child['next_due'] = log_entry['complete_by']

                    # Ensure datetimes are naive before calculations
                    next_due = self.ensure_naive_datetime(datetime.fromisoformat(log_entry['complete_by']))
                    logged_at = self.ensure_naive_datetime(datetime.fromisoformat(log_entry['logged_at']))

                    # Calculate days until due
                    child['days_until_due'] = (next_due - now).total_seconds() / (3600 * 24)

                    # Calculate time since last log
                    child['time_since_last_log'] = (now - logged_at).total_seconds() / (3600 * 24)
                else:
                    # If no log entry exists, use created_at as a reference point
                    created_at = self.ensure_naive_datetime(datetime.fromisoformat(child['created_at']))
                    freq = float(child['frequency_in_days'])

                    # For next_due, add frequency to created_at
                    next_due = created_at + timedelta(days=freq)
                    child['next_due'] = next_due.isoformat()
                    child['days_until_due'] = (next_due - now).total_seconds() / (3600 * 24)

                    # For time_since_last_log, use time since creation
                    child['last_logged'] = child['created_at']
                    child['time_since_last_log'] = (now - created_at).total_seconds() / (3600 * 24)

                # Add the parent's name for reference
                child['parent'] = parent_name
                leaf_chores.append(child)

        # Sort the leaf chores by days_until_due before returning
        leaf_chores.sort(key=lambda x: x['days_until_due'])

        return leaf_chores

    def chore_hierarchial_lineage(self, root_name, include_terminals=False, indent=0, result=None):
        """
        Recursively builds a string representation of the hierarchy of parent chores starting from root_name.

        Parameters:
            root_name: The starting chore name
            include_terminals: Whether to include terminal nodes (chores with no children)
            indent: Current indentation level (for recursive calls)
            result: List to accumulate results (used internally for recursion)

        Returns:
            String representation of the chore hierarchy
        """
        # Initialize result list for first call
        if result is None:
            result = []

        # Check if root chore exists
        cur = self.connection.cursor()
        cur.execute("SELECT 1 FROM chores WHERE name = ?", (root_name,))
        if not cur.fetchone():
            return f"Chore '{root_name}' not found."

        # Add the root on first call (if indent is 0)
        if indent == 0:
            result.append(root_name)

        # Get direct children of this parent
        children = self.get_children_of_parent(root_name)

        if not children:
            return "\n".join(result)  # No children, so return what we have

        # Process each child
        for i, child_name in enumerate(children):
            # Check if this child is itself a parent
            cur.execute("SELECT 1 FROM parent_chores WHERE parent_chore = ?", (child_name,))
            has_children = bool(cur.fetchone())

            # Determine if we should include this child
            should_include = has_children or include_terminals

            if should_include:
                # Choose the appropriate branch character
                is_last = (i == len(children) - 1)
                branch = "└─ " if is_last else "├─ "

                # Add child with appropriate indentation
                result.append("  " * indent + branch + child_name)

                # Recursively process its children with increased indentation
                if has_children:
                    next_indent = indent + 1
                    self.chore_hierarchial_lineage(child_name, include_terminals, next_indent, result)

        # Return the complete string representation
        return "\n".join(result)

    def get_recursive_filtered_children(self, root_name, active_only=True, include_non_leaves=False):
        """
        Returns a list of chores underneath the given root chore that pass the filtering criteria.
        A chore is accepted only if every parent associated with it is contained within the descendant tree.
        """
        # Compute the full set of allowed descendant chores (including the root)
        allowed = self._compute_allowed_children(root_name, active_only)

        # Filter allowed to only keep nodes where every parent is also in the allowed set.
        final_set = set()
        cur = self.connection.cursor()
        for node in allowed:
            cur.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (node,))
            all_parents = set(row["parent_chore"] for row in cur.fetchall())
            if all_parents.issubset(allowed):
                final_set.add(node)

        # Determine which nodes to return.
        nodes_to_return = set()
        if include_non_leaves:
            # Return all nodes except the root itself.
            nodes_to_return = final_set - {root_name}
        else:
            # Only return leaf nodes (nodes that have no children in final_set).
            for node in final_set:
                if node == root_name:
                    continue
                cur.execute("SELECT chore_name FROM parent_chores WHERE parent_chore = ?", (node,))
                children = set(row["chore_name"] for row in cur.fetchall())
                if not children.intersection(final_set):
                    nodes_to_return.add(node)

        # Prepare the result listing with details from chores and the latest log entries.
        result = []
        # Ensure we have a naive datetime for "now"
        now = self.ensure_naive_datetime(datetime.now())

        for node in nodes_to_return:
            cur.execute("SELECT * FROM chores WHERE name = ?", (node,))
            chore_row = cur.fetchone()
            if not chore_row:
                continue
            data = dict(chore_row)

            # Also record the list of parent chores for reference.
            cur.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (node,))
            parent_list = [row["parent_chore"] for row in cur.fetchall()]
            data["allowed_parents"] = parent_list

            # Retrieve the latest log entry for this chore.
            cur.execute("""
                SELECT logged_at, complete_by
                FROM logs
                WHERE chore_name = ?
                ORDER BY logged_at DESC
                LIMIT 1
            """, (node,))
            log_entry = cur.fetchone()
            if log_entry:
                try:
                    # Ensure both datetimes are naive
                    next_due = self.ensure_naive_datetime(log_entry["complete_by"])
                    logged_at = self.ensure_naive_datetime(log_entry["logged_at"])

                    if next_due:
                        data["next_due"] = next_due.isoformat()
                        # Now both are naive, safe to subtract
                        data["days_until_due"] = (next_due - now).total_seconds() / (3600 * 24)
                    if logged_at:
                        data["last_logged"] = logged_at.isoformat()
                        data["time_since_last_log"] = (now - logged_at).total_seconds() / (3600 * 24)
                except Exception as e:
                    print(f"Error processing datetime for {node}: {e}")
                    next_due = None
                    logged_at = None
            else:
                # No log entry; use created_at as the reference.
                created_at = self.ensure_naive_datetime(data["created_at"])
                freq = float(data["frequency_in_days"])
                next_due = created_at + timedelta(days=freq)
                data["next_due"] = next_due.isoformat()
                data["days_until_due"] = (next_due - now).total_seconds() / (3600 * 24)
                data["last_logged"] = data["created_at"]
                data["time_since_last_log"] = (now - created_at).total_seconds() / (3600 * 24)
            result.append(data)

        result.sort(key=lambda x: x.get("days_until_due", float("inf")))
        return result


    def _compute_allowed_children(self, root_name, active_only=True):
        """
        Computes the set of all descendant chore names starting at the given root_name.
        This uses a breadth-first search (BFS) over the parent_chores relationship.

        Parameters:
            root_name: The starting chore name.
            active_only: If True, only includes active chores.

        Returns:
            A set of chore names that are reachable from the root (including the root).
        """
        allowed = set()
        frontier = [root_name]
        cur = self.connection.cursor()
        active_clause = "AND c.active = 1" if active_only else ""
        while frontier:
            current = frontier.pop()
            if current in allowed:
                continue
            allowed.add(current)
            cur.execute(f"""
                SELECT c.name
                FROM chores c
                JOIN parent_chores pc ON c.name = pc.chore_name
                WHERE pc.parent_chore = ? {active_clause}
            """, (current,))
            children = [row["name"] for row in cur.fetchall()]
            frontier.extend(children)
        return allowed

    def get_chore_details(self, name):
        """
        Returns the details of the chore identified by `name`, including:
        - Metadata from the `chores` table
        - A list of associated parent chores from the `parent_chores` table
        - A list of immediate children (chores for which this chore is a parent)

        Parameters:
            name (str): The name of the chore.

        Returns:
            dict: A dictionary containing the chore details along with lists of
                its parents and immediate children.
                If the chore is not found, prints a message and returns None.
        """
        cur = self.connection.cursor()

        # Retrieve the chore metadata.
        cur.execute("SELECT * FROM chores WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            print(f"Chore '{name}' not found.")
            return None

        details = dict(row)

        # Retrieve associated parent chores.
        cur.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (name,))
        parents = [r["parent_chore"] for r in cur.fetchall()]
        details["parents"] = parents

        # Retrieve immediate children (chores that list 'name' as a parent).
        cur.execute("SELECT chore_name FROM parent_chores WHERE parent_chore = ?", (name,))
        children = [r["chore_name"] for r in cur.fetchall()]
        details["children"] = children

        return details

    def get_children_of_parent(self, parent_name, active_only=True):
        """
        Returns a list of child chore names for a given parent chore.

        Parameters:
            parent_name: Name of the parent chore
            active_only: If True, only returns active chores

        Returns:
            List of chore names
        """
        cur = self.connection.cursor()

        query = """
            SELECT c.name
            FROM chores c
            JOIN parent_chores pc ON c.name = pc.chore_name
            WHERE pc.parent_chore = ?
        """

        if active_only:
            query += " AND c.active = 1"

        cur.execute(query, (parent_name,))
        return [row["name"] for row in cur.fetchall()]
