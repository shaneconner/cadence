"""
Experiment with model improvements.

Tests:
1. Hyperparameter variations
2. Additional features
3. Different algorithms
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
import json

# Config
DATA_PATH = Path(__file__).parent / "training_data.parquet"
EMBEDDINGS_PATH = Path(__file__).parent / "cadence_embeddings.json"

BASE_FEATURE_COLS = [
    'hour', 'day_of_week', 'is_new_session', 'session_position',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(32)] + [f'prev_emb_{i}' for i in range(32)]


def compute_hit_at_k(y_true, y_pred, groups, k=1):
    """Compute Hit@K for ranking evaluation."""
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


def load_data():
    """Load and split data."""
    df = pd.read_parquet(DATA_PATH)

    # Time-based split
    unique_times = df['logged_at'].unique()
    unique_times = np.sort(unique_times)
    split_idx = int(len(unique_times) * 0.8)
    train_times = set(unique_times[:split_idx])
    test_times = set(unique_times[split_idx:])

    train_mask = df['logged_at'].isin(train_times)
    test_mask = df['logged_at'].isin(test_times)

    return df, train_mask, test_mask


def add_extra_features(df):
    """Add additional features for experimentation."""
    df = df.copy()

    # Weekend indicator
    df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)

    # Time of day buckets
    df['morning'] = ((df['hour'] >= 5) & (df['hour'] < 12)).astype(int)
    df['afternoon'] = ((df['hour'] >= 12) & (df['hour'] < 18)).astype(int)
    df['evening'] = ((df['hour'] >= 18) | (df['hour'] < 5)).astype(int)

    # Due date buckets
    df['overdue'] = (df['days_until_due'] < 0).astype(int)
    df['due_soon'] = ((df['days_until_due'] >= 0) & (df['days_until_due'] < 3)).astype(int)
    df['due_later'] = (df['days_until_due'] >= 3).astype(int)

    # Log transform of times_logged
    df['log_times_logged'] = np.log1p(df['times_logged'])

    return df


def evaluate_model(model, X_train, y_train, X_test, y_test, groups_test, name):
    """Train and evaluate a model."""
    model.fit(X_train, y_train)
    y_pred = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_pred)
    hit1 = compute_hit_at_k(y_test, y_pred, groups_test, k=1)
    hit3 = compute_hit_at_k(y_test, y_pred, groups_test, k=3)

    print(f"\n{name}:")
    print(f"  AUC: {auc:.4f}")
    print(f"  Hit@1: {hit1:.2%}")
    print(f"  Hit@3: {hit3:.2%}")

    return {'name': name, 'auc': auc, 'hit@1': hit1, 'hit@3': hit3}


def main():
    print("Loading data...")
    df, train_mask, test_mask = load_data()

    # Add extra features
    df = add_extra_features(df)

    # Feature sets to try
    extra_features = ['is_weekend', 'morning', 'afternoon', 'evening',
                      'overdue', 'due_soon', 'due_later', 'log_times_logged']

    all_features = BASE_FEATURE_COLS + extra_features

    X_base_train = df.loc[train_mask, BASE_FEATURE_COLS].values
    X_base_test = df.loc[test_mask, BASE_FEATURE_COLS].values

    X_ext_train = df.loc[train_mask, all_features].values
    X_ext_test = df.loc[test_mask, all_features].values

    y_train = df.loc[train_mask, 'label'].values
    y_test = df.loc[test_mask, 'label'].values
    groups_test = df.loc[test_mask, 'logged_at'].values

    print(f"Train: {len(X_base_train)}, Test: {len(X_base_test)}")

    results = []

    print("\n" + "="*60)
    print("EXPERIMENT 1: Hyperparameter Variations")
    print("="*60)

    # Baseline
    results.append(evaluate_model(
        GradientBoostingClassifier(n_estimators=100, max_depth=6, random_state=42),
        X_base_train, y_train, X_base_test, y_test, groups_test,
        "GB (baseline: depth=6, n=100)"
    ))

    # Deeper trees
    results.append(evaluate_model(
        GradientBoostingClassifier(n_estimators=100, max_depth=8, random_state=42),
        X_base_train, y_train, X_base_test, y_test, groups_test,
        "GB (deeper: depth=8)"
    ))

    # More trees
    results.append(evaluate_model(
        GradientBoostingClassifier(n_estimators=200, max_depth=6, random_state=42),
        X_base_train, y_train, X_base_test, y_test, groups_test,
        "GB (more trees: n=200)"
    ))

    # Smaller learning rate + more trees
    results.append(evaluate_model(
        GradientBoostingClassifier(n_estimators=200, max_depth=6, learning_rate=0.05, random_state=42),
        X_base_train, y_train, X_base_test, y_test, groups_test,
        "GB (lower lr: 0.05, n=200)"
    ))

    print("\n" + "="*60)
    print("EXPERIMENT 2: Additional Features")
    print("="*60)

    # Extended features
    results.append(evaluate_model(
        GradientBoostingClassifier(n_estimators=100, max_depth=6, random_state=42),
        X_ext_train, y_train, X_ext_test, y_test, groups_test,
        "GB + extra features"
    ))

    print("\n" + "="*60)
    print("EXPERIMENT 3: Different Algorithms")
    print("="*60)

    # Random Forest
    results.append(evaluate_model(
        RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1),
        X_base_train, y_train, X_base_test, y_test, groups_test,
        "RandomForest"
    ))

    # Logistic Regression (simple baseline)
    results.append(evaluate_model(
        LogisticRegression(max_iter=1000, random_state=42),
        X_base_train, y_train, X_base_test, y_test, groups_test,
        "LogisticRegression"
    ))

    # Summary
    print("\n" + "="*60)
    print("SUMMARY (sorted by Hit@1)")
    print("="*60)

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('hit@1', ascending=False)

    for _, row in results_df.iterrows():
        print(f"  {row['hit@1']:.2%} Hit@1 | {row['auc']:.4f} AUC | {row['name']}")

    print("\n" + "="*60)
    print("FEATURE IMPORTANCE ANALYSIS (best model)")
    print("="*60)

    # Retrain best model and show feature importance
    best_model = GradientBoostingClassifier(n_estimators=100, max_depth=6, random_state=42)
    best_model.fit(X_base_train, y_train)

    importance_df = pd.DataFrame({
        'feature': BASE_FEATURE_COLS,
        'importance': best_model.feature_importances_
    }).sort_values('importance', ascending=False)

    print("\nTop 15 features:")
    for _, row in importance_df.head(15).iterrows():
        print(f"  {row['importance']:.4f}  {row['feature']}")

    print("\nDone!")


if __name__ == "__main__":
    main()
