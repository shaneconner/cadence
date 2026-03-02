"""
Hyperparameter optimization for the chore prediction model.

Tests different model configurations to maximize Hit@1.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from itertools import product
import warnings
warnings.filterwarnings('ignore')

DATA_PATH = Path(__file__).parent / "training_data.parquet"

EMBEDDING_DIM = 32

# Feature set for production model (v1.3 with new temporal features)
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


def evaluate_model(model, X_train, y_train, X_test, y_test, groups_test):
    """Train and evaluate a model, return Hit@1."""
    model.fit(X_train, y_train)
    y_pred = model.predict_proba(X_test)[:, 1]
    hit1 = compute_hit_at_k(y_test, y_pred, groups_test, k=1)
    hit3 = compute_hit_at_k(y_test, y_pred, groups_test, k=3)
    auc = roc_auc_score(y_test, y_pred)
    return hit1, hit3, auc


def main():
    print("=" * 70)
    print("HYPERPARAMETER OPTIMIZATION")
    print("=" * 70)

    df = pd.read_parquet(DATA_PATH)
    avail = [f for f in FEATURES if f in df.columns]
    print(f"\nDataset: {len(df)} rows")
    print(f"Features: {len(avail)}")

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

    print(f"Train: {len(X_train)} | Test: {len(X_test)}")

    results = []

    # ========== Gradient Boosting Grid Search ==========
    print("\n" + "-" * 70)
    print("GRADIENT BOOSTING GRID SEARCH")
    print("-" * 70)

    gb_params = {
        'n_estimators': [50, 100, 200],
        'max_depth': [4, 6, 8, 10],
        'learning_rate': [0.05, 0.1, 0.2],
        'subsample': [0.8, 1.0],
    }

    best_gb_hit1 = 0
    best_gb_config = None

    total_configs = np.prod([len(v) for v in gb_params.values()])
    print(f"Testing {total_configs} configurations...")

    config_idx = 0
    for n_est, depth, lr, subsample in product(
        gb_params['n_estimators'],
        gb_params['max_depth'],
        gb_params['learning_rate'],
        gb_params['subsample']
    ):
        config_idx += 1
        model = GradientBoostingClassifier(
            n_estimators=n_est,
            max_depth=depth,
            learning_rate=lr,
            subsample=subsample,
            random_state=42,
            verbose=0
        )
        hit1, hit3, auc = evaluate_model(model, X_train, y_train, X_test, y_test, groups_test)

        if hit1 > best_gb_hit1:
            best_gb_hit1 = hit1
            best_gb_config = (n_est, depth, lr, subsample)
            print(f"  [{config_idx}/{total_configs}] n={n_est}, d={depth}, lr={lr}, sub={subsample} -> Hit@1: {hit1:.1%} *NEW BEST*")

        results.append({
            'model': 'GradientBoosting',
            'n_estimators': n_est,
            'max_depth': depth,
            'learning_rate': lr,
            'subsample': subsample,
            'hit@1': hit1,
            'hit@3': hit3,
            'auc': auc
        })

    # ========== Random Forest ==========
    print("\n" + "-" * 70)
    print("RANDOM FOREST")
    print("-" * 70)

    rf_params = {
        'n_estimators': [100, 200],
        'max_depth': [10, 20, None],
        'min_samples_split': [2, 5],
    }

    for n_est, depth, min_split in product(
        rf_params['n_estimators'],
        rf_params['max_depth'],
        rf_params['min_samples_split']
    ):
        model = RandomForestClassifier(
            n_estimators=n_est,
            max_depth=depth,
            min_samples_split=min_split,
            random_state=42,
            n_jobs=-1
        )
        hit1, hit3, auc = evaluate_model(model, X_train, y_train, X_test, y_test, groups_test)
        print(f"  n={n_est}, d={depth}, split={min_split} -> Hit@1: {hit1:.1%}")

        results.append({
            'model': 'RandomForest',
            'n_estimators': n_est,
            'max_depth': depth,
            'min_samples_split': min_split,
            'hit@1': hit1,
            'hit@3': hit3,
            'auc': auc
        })

    # ========== Logistic Regression ==========
    print("\n" + "-" * 70)
    print("LOGISTIC REGRESSION")
    print("-" * 70)

    for C in [0.1, 1.0, 10.0]:
        model = LogisticRegression(C=C, max_iter=1000, random_state=42)
        hit1, hit3, auc = evaluate_model(model, X_train, y_train, X_test, y_test, groups_test)
        print(f"  C={C} -> Hit@1: {hit1:.1%}")

        results.append({
            'model': 'LogisticRegression',
            'C': C,
            'hit@1': hit1,
            'hit@3': hit3,
            'auc': auc
        })

    # ========== Summary ==========
    print("\n" + "=" * 70)
    print("TOP 10 CONFIGURATIONS BY HIT@1")
    print("=" * 70)

    results_df = pd.DataFrame(results)
    top10 = results_df.nlargest(10, 'hit@1')

    for idx, row in top10.iterrows():
        config_str = f"{row['model']}"
        if row['model'] == 'GradientBoosting':
            config_str += f" (n={row['n_estimators']}, d={row['max_depth']}, lr={row['learning_rate']}, sub={row['subsample']})"
        elif row['model'] == 'RandomForest':
            config_str += f" (n={row['n_estimators']}, d={row['max_depth']}, split={row['min_samples_split']})"
        elif row['model'] == 'LogisticRegression':
            config_str += f" (C={row['C']})"

        print(f"  Hit@1: {row['hit@1']:.1%} | Hit@3: {row['hit@3']:.1%} | {config_str}")

    # Best overall
    best = results_df.loc[results_df['hit@1'].idxmax()]
    print(f"\n{'='*70}")
    print(f"BEST MODEL: {best['model']} with Hit@1 = {best['hit@1']:.1%}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
