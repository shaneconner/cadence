"""
Create the suggestions table for collecting feedback/negative samples.

This table tracks:
- What suggestions were shown
- What was actually selected
- Context at the time of suggestion

This data can be used to train improved models with explicit negative examples.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "chore_data.db"


def create_suggestions_table(db_path: str = None):
    """Create the suggestions table if it doesn't exist."""
    db_path = db_path or str(DB_PATH)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- When and what was suggested
            suggested_at TEXT NOT NULL,
            session_id TEXT,  -- Groups suggestions shown together

            -- The suggested chore
            suggested_chore TEXT NOT NULL,
            suggestion_rank INTEGER,  -- 1 = top suggestion, 2 = second, etc.
            suggestion_score REAL,  -- Model confidence score

            -- Outcome
            was_selected INTEGER DEFAULT 0,  -- 1 if user selected this, 0 if not
            selected_at TEXT,  -- When it was selected (if was_selected=1)

            -- Context at suggestion time (for model training)
            prev_chores_json TEXT,  -- JSON array of previous chores in session
            days_until_due REAL,
            hour INTEGER,
            day_of_week INTEGER,

            FOREIGN KEY(suggested_chore) REFERENCES chores(name)
        )
    """)

    # Create indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_suggestions_session
        ON suggestions(session_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_suggestions_suggested_at
        ON suggestions(suggested_at)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_suggestions_was_selected
        ON suggestions(was_selected)
    """)

    conn.commit()
    conn.close()

    print(f"Created suggestions table in {db_path}")


def get_feedback_stats(db_path: str = None):
    """Get statistics about collected feedback."""
    db_path = db_path or str(DB_PATH)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if table exists
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='suggestions'
    """)
    if not cursor.fetchone():
        print("Suggestions table does not exist yet.")
        conn.close()
        return

    # Total suggestions
    cursor.execute("SELECT COUNT(*) FROM suggestions")
    total = cursor.fetchone()[0]

    # Selected vs not selected
    cursor.execute("SELECT was_selected, COUNT(*) FROM suggestions GROUP BY was_selected")
    by_selection = {row[0]: row[1] for row in cursor.fetchall()}

    # Unique sessions
    cursor.execute("SELECT COUNT(DISTINCT session_id) FROM suggestions")
    sessions = cursor.fetchone()[0]

    # Hit rate by rank
    cursor.execute("""
        SELECT suggestion_rank,
               SUM(was_selected) as selected,
               COUNT(*) as total
        FROM suggestions
        WHERE suggestion_rank IS NOT NULL
        GROUP BY suggestion_rank
        ORDER BY suggestion_rank
        LIMIT 10
    """)
    by_rank = cursor.fetchall()

    conn.close()

    print(f"\nFeedback Statistics:")
    print(f"  Total suggestions: {total}")
    print(f"  Selected: {by_selection.get(1, 0)}")
    print(f"  Not selected: {by_selection.get(0, 0)}")
    print(f"  Unique sessions: {sessions}")

    if by_rank:
        print(f"\n  Hit rate by rank:")
        for rank, selected, total_at_rank in by_rank:
            rate = selected / total_at_rank if total_at_rank > 0 else 0
            print(f"    Rank {rank}: {rate:.1%} ({selected}/{total_at_rank})")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        get_feedback_stats()
    else:
        create_suggestions_table()
        get_feedback_stats()
