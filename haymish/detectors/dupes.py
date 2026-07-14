"""Exact duplicates via osxphotos signature matching (size + date + dimensions).

Near-duplicate perceptual hashing (imagehash) lands with the sweep engine —
it needs local originals, so scan reports exact dupes only.
"""

from __future__ import annotations

from ..library import score
from ..types import Candidate, DetectorResult


def best_of_group(group: list):
    """Keep the highest-resolution copy; break ties on aesthetic score."""
    def key(p):
        return (
            (getattr(p, "width", 0) or 0) * (getattr(p, "height", 0) or 0),
            score(p, "overall") or 0.0,
        )
    return max(group, key=key)


def detect(photos: list) -> DetectorResult:
    seen: set[str] = set()
    groups = 0
    extra_copies: list[Candidate] = []
    reclaimable = 0
    for p in photos:
        if p.uuid in seen:
            continue
        dups = getattr(p, "duplicates", None) or []
        if not dups:
            continue
        group = [p] + [d for d in dups if d.uuid != p.uuid]
        for g in group:
            seen.add(g.uuid)
        groups += 1
        keep = best_of_group(group)
        for g in group:
            if g.uuid == keep.uuid:
                continue
            size = getattr(g, "original_filesize", 0) or 0
            reclaimable += size
            extra_copies.append(
                Candidate(g.uuid, g.original_filename, g.date,
                          reason=f"duplicate of {keep.original_filename}",
                          extra={"bytes": size})
            )
    return DetectorResult(
        name="dupes",
        title="Duplicates (exact)",
        candidates=extra_copies,
        stats={"groups": groups, "extra_copies": len(extra_copies),
               "reclaimable_mb": round(reclaimable / 1_000_000, 1)},
    )
