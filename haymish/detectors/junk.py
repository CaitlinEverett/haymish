"""Junk candidates from Apple's curation scores (failure, noise).

Score distributions vary per library, so scan reports counts at multiple
thresholds for calibration before this rule is ever allowed to act.
"""

from __future__ import annotations

from ..library import score
from ..types import Candidate, DetectorResult

FAILURE_TIERS = (0.2, 0.5, 0.8)
NOISE_TIERS = (0.5, 0.8)

# Defaults for the acting rule; scan stats exist to tune these.
ACT_FAILURE = 0.5
ACT_NOISE = 0.8


def detect(photos: list) -> DetectorResult:
    tiers = {f"failure>{t}": 0 for t in FAILURE_TIERS} | {f"noise>{t}": 0 for t in NOISE_TIERS}
    scored = 0
    candidates = []
    for p in photos:
        failure = score(p, "failure")
        noise = score(p, "noise")
        if failure is None and noise is None:
            continue
        scored += 1
        for t in FAILURE_TIERS:
            if failure is not None and failure > t:
                tiers[f"failure>{t}"] += 1
        for t in NOISE_TIERS:
            if noise is not None and noise > t:
                tiers[f"noise>{t}"] += 1
        if (failure or 0) > ACT_FAILURE or (noise or 0) > ACT_NOISE:
            candidates.append(
                Candidate(p.uuid, p.original_filename, p.date,
                          reason=f"failure={failure:.2f} noise={noise if noise is None else round(noise, 2)}")
            )
    notes = []
    if photos and scored == 0:
        notes.append("No curation scores found in this library — junk detection unavailable.")
    return DetectorResult(
        name="junk",
        title="Junk candidates (blurry/failed shots)",
        candidates=candidates,
        stats={"threshold_calibration": tiers, "photos_with_scores": scored},
        notes=notes,
    )
