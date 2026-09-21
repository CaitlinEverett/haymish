"""Semantic search over the AI index: embed the query, cosine against every
indexed photo. Numpy over a few tens of thousands of vectors is instant — no
vector database needed at personal-library scale.
"""

from __future__ import annotations

import numpy as np

from ..catalog import Catalog
from ..config import Config
from . import ollama_client
from .ollama_client import AIError

# Instruction prefix improves retrieval quality for instruction-tuned embedding
# models (qwen3-embedding family); harmless no-op text for models that ignore it.
_QUERY_PREFIX = "Instruct: Given a photo search query, retrieve matching photo descriptions\nQuery: "


def semantic_scores(config: Config, catalog: Catalog, query: str) -> dict[str, float]:
    """uuid -> cosine similarity in [0..1]-ish (raw cosine; embedding models keep
    these positive in practice). Empty dict when nothing is indexed yet."""
    rows = catalog.all_embeddings(config.ai_embed_model)
    if not rows:
        return {}

    query_vec = ollama_client.embed(config.ollama_host, config.ai_embed_model,
                                     [_QUERY_PREFIX + query])[0]
    q = np.asarray(query_vec, dtype=np.float32)
    q /= (np.linalg.norm(q) or 1.0)

    dim = rows[0][2]
    uuids = [r[0] for r in rows]
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), dim)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    scores = (matrix @ q) / norms
    return dict(zip(uuids, scores.tolist()))


def top_matches(scores: dict[str, float], k: int) -> list[tuple[str, float]]:
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]


def gallery_scores(
    scores: dict[str, float],
    events: list,
    min_members: int = 1,
) -> list[tuple[object, float, int, int]]:
    """Score and rank gallery events by how well their members match a query.

    For each event, computes the mean semantic score of its *indexed* members.
    Events with no indexed members are included with score 0 so the caller can
    warn about missing coverage.

    Returns (event, mean_score, indexed_count, total_count) sorted by
    descending mean score.
    """
    ranked: list[tuple[object, float, int, int]] = []
    for event in events:
        member_scores = [scores[u] for u in event.uuids if u in scores]
        total = len(event.uuids)
        indexed = len(member_scores)
        mean = sum(member_scores) / indexed if indexed else 0.0
        if indexed >= min_members or indexed == 0:
            ranked.append((event, mean, indexed, total))
    ranked.sort(key=lambda r: r[1], reverse=True)
    return ranked


def index_coverage(config: Config, catalog: Catalog, photos: list) -> tuple[int, int]:
    """(indexed, total) for the current embed model — lets callers warn when the
    index is stale instead of silently searching a fraction of the library."""
    embedded = catalog.embedded_uuids(config.ai_embed_model)
    return sum(1 for p in photos if p.uuid in embedded), len(photos)
