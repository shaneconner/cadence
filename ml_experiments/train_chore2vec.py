"""
Train chore embeddings using co-occurrence matrix + SVD.

Exercises that tend to be done together in sessions will have similar embeddings.
Uses sklearn (more compatible) instead of gensim.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from collections import Counter
import json

# Config
INPUT_PATH = Path(__file__).parent / "leaf_sessions.csv"
EMBEDDINGS_PATH = Path(__file__).parent / "cadence_embeddings.json"

EMBEDDING_DIM = 32
WINDOW_SIZE = 5   # How many exercises before/after to consider
MIN_COUNT = 2     # Minimum times an exercise must appear


def load_sessions(path: Path) -> list[list[str]]:
    """Load sessions as lists of exercise names."""
    df = pd.read_csv(path)

    sessions = []
    for session_id, group in df.groupby('session_id'):
        group = group.sort_values('logged_at')
        exercises = group['chore_name'].tolist()
        if len(exercises) >= 2:
            sessions.append(exercises)

    return sessions


def build_cooccurrence_matrix(sessions: list[list[str]], window: int, min_count: int):
    """
    Build co-occurrence matrix from sessions.
    Returns: (matrix, word_to_idx, idx_to_word)
    """
    # Count word frequencies
    word_counts = Counter()
    for session in sessions:
        word_counts.update(session)

    # Filter by min_count
    vocab = [w for w, c in word_counts.items() if c >= min_count]
    vocab = sorted(vocab)
    word_to_idx = {w: i for i, w in enumerate(vocab)}
    idx_to_word = {i: w for w, i in word_to_idx.items()}

    print(f"  Vocabulary size: {len(vocab)} (after min_count={min_count})")

    # Build co-occurrence matrix
    n = len(vocab)
    cooc = np.zeros((n, n), dtype=np.float32)

    for session in sessions:
        # Filter to vocab words
        session_filtered = [w for w in session if w in word_to_idx]

        for i, word in enumerate(session_filtered):
            word_idx = word_to_idx[word]

            # Look at context window
            start = max(0, i - window)
            end = min(len(session_filtered), i + window + 1)

            for j in range(start, end):
                if i != j:
                    context_word = session_filtered[j]
                    context_idx = word_to_idx[context_word]
                    # Weight by distance
                    distance = abs(i - j)
                    cooc[word_idx, context_idx] += 1.0 / distance

    return cooc, word_to_idx, idx_to_word


def train_embeddings(cooc: np.ndarray, dim: int) -> np.ndarray:
    """
    Train embeddings using SVD on PPMI-weighted co-occurrence matrix.
    """
    # Apply PPMI (Positive Pointwise Mutual Information)
    row_sums = cooc.sum(axis=1, keepdims=True)
    col_sums = cooc.sum(axis=0, keepdims=True)
    total = cooc.sum()

    # Avoid division by zero
    row_sums = np.maximum(row_sums, 1e-10)
    col_sums = np.maximum(col_sums, 1e-10)

    # PMI = log(P(w,c) / (P(w) * P(c)))
    expected = (row_sums * col_sums) / total
    pmi = np.log(np.maximum(cooc / expected, 1e-10))

    # PPMI: set negative values to 0
    ppmi = np.maximum(pmi, 0)

    # SVD to get embeddings
    actual_dim = min(dim, ppmi.shape[0] - 1)
    svd = TruncatedSVD(n_components=actual_dim, random_state=42)
    embeddings = svd.fit_transform(ppmi)

    # Normalize embeddings
    embeddings = normalize(embeddings)

    print(f"  Explained variance ratio: {svd.explained_variance_ratio_.sum():.3f}")

    return embeddings


def find_similar(embeddings: np.ndarray, idx_to_word: dict, word_to_idx: dict,
                 word: str, topn: int = 10):
    """Find most similar words using cosine similarity."""
    if word not in word_to_idx:
        return []

    word_idx = word_to_idx[word]
    word_vec = embeddings[word_idx]

    # Cosine similarity (embeddings are normalized)
    similarities = embeddings @ word_vec

    # Get top indices (excluding self)
    top_indices = np.argsort(similarities)[::-1][1:topn+1]

    return [(idx_to_word[i], similarities[i]) for i in top_indices]


def export_embeddings(embeddings: np.ndarray, idx_to_word: dict, path: Path):
    """Export embeddings to JSON."""
    emb_dict = {}
    for idx, word in idx_to_word.items():
        emb_dict[word] = embeddings[idx].tolist()

    with open(path, 'w') as f:
        json.dump(emb_dict, f, indent=2)


def main():
    print(f"Loading sessions from {INPUT_PATH}")
    sessions = load_sessions(INPUT_PATH)
    print(f"  Loaded {len(sessions)} sessions with 2+ exercises")

    total_exercises = sum(len(s) for s in sessions)
    print(f"  Total exercise instances: {total_exercises}")

    print(f"\nBuilding co-occurrence matrix (window={WINDOW_SIZE})...")
    cooc, word_to_idx, idx_to_word = build_cooccurrence_matrix(
        sessions, WINDOW_SIZE, MIN_COUNT
    )

    print(f"\nTraining embeddings (dim={EMBEDDING_DIM})...")
    embeddings = train_embeddings(cooc, EMBEDDING_DIM)
    print(f"  Final embedding shape: {embeddings.shape}")

    print(f"\nExporting embeddings to {EMBEDDINGS_PATH}")
    export_embeddings(embeddings, idx_to_word, EMBEDDINGS_PATH)

    # Demo: find similar exercises
    print("\n" + "="*60)
    print("SIMILARITY EXAMPLES")
    print("="*60)

    test_exercises = [
        "V-Max (Bouldering)",
        "Tension Board",
        "Daily Meds + Supplements",
        "Wash Laundry",
        "Dental Hygiene",
        "Skin Care",
        "Dragon Flags",
    ]

    for exercise in test_exercises:
        if exercise in word_to_idx:
            print(f"\nMost similar to '{exercise}':")
            similar = find_similar(embeddings, idx_to_word, word_to_idx, exercise, topn=5)
            for name, score in similar:
                print(f"  {score:.3f}  {name}")
        else:
            print(f"\n'{exercise}' not in vocabulary (< {MIN_COUNT} occurrences)")

    # Analyze clustering
    print("\n" + "="*60)
    print("EMBEDDING STATS")
    print("="*60)
    print(f"Vocabulary size: {len(word_to_idx)}")
    print(f"Embedding dimension: {embeddings.shape[1]}")

    print(f"\nDone!")


if __name__ == "__main__":
    main()
