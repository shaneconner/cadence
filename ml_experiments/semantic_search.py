"""
Semantic search for chore names using sentence-transformers.

Uses a small, fast model (all-MiniLM-L6-v2, ~22MB) to find semantically similar chores.
Embeddings are cached to disk for fast startup.
"""

import json
import sqlite3
import numpy as np
from pathlib import Path
from typing import List, Tuple

# Paths
MODEL_DIR = Path(__file__).parent
CACHE_PATH = MODEL_DIR / "name_embeddings.npz"
NAMES_PATH = MODEL_DIR / "cadence_names_cache.json"
DB_PATH = MODEL_DIR.parent / "data" / "chore_data.db"

# Model name - small and fast
MODEL_NAME = "all-MiniLM-L6-v2"

# Lazy-loaded globals
_model = None
_embeddings = None
_chore_names = None


def _load_model():
    """Load the sentence transformer model (lazy)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_active_chores(db_path: str = None) -> List[Tuple[str, str]]:
    """Get all active chore names and descriptions from database."""
    db = db_path or str(DB_PATH)
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT name, description FROM chores WHERE active = 1")
    results = [(row[0], row[1] or "") for row in cursor.fetchall()]
    conn.close()
    return results


def build_cache(db_path: str = None):
    """Build and cache embeddings for all active chores."""
    print("Building semantic search cache...")

    model = _load_model()
    chores = _get_active_chores(db_path)

    # Create search text: name + description for richer matching
    names = [c[0] for c in chores]
    search_texts = [f"{c[0]} {c[1]}" for c in chores]

    print(f"Embedding {len(search_texts)} chores...")
    embeddings = model.encode(search_texts, show_progress_bar=True, convert_to_numpy=True)

    # Save to disk
    np.savez_compressed(CACHE_PATH, embeddings=embeddings)
    with open(NAMES_PATH, 'w') as f:
        json.dump(names, f)

    print(f"Cache saved to {CACHE_PATH}")
    return names, embeddings


def _load_cache():
    """Load cached embeddings, rebuilding if necessary."""
    global _embeddings, _chore_names

    if _embeddings is not None and _chore_names is not None:
        return _chore_names, _embeddings

    # Check if cache exists
    if CACHE_PATH.exists() and NAMES_PATH.exists():
        try:
            data = np.load(CACHE_PATH)
            _embeddings = data['embeddings']
            with open(NAMES_PATH) as f:
                _chore_names = json.load(f)
            return _chore_names, _embeddings
        except Exception as e:
            print(f"Cache load failed: {e}, rebuilding...")

    # Build cache if not exists
    _chore_names, _embeddings = build_cache()
    return _chore_names, _embeddings


def find_similar_semantic(query: str, limit: int = 5, db_path: str = None) -> List[Tuple[str, float]]:
    """
    Find chores semantically similar to the query.

    Args:
        query: The search query (e.g., "Take Medications")
        limit: Max number of results to return
        db_path: Optional database path

    Returns:
        List of (chore_name, similarity_score) tuples, sorted by similarity descending.
    """
    model = _load_model()
    chore_names, embeddings = _load_cache()

    # Embed the query
    query_embedding = model.encode([query], convert_to_numpy=True)[0]

    # Compute cosine similarities
    # Normalize embeddings for cosine similarity
    query_norm = query_embedding / np.linalg.norm(query_embedding)
    emb_norms = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    similarities = np.dot(emb_norms, query_norm)

    # Get top matches
    top_indices = np.argsort(similarities)[::-1][:limit]

    results = []
    for idx in top_indices:
        results.append((chore_names[idx], float(similarities[idx])))

    return results


def refresh_cache(db_path: str = None):
    """Force rebuild of the embedding cache."""
    global _embeddings, _chore_names
    _embeddings = None
    _chore_names = None
    return build_cache(db_path)


# Quick test
if __name__ == "__main__":
    print("Testing semantic search...")

    # Test queries
    test_queries = [
        "Take Medications",
        "wash clothes",
        "arm workout",
        "stretch legs",
        "clean house"
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        results = find_similar_semantic(query, limit=5)
        for name, score in results:
            print(f"  {score:.3f}: {name}")
