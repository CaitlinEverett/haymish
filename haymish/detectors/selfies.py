"""Selfies via Photos' own flag, with EXIF front-camera fallback."""

from __future__ import annotations

from ..library import is_selfie
from ..types import Candidate, DetectorResult


def detect(photos: list) -> DetectorResult:
    candidates = []
    native_flag = 0
    exif_only = 0
    for p in photos:
        native = bool(getattr(p, "selfie", False))
        if native or is_selfie(p):
            if native:
                native_flag += 1
            else:
                exif_only += 1
            candidates.append(
                Candidate(p.uuid, p.original_filename, p.date,
                          reason="Photos selfie flag" if native else "front-camera EXIF")
            )
    return DetectorResult(
        name="selfies",
        title="Selfies",
        candidates=candidates,
        stats={"photos_flag": native_flag, "exif_fallback_only": exif_only},
    )
