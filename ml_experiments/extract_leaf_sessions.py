"""
Extract leaf exercise logs and group into sessions.

This script reconstructs which exercises were the actual "leaf" exercises
(the ones you selected) vs the parent categories that were auto-logged.

Output: CSV with columns:
- session_id: unique session identifier
- leaf_chore: the actual exercise/chore selected
- logged_at: timestamp
- hour, day_of_week: temporal features
- prev_leaf_1, prev_leaf_2, ...: previous leaf exercises in session
- minutes_since_last: time gap from previous log
"""

import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

# Config
DB_PATH = Path(__file__).parent.parent / "data" / "chore_data.db"
OUTPUT_PATH = Path(__file__).parent / "leaf_sessions.csv"
SESSION_GAP_MINUTES = 30  # New session if gap > this


def get_parent_chores(conn: sqlite3.Connection) -> set:
    """Get all chores that are parents of other chores."""
    query = """
        SELECT DISTINCT parent_chore
        FROM parent_chores
        WHERE parent_chore IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    return set(df['parent_chore'].tolist())


def get_genuine_logs(conn: sqlite3.Connection) -> pd.DataFrame:
    """Get all genuine (non-adjustment) logs."""
    query = """
        SELECT id, chore_name, logged_at, is_genuine
        FROM logs
        WHERE is_genuine = 1
        ORDER BY id
    """
    df = pd.read_sql_query(query, conn)
    df['logged_at'] = pd.to_datetime(df['logged_at'], format='ISO8601')
    return df


def identify_leaf_logs(logs_df: pd.DataFrame, parent_chores: set) -> pd.DataFrame:
    """
    Identify which logs are leaf exercises vs auto-logged parents.

    Strategy:
    - Group logs into batches (same second or sequential within ~1 second)
    - Within each batch, the leaf is:
      1. The chore that is NOT a parent of anything, OR
      2. The first log by ID (fallback)
    """
    logs_df = logs_df.copy()
    logs_df['is_parent'] = logs_df['chore_name'].isin(parent_chores)

    # Create batch ID based on time gaps
    # If gap > 1 second, it's a new batch
    logs_df['time_diff'] = logs_df['logged_at'].diff().dt.total_seconds().fillna(999)
    logs_df['new_batch'] = logs_df['time_diff'] > 1.0
    logs_df['batch_id'] = logs_df['new_batch'].cumsum()

    # For each batch, identify the leaf
    leaf_logs = []

    for batch_id, batch in logs_df.groupby('batch_id'):
        # Try to find non-parent chores in this batch
        non_parents = batch[~batch['is_parent']]

        if len(non_parents) >= 1:
            # Take the first non-parent (should be the leaf)
            leaf = non_parents.iloc[0]
        else:
            # Fallback: take first log in batch (shouldn't happen often)
            leaf = batch.iloc[0]

        leaf_logs.append({
            'id': leaf['id'],
            'chore_name': leaf['chore_name'],
            'logged_at': leaf['logged_at'],
            'batch_id': batch_id,
            'batch_size': len(batch),
            'parents_logged': len(batch) - 1
        })

    return pd.DataFrame(leaf_logs)


def create_sessions(leaf_df: pd.DataFrame, gap_minutes: int = 30) -> pd.DataFrame:
    """
    Group leaf logs into sessions based on time gaps.

    A new session starts when gap > gap_minutes.
    """
    leaf_df = leaf_df.copy().sort_values('logged_at').reset_index(drop=True)

    # Calculate time since previous leaf
    leaf_df['time_diff_minutes'] = (
        leaf_df['logged_at'].diff().dt.total_seconds() / 60
    ).fillna(999)

    # New session if gap > threshold
    leaf_df['new_session'] = leaf_df['time_diff_minutes'] > gap_minutes
    leaf_df['session_id'] = leaf_df['new_session'].cumsum()

    return leaf_df


def add_sequence_features(df: pd.DataFrame, n_prev: int = 5) -> pd.DataFrame:
    """Add previous leaf exercises as features."""
    df = df.copy()

    for i in range(1, n_prev + 1):
        df[f'prev_leaf_{i}'] = df['chore_name'].shift(i)
        # Only keep if same session
        df.loc[df['session_id'] != df['session_id'].shift(i), f'prev_leaf_{i}'] = None

    return df


def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add hour and day of week features."""
    df = df.copy()
    df['hour'] = df['logged_at'].dt.hour
    df['day_of_week'] = df['logged_at'].dt.dayofweek  # 0=Monday
    df['date'] = df['logged_at'].dt.date
    return df


def main():
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    # Step 1: Get parent chores
    print("Identifying parent chores...")
    parent_chores = get_parent_chores(conn)
    print(f"  Found {len(parent_chores)} parent categories")

    # Step 2: Get genuine logs
    print("Loading genuine logs...")
    logs_df = get_genuine_logs(conn)
    print(f"  Loaded {len(logs_df):,} genuine logs")

    # Step 3: Identify leaf logs
    print("Identifying leaf exercises...")
    leaf_df = identify_leaf_logs(logs_df, parent_chores)
    print(f"  Identified {len(leaf_df):,} leaf exercise logs")
    print(f"  Average parents per leaf: {leaf_df['parents_logged'].mean():.1f}")

    # Step 4: Create sessions
    print(f"Grouping into sessions (gap > {SESSION_GAP_MINUTES} min)...")
    leaf_df = create_sessions(leaf_df, SESSION_GAP_MINUTES)
    n_sessions = leaf_df['session_id'].nunique()
    print(f"  Created {n_sessions:,} sessions")
    print(f"  Average leaves per session: {len(leaf_df) / n_sessions:.1f}")

    # Step 5: Add features
    print("Adding sequence features...")
    leaf_df = add_sequence_features(leaf_df, n_prev=5)

    print("Adding temporal features...")
    leaf_df = add_temporal_features(leaf_df)

    # Step 6: Save
    print(f"Saving to {OUTPUT_PATH}...")
    leaf_df.to_csv(OUTPUT_PATH, index=False)

    # Summary stats
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    print(f"Total leaf logs: {len(leaf_df):,}")
    print(f"Unique exercises: {leaf_df['chore_name'].nunique()}")
    print(f"Sessions: {n_sessions:,}")
    print(f"Date range: {leaf_df['date'].min()} to {leaf_df['date'].max()}")

    print("\nTop 15 most logged leaf exercises:")
    top_exercises = leaf_df['chore_name'].value_counts().head(15)
    for name, count in top_exercises.items():
        print(f"  {count:4d}  {name}")

    print("\nSession size distribution:")
    session_sizes = leaf_df.groupby('session_id').size()
    print(f"  Min: {session_sizes.min()}")
    print(f"  Median: {session_sizes.median():.0f}")
    print(f"  Mean: {session_sizes.mean():.1f}")
    print(f"  Max: {session_sizes.max()}")

    conn.close()
    print(f"\nDone! Output saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
