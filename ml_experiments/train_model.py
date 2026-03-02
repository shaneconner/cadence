"""
Train XGBoost model to predict which chore will be selected.

Evaluates using:
- AUC-ROC: How well can we distinguish selected from non-selected?
- Hit@K: Was the actual selection in the top K predictions?
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.model_selection import train_test_split, GroupKFold
from sklearn.metrics import roc_auc_score, precision_recall_curve, average_precision_score
from sklearn.ensemble import GradientBoostingClassifier
import joblib
import json

# Config
DATA_PATH = Path(__file__).parent / "training_data.parquet"
MODEL_PATH = Path(__file__).parent / "cadence_predictor.joblib"

FEATURE_COLS = [
    'hour', 'day_of_week', 'is_weekend', 'time_bucket', 'hours_since_last_log',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(32)] + [f'prev_emb_{i}' for i in range(32)]


def compute_hit_at_k(y_true: np.ndarray, y_pred: np.ndarray,
                     groups: np.ndarray, k_values: list = [1, 3, 5, 10]) -> dict:
    """
    Compute Hit@K: For each group (timestamp), was the positive in top K predictions?
    """
    results = {f'hit@{k}': 0 for k in k_values}
    n_groups = 0

    unique_groups = np.unique(groups)
    for group in unique_groups:
        mask = groups == group
        group_true = y_true[mask]
        group_pred = y_pred[mask]

        if group_true.sum() == 0:  # No positive in this group
            continue

        n_groups += 1

        # Rank by prediction score (descending)
        ranked_indices = np.argsort(group_pred)[::-1]
        ranked_true = group_true[ranked_indices]

        # Check if positive is in top K
        for k in k_values:
            if ranked_true[:k].sum() > 0:
                results[f'hit@{k}'] += 1

    # Convert to percentages
    for k in k_values:
        results[f'hit@{k}'] = results[f'hit@{k}'] / n_groups if n_groups > 0 else 0

    return results


def analyze_feature_importance(model, feature_names: list) -> pd.DataFrame:
    """Get feature importance from the model."""
    importance = model.feature_importances_
    df = pd.DataFrame({
        'feature': feature_names,
        'importance': importance
    }).sort_values('importance', ascending=False)
    return df


def main():
    print("Loading training data...")
    df = pd.read_parquet(DATA_PATH)
    print(f"  Total examples: {len(df)}")
    print(f"  Positive rate: {df['label'].mean():.2%}")

    # Features and labels
    X = df[FEATURE_COLS].values
    y = df['label'].values
    groups = df['logged_at'].values  # Group by timestamp for Hit@K

    # Time-based split (use last 20% as test)
    unique_times = df['logged_at'].unique()
    unique_times = np.sort(unique_times)
    split_idx = int(len(unique_times) * 0.8)
    train_times = set(unique_times[:split_idx])
    test_times = set(unique_times[split_idx:])

    train_mask = df['logged_at'].isin(train_times)
    test_mask = df['logged_at'].isin(test_times)

    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    groups_test = groups[test_mask]

    print(f"\nTrain set: {len(X_train)} examples ({y_train.sum()} positive)")
    print(f"Test set: {len(X_test)} examples ({y_test.sum()} positive)")

    # Train Gradient Boosting (tuned hyperparams from optimization study 2025-12-31 v1.3)
    print("\nTraining GradientBoosting...")
    model = GradientBoostingClassifier(
        n_estimators=50,  # Fewer trees sufficient with deeper depth
        max_depth=10,  # Deeper trees optimal with new temporal features
        learning_rate=0.1,  # Lower LR works better with deeper trees
        subsample=0.8,
        random_state=42,
        verbose=1,
    )

    model.fit(X_train, y_train)

    # Predictions
    y_pred = model.predict_proba(X_test)[:, 1]

    # Metrics
    print("\n" + "="*60)
    print("MODEL PERFORMANCE")
    print("="*60)

    auc = roc_auc_score(y_test, y_pred)
    ap = average_precision_score(y_test, y_pred)
    print(f"AUC-ROC: {auc:.4f}")
    print(f"Average Precision: {ap:.4f}")

    # Hit@K
    hit_results = compute_hit_at_k(y_test, y_pred, groups_test)
    print("\nHit@K (was selected chore in top K predictions?):")
    for metric, value in hit_results.items():
        print(f"  {metric}: {value:.2%}")

    # Feature importance
    print("\n" + "="*60)
    print("TOP 20 FEATURE IMPORTANCE")
    print("="*60)
    importance_df = analyze_feature_importance(model, FEATURE_COLS)
    for _, row in importance_df.head(20).iterrows():
        print(f"  {row['importance']:.4f}  {row['feature']}")

    # Save model
    print(f"\nSaving model to {MODEL_PATH}...")
    joblib.dump(model, MODEL_PATH)

    # Baseline comparison: due date only
    print("\n" + "="*60)
    print("BASELINE COMPARISON")
    print("="*60)

    # Due date baseline: predict by days_until_due (lower = more likely)
    due_idx = FEATURE_COLS.index('days_until_due')
    due_baseline = -X_test[:, due_idx]  # Negate so lower due = higher score

    due_auc = roc_auc_score(y_test, due_baseline)
    due_hit = compute_hit_at_k(y_test, due_baseline, groups_test)
    print(f"Due-date-only baseline:")
    print(f"  AUC-ROC: {due_auc:.4f}")
    for metric, value in due_hit.items():
        print(f"  {metric}: {value:.2%}")

    # Random baseline
    print(f"\nRandom baseline:")
    print(f"  AUC-ROC: 0.5000")
    n_candidates = len(X_test) / y_test.sum()  # Avg candidates per selection
    for k in [1, 3, 5, 10]:
        random_hit = min(k / n_candidates, 1.0)
        print(f"  hit@{k}: {random_hit:.2%}")

    print("\nDone!")


if __name__ == "__main__":
    main()
