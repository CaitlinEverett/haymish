"""Receipt candidates from Apple's ML labels + Photos' indexed OCR text.

Tier-1 heuristics only (free). Ambiguous cases get an LLM pass in the sweep
engine when the rule configures a classify backend.
"""

from __future__ import annotations

import re

from ..library import detected_text, labels
from ..types import Candidate, DetectorResult

RECEIPT_LABELS = {"receipt", "receipts", "document", "documents", "invoice", "menu", "handwriting", "text"}
STRONG_LABELS = {"receipt", "receipts", "invoice"}
MONEY_RE = re.compile(r"[$€£]\s?\d{1,6}[.,]\d{2}")
TOTAL_RE = re.compile(r"\b(sub)?total|amount due|balance due|tax|cash|change due|visa|mastercard\b", re.I)


def looks_like_receipt(photo) -> tuple[bool, float, str] | None:
    """Returns (is_candidate, confidence, reason) or None."""
    lbls = set(labels(photo))
    text = detected_text(photo)
    money_hits = len(MONEY_RE.findall(text))
    total_hits = len(TOTAL_RE.findall(text))

    if lbls & STRONG_LABELS:
        return True, 0.9, f"Apple label: {sorted(lbls & STRONG_LABELS)}"
    if money_hits >= 2 and total_hits >= 1:
        return True, 0.8, f"OCR: {money_hits} amounts + total/tax keywords"
    if (lbls & RECEIPT_LABELS) and (money_hits >= 1 or total_hits >= 2):
        return True, 0.6, f"label {sorted(lbls & RECEIPT_LABELS)} + OCR signals"
    return None


def detect(photos: list) -> DetectorResult:
    candidates = []
    strong = 0
    text_coverage = 0
    for p in photos:
        if detected_text(p):
            text_coverage += 1
        hit = looks_like_receipt(p)
        if hit:
            _, conf, reason = hit
            if conf >= 0.8:
                strong += 1
            candidates.append(
                Candidate(p.uuid, p.original_filename, p.date, reason=reason, confidence=conf)
            )
    notes = []
    if photos and text_coverage == 0:
        notes.append(
            "Photos' indexed OCR text is unavailable on this library — receipt/message "
            "detection is running on labels only. A Vision OCR fallback pass would restore accuracy."
        )
    return DetectorResult(
        name="receipts",
        title="Receipt candidates",
        candidates=candidates,
        stats={"strong": strong, "weaker": len(candidates) - strong,
               "ocr_text_coverage": f"{text_coverage}/{len(photos)}"},
        notes=notes,
    )
