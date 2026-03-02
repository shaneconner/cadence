"""
Advanced feature engineering experiments.

Tests:
1. Temporal sequence features (last N days of activity)
2. Pretrained semantic embeddings (sentence-transformers)
3. Aggregated category embeddings
4. Time-decay weighted history

OPTIMIZED: Preloads all data into memory for batch processing instead of per-row SQL queries.
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
OUTPUT_PATH = Path(__file__).parent / "training_data_advanced.parquet"

EMBEDDING_DIM = 32


def load_embeddings():
    with open(EMBEDDINGS_PATH) as f:
        return json.load(f)


def preload_all_data(conn):
    """Load all necessary data into memory once."""
    print("  Preloading logs...")

    # Load all genuine logs with their chore names
    logs_df = pd.read_sql_query("""
        SELECT l.chore_name, l.logged_at
        FROM logs l
        WHERE l.is_genuine = 1
        ORDER BY l.logged_at
    """, conn)
    logs_df['logged_at'] = pd.to_datetime(logs_df['logged_at'], format='ISO8601')

    # Get set of parent chores (to filter leaf chores)
    parents = pd.read_sql_query("""
        SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
    """, conn)
    parent_set = set(parents['parent_chore'].tolist())

    # Filter to leaf chores only
    leaf_logs_df = logs_df[~logs_df['chore_name'].isin(parent_set)].copy()
    print(f"    Loaded {len(logs_df)} total logs, {len(leaf_logs_df)} leaf logs")

    # Preload category membership for category activity counts
    print("  Preloading category membership...")
    category_membership = {}
    for cat in ['Exercise', 'Household', 'Plant', 'Dog', 'Personal Care']:
        descendants = pd.read_sql_query(f"""
            WITH RECURSIVE descendants AS (
                SELECT chore_name FROM parent_chores WHERE parent_chore = ?
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
            )
            SELECT chore_name FROM descendants
        """, conn, params=(cat,))
        category_membership[cat.lower()] = set(descendants['chore_name'].tolist())

    return leaf_logs_df, logs_df, category_membership


def compute_decay_weighted_embedding_batch(
    history_df: pd.DataFrame,
    embeddings: dict,
    reference_time: datetime,
    half_life_days: float = 3.0
) -> np.ndarray:
    """Compute time-decay weighted average of chore embeddings."""
    if len(history_df) == 0:
        return np.zeros(EMBEDDING_DIM)

    weighted_sum = np.zeros(EMBEDDING_DIM)
    total_weight = 0

    for _, row in history_df.iterrows():
        chore = row['chore_name']
        if chore not in embeddings:
            continue

        days_ago = (reference_time - row['logged_at']).total_seconds() / 86400
        weight = np.exp(-days_ago / half_life_days)

        emb = np.array(embeddings[chore])
        weighted_sum += weight * emb
        total_weight += weight

    if total_weight > 0:
        return weighted_sum / total_weight
    return np.zeros(EMBEDDING_DIM)


def try_load_sentence_transformer():
    """Try to load sentence-transformers for semantic embeddings."""
    import os
    if not os.environ.get('USE_SEMANTIC'):
        print("Skipping sentence-transformers (set USE_SEMANTIC=1 to enable)")
        return None
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM-L6-v2')
        return model
    except ImportError:
        print("sentence-transformers not available")
        return None


def compute_semantic_embeddings(model, chore_names: list, descriptions: dict) -> dict:
    """Compute semantic embeddings for chore names + descriptions."""
    if model is None:
        return {}

    texts = []
    names = []
    for name in chore_names:
        desc = descriptions.get(name, "")
        text = f"{name}. {desc}" if desc else name
        texts.append(text)
        names.append(name)

    embeddings = model.encode(texts, show_progress_bar=True)

    return {name: emb.tolist() for name, emb in zip(names, embeddings)}


def build_advanced_features_batch(
    leaf_df: pd.DataFrame,
    embeddings: dict,
    leaf_logs_df: pd.DataFrame,
    all_logs_df: pd.DataFrame,
    category_membership: dict,
    semantic_embeddings: dict = None
) -> pd.DataFrame:
    """Build advanced feature set using batch processing."""

    # Convert leaf_df logged_at to datetime for comparisons
    leaf_df = leaf_df.copy()
    leaf_df['logged_at_dt'] = pd.to_datetime(leaf_df['logged_at'], format='ISO8601')

    # Sort logs for efficient lookups
    leaf_logs_sorted = leaf_logs_df.sort_values('logged_at').reset_index(drop=True)
    all_logs_sorted = all_logs_df.sort_values('logged_at').reset_index(drop=True)

    # Precompute "days since last done" for each chore
    print("  Precomputing days since last done...")
    last_done_by_chore = {}
    for chore in leaf_df['chore_name'].unique():
        chore_logs = all_logs_sorted[all_logs_sorted['chore_name'] == chore]['logged_at'].tolist()
        last_done_by_chore[chore] = chore_logs  # List of all times this chore was done

    features_list = []
    total_rows = len(leaf_df)

    print(f"Building advanced features for {total_rows} rows...")

    for idx, row in leaf_df.iterrows():
        if idx % 500 == 0:
            print(f"  Processing {idx}/{total_rows}...")

        chore = row['chore_name']
        logged_at_dt = row['logged_at_dt']
        logged_at_str = row['logged_at']

        features = {
            'chore_name': chore,
            'logged_at': logged_at_str,
        }

        # Filter history using pandas (vectorized, uses datetime index)
        cutoff_7d = logged_at_dt - timedelta(days=7)
        cutoff_1d = logged_at_dt - timedelta(days=1)

        history_7d = leaf_logs_sorted[
            (leaf_logs_sorted['logged_at'] < logged_at_dt) &
            (leaf_logs_sorted['logged_at'] >= cutoff_7d)
        ]
        history_1d = leaf_logs_sorted[
            (leaf_logs_sorted['logged_at'] < logged_at_dt) &
            (leaf_logs_sorted['logged_at'] >= cutoff_1d)
        ]

        # === 1. Time-decay weighted history (last 7 days) ===
        decay_emb_7d = compute_decay_weighted_embedding_batch(
            history_7d, embeddings, logged_at_dt, half_life_days=3.0
        )
        for i, v in enumerate(decay_emb_7d):
            features[f'hist_7d_emb_{i}'] = v

        # === 2. Time-decay weighted history (last 1 day) ===
        decay_emb_1d = compute_decay_weighted_embedding_batch(
            history_1d, embeddings, logged_at_dt, half_life_days=0.5
        )
        for i, v in enumerate(decay_emb_1d):
            features[f'hist_1d_emb_{i}'] = v

        # === 3. Category activity counts (last 7 days) ===
        for cat, members in category_membership.items():
            cat_history = history_7d[history_7d['chore_name'].isin(members)]
            features[f'cat_activity_{cat}'] = len(cat_history)

        # === 4. Unique chores done recently ===
        features['unique_chores_7d'] = history_7d['chore_name'].nunique()
        features['unique_chores_1d'] = history_1d['chore_name'].nunique()

        # === 5. Days since this specific chore was last done ===
        chore_history = last_done_by_chore.get(chore, [])
        prev_times = [t for t in chore_history if t < logged_at_dt]
        if prev_times:
            last_time = max(prev_times)
            features['days_since_last_done'] = (logged_at_dt - last_time).total_seconds() / 86400
        else:
            features['days_since_last_done'] = 999

        # === 6. Semantic embeddings (if available) ===
        if semantic_embeddings and chore in semantic_embeddings:
            sem_emb = semantic_embeddings[chore]
            for i, v in enumerate(sem_emb[:32]):
                features[f'semantic_emb_{i}'] = v

        # === 7. Similarity to recent history ===
        if chore in embeddings:
            chore_emb = np.array(embeddings[chore])
            norm_chore = np.linalg.norm(chore_emb)
            norm_7d = np.linalg.norm(decay_emb_7d)
            norm_1d = np.linalg.norm(decay_emb_1d)

            sim_7d = np.dot(chore_emb, decay_emb_7d) / (norm_chore * norm_7d + 1e-10) if norm_7d > 0 else 0
            sim_1d = np.dot(chore_emb, decay_emb_1d) / (norm_chore * norm_1d + 1e-10) if norm_1d > 0 else 0
            features['similarity_to_7d_history'] = sim_7d
            features['similarity_to_1d_history'] = sim_1d
        else:
            features['similarity_to_7d_history'] = 0
            features['similarity_to_1d_history'] = 0

        features_list.append(features)

    return pd.DataFrame(features_list)


def main():
    print("Loading data...")

    # Load embeddings
    embeddings = load_embeddings()
    print(f"  Loaded {len(embeddings)} chore embeddings")

    # Load leaf sessions
    leaf_df = pd.read_csv(LEAF_SESSIONS_PATH)
    print(f"  Loaded {len(leaf_df)} leaf sessions")

    # Connect to DB
    conn = sqlite3.connect(DB_PATH)

    # Preload all data into memory (key optimization)
    print("\nPreloading data into memory...")
    leaf_logs_df, all_logs_df, category_membership = preload_all_data(conn)

    # Try to load semantic embeddings
    print("\nTrying to load sentence-transformers...")
    st_model = try_load_sentence_transformer()

    semantic_embeddings = {}
    if st_model:
        print("Computing semantic embeddings for all chores...")
        cursor = conn.cursor()
        cursor.execute("SELECT name, description FROM chores WHERE active = 1")
        descriptions = {row[0]: row[1] or "" for row in cursor.fetchall()}
        chore_names = list(set(leaf_df['chore_name'].tolist()))
        semantic_embeddings = compute_semantic_embeddings(st_model, chore_names, descriptions)
        print(f"  Computed {len(semantic_embeddings)} semantic embeddings")

    # Build advanced features using batch processing
    print("\nBuilding advanced features (batch mode)...")
    advanced_df = build_advanced_features_batch(
        leaf_df, embeddings, leaf_logs_df, all_logs_df,
        category_membership, semantic_embeddings
    )

    # Merge with original features
    original_cols = ['chore_name', 'logged_at', 'hour', 'day_of_week', 'session_id',
                     'is_new_session', 'session_position', 'prev_leaf_1', 'prev_leaf_2', 'prev_leaf_3']
    existing_cols = [c for c in original_cols if c in leaf_df.columns]

    merged_df = leaf_df[existing_cols].merge(
        advanced_df,
        on=['chore_name', 'logged_at'],
        how='inner'
    )

    # Save
    print(f"\nSaving to {OUTPUT_PATH}...")
    merged_df.to_parquet(OUTPUT_PATH, index=False)

    # Summary
    print("\n" + "="*60)
    print("ADVANCED FEATURES SUMMARY")
    print("="*60)
    print(f"Total rows: {len(merged_df)}")
    print(f"Total features: {len(merged_df.columns)}")
    print("\nNew feature groups:")
    print(f"  - hist_7d_emb_*: 32 dims (decay-weighted 7-day history)")
    print(f"  - hist_1d_emb_*: 32 dims (decay-weighted 1-day history)")
    print(f"  - cat_activity_*: 5 dims (category activity counts)")
    print(f"  - unique_chores_*: 2 dims (diversity metrics)")
    print(f"  - days_since_last_done: 1 dim")
    print(f"  - similarity_to_*_history: 2 dims")
    if semantic_embeddings:
        print(f"  - semantic_emb_*: 32 dims (sentence-transformer)")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
