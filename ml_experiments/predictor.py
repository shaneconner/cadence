"""
Chore prediction module for MCP server integration.

Provides a simple interface to predict which chores the user is likely to select next.
"""

import json
import sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# Paths
MODEL_DIR = Path(__file__).parent
EMBEDDINGS_PATH = MODEL_DIR / "cadence_embeddings.json"
MODEL_PATH = MODEL_DIR / "cadence_predictor.joblib"
DB_PATH = MODEL_DIR.parent / "data" / "chore_data.db"

# Feature columns (must match training)
FEATURE_COLS = [
    'hour', 'day_of_week', 'is_weekend', 'time_bucket', 'hours_since_last_log',
    'days_until_due', 'times_logged', 'adjustment_rate', 'context_similarity'
] + [f'emb_{i}' for i in range(32)] + [f'prev_emb_{i}' for i in range(32)]

EMBEDDING_DIM = 32


class CadencePredictor:
    """Predicts which chores the user is likely to select next."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self.model = None
        self.embeddings = None
        self.chore_stats = None
        self._load_model()

    def _load_model(self):
        """Load the trained model and embeddings."""
        import joblib

        if MODEL_PATH.exists():
            self.model = joblib.load(MODEL_PATH)
        else:
            raise FileNotFoundError(f"Model not found at {MODEL_PATH}")

        if EMBEDDINGS_PATH.exists():
            with open(EMBEDDINGS_PATH) as f:
                self.embeddings = json.load(f)
        else:
            raise FileNotFoundError(f"Embeddings not found at {EMBEDDINGS_PATH}")

        # Load chore stats
        self._load_chore_stats()

    def _load_chore_stats(self):
        """Load historical stats for all chores."""
        conn = sqlite3.connect(self.db_path)

        # Times logged
        cursor = conn.execute("""
            SELECT chore_name, COUNT(*) as times_logged
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        """)
        times_logged = {row[0]: row[1] for row in cursor.fetchall()}

        # Adjustment counts
        cursor = conn.execute("""
            SELECT chore_name, COUNT(*) as adj_count
            FROM logs WHERE is_genuine = 0
            GROUP BY chore_name
        """)
        adj_counts = {row[0]: row[1] for row in cursor.fetchall()}

        self.chore_stats = {}
        for chore in set(times_logged.keys()) | set(adj_counts.keys()):
            tl = times_logged.get(chore, 0)
            ac = adj_counts.get(chore, 0)
            self.chore_stats[chore] = {
                'times_logged': tl,
                'adjustment_rate': ac / (tl + 1)
            }

        conn.close()

    def _get_embedding(self, chore: str) -> list:
        """Get embedding for a chore, or zeros if not found."""
        return self.embeddings.get(chore, [0.0] * EMBEDDING_DIM)

    def _compute_context_similarity(self, chore_emb: list, prev_embs: list) -> float:
        """Compute cosine similarity between chore and previous context."""
        if not prev_embs:
            return 0.0

        chore_vec = np.array(chore_emb)
        avg_prev = np.mean(prev_embs, axis=0)

        norm_chore = np.linalg.norm(chore_vec)
        norm_prev = np.linalg.norm(avg_prev)

        if norm_chore < 1e-10 or norm_prev < 1e-10:
            return 0.0

        return float(np.dot(chore_vec, avg_prev) / (norm_chore * norm_prev))

    def _build_features(
        self,
        chore: str,
        prev_chores: list,
        hour: int,
        day_of_week: int,
        days_until_due: float,
        hours_since_last_log: float
    ) -> np.ndarray:
        """Build feature vector for a single candidate chore."""
        features = {}

        # Temporal
        features['hour'] = hour
        features['day_of_week'] = day_of_week

        # Derived temporal features
        features['is_weekend'] = 1 if day_of_week >= 5 else 0
        if 5 <= hour < 12:
            features['time_bucket'] = 0  # morning
        elif 12 <= hour < 18:
            features['time_bucket'] = 1  # afternoon
        elif 18 <= hour < 24:
            features['time_bucket'] = 2  # evening
        else:
            features['time_bucket'] = 3  # night

        features['hours_since_last_log'] = hours_since_last_log

        # Due date
        features['days_until_due'] = days_until_due

        # Historical
        stats = self.chore_stats.get(chore, {'times_logged': 0, 'adjustment_rate': 0})
        features['times_logged'] = stats['times_logged']
        features['adjustment_rate'] = stats['adjustment_rate']

        # Embeddings
        chore_emb = self._get_embedding(chore)
        for i, v in enumerate(chore_emb):
            features[f'emb_{i}'] = v

        # Previous chore embeddings
        prev_embs = [self._get_embedding(pc) for pc in prev_chores[:3] if pc]
        if prev_embs:
            avg_prev = np.mean(prev_embs, axis=0)
        else:
            avg_prev = [0.0] * EMBEDDING_DIM
        for i, v in enumerate(avg_prev):
            features[f'prev_emb_{i}'] = v

        # Context similarity
        features['context_similarity'] = self._compute_context_similarity(chore_emb, prev_embs)

        # Build array in correct order
        return np.array([features[col] for col in FEATURE_COLS])

    def predict(
        self,
        candidate_chores: list[dict],
        prev_chores: list[str] = None,
        last_log_time: datetime = None,
        top_k: int = 10,
        now: datetime = None
    ) -> list[dict]:
        """
        Predict scores for candidate chores.

        Args:
            candidate_chores: List of dicts with 'name' and 'days_until_due'
            prev_chores: List of recently logged chore names (most recent first)
            last_log_time: When the last chore was logged (for hours_since_last_log)
            top_k: Number of top predictions to return
            now: Current datetime (defaults to now in Central Time)

        Returns:
            List of dicts with 'name', 'score', 'days_until_due', ranked by score
        """
        if now is None:
            ct = ZoneInfo("America/Chicago")
            now = datetime.now(ct)

        hour = now.hour
        day_of_week = now.weekday()
        prev_chores = prev_chores or []

        # Compute hours since last log
        if last_log_time:
            hours_since_last_log = (now - last_log_time).total_seconds() / 3600
        else:
            hours_since_last_log = 999  # No previous log

        # Build feature matrix
        X = []
        for chore_info in candidate_chores:
            name = chore_info['name']
            days_until_due = chore_info.get('days_until_due', 0)

            features = self._build_features(
                chore=name,
                prev_chores=prev_chores,
                hour=hour,
                day_of_week=day_of_week,
                days_until_due=days_until_due,
                hours_since_last_log=hours_since_last_log
            )
            X.append(features)

        X = np.array(X)

        # Predict
        scores = self.model.predict_proba(X)[:, 1]

        # Build results
        results = []
        for i, chore_info in enumerate(candidate_chores):
            results.append({
                'name': chore_info['name'],
                'score': round(float(scores[i]), 4),
                'days_until_due': chore_info.get('days_until_due', 0)
            })

        # Sort by score descending
        results.sort(key=lambda x: -x['score'])

        return results[:top_k]


# Singleton instance for MCP server
_predictor = None


def get_predictor(db_path: str = None) -> CadencePredictor:
    """Get or create the singleton predictor instance."""
    global _predictor
    if _predictor is None:
        _predictor = CadencePredictor(db_path)
    return _predictor
