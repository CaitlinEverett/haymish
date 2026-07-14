"""Screenshots, bucketed by age — the age gates drive the lifecycle rules."""

from __future__ import annotations

from ..library import photo_age_days
from ..types import Candidate, DetectorResult

AGE_BUCKETS = [(0, 14), (14, 30), (30, 90), (90, None)]


def detect(photos: list) -> DetectorResult:
    candidates = []
    buckets = {f"{lo}-{hi if hi else '∞'}d": 0 for lo, hi in AGE_BUCKETS}
    for p in photos:
        if not getattr(p, "screenshot", False):
            continue
        age = photo_age_days(p)
        for lo, hi in AGE_BUCKETS:
            if age is not None and age >= lo and (hi is None or age < hi):
                buckets[f"{lo}-{hi if hi else '∞'}d"] += 1
                break
        candidates.append(
            Candidate(p.uuid, p.original_filename, p.date,
                      reason=f"screenshot, {age:.0f}d old" if age is not None else "screenshot")
        )
    return DetectorResult(
        name="screenshots",
        title="Screenshots",
        candidates=candidates,
        stats={"by_age": buckets},
    )
