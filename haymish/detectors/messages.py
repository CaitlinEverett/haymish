"""Message/chat screenshots via layout heuristics on Photos' indexed OCR text."""

from __future__ import annotations

import re

from ..library import detected_text
from ..types import Candidate, DetectorResult

CHAT_MARKERS = re.compile(
    r"\b(iMessage|Delivered|Read \d|Text Message|Reply|Tapback|typing…|"
    r"WhatsApp|Messenger|Slack|Signal|Telegram)\b"
)
TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}\s?(AM|PM)?\b", re.I)


def looks_like_message(photo) -> tuple[bool, float, str] | None:
    if not getattr(photo, "screenshot", False):
        return None
    text = detected_text(photo)
    if not text:
        return None
    markers = CHAT_MARKERS.findall(text)
    timestamps = TIMESTAMP_RE.findall(text)
    if markers:
        return True, 0.85, f"chat markers: {sorted(set(markers))[:3]}"
    if len(timestamps) >= 3:
        return True, 0.5, f"{len(timestamps)} message-style timestamps"
    return None


def detect(photos: list) -> DetectorResult:
    candidates = []
    for p in photos:
        hit = looks_like_message(p)
        if hit:
            _, conf, reason = hit
            candidates.append(
                Candidate(p.uuid, p.original_filename, p.date, reason=reason, confidence=conf)
            )
    return DetectorResult(
        name="messages",
        title="Message screenshots",
        candidates=candidates,
    )
