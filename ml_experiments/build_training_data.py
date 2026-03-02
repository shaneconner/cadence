"""
Build training dataset for chore prediction model.

For each leaf log, we create:
- Positive example: the exercise that was actually selected
- Negative examples: other exercises that could have been selected (sampled)

Features include:
- Chore embedding
- Previous chore embeddings (session context)
- Temporal (hour, day of week)
- Days until due at selection time
- Historical (times logged, adjustment rate)
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json

# Config
DB_PATH = Path(__file__).parent.parent / "data" / "chore_data.db"
LEAF_SESSIONS_PATH = Path(__file__).parent / "leaf_sessions.csv"
EMBEDDINGS_PATH = Path(__file__).parent / "cadence_embeddings.json"
OUTPUT_PATH = Path(__file__).parent / "training_data.parquet"

NEGATIVE_SAMPLES_PER_POSITIVE = 5
EMBEDDING_DIM = 32


def load_embeddings(path: Path) -> dict:
    """Load chore embeddings from JSON."""
    with open(path) as f:
        return json.load(f)


def get_chore_stats(conn: sqlite3.Connection) -> pd.DataFrame:
    """Get historical stats for each chore."""
    # Times logged (genuine only)
    genuine_counts = pd.read_sql_query("""
        SELECT chore_name, COUNT(*) as times_logged
        FROM logs WHERE is_genuine = 1
        GROUP BY chore_name
    """, conn)

    # Adjustment counts (non-genuine)
    adjustment_counts = pd.read_sql_query("""
        SELECT chore_name, COUNT(*) as adjustment_count
        FROM logs WHERE is_genuine = 0
        GROUP BY chore_name
    """, conn)

    # Merge
    stats = genuine_counts.merge(adjustment_counts, on='chore_name', how='left')
    stats['adjustment_count'] = stats['adjustment_count'].fillna(0)
    stats['adjustment_rate'] = stats['adjustment_count'] / (stats['times_logged'] + 1)

    return stats


def get_due_dates_at_time(conn: sqlite3.Connection, timestamp: str) -> pd.DataFrame:
    """
    Reconstruct what the due dates would have been at a given timestamp.

    This is tricky - we need to look at the complete_by from logs
    to see when each chore was due at that point in time.

    Simplified approach: use the most recent complete_by before the timestamp.
    """
    query = """
        SELECT chore_name, complete_by, logged_at
        FROM logs
        WHERE logged_at <= ?
        AND complete_by IS NOT NULL
        ORDER BY logged_at DESC
    """
    df = pd.read_sql_query(query, conn, params=[timestamp])

    # Get most recent complete_by for each chore
    due_dates = df.groupby('chore_name').first().reset_index()[['chore_name', 'complete_by']]
    due_dates['complete_by'] = pd.to_datetime(due_dates['complete_by'], format='ISO8601')

    return due_dates


def get_active_leaf_chores(conn: sqlite3.Connection) -> set:
    """Get all active chores that are leaves (not parents of anything)."""
    query = """
        SELECT c.name
        FROM chores c
        WHERE c.active = 1
        AND c.name NOT IN (
            SELECT DISTINCT parent_chore
            FROM parent_chores
            WHERE parent_chore IS NOT NULL
        )
    """
    df = pd.read_sql_query(query, conn)
    return set(df['name'].tolist())


def compute_embedding_features(chore: str, prev_chores: list, embeddings: dict) -> dict:
    """Compute embedding-based features for a chore."""
    features = {}

    # Default embedding (zeros if not found)
    default_emb = [0.0] * EMBEDDING_DIM

    # Current chore embedding
    chore_emb = embeddings.get(chore, default_emb)
    for i, v in enumerate(chore_emb):
        features[f'emb_{i}'] = v

    # Previous chore embeddings (average of last 3)
    prev_embs = []
    for pc in prev_chores[:3]:
        if pc and pc in embeddings:
            prev_embs.append(embeddings[pc])

    if prev_embs:
        avg_prev = np.mean(prev_embs, axis=0)
    else:
        avg_prev = default_emb

    for i, v in enumerate(avg_prev):
        features[f'prev_emb_{i}'] = v

    # Similarity to previous context
    if prev_embs and chore in embeddings:
        chore_vec = np.array(chore_emb)
        prev_vec = np.array(avg_prev)
        # Cosine similarity
        sim = np.dot(chore_vec, prev_vec) / (np.linalg.norm(chore_vec) * np.linalg.norm(prev_vec) + 1e-10)
        features['context_similarity'] = sim
    else:
        features['context_similarity'] = 0.0

    return features


def build_training_examples(
    leaf_df: pd.DataFrame,
    embeddings: dict,
    chore_stats: pd.DataFrame,
    active_leaves: set,
    conn: sqlite3.Connection,
    neg_samples: int = 5
) -> pd.DataFrame:
    """Build positive and negative training examples."""

    examples = []
    stats_dict = chore_stats.set_index('chore_name').to_dict('index')
    active_leaves_list = list(active_leaves)

    # Cache for due dates (expensive to compute)
    due_cache = {}

    # Sort leaf_df by time to compute prev_chores properly
    leaf_df = leaf_df.sort_values('logged_at').reset_index(drop=True)

    print(f"Building examples from {len(leaf_df)} leaf logs...")

    for idx, row in leaf_df.iterrows():
        if idx % 500 == 0:
            print(f"  Processing {idx}/{len(leaf_df)}...")

        chore = row['chore_name']
        logged_at = row['logged_at']
        hour = row['hour']
        dow = row['day_of_week']

        # Get previous 3 chores (regardless of session/time gap)
        if idx >= 3:
            prev_chores = [
                leaf_df.iloc[idx-1]['chore_name'],
                leaf_df.iloc[idx-2]['chore_name'],
                leaf_df.iloc[idx-3]['chore_name'],
            ]
            prev_logged_at = leaf_df.iloc[idx-1]['logged_at']
        elif idx >= 1:
            prev_chores = [leaf_df.iloc[i]['chore_name'] for i in range(idx-1, -1, -1)]
            prev_logged_at = leaf_df.iloc[idx-1]['logged_at']
        else:
            prev_chores = []
            prev_logged_at = None

        # Compute hours since last log (continuous feature)
        if prev_logged_at:
            logged_dt = pd.to_datetime(logged_at)
            prev_dt = pd.to_datetime(prev_logged_at)
            hours_since_last_log = (logged_dt - prev_dt).total_seconds() / 3600
        else:
            hours_since_last_log = 999  # First log ever

        # Get due dates at this timestamp (with caching by date)
        date_key = logged_at[:10]  # YYYY-MM-DD
        if date_key not in due_cache:
            due_cache[date_key] = get_due_dates_at_time(conn, logged_at)
        due_df = due_cache[date_key]
        due_dict = dict(zip(due_df['chore_name'], due_df['complete_by']))

        # Compute days until due for target chore
        logged_dt = pd.to_datetime(logged_at)
        if chore in due_dict:
            days_until_due = (due_dict[chore] - logged_dt).total_seconds() / 86400
        else:
            days_until_due = 0  # Unknown

        # Derived temporal features
        is_weekend = 1 if dow >= 5 else 0  # Saturday=5, Sunday=6
        # Time buckets: morning (5-11), afternoon (12-17), evening (18-23), night (0-4)
        if 5 <= hour < 12:
            time_bucket = 0  # morning
        elif 12 <= hour < 18:
            time_bucket = 1  # afternoon
        elif 18 <= hour < 24:
            time_bucket = 2  # evening
        else:
            time_bucket = 3  # night (0-4)

        # Build positive example (removed is_new_session and session_position, added hours_since_last_log)
        pos_features = {
            'chore_name': chore,
            'label': 1,
            'hour': hour,
            'day_of_week': dow,
            'is_weekend': is_weekend,
            'time_bucket': time_bucket,
            'hours_since_last_log': hours_since_last_log,
            'days_until_due': days_until_due,
            'times_logged': stats_dict.get(chore, {}).get('times_logged', 0),
            'adjustment_rate': stats_dict.get(chore, {}).get('adjustment_rate', 0),
            'logged_at': logged_at,
        }
        pos_features.update(compute_embedding_features(chore, prev_chores, embeddings))
        examples.append(pos_features)

        # Build negative examples (sample from other active leaves)
        neg_candidates = [c for c in active_leaves_list if c != chore and c not in prev_chores]
        if len(neg_candidates) > neg_samples:
            neg_chores = np.random.choice(neg_candidates, neg_samples, replace=False)
        else:
            neg_chores = neg_candidates

        for neg_chore in neg_chores:
            if neg_chore in due_dict:
                neg_days_until_due = (due_dict[neg_chore] - logged_dt).total_seconds() / 86400
            else:
                neg_days_until_due = 0

            neg_features = {
                'chore_name': neg_chore,
                'label': 0,
                'hour': hour,
                'day_of_week': dow,
                'is_weekend': is_weekend,
                'time_bucket': time_bucket,
                'hours_since_last_log': hours_since_last_log,
                'days_until_due': neg_days_until_due,
                'times_logged': stats_dict.get(neg_chore, {}).get('times_logged', 0),
                'adjustment_rate': stats_dict.get(neg_chore, {}).get('adjustment_rate', 0),
                'logged_at': logged_at,
            }
            neg_features.update(compute_embedding_features(neg_chore, prev_chores, embeddings))
            examples.append(neg_features)

    return pd.DataFrame(examples)


def main():
    print("Loading data...")

    # Load leaf sessions
    leaf_df = pd.read_csv(LEAF_SESSIONS_PATH)
    print(f"  Leaf sessions: {len(leaf_df)}")

    # Load embeddings
    embeddings = load_embeddings(EMBEDDINGS_PATH)
    print(f"  Embeddings: {len(embeddings)} chores")

    # Connect to DB
    conn = sqlite3.connect(DB_PATH)

    # Get chore stats
    print("Computing chore statistics...")
    chore_stats = get_chore_stats(conn)
    print(f"  Stats for {len(chore_stats)} chores")

    # Get active leaf chores
    active_leaves = get_active_leaf_chores(conn)
    print(f"  Active leaf chores: {len(active_leaves)}")

    # Build training data
    print(f"\nBuilding training data (neg_samples={NEGATIVE_SAMPLES_PER_POSITIVE})...")
    train_df = build_training_examples(
        leaf_df, embeddings, chore_stats, active_leaves, conn,
        neg_samples=NEGATIVE_SAMPLES_PER_POSITIVE
    )

    conn.close()

    # Summary
    print("\n" + "="*60)
    print("TRAINING DATA SUMMARY")
    print("="*60)
    print(f"Total examples: {len(train_df)}")
    print(f"Positive examples: {train_df['label'].sum()}")
    print(f"Negative examples: {(train_df['label'] == 0).sum()}")
    print(f"Features: {len(train_df.columns)}")

    # Save
    print(f"\nSaving to {OUTPUT_PATH}...")
    train_df.to_parquet(OUTPUT_PATH, index=False)

    # Preview
    print("\nFeature columns:")
    for col in sorted(train_df.columns):
        if not col.startswith('emb_') and not col.startswith('prev_emb_'):
            print(f"  {col}: {train_df[col].dtype}")
    print(f"  emb_0 ... emb_{EMBEDDING_DIM-1}: float64")
    print(f"  prev_emb_0 ... prev_emb_{EMBEDDING_DIM-1}: float64")

    print("\nDone!")


if __name__ == "__main__":
    np.random.seed(42)
    main()
