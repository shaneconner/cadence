"""
Fair comparison between sequence model and pointwise model.

Both models rank the SAME candidate set for each prediction.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
import json
import joblib

# Config
LEAF_SESSIONS_PATH = Path(__file__).parent / "leaf_sessions.csv"
EMBEDDINGS_PATH = Path(__file__).parent / "cadence_embeddings.json"
TRAINING_DATA_PATH = Path(__file__).parent / "training_data.parquet"
GB_MODEL_PATH = Path(__file__).parent / "cadence_predictor.joblib"

FEATURE_COLS = [
    'hour', 'day_of_week', 'is_new_session', 'session_position',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(32)] + [f'prev_emb_{i}' for i in range(32)]


class MarkovChainPredictor:
    def __init__(self, smoothing: float = 0.1):
        self.transition_counts = defaultdict(lambda: defaultdict(float))
        self.total_counts = defaultdict(float)
        self.smoothing = smoothing
        self.vocab = set()

    def fit(self, sessions: list[list[str]]):
        for session in sessions:
            for i in range(len(session) - 1):
                current = session[i]
                next_item = session[i + 1]
                self.transition_counts[current][next_item] += 1
                self.total_counts[current] += 1
                self.vocab.add(current)
                self.vocab.add(next_item)

    def score(self, current: str, next_item: str) -> float:
        if current not in self.transition_counts:
            return self.smoothing / (self.smoothing * len(self.vocab) + 1)
        counts = self.transition_counts[current]
        total = self.total_counts[current] + self.smoothing * len(self.vocab)
        count = counts.get(next_item, 0) + self.smoothing
        return count / total


def load_sessions(path: Path) -> list[list[str]]:
    df = pd.read_csv(path)
    sessions = []
    for session_id, group in df.groupby('session_id'):
        group = group.sort_values('logged_at')
        exercises = group['chore_name'].tolist()
        sessions.append(exercises)
    return sessions


def main():
    print("Loading data...")

    # Load sessions for Markov training
    sessions = load_sessions(LEAF_SESSIONS_PATH)
    sessions = [s for s in sessions if len(s) >= 2]

    # Split same as training data (80/20 time-based)
    split_idx = int(len(sessions) * 0.8)
    train_sessions = sessions[:split_idx]

    # Load training data (has the exact candidate sets)
    train_df = pd.read_parquet(TRAINING_DATA_PATH)

    # Get test timestamps
    unique_times = train_df['logged_at'].unique()
    unique_times = np.sort(unique_times)
    split_time_idx = int(len(unique_times) * 0.8)
    test_times = set(unique_times[split_time_idx:])

    test_df = train_df[train_df['logged_at'].isin(test_times)].copy()

    print(f"  Train sessions (for Markov): {len(train_sessions)}")
    print(f"  Test examples: {len(test_df)}")
    print(f"  Test groups (decisions): {test_df['logged_at'].nunique()}")

    # Train Markov
    print("\nTraining Markov model...")
    markov = MarkovChainPredictor(smoothing=0.1)
    markov.fit(train_sessions)
    print(f"  Vocabulary: {len(markov.vocab)}")

    # Load GB model
    print("\nLoading GradientBoosting model...")
    gb_model = joblib.load(GB_MODEL_PATH)

    # For each test group, rank candidates with both models
    print("\nEvaluating both models on same candidate sets...")

    # Get previous exercise for each test timestamp
    leaf_df = pd.read_csv(LEAF_SESSIONS_PATH)

    results = {'markov': {'hit@1': 0, 'hit@3': 0}, 'gb': {'hit@1': 0, 'hit@3': 0}}
    n_groups = 0

    for logged_at, group in test_df.groupby('logged_at'):
        candidates = group['chore_name'].tolist()
        labels = group['label'].tolist()

        if sum(labels) == 0:
            continue

        n_groups += 1
        positive_idx = labels.index(1)
        positive_chore = candidates[positive_idx]

        # Get previous chore for Markov context
        prev_chores = []
        for col in ['prev_leaf_1', 'prev_leaf_2', 'prev_leaf_3']:
            if col in leaf_df.columns:
                row = leaf_df[leaf_df['logged_at'] == logged_at]
                if not row.empty and pd.notna(row[col].values[0]):
                    prev_chores.append(row[col].values[0])

        prev_chore = prev_chores[0] if prev_chores else None

        # Markov scores
        if prev_chore:
            markov_scores = [markov.score(prev_chore, c) for c in candidates]
        else:
            markov_scores = [1.0 / len(candidates)] * len(candidates)

        markov_ranked = np.argsort(markov_scores)[::-1]
        markov_positive_rank = list(markov_ranked).index(positive_idx) + 1

        if markov_positive_rank <= 1:
            results['markov']['hit@1'] += 1
        if markov_positive_rank <= 3:
            results['markov']['hit@3'] += 1

        # GB scores
        X = group[FEATURE_COLS].values
        gb_scores = gb_model.predict_proba(X)[:, 1]
        gb_ranked = np.argsort(gb_scores)[::-1]
        gb_positive_rank = list(gb_ranked).index(positive_idx) + 1

        if gb_positive_rank <= 1:
            results['gb']['hit@1'] += 1
        if gb_positive_rank <= 3:
            results['gb']['hit@3'] += 1

    print(f"\nTotal test decisions: {n_groups}")
    print("\n" + "="*60)
    print("FAIR COMPARISON (same candidates)")
    print("="*60)

    print(f"\nMarkov (sequence only):")
    print(f"  Hit@1: {results['markov']['hit@1']/n_groups:.2%}")
    print(f"  Hit@3: {results['markov']['hit@3']/n_groups:.2%}")

    print(f"\nGradientBoosting (context + embeddings):")
    print(f"  Hit@1: {results['gb']['hit@1']/n_groups:.2%}")
    print(f"  Hit@3: {results['gb']['hit@3']/n_groups:.2%}")

    # Analyze where each model wins
    print("\n" + "="*60)
    print("ANALYSIS: What does each model capture?")
    print("="*60)

    print("""
    Markov: P(next | previous exercise)
    - Pure sequence patterns
    - No context (time, due dates)

    GB: Score(candidate | features)
    - Embeddings (smoothed sequence patterns)
    - Due dates
    - Time of day
    - Session context
    - Historical frequency

    The gap shows how much signal is in context vs raw sequences.
    """)

    print("Done!")


if __name__ == "__main__":
    main()
