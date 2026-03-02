"""
Build training data with advanced features for ALL candidates.

This properly computes history-based features for every (timestamp, candidate) pair,
not just the selected chore, to avoid data leakage.
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import json

DB_PATH = Path(__file__).parent.parent / "data" / "chore_data.db"
EMBEDDINGS_PATH = Path(__file__).parent / "cadence_embeddings.json"
LEAF_SESSIONS_PATH = Path(__file__).parent / "leaf_sessions.csv"
OUTPUT_PATH = Path(__file__).parent / "training_data_advanced_full.parquet"

EMBEDDING_DIM = 32


def load_embeddings():
    with open(EMBEDDINGS_PATH) as f:
        return json.load(f)


def preload_data(conn):
    """Load all necessary data into memory."""
    print("  Loading logs...")
    logs_df = pd.read_sql_query("""
        SELECT l.chore_name, l.logged_at
        FROM logs l
        WHERE l.is_genuine = 1
        ORDER BY l.logged_at
    """, conn)
    logs_df['logged_at'] = pd.to_datetime(logs_df['logged_at'], format='ISO8601')

    # Get parent set
    parents = pd.read_sql_query("""
        SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
    """, conn)
    parent_set = set(parents['parent_chore'].tolist())

    # Filter to leaf
    leaf_logs = logs_df[~logs_df['chore_name'].isin(parent_set)].copy()

    # Load chore metadata
    print("  Loading chore metadata...")
    chores_df = pd.read_sql_query("""
        SELECT name, frequency_in_days
        FROM chores
        WHERE active = 1
        AND name NOT IN (SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL)
    """, conn)

    # Category membership
    print("  Loading category membership...")
    category_membership = {}
    for cat in ['Exercise', 'Household', 'Plant', 'Dog', 'Personal Care']:
        descendants = pd.read_sql_query("""
            WITH RECURSIVE descendants AS (
                SELECT chore_name FROM parent_chores WHERE parent_chore = ?
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
            )
            SELECT chore_name FROM descendants
        """, conn, params=(cat,))
        category_membership[cat.lower()] = set(descendants['chore_name'].tolist())

    return leaf_logs, chores_df, category_membership, parent_set


def compute_decay_embedding(history_df, embeddings, ref_time, half_life_days=3.0):
    """Compute decay-weighted embedding from history."""
    if len(history_df) == 0:
        return np.zeros(EMBEDDING_DIM)

    weighted_sum = np.zeros(EMBEDDING_DIM)
    total_weight = 0

    for _, row in history_df.iterrows():
        chore = row['chore_name']
        if chore not in embeddings:
            continue

        days_ago = (ref_time - row['logged_at']).total_seconds() / 86400
        weight = np.exp(-days_ago / half_life_days)

        emb = np.array(embeddings[chore])
        weighted_sum += weight * emb
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else np.zeros(EMBEDDING_DIM)


def main():
    print("=" * 70)
    print("BUILDING TRAINING DATA WITH ADVANCED FEATURES")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    embeddings = load_embeddings()
    print(f"  Loaded {len(embeddings)} embeddings")

    leaf_sessions = pd.read_csv(LEAF_SESSIONS_PATH)
    leaf_sessions['logged_at_dt'] = pd.to_datetime(leaf_sessions['logged_at'], format='ISO8601')
    print(f"  Loaded {len(leaf_sessions)} leaf sessions")

    conn = sqlite3.connect(DB_PATH)
    leaf_logs, chores_df, category_membership, parent_set = preload_data(conn)

    # Get all candidate chores (active leaves with embeddings that have been logged)
    logged_chores = set(leaf_logs['chore_name'].unique())
    candidate_chores = [c for c in chores_df['name'].tolist() if c in embeddings and c in logged_chores]
    print(f"  {len(candidate_chores)} candidate chores")

    # Build log history lookup
    print("\nBuilding history lookup...")
    chore_log_history = {}
    for chore in candidate_chores:
        chore_log_history[chore] = leaf_logs[leaf_logs['chore_name'] == chore]['logged_at'].tolist()

    # Sort leaf_logs for efficient slicing
    leaf_logs_sorted = leaf_logs.sort_values('logged_at').reset_index(drop=True)

    # Sample timestamps (every 6th to keep manageable)
    unique_times = leaf_sessions['logged_at_dt'].unique()
    unique_times = np.sort(unique_times)
    sample_times = unique_times[::6]  # Every 6th timestamp
    print(f"  Sampling {len(sample_times)} of {len(unique_times)} timestamps")

    # Build training data
    rows = []
    print("\nBuilding training examples...")

    for t_idx, ref_time_np in enumerate(sample_times):
        if t_idx % 100 == 0:
            print(f"  Processing timestamp {t_idx}/{len(sample_times)}...")

        # Convert numpy.datetime64 to datetime
        ref_time = pd.Timestamp(ref_time_np).to_pydatetime()
        ref_time_str = ref_time.isoformat()

        # Get the actual selected chore at this time
        matching_rows = leaf_sessions[leaf_sessions['logged_at_dt'] == ref_time_np]
        if len(matching_rows) == 0:
            continue
        selected_row = matching_rows.iloc[0]
        selected_chore = selected_row['chore_name']

        # Skip if selected chore is not in our candidate set (e.g., it's a category node)
        if selected_chore not in chore_log_history:
            continue

        # Historical context (before this timestamp)
        ref_ts = pd.Timestamp(ref_time)
        cutoff_7d = ref_ts - timedelta(days=7)
        cutoff_1d = ref_ts - timedelta(days=1)

        history_7d = leaf_logs_sorted[
            (leaf_logs_sorted['logged_at'] < ref_ts) &
            (leaf_logs_sorted['logged_at'] >= cutoff_7d)
        ]
        history_1d = leaf_logs_sorted[
            (leaf_logs_sorted['logged_at'] < ref_ts) &
            (leaf_logs_sorted['logged_at'] >= cutoff_1d)
        ]

        # Compute context embeddings (same for all candidates at this timestamp)
        hist_7d_emb = compute_decay_embedding(history_7d, embeddings, ref_time, 3.0)
        hist_1d_emb = compute_decay_embedding(history_1d, embeddings, ref_time, 0.5)

        # Category activity (same for all candidates)
        cat_activity = {}
        for cat, members in category_membership.items():
            cat_activity[cat] = len(history_7d[history_7d['chore_name'].isin(members)])

        unique_7d = history_7d['chore_name'].nunique()
        unique_1d = history_1d['chore_name'].nunique()

        # Get due candidates (top 10 by due date at this time)
        # Simulate due dates at ref_time by looking at what was logged
        due_candidates = []
        for chore in candidate_chores:
            # Skip chores logged too recently (within 30min)
            recent_logs = [t for t in chore_log_history[chore] if t < ref_ts and t > ref_ts - timedelta(minutes=30)]
            if recent_logs:
                continue
            due_candidates.append(chore)

        # Take random sample of 5 negatives + the positive
        np.random.seed(int(ref_time.timestamp()) % 2**31)
        negatives = [c for c in due_candidates if c != selected_chore]
        if len(negatives) > 5:
            negatives = list(np.random.choice(negatives, 5, replace=False))

        candidates = [selected_chore] + negatives

        for chore in candidates:
            if chore not in embeddings:
                continue

            label = 1 if chore == selected_chore else 0

            # Chore-specific features
            chore_emb = np.array(embeddings[chore])

            # Days since last done (for THIS candidate)
            prev_times = [t for t in chore_log_history[chore] if t < ref_ts]
            if prev_times:
                days_since_last = (ref_ts - max(prev_times)).total_seconds() / 86400
            else:
                days_since_last = 999

            # Similarity to history
            norm_chore = np.linalg.norm(chore_emb)
            norm_7d = np.linalg.norm(hist_7d_emb)
            norm_1d = np.linalg.norm(hist_1d_emb)

            sim_7d = np.dot(chore_emb, hist_7d_emb) / (norm_chore * norm_7d + 1e-10) if norm_7d > 0 else 0
            sim_1d = np.dot(chore_emb, hist_1d_emb) / (norm_chore * norm_1d + 1e-10) if norm_1d > 0 else 0

            row = {
                'chore_name': chore,
                'logged_at': ref_time_str,
                'label': label,
                'hour': ref_time.hour,
                'day_of_week': ref_time.weekday(),
                'days_since_last_done': days_since_last,
                'similarity_to_7d_history': sim_7d,
                'similarity_to_1d_history': sim_1d,
                'unique_chores_7d': unique_7d,
                'unique_chores_1d': unique_1d,
            }

            # Category activity
            for cat, count in cat_activity.items():
                row[f'cat_activity_{cat}'] = count

            # Chore embedding
            for i, v in enumerate(chore_emb):
                row[f'emb_{i}'] = v

            # History embeddings
            for i, v in enumerate(hist_7d_emb):
                row[f'hist_7d_emb_{i}'] = v
            for i, v in enumerate(hist_1d_emb):
                row[f'hist_1d_emb_{i}'] = v

            rows.append(row)

    # Create DataFrame
    df = pd.DataFrame(rows)
    print(f"\nBuilt {len(df)} training examples")
    print(f"  Positive rate: {df['label'].mean():.2%}")

    # Save
    print(f"\nSaving to {OUTPUT_PATH}...")
    df.to_parquet(OUTPUT_PATH, index=False)

    conn.close()
    print("Done!")


if __name__ == "__main__":
    main()
