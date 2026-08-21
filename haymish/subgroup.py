"""Split a large match set into labelled sub-groups using the AI index.

Why this exists: a rule like `screenshots-general` can match thousands of
photos, and a flat grid of thousands is not reviewable. Worse, the matches are
genuinely heterogeneous — a real library's screenshot bucket holds FaceTime
stills, saved photos of people, web pages, maps, receipts and memes all at once,
so there is no single right answer to "apply this rule?".

Sub-grouping turns per-item decisions into per-group ones: "these 340 are
FaceTime calls, keep them all" is one judgment instead of 340.

Everything here runs on embeddings already cached by `haymish index` — no
inference, no network. Clustering is plain k-means in numpy; the goal is a
useful partition for a human eye, not a defensible statistical model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Words that describe almost any photo, so they never distinguish one group of
# them from another. Kept deliberately short -- over-filtering makes labels
# vague ("group 3") rather than wrong.
_STOPWORDS = {
    "photo", "image", "picture", "screenshot", "shows", "showing", "displaying",
    "displays", "featuring", "depicting", "depicts", "visible", "appears", "with",
    "that", "this", "there", "here", "from", "into", "onto", "over", "under",
    "and", "the", "for", "are", "was", "were", "has", "have", "its", "his", "her",
    "their", "some", "several", "other", "another", "which", "while", "also",
    "text", "reads", "reading", "background", "foreground", "left", "right",
    "top", "bottom", "center", "centre", "close", "view", "type", "kind",
}
_MIN_GROUP = 3          # below this a "group" is just noise
_MAX_GROUPS = 12        # more than this stops being easier than a flat list
_LABEL_TERMS = 3


@dataclass
class SubGroup:
    key: str
    label: str
    uuids: list[str]
    size: int
    terms: list[str] = field(default_factory=list)


def _kmeans(matrix: np.ndarray, k: int, seed: int = 0, iters: int = 25):
    """Minimal k-means on L2-normalised vectors (so Euclidean ≈ cosine).

    Deterministic by construction: k-means++ seeding driven by a fixed RNG, so
    the same review queue subdivides the same way every time. A queue that
    reshuffled itself between visits would be maddening to work through.
    """
    rng = np.random.default_rng(seed)
    n = matrix.shape[0]

    centers = [matrix[rng.integers(n)]]
    for _ in range(1, k):
        d = np.min(
            np.stack([np.sum((matrix - c) ** 2, axis=1) for c in centers]), axis=0
        )
        total = d.sum()
        if total <= 0:
            centers.append(matrix[rng.integers(n)])
            continue
        centers.append(matrix[rng.choice(n, p=d / total)])
    centers = np.stack(centers)

    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        distances = ((matrix[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        new_labels = distances.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            members = matrix[labels == j]
            if len(members):
                centers[j] = members.mean(axis=0)
    return labels


def _terms_for(texts: list[str]) -> list[str]:
    from collections import Counter
    import re

    counter: Counter = Counter()
    for text in texts:
        words = {w for w in re.findall(r"[a-z][a-z'-]{2,}", (text or "").lower())
                 if w not in _STOPWORDS}
        counter.update(words)
    return counter


def _label_groups(groups: dict[int, list[str]], text_for: dict[str, str]) -> dict[int, list[str]]:
    """Pick the terms that make each group *different* from the others.

    Plain frequency picks the same generic words for every group; scoring a
    term by how concentrated it is in one group is what produces "facetime" for
    the video-call cluster instead of "screen" for all of them.
    """
    per_group = {gid: _terms_for([text_for.get(u, "") for u in uuids])
                 for gid, uuids in groups.items()}
    totals: dict[str, int] = {}
    for counter in per_group.values():
        for term, n in counter.items():
            totals[term] = totals.get(term, 0) + n

    labels: dict[int, list[str]] = {}
    for gid, counter in per_group.items():
        size = max(len(groups[gid]), 1)
        scored = []
        for term, n in counter.items():
            if n < 2:
                continue
            share = n / totals[term]          # how exclusive to this group
            coverage = n / size               # how typical within this group
            scored.append((share * coverage, term))
        scored.sort(reverse=True)
        labels[gid] = [term for _, term in scored[:_LABEL_TERMS]]
    return labels


def subgroup_photos(config, catalog, photos, max_groups: int = _MAX_GROUPS,
                    min_group: int = _MIN_GROUP) -> list[SubGroup]:
    """Partition `photos` into labelled sub-groups. Empty list when there's
    nothing useful to do — too few photos, or no embeddings for them."""
    uuids = [p.uuid for p in photos]
    if len(uuids) < min_group * 2:
        return []

    rows = catalog.all_embeddings(config.ai_embed_model)
    if not rows:
        return []
    wanted = set(uuids)
    rows = [r for r in rows if r[0] in wanted]
    if len(rows) < min_group * 2:
        return []

    dim = rows[0][2]
    keys = [r[0] for r in rows]
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), dim)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms

    # Enough groups to separate distinct kinds, few enough to stay scannable.
    k = max(2, min(max_groups, int(np.sqrt(len(rows) / 2)) or 2))
    assignments = _kmeans(matrix, k)

    grouped: dict[int, list[str]] = {}
    for uuid, gid in zip(keys, assignments):
        grouped.setdefault(int(gid), []).append(uuid)

    # Text to label from: caption first (richest), then Photos' own OCR/labels.
    from . import library

    by_uuid = {p.uuid: p for p in photos}
    text_for: dict[str, str] = {}
    for uuid in keys:
        photo = by_uuid.get(uuid)
        parts = [catalog.get_caption(uuid) or ""]
        if photo is not None:
            parts.append(" ".join(library.labels(photo)))
            parts.append(library.detected_text(photo)[:400])
        text_for[uuid] = " ".join(parts)

    term_map = _label_groups(grouped, text_for)

    out: list[SubGroup] = []
    leftovers: list[str] = []
    for gid, members in grouped.items():
        if len(members) < min_group:
            leftovers.extend(members)
            continue
        terms = term_map.get(gid, [])
        label = ", ".join(terms) if terms else "mixed"
        out.append(SubGroup(key=f"g{gid}", label=label, uuids=members,
                             size=len(members), terms=terms))

    out.sort(key=lambda g: g.size, reverse=True)
    if leftovers:
        out.append(SubGroup(key="other", label="everything else",
                             uuids=leftovers, size=len(leftovers)))
    return out
