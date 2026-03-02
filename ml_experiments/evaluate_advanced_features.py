"""
Evaluate advanced features on properly balanced training data.

This compares models with different feature sets to see if the
history-based features actually improve prediction.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

DATA_PATH = Path(__file__).parent / "training_data_advanced_full.parquet"

EMBEDDING_DIM = 32

# Feature groups
TEMPORAL = ['hour', 'day_of_week']
HISTORY_SIMPLE = ['days_since_last_done', 'unique_chores_7d', 'unique_chores_1d']
SIMILARITY = ['similarity_to_7d_history', 'similarity_to_1d_history']
CATEGORY_ACTIVITY = [f'cat_activity_{c}' for c in ['exercise', 'household', 'plant', 'dog', 'personal care']]
CHORE_EMB = [f'emb_{i}' for i in range(EMBEDDING_DIM)]
HIST_7D_EMB = [f'hist_7d_emb_{i}' for i in range(EMBEDDING_DIM)]
HIST_1D_EMB = [f'hist_1d_emb_{i}' for i in range(EMBEDDING_DIM)]


def compute_hit_at_k(y_true, y_pred, groups, k_values=[1, 3, 5]):
    """Compute Hit@K for each group."""
    results = {f'hit@{k}': 0 for k in k_values}
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

        for k in k_values:
            if ranked_true[:k].sum() > 0:
                results[f'hit@{k}'] += 1

    for k in k_values:
        results[f'hit@{k}'] = results[f'hit@{k}'] / n_groups if n_groups > 0 else 0

    return results, n_groups


def train_and_evaluate(df, features, name):
    """Train and evaluate a model."""
    avail = [f for f in features if f in df.columns]
    if not avail:
        print(f"  {name}: No features available!")
        return None

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

    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
        verbose=0
    )
    model.fit(X_train, y_train)
    y_pred = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_pred)
    hit_results, n_groups = compute_hit_at_k(y_test, y_pred, groups_test)

    return {
        'name': name,
        'features': len(avail),
        'auc': auc,
        **hit_results,
        'n_groups': n_groups,
        'model': model,
        'feature_names': avail
    }


def main():
    print("=" * 70)
    print("ADVANCED FEATURES EVALUATION")
    print("=" * 70)

    df = pd.read_parquet(DATA_PATH)
    print(f"\nDataset: {len(df)} rows, {df['label'].mean():.1%} positive")
    print(f"Columns: {list(df.columns)[:10]}...")

    results = []

    # Model 1: Chore embedding only (baseline)
    print("\n" + "-" * 70)
    print("TESTING FEATURE COMBINATIONS")
    print("-" * 70)

    configs = [
        ("Chore Embedding Only", CHORE_EMB),
        ("+ Temporal", CHORE_EMB + TEMPORAL),
        ("+ Days Since Last", CHORE_EMB + TEMPORAL + ['days_since_last_done']),
        ("+ All History Simple", CHORE_EMB + TEMPORAL + HISTORY_SIMPLE),
        ("+ Similarity", CHORE_EMB + TEMPORAL + HISTORY_SIMPLE + SIMILARITY),
        ("+ Category Activity", CHORE_EMB + TEMPORAL + HISTORY_SIMPLE + SIMILARITY + CATEGORY_ACTIVITY),
        ("+ 7D History Emb", CHORE_EMB + TEMPORAL + HISTORY_SIMPLE + SIMILARITY + CATEGORY_ACTIVITY + HIST_7D_EMB),
        ("Full Model", CHORE_EMB + TEMPORAL + HISTORY_SIMPLE + SIMILARITY + CATEGORY_ACTIVITY + HIST_7D_EMB + HIST_1D_EMB),
    ]

    for name, features in configs:
        result = train_and_evaluate(df, features, name)
        if result:
            results.append(result)
            print(f"{name:25s} | {result['features']:3d} feats | AUC {result['auc']:.3f} | Hit@1 {result['hit@1']:.1%} | Hit@3 {result['hit@3']:.1%}")

    # Summary
    print("\n" + "=" * 70)
    print("ABLATION SUMMARY")
    print("=" * 70)

    baseline = results[0]['hit@1']
    for r in results:
        delta = r['hit@1'] - baseline
        delta_str = f"+{delta:.1%}" if delta >= 0 else f"{delta:.1%}"
        print(f"{r['name']:25s} | Hit@1: {r['hit@1']:.1%} ({delta_str})")

    # Feature importance for best model
    best = max(results, key=lambda x: x['hit@1'])
    print(f"\nBest model: {best['name']}")
    print("\nTop 15 Feature Importance:")

    importance = best['model'].feature_importances_
    imp_df = pd.DataFrame({
        'feature': best['feature_names'],
        'importance': importance
    }).sort_values('importance', ascending=False)

    for _, row in imp_df.head(15).iterrows():
        print(f"  {row['importance']:.4f}  {row['feature']}")


if __name__ == "__main__":
    main()
