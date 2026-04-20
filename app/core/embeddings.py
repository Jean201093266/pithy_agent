"""Shared embedding and similarity utilities.

Centralizes cosine similarity and text embedding logic used across
db.py, memory.py, and memory_enhanced.py.
"""
from __future__ import annotations

import logging
import math
import re

LOGGER = logging.getLogger(__name__)

# Cache for sentence-transformers model (loaded once)
_st_model = None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Uses numpy when available for ~50x speedup on typical embedding sizes.
    """
    size = min(len(a), len(b))
    if size == 0:
        return 0.0
    try:
        import numpy as np
        va = np.asarray(a[:size], dtype=np.float32)
        vb = np.asarray(b[:size], dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    except ImportError:
        pass
    dot = sum(float(a[i]) * float(b[i]) for i in range(size))
    norm_a = math.sqrt(sum(float(a[i]) * float(a[i]) for i in range(size)))
    norm_b = math.sqrt(sum(float(b[i]) * float(b[i]) for i in range(size)))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_text(text: str, dims: int = 64) -> list[float]:
    """Generate text embedding.

    Tries in order:
    1. sentence-transformers (local, high quality)
    2. Hash-based pseudo-embedding (fallback)
    """
    global _st_model

    # Try sentence-transformers
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        if _st_model is None:
            _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        vec = _st_model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except ImportError:
        pass
    except Exception as exc:
        LOGGER.debug("sentence-transformers embed failed: %s", exc)

    # Hash-based fallback
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    vec = [0.0] * dims
    if not tokens:
        return vec
    for token in tokens:
        h = hash(token)
        idx = h % dims
        sign = 1.0 if (h >> 1) & 1 else -1.0
        vec[idx] += sign

    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]

