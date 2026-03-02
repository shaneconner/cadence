"""
Incremental retraining - only process new logs since last training.

This is much faster than full retraining because:
1. Only extracts new leaf sessions
2. Only builds training data for new logs
3. Appends to existing training data
4. Retrains model on combined data (model training is fast)

Run this instead of retrain_model.py for regular updates.
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
import joblib
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

# Paths
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR.parent / "data" / "chore_data.db"
METADATA_PATH = SCRIPT_DIR / "model_metadata.json"
EMBEDDINGS_PATH = SCRIPT_DIR / "cadence_embeddings.json"
TRAINING_DATA_PATH = SCRIPT_DIR / "training_data.parquet"
MODEL_PATH = SCRIPT_DIR / "cadence_predictor.joblib"

NEGATIVE_SAMPLES_PER_POSITIVE = 5
EMBEDDING_DIM = 32

FEATURE_COLS = [
    'hour', 'day_of_week', 'is_weekend', 'time_bucket', 'hours_since_last_log',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(32)] + [f'prev_emb_{i}' for i in range(32)]


def load_metadata() -> dict:
    """Load training metadata."""
    if METADATA_PATH.exists():
        with open(METADATA_PATH) as f:
            return json.load(f)
    return {}


def save_metadata(metadata: dict):
    """Save training metadata."""
    with open(METADATA_PATH, 'w') as f:
        json.dump(metadata, f, indent=2)


def load_embeddings() -> dict:
    """Load chore embeddings."""
    with open(EMBEDDINGS_PATH) as f:
        return json.load(f)


def get_new_leaf_logs(conn: sqlite3.Connection, since_date: str) -> pd.DataFrame:
    """Get leaf logs (genuine only) since a given date."""
    query = """
        SELECT
            l.chore_name,
            l.logged_at,
            CAST(strftime('%H', l.logged_at) AS INTEGER) as hour,
            CAST(strftime('%w', l.logged_at) AS INTEGER) as day_of_week
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.logged_at > ?
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore
            FROM parent_chores
            WHERE parent_chore IS NOT NULL
        )
        ORDER BY l.logged_at
    """
    return pd.read_sql_query(query, conn, params=[since_date])


def get_all_leaf_logs(conn: sqlite3.Connection) -> pd.DataFrame:
    """Get all leaf logs for context (needed for prev_chores)."""
    query = """
        SELECT
            l.chore_name,
            l.logged_at
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore
            FROM parent_chores
            WHERE parent_chore IS NOT NULL
        )
        ORDER BY l.logged_at
    """
    return pd.read_sql_query(query, conn)


def get_chore_stats(conn: sqlite3.Connection) -> dict:
    """Get historical stats for each chore."""
    genuine = pd.read_sql_query("""
        SELECT chore_name, COUNT(*) as times_logged
        FROM logs WHERE is_genuine = 1
        GROUP BY chore_name
    """, conn)

    adjustments = pd.read_sql_query("""
        SELECT chore_name, COUNT(*) as adj_count
        FROM logs WHERE is_genuine = 0
        GROUP BY chore_name
    """, conn)

    stats = genuine.merge(adjustments, on='chore_name', how='left')
    stats['adj_count'] = stats['adj_count'].fillna(0)
    stats['adjustment_rate'] = stats['adj_count'] / (stats['times_logged'] + 1)

    return stats.set_index('chore_name').to_dict('index')


def get_active_leaf_chores(conn: sqlite3.Connection) -> list:
    """Get active leaf chores."""
    query = """
        SELECT c.name FROM chores c
        WHERE c.active = 1
        AND c.name NOT IN (
            SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
        )
    """
    df = pd.read_sql_query(query, conn)
    return df['name'].tolist()


def get_due_dates_at_time(conn: sqlite3.Connection, timestamp: str) -> dict:
    """Get due dates at a given timestamp."""
    query = """
        SELECT chore_name, complete_by
        FROM logs
        WHERE logged_at <= ? AND complete_by IS NOT NULL
        ORDER BY logged_at DESC
    """
    df = pd.read_sql_query(query, conn, params=[timestamp])
    due = df.groupby('chore_name').first().reset_index()[['chore_name', 'complete_by']]
    due['complete_by'] = pd.to_datetime(due['complete_by'], format='ISO8601')
    return dict(zip(due['chore_name'], due['complete_by']))


def compute_embedding_features(chore: str, prev_chores: list, embeddings: dict) -> dict:
    """Compute embedding features."""
    features = {}
    default_emb = [0.0] * EMBEDDING_DIM

    chore_emb = embeddings.get(chore, default_emb)
    for i, v in enumerate(chore_emb):
        features[f'emb_{i}'] = v

    prev_embs = [embeddings[pc] for pc in prev_chores[:3] if pc in embeddings]
    avg_prev = np.mean(prev_embs, axis=0) if prev_embs else default_emb

    for i, v in enumerate(avg_prev):
        features[f'prev_emb_{i}'] = v

    if prev_embs and chore in embeddings:
        chore_vec = np.array(chore_emb)
        prev_vec = np.array(avg_prev)
        sim = np.dot(chore_vec, prev_vec) / (np.linalg.norm(chore_vec) * np.linalg.norm(prev_vec) + 1e-10)
        features['context_similarity'] = sim
    else:
        features['context_similarity'] = 0.0

    return features


def build_examples_for_logs(
    new_logs: pd.DataFrame,
    all_logs: pd.DataFrame,
    embeddings: dict,
    stats: dict,
    active_leaves: list,
    conn: sqlite3.Connection
) -> pd.DataFrame:
    """Build training examples for new logs only."""

    examples = []
    due_cache = {}

    # Create index lookup for prev_chores
    all_logs = all_logs.sort_values('logged_at').reset_index(drop=True)
    log_to_idx = {row['logged_at']: idx for idx, row in all_logs.iterrows()}

    print(f"Building examples for {len(new_logs)} new logs...")

    for i, row in new_logs.iterrows():
        if i % 100 == 0:
            print(f"  Processing {i}/{len(new_logs)}...")

        chore = row['chore_name']
        logged_at = row['logged_at']
        hour = row['hour']
        dow = row['day_of_week']

        # Find prev_chores from all_logs
        idx = log_to_idx.get(logged_at, -1)
        if idx >= 3:
            prev_chores = [
                all_logs.iloc[idx-1]['chore_name'],
                all_logs.iloc[idx-2]['chore_name'],
                all_logs.iloc[idx-3]['chore_name'],
            ]
            prev_logged_at = all_logs.iloc[idx-1]['logged_at']
        elif idx >= 1:
            prev_chores = [all_logs.iloc[j]['chore_name'] for j in range(idx-1, -1, -1)]
            prev_logged_at = all_logs.iloc[idx-1]['logged_at']
        else:
            prev_chores = []
            prev_logged_at = None

        # Hours since last log
        if prev_logged_at:
            logged_dt = pd.to_datetime(logged_at)
            prev_dt = pd.to_datetime(prev_logged_at)
            hours_since = (logged_dt - prev_dt).total_seconds() / 3600
        else:
            hours_since = 999

        # Due dates
        date_key = logged_at[:10]
        if date_key not in due_cache:
            due_cache[date_key] = get_due_dates_at_time(conn, logged_at)
        due_dict = due_cache[date_key]

        logged_dt = pd.to_datetime(logged_at)
        days_until_due = (due_dict[chore] - logged_dt).total_seconds() / 86400 if chore in due_dict else 0

        # Temporal features
        is_weekend = 1 if dow >= 5 else 0
        if 5 <= hour < 12:
            time_bucket = 0
        elif 12 <= hour < 18:
            time_bucket = 1
        elif 18 <= hour < 24:
            time_bucket = 2
        else:
            time_bucket = 3

        # Positive example
        pos_features = {
            'chore_name': chore,
            'label': 1,
            'hour': hour,
            'day_of_week': dow,
            'is_weekend': is_weekend,
            'time_bucket': time_bucket,
            'hours_since_last_log': hours_since,
            'days_until_due': days_until_due,
            'times_logged': stats.get(chore, {}).get('times_logged', 0),
            'adjustment_rate': stats.get(chore, {}).get('adjustment_rate', 0),
            'logged_at': logged_at,
        }
        pos_features.update(compute_embedding_features(chore, prev_chores, embeddings))
        examples.append(pos_features)

        # Negative examples
        neg_candidates = [c for c in active_leaves if c != chore and c not in prev_chores]
        neg_chores = np.random.choice(
            neg_candidates,
            min(NEGATIVE_SAMPLES_PER_POSITIVE, len(neg_candidates)),
            replace=False
        ) if neg_candidates else []

        for neg_chore in neg_chores:
            neg_due = (due_dict[neg_chore] - logged_dt).total_seconds() / 86400 if neg_chore in due_dict else 0
            neg_features = {
                'chore_name': neg_chore,
                'label': 0,
                'hour': hour,
                'day_of_week': dow,
                'is_weekend': is_weekend,
                'time_bucket': time_bucket,
                'hours_since_last_log': hours_since,
                'days_until_due': neg_due,
                'times_logged': stats.get(neg_chore, {}).get('times_logged', 0),
                'adjustment_rate': stats.get(neg_chore, {}).get('adjustment_rate', 0),
                'logged_at': logged_at,
            }
            neg_features.update(compute_embedding_features(neg_chore, prev_chores, embeddings))
            examples.append(neg_features)

    return pd.DataFrame(examples)


def train_model(df: pd.DataFrame) -> tuple:
    """Train the model and return metrics."""
    X = df[FEATURE_COLS].values
    y = df['label'].values
    groups = df['logged_at'].values

    # Time-based split
    unique_times = np.sort(df['logged_at'].unique())
    split_idx = int(len(unique_times) * 0.8)
    train_times = set(unique_times[:split_idx])

    train_mask = df['logged_at'].isin(train_times)
    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[~train_mask], y[~train_mask]
    groups_test = groups[~train_mask]

    print(f"\nTraining on {len(X_train)} examples, testing on {len(X_test)}...")

    model = GradientBoostingClassifier(
        n_estimators=50,
        max_depth=10,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
        verbose=1,
    )
    model.fit(X_train, y_train)

    # Evaluate
    y_pred = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_pred)

    # Hit@K
    hit_at_1 = 0
    hit_at_3 = 0
    n_groups = 0

    for group in np.unique(groups_test):
        mask = groups_test == group
        if y_test[mask].sum() == 0:
            continue
        n_groups += 1
        ranked = np.argsort(y_pred[mask])[::-1]
        ranked_true = y_test[mask][ranked]
        if ranked_true[:1].sum() > 0:
            hit_at_1 += 1
        if ranked_true[:3].sum() > 0:
            hit_at_3 += 1

    hit_at_1 = hit_at_1 / n_groups if n_groups else 0
    hit_at_3 = hit_at_3 / n_groups if n_groups else 0

    print(f"\nResults: AUC={auc:.4f}, Hit@1={hit_at_1:.2%}, Hit@3={hit_at_3:.2%}")

    return model, hit_at_1, hit_at_3


def main():
    np.random.seed(42)

    print("="*60)
    print("INCREMENTAL MODEL RETRAINING")
    print("="*60)

    # Load metadata
    metadata = load_metadata()
    last_trained = metadata.get('last_trained', '2020-01-01')
    print(f"\nLast trained: {last_trained}")

    # Connect to DB
    conn = sqlite3.connect(DB_PATH)

    # Get new logs since last training
    new_logs = get_new_leaf_logs(conn, last_trained)
    print(f"New logs since then: {len(new_logs)}")

    if len(new_logs) == 0:
        print("\nNo new logs to train on. Exiting.")
        conn.close()
        return

    # Load existing training data
    if TRAINING_DATA_PATH.exists():
        existing_df = pd.read_parquet(TRAINING_DATA_PATH)
        print(f"Existing training data: {len(existing_df)} examples")
    else:
        existing_df = pd.DataFrame()
        print("No existing training data found.")

    # Load embeddings
    embeddings = load_embeddings()
    print(f"Loaded {len(embeddings)} embeddings")

    # Get stats and active leaves
    stats = get_chore_stats(conn)
    active_leaves = get_active_leaf_chores(conn)
    all_logs = get_all_leaf_logs(conn)

    print(f"Active leaf chores: {len(active_leaves)}")

    # Build examples for new logs only
    new_examples = build_examples_for_logs(
        new_logs, all_logs, embeddings, stats, active_leaves, conn
    )
    conn.close()

    print(f"\nNew training examples: {len(new_examples)}")

    # Combine with existing
    if len(existing_df) > 0:
        combined_df = pd.concat([existing_df, new_examples], ignore_index=True)
        # Remove duplicates by logged_at + chore_name
        combined_df = combined_df.drop_duplicates(subset=['logged_at', 'chore_name'], keep='last')
    else:
        combined_df = new_examples

    print(f"Combined training data: {len(combined_df)} examples")

    # Save combined training data
    combined_df.to_parquet(TRAINING_DATA_PATH, index=False)

    # Train model
    model, hit_at_1, hit_at_3 = train_model(combined_df)

    # Save model
    joblib.dump(model, MODEL_PATH)
    print(f"\nModel saved to {MODEL_PATH}")

    # Update metadata
    version = metadata.get('model_version', '1.0')
    try:
        major, minor = version.split('.')
        version = f"{major}.{int(minor) + 1}"
    except:
        version = "1.1"

    new_metadata = {
        "last_trained": datetime.now().isoformat(),
        "training_data_size": len(combined_df),
        "leaf_logs_at_training": len(all_logs),
        "model_version": version,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "notes": f"Incremental retrain on {datetime.now().strftime('%Y-%m-%d')} (+{len(new_logs)} logs)"
    }
    save_metadata(new_metadata)

    print("\n" + "="*60)
    print("INCREMENTAL RETRAINING COMPLETE")
    print("="*60)
    print(f"New logs processed: {len(new_logs)}")
    print(f"Total training examples: {len(combined_df)}")
    print(f"Model version: {version}")
    print(f"Hit@1: {hit_at_1:.2%}")
    print(f"Hit@3: {hit_at_3:.2%}")


if __name__ == "__main__":
    main()
