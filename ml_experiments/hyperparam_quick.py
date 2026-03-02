"""
Quick hyperparameter study - fewer configurations for faster results.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score
import sys

DATA_PATH = Path(__file__).parent / "training_data.parquet"
EMBEDDING_DIM = 32

FEATURES = [
    'hour', 'day_of_week', 'is_weekend', 'time_bucket', 'hours_since_last_log',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(EMBEDDING_DIM)] + [f'prev_emb_{i}' for i in range(EMBEDDING_DIM)]


def compute_hit_at_k(y_true, y_pred, groups, k=1):
    """Compute Hit@K."""
    hits = 0
    n_groups = 0

    unique_groups = np.unique(groups)
    for group in unique_groups:
        mask = groups == group
        group_true = y_true[mask]
        group_pred = y_pred[mask]

        if group_true.sum() == 0:
            continue

        n_groups += 1
        ranked_indices = np.argsort(group_pred)[::-1]
        ranked_true = group_true[ranked_indices]

        if ranked_true[:k].sum() > 0:
            hits += 1

    return hits / n_groups if n_groups > 0 else 0


def main():
    print("=" * 60, flush=True)
    print("QUICK HYPERPARAMETER STUDY", flush=True)
    print("=" * 60, flush=True)

    df = pd.read_parquet(DATA_PATH)
    avail = [f for f in FEATURES if f in df.columns]
    print(f"\nDataset: {len(df)} rows", flush=True)

    X = df[avail].values
    y = df['label'].values
    groups = df['logged_at'].values

    # Time-based split
    unique_times = np.unique(groups)
    unique_times = np.sort(unique_times)
    split_idx = int(len(unique_times) * 0.8)
    train_times = set(unique_times[:split_idx])
    test_times = set(unique_times[split_idx:])

    train_mask = df['logged_at'].isin(train_times)
    test_mask = df['logged_at'].isin(test_times)

    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    groups_test = groups[test_mask]

    print(f"Train: {len(X_train)} | Test: {len(X_test)}", flush=True)

    # Key configurations to test
    configs = [
        # Current best
        {'n_estimators': 50, 'max_depth': 10, 'learning_rate': 0.1, 'subsample': 0.8},
        # More trees
        {'n_estimators': 100, 'max_depth': 10, 'learning_rate': 0.1, 'subsample': 0.8},
        {'n_estimators': 200, 'max_depth': 10, 'learning_rate': 0.05, 'subsample': 0.8},
        # Deeper
        {'n_estimators': 50, 'max_depth': 12, 'learning_rate': 0.1, 'subsample': 0.8},
        {'n_estimators': 100, 'max_depth': 8, 'learning_rate': 0.1, 'subsample': 0.8},
        # Higher learning rate
        {'n_estimators': 50, 'max_depth': 10, 'learning_rate': 0.2, 'subsample': 0.8},
        # Full data
        {'n_estimators': 50, 'max_depth': 10, 'learning_rate': 0.1, 'subsample': 1.0},
        # Shallow but many trees
        {'n_estimators': 200, 'max_depth': 6, 'learning_rate': 0.1, 'subsample': 0.8},
    ]

    results = []
    best_hit1 = 0
    best_config = None

    print(f"\nTesting {len(configs)} configurations...\n", flush=True)

    for i, cfg in enumerate(configs, 1):
        model = GradientBoostingClassifier(
            n_estimators=cfg['n_estimators'],
            max_depth=cfg['max_depth'],
            learning_rate=cfg['learning_rate'],
            subsample=cfg['subsample'],
            random_state=42,
            verbose=0
        )

        model.fit(X_train, y_train)
        y_pred = model.predict_proba(X_test)[:, 1]

        hit1 = compute_hit_at_k(y_test, y_pred, groups_test, k=1)
        hit3 = compute_hit_at_k(y_test, y_pred, groups_test, k=3)
        auc = roc_auc_score(y_test, y_pred)

        marker = ""
        if hit1 > best_hit1:
            best_hit1 = hit1
            best_config = cfg
            marker = " *BEST*"

        print(f"[{i}/{len(configs)}] n={cfg['n_estimators']:3d}, d={cfg['max_depth']:2d}, "
              f"lr={cfg['learning_rate']:.2f}, sub={cfg['subsample']:.1f} -> "
              f"Hit@1: {hit1:.1%}, Hit@3: {hit3:.1%}{marker}", flush=True)

        results.append({
            **cfg,
            'hit@1': hit1,
            'hit@3': hit3,
            'auc': auc
        })

    print("\n" + "=" * 60, flush=True)
    print("BEST CONFIGURATION", flush=True)
    print("=" * 60, flush=True)
    print(f"Hit@1: {best_hit1:.1%}", flush=True)
    print(f"Config: {best_config}", flush=True)

    # Show all results sorted
    print("\n" + "-" * 60, flush=True)
    print("ALL RESULTS (sorted by Hit@1)", flush=True)
    print("-" * 60, flush=True)

    results_df = pd.DataFrame(results).sort_values('hit@1', ascending=False)
    for _, row in results_df.iterrows():
        print(f"Hit@1: {row['hit@1']:.1%} | Hit@3: {row['hit@3']:.1%} | "
              f"n={int(row['n_estimators'])}, d={int(row['max_depth'])}, "
              f"lr={row['learning_rate']:.2f}, sub={row['subsample']:.1f}", flush=True)


if __name__ == "__main__":
    main()
