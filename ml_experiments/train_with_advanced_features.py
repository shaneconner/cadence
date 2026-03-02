"""
Train model with advanced features and compare to baseline.

Evaluates whether the new sequence features (7d/1d history embeddings,
category activity, days since last done) improve Hit@1 over baseline.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score
import joblib

# Paths
SCRIPT_DIR = Path(__file__).parent
BASELINE_DATA = SCRIPT_DIR / "training_data.parquet"
ADVANCED_DATA = SCRIPT_DIR / "training_data_advanced.parquet"


# Feature sets
BASELINE_FEATURES = [
    'hour', 'day_of_week', 'is_new_session', 'session_position',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(32)] + [f'prev_emb_{i}' for i in range(32)]

ADVANCED_FEATURES = (
    [f'hist_7d_emb_{i}' for i in range(32)] +
    [f'hist_1d_emb_{i}' for i in range(32)] +
    ['cat_activity_exercise', 'cat_activity_household', 'cat_activity_plant',
     'cat_activity_dog', 'cat_activity_personal care'] +
    ['unique_chores_7d', 'unique_chores_1d', 'days_since_last_done',
     'similarity_to_7d_history', 'similarity_to_1d_history']
)


def compute_hit_at_k(y_true, y_pred, groups, k_values=[1, 3, 5, 10]):
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

    return results


def train_and_evaluate(X_train, y_train, X_test, y_test, groups_test, name="Model"):
    """Train model and return metrics."""
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=8,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
        verbose=0
    )
    model.fit(X_train, y_train)
    y_pred = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_pred)
    hit_results = compute_hit_at_k(y_test, y_pred, groups_test)

    return model, auc, hit_results


def main():
    print("=" * 70)
    print("ADVANCED FEATURES EXPERIMENT")
    print("=" * 70)

    # Load baseline data
    print("\nLoading baseline training data...")
    baseline_df = pd.read_parquet(BASELINE_DATA)
    print(f"  Baseline: {len(baseline_df)} rows")

    # Load advanced data
    print("Loading advanced training data...")
    advanced_df = pd.read_parquet(ADVANCED_DATA)
    print(f"  Advanced: {len(advanced_df)} rows")

    # Merge to get combined features (need to match on chore_name + logged_at)
    print("\nMerging datasets...")

    # Baseline has one row per (timestamp, candidate) - it's expanded with negatives
    # Advanced has one row per actual log - need to merge carefully

    # First, let's check if we can simply use advanced features to augment training
    # The advanced features are per-row in leaf_sessions, which maps to the positive examples
    # We need to add them to the full training data

    # Actually, let's rebuild training data with advanced features
    # For now, let's train on the same split as baseline but add history features

    # Simple approach: train on advanced features only (smaller dataset, no negatives yet)
    # Then compare to baseline

    # Check what columns we have
    print(f"\nAdvanced columns: {list(advanced_df.columns)[:15]}...")

    # We need to rebuild the full training pipeline with negatives
    # But for quick comparison, let's see if just the positive samples work better

    # Actually, let's load and merge properly
    # The baseline training_data.parquet has both positives and negatives
    # We can join advanced features on (chore_name, logged_at) where label=1

    # For label=1 rows, merge in the advanced history features
    print("Joining advanced features to training data...")

    # Keep original baseline columns
    merged = baseline_df.copy()

    # Merge advanced features for positive examples
    advanced_cols = ['chore_name', 'logged_at'] + ADVANCED_FEATURES
    existing_adv_cols = [c for c in advanced_cols if c in advanced_df.columns]

    if len(existing_adv_cols) > 2:  # More than just keys
        # Left join - will get NaN for negatives and positives not in advanced
        merged = merged.merge(
            advanced_df[existing_adv_cols],
            on=['chore_name', 'logged_at'],
            how='left'
        )

        # Fill NaN with 0 for advanced features (negatives don't have history context)
        for col in existing_adv_cols[2:]:  # Skip keys
            if col in merged.columns:
                merged[col] = merged[col].fillna(0)

    print(f"  Merged dataset: {len(merged)} rows")

    # Determine which advanced features actually exist
    available_advanced = [f for f in ADVANCED_FEATURES if f in merged.columns]
    print(f"  Available advanced features: {len(available_advanced)}")

    # Time-based split
    unique_times = merged['logged_at'].unique()
    unique_times = np.sort(unique_times)
    split_idx = int(len(unique_times) * 0.8)
    train_times = set(unique_times[:split_idx])
    test_times = set(unique_times[split_idx:])

    train_mask = merged['logged_at'].isin(train_times)
    test_mask = merged['logged_at'].isin(test_times)

    y = merged['label'].values
    groups = merged['logged_at'].values

    # ========== Model 1: Baseline features only ==========
    print("\n" + "-" * 70)
    print("MODEL 1: Baseline Features Only")
    print("-" * 70)

    avail_baseline = [f for f in BASELINE_FEATURES if f in merged.columns]
    X_baseline = merged[avail_baseline].values
    print(f"Features: {len(avail_baseline)}")

    X_train_b = X_baseline[train_mask]
    y_train_b = y[train_mask]
    X_test_b = X_baseline[test_mask]
    y_test_b = y[test_mask]
    groups_test = groups[test_mask]

    print(f"Train: {len(X_train_b)} | Test: {len(X_test_b)}")

    model_b, auc_b, hit_b = train_and_evaluate(
        X_train_b, y_train_b, X_test_b, y_test_b, groups_test, "Baseline"
    )
    print(f"AUC: {auc_b:.4f}")
    for k, v in hit_b.items():
        print(f"  {k}: {v:.2%}")

    # ========== Model 2: Baseline + Advanced features ==========
    print("\n" + "-" * 70)
    print("MODEL 2: Baseline + Advanced Features")
    print("-" * 70)

    combined_features = avail_baseline + available_advanced
    X_combined = merged[combined_features].values
    print(f"Features: {len(combined_features)} ({len(avail_baseline)} base + {len(available_advanced)} advanced)")

    X_train_c = X_combined[train_mask]
    X_test_c = X_combined[test_mask]

    model_c, auc_c, hit_c = train_and_evaluate(
        X_train_c, y_train_b, X_test_c, y_test_b, groups_test, "Combined"
    )
    print(f"AUC: {auc_c:.4f}")
    for k, v in hit_c.items():
        print(f"  {k}: {v:.2%}")

    # ========== Model 3: Advanced features only (ablation) ==========
    print("\n" + "-" * 70)
    print("MODEL 3: Advanced Features Only (ablation)")
    print("-" * 70)

    # Include basic temporal + advanced history
    minimal_features = ['hour', 'day_of_week', 'is_new_session', 'session_position']
    minimal_avail = [f for f in minimal_features if f in merged.columns]
    ablation_features = minimal_avail + available_advanced
    X_ablation = merged[ablation_features].values
    print(f"Features: {len(ablation_features)}")

    X_train_a = X_ablation[train_mask]
    X_test_a = X_ablation[test_mask]

    model_a, auc_a, hit_a = train_and_evaluate(
        X_train_a, y_train_b, X_test_a, y_test_b, groups_test, "Advanced-only"
    )
    print(f"AUC: {auc_a:.4f}")
    for k, v in hit_a.items():
        print(f"  {k}: {v:.2%}")

    # ========== Summary ==========
    print("\n" + "=" * 70)
    print("SUMMARY: Hit@1 Comparison")
    print("=" * 70)
    print(f"Baseline:             {hit_b['hit@1']:.2%}")
    print(f"Baseline + Advanced:  {hit_c['hit@1']:.2%}  ({hit_c['hit@1'] - hit_b['hit@1']:+.2%})")
    print(f"Advanced only:        {hit_a['hit@1']:.2%}  ({hit_a['hit@1'] - hit_b['hit@1']:+.2%})")

    # Feature importance for best model
    print("\n" + "=" * 70)
    print("TOP 20 FEATURE IMPORTANCE (Combined Model)")
    print("=" * 70)
    importance = model_c.feature_importances_
    imp_df = pd.DataFrame({
        'feature': combined_features,
        'importance': importance
    }).sort_values('importance', ascending=False)

    for _, row in imp_df.head(20).iterrows():
        print(f"  {row['importance']:.4f}  {row['feature']}")

    # Save best model if it improved
    if hit_c['hit@1'] > hit_b['hit@1']:
        print(f"\nAdvanced features improved Hit@1 by {hit_c['hit@1'] - hit_b['hit@1']:+.2%}!")
        print("Consider integrating into production model.")
    else:
        print(f"\nAdvanced features did not improve Hit@1. Baseline remains best.")


if __name__ == "__main__":
    main()
