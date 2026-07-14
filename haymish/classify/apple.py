"""Free tier-1 classify backend: Apple's on-device ML, no network, no LLM.

This is a coarse pre-filter, not a real NLU match. It extracts a handful of
content keywords from the rule's prompt and checks for literal-substring
overlap against Apple's existing photo labels, falling back to a live Vision
VNClassifyImageRequest pass when that's inconclusive. It cannot understand
negation, compound conditions ("a screenshot OR a receipt"), counting, or
anything requiring actual reasoning about the image — it only knows whether
Apple's own label vocabulary happens to share words with the prompt. Rules
that need real judgment should use backend="ollama" or backend="claude";
"apple" exists for users who want a zero-network, zero-cost opt-in tier and
are OK with its blunt-instrument behavior.
"""

from __future__ import annotations

import re

from .. import library
from .base import ClassifyError, ClassifyResult, require_local_path

_STOPWORDS = {
    "is", "this", "a", "an", "the", "of", "does", "do", "are", "was", "were",
    "answer", "only", "yes", "or", "no", "and", "with", "for", "in", "on",
    "to", "it", "that", "these", "those", "has", "have", "photo", "picture",
    "image", "please", "you", "think", "would", "say", "look", "looks",
    "like", "there", "any", "showing", "show", "shows", "containing",
    "contain", "contains", "if", "what", "which", "than", "then",
}

_WORD_RE = re.compile(r"[a-z0-9]+")

VISION_HIGH_CONFIDENCE = 0.5
CONF_LIVE_VISION_MATCH = 0.7
CONF_EXISTING_LABEL_MATCH = 0.6
CONF_NO_MATCH = 0.3
TOP_VISION_RESULTS = 10


def _prompt_keywords(prompt: str) -> set[str]:
    words = _WORD_RE.findall(prompt.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _label_overlap(keywords: set[str], labels: list[str]) -> bool:
    for label in labels:
        label_words = _WORD_RE.findall(label)
        if keywords & set(label_words):
            return True
        for kw in keywords:
            if kw in label:
                return True
    return False


def _run_vision_classify(path: str):
    import objc
    import Quartz  # noqa: F401  (registers CGImage helpers Vision needs)
    import Vision
    from Foundation import NSURL

    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        req = Vision.VNClassifyImageRequest.alloc().init()
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise ClassifyError(f"Vision classify request failed: {err}")
        results = req.results() or []
        return [(r.identifier().lower(), float(r.confidence())) for r in results[:TOP_VISION_RESULTS]]


def classify(photo, prompt: str, config) -> ClassifyResult:
    """Coarse keyword-overlap match against Apple's ML labels, with a live
    Vision fallback. See module docstring for the honest caveats.
    """
    keywords = _prompt_keywords(prompt)
    existing_labels = library.labels(photo)

    if keywords and _label_overlap(keywords, existing_labels):
        return ClassifyResult(
            verdict=True,
            confidence=CONF_EXISTING_LABEL_MATCH,
            detail=f"matched existing Photos labels (keywords: {sorted(keywords)})",
        )

    path = require_local_path(photo)  # (a) was inconclusive and we truly have no other signal

    vision_results = _run_vision_classify(path)
    if keywords:
        for identifier, confidence in vision_results:
            if identifier in keywords or any(kw in identifier for kw in keywords):
                if confidence > VISION_HIGH_CONFIDENCE:
                    return ClassifyResult(
                        verdict=True,
                        confidence=CONF_LIVE_VISION_MATCH,
                        detail=f"live Vision match: {identifier!r} ({confidence:.2f})",
                    )

    return ClassifyResult(
        verdict=False,
        confidence=CONF_NO_MATCH,
        detail="no matching labels -- consider a stronger backend for this rule",
    )
