"""
Sequence model for next-chore prediction.

Approaches:
1. Markov chain (baseline) - P(next | last)
2. LSTM with pretrained embeddings
3. Simple attention over session history
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
import json

# Config
LEAF_SESSIONS_PATH = Path(__file__).parent / "leaf_sessions.csv"
EMBEDDINGS_PATH = Path(__file__).parent / "cadence_embeddings.json"


def load_sessions(path: Path) -> list[list[str]]:
    """Load sessions as lists of exercise names."""
    df = pd.read_csv(path)
    sessions = []
    for session_id, group in df.groupby('session_id'):
        group = group.sort_values('logged_at')
        exercises = group['chore_name'].tolist()
        sessions.append(exercises)
    return sessions


def load_embeddings(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


# =============================================================================
# Markov Chain Model
# =============================================================================

class MarkovChainPredictor:
    """Simple first-order Markov chain: P(next | current)"""

    def __init__(self, smoothing: float = 1.0):
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

        # Also track session starts
        for session in sessions:
            if session:
                self.transition_counts['__START__'][session[0]] += 1
                self.total_counts['__START__'] += 1

    def predict_next(self, current: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Predict most likely next items given current."""
        if current not in self.transition_counts:
            # Fall back to most common overall
            all_counts = defaultdict(float)
            for next_items in self.transition_counts.values():
                for item, count in next_items.items():
                    all_counts[item] += count
            sorted_items = sorted(all_counts.items(), key=lambda x: -x[1])
            total = sum(all_counts.values())
            return [(item, count/total) for item, count in sorted_items[:top_k]]

        counts = self.transition_counts[current]
        total = self.total_counts[current] + self.smoothing * len(self.vocab)

        probs = []
        for item in self.vocab:
            count = counts.get(item, 0) + self.smoothing
            probs.append((item, count / total))

        probs.sort(key=lambda x: -x[1])
        return probs[:top_k]

    def score(self, current: str, next_item: str) -> float:
        """Get probability of next_item given current."""
        counts = self.transition_counts[current]
        total = self.total_counts[current] + self.smoothing * len(self.vocab)
        count = counts.get(next_item, 0) + self.smoothing
        return count / total


class HigherOrderMarkov:
    """N-gram model: P(next | last N items)"""

    def __init__(self, order: int = 2, smoothing: float = 1.0):
        self.order = order
        self.smoothing = smoothing
        self.transition_counts = defaultdict(lambda: defaultdict(float))
        self.total_counts = defaultdict(float)
        self.vocab = set()
        # Fallback to lower order models
        self.fallback = MarkovChainPredictor(smoothing) if order > 1 else None

    def fit(self, sessions: list[list[str]]):
        if self.fallback:
            self.fallback.fit(sessions)

        for session in sessions:
            for item in session:
                self.vocab.add(item)

            # Pad with start tokens
            padded = ['__START__'] * self.order + session

            for i in range(len(session)):
                context = tuple(padded[i:i + self.order])
                next_item = padded[i + self.order]
                self.transition_counts[context][next_item] += 1
                self.total_counts[context] += 1

    def predict_next(self, history: list[str], top_k: int = 10) -> list[tuple[str, float]]:
        """Predict next given history."""
        # Pad history if needed
        if len(history) < self.order:
            history = ['__START__'] * (self.order - len(history)) + history

        context = tuple(history[-self.order:])

        if context not in self.transition_counts:
            # Fall back to lower order
            if self.fallback and history:
                return self.fallback.predict_next(history[-1], top_k)
            else:
                # Uniform
                return [(item, 1/len(self.vocab)) for item in list(self.vocab)[:top_k]]

        counts = self.transition_counts[context]
        total = self.total_counts[context] + self.smoothing * len(self.vocab)

        probs = []
        for item in self.vocab:
            count = counts.get(item, 0) + self.smoothing
            probs.append((item, count / total))

        probs.sort(key=lambda x: -x[1])
        return probs[:top_k]


# =============================================================================
# Evaluation
# =============================================================================

def evaluate_model(model, test_sessions: list[list[str]], name: str):
    """Evaluate a sequence model on test sessions."""
    hit_at_k = {1: 0, 3: 0, 5: 0, 10: 0}
    total = 0
    mrr_sum = 0  # Mean Reciprocal Rank

    for session in test_sessions:
        for i in range(len(session) - 1):
            history = session[:i + 1]
            actual_next = session[i + 1]
            total += 1

            # Get predictions
            if hasattr(model, 'predict_next'):
                if isinstance(model, MarkovChainPredictor):
                    predictions = model.predict_next(history[-1], top_k=50)
                else:
                    predictions = model.predict_next(history, top_k=50)
            else:
                continue

            pred_items = [p[0] for p in predictions]

            # Hit@K
            for k in hit_at_k.keys():
                if actual_next in pred_items[:k]:
                    hit_at_k[k] += 1

            # MRR
            if actual_next in pred_items:
                rank = pred_items.index(actual_next) + 1
                mrr_sum += 1 / rank

    print(f"\n{name}:")
    print(f"  Total predictions: {total}")
    for k, hits in hit_at_k.items():
        print(f"  Hit@{k}: {hits/total:.2%}")
    print(f"  MRR: {mrr_sum/total:.4f}")

    return hit_at_k, mrr_sum / total


def main():
    print("Loading data...")
    sessions = load_sessions(LEAF_SESSIONS_PATH)
    print(f"  Total sessions: {len(sessions)}")
    print(f"  Total items: {sum(len(s) for s in sessions)}")

    # Filter to sessions with 2+ items (need transitions)
    sessions = [s for s in sessions if len(s) >= 2]
    print(f"  Sessions with 2+ items: {len(sessions)}")

    # Time-based split
    split_idx = int(len(sessions) * 0.8)
    train_sessions = sessions[:split_idx]
    test_sessions = sessions[split_idx:]

    print(f"\nTrain sessions: {len(train_sessions)}")
    print(f"Test sessions: {len(test_sessions)}")

    # Train models
    print("\n" + "="*60)
    print("TRAINING MODELS")
    print("="*60)

    # 1. First-order Markov
    print("\nTraining Markov (order=1)...")
    markov1 = MarkovChainPredictor(smoothing=0.1)
    markov1.fit(train_sessions)
    print(f"  Vocabulary: {len(markov1.vocab)} items")

    # 2. Second-order Markov (bigram context)
    print("\nTraining Markov (order=2)...")
    markov2 = HigherOrderMarkov(order=2, smoothing=0.1)
    markov2.fit(train_sessions)

    # 3. Third-order Markov
    print("\nTraining Markov (order=3)...")
    markov3 = HigherOrderMarkov(order=3, smoothing=0.1)
    markov3.fit(train_sessions)

    # Evaluate
    print("\n" + "="*60)
    print("EVALUATION ON TEST SET")
    print("="*60)

    evaluate_model(markov1, test_sessions, "Markov (order=1)")
    evaluate_model(markov2, test_sessions, "Markov (order=2)")
    evaluate_model(markov3, test_sessions, "Markov (order=3)")

    # Show some example predictions
    print("\n" + "="*60)
    print("EXAMPLE PREDICTIONS")
    print("="*60)

    example_contexts = [
        ["V-Max (Bouldering)"],
        ["Daily Meds + Supplements", "Skin Care"],
        ["Wash Laundry"],
        ["Tension Board", "Max Ladder"],
    ]

    for history in example_contexts:
        print(f"\nAfter: {' → '.join(history)}")
        print("  Markov-1 predicts:")
        for item, prob in markov1.predict_next(history[-1], top_k=5):
            print(f"    {prob:.3f}  {item}")

        if len(history) >= 2:
            print("  Markov-2 predicts:")
            for item, prob in markov2.predict_next(history, top_k=5):
                print(f"    {prob:.3f}  {item}")

    print("\nDone!")


if __name__ == "__main__":
    main()
