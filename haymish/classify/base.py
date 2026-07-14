"""Classify backend contract.

A backend is a module exposing:

    def classify(photo, prompt: str, config: "Config") -> ClassifyResult

`photo` is an osxphotos Photo object. `prompt` is the rule's classify.prompt
string (a yes/no question about the image). `config` is the loaded Config
(haymish.config.Config) — backends read [global.ollama]/[global.claude]
settings from it.

Raise ClassifyError for anything that isn't a normal "no" verdict: the local
original isn't downloaded, the network call failed, the API key is missing.
The sweep engine catches ClassifyError, skips the photo for that rule, and
notes it in the report rather than silently treating "couldn't classify" as
"didn't match."

Implementations MUST NOT mutate the photo or touch the filesystem outside of
reading the photo's own local copy (`photo.path`) into memory.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass


class ClassifyError(Exception):
    """A backend could not produce a verdict (missing local file, network/API failure, etc.)."""


@dataclass
class ClassifyResult:
    verdict: bool
    confidence: float  # 0.0-1.0
    detail: str = ""   # short human-readable reason, shown in reports


_BACKENDS = {
    "apple": "haymish.classify.apple",
    "ollama": "haymish.classify.ollama_llm",
    "claude": "haymish.classify.claude_llm",
}


def get_backend(name: str):
    """Returns the backend module for `name`. Raises ValueError for unknown names
    (config.py already validates this at load time, so this should only trip on
    a programming error, not bad user config)."""
    if name not in _BACKENDS:
        raise ValueError(f"unknown classify backend {name!r} (valid: {sorted(_BACKENDS)})")
    return importlib.import_module(_BACKENDS[name])


def require_local_path(photo) -> str:
    """Shared helper: every image-based backend needs the original on disk.

    osxphotos' `photo.path` is None when the original lives only in iCloud
    (Optimize Mac Storage). Rather than each backend silently returning a
    false "no", this raises so the caller can distinguish "classified as no"
    from "couldn't even look at it".
    """
    path = getattr(photo, "path", None)
    if not path:
        raise ClassifyError(
            f"{getattr(photo, 'uuid', '?')}: original not downloaded locally "
            f"(iCloud storage-optimized) — run with a local copy or export first"
        )
    return path
