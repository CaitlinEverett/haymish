"""Resolve configured Ollama models to something actually pulled locally.

Configured tags in rules.toml often drift from what's on disk (gemma3:4b vs
qwen3-vl:8b). Callers pass a preferred model; we return an effective tag plus
whether we fell back, so doctor/UI can warn without forcing `ollama pull`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ollama_client import available_models, model_available

Role = Literal["classify", "caption", "planner", "embed"]

VISION_MARKERS = (
    "gemma3", "qwen2.5vl", "qwen3-vl", "llava", "llama3.2-vision", "minicpm-v", "moondream",
)


@dataclass(frozen=True)
class ResolvedModel:
    model: str
    fallback_used: bool
    note: str = ""


def _is_vision(name: str) -> bool:
    lower = name.lower()
    return any(m in lower for m in VISION_MARKERS)


def _prefer_sorted(names: list[str]) -> list[str]:
    """Prefer smaller / common tags first for interactive path latency."""
    def key(n: str) -> tuple:
        # Prefer :8b / :4b over :32b / :70b for interactive; stable alpha otherwise
        size = 99
        for token, rank in (("1b", 1), ("2b", 2), ("3b", 3), ("4b", 4), ("7b", 5),
                            ("8b", 6), ("9b", 7), ("14b", 8), ("27b", 9), ("32b", 10),
                            ("35b", 11), ("70b", 12)):
            if token in n:
                size = rank
                break
        return (size, n)
    return sorted(names, key=key)


def resolve_model(host: str, preferred: str, role: Role) -> ResolvedModel:
    """Pick an installed model for `role`, preferring `preferred` when available."""
    preferred = (preferred or "").strip()
    if preferred and model_available(host, preferred):
        return ResolvedModel(preferred, False)

    models = available_models(host)
    if not models:
        return ResolvedModel(
            preferred or "(none)",
            True,
            f"Ollama unreachable at {host}; cannot resolve {role} model",
        )

    candidates: list[str]
    if role in ("classify", "caption"):
        candidates = _prefer_sorted([m for m in models if _is_vision(m)])
    elif role == "embed":
        # Prefer known embedding families
        emb = [m for m in models if "embed" in m.lower() or "nomic" in m.lower()]
        candidates = _prefer_sorted(emb) or _prefer_sorted(list(models))
    else:  # planner — any text-capable; prefer non-vision if present
        non_vis = [m for m in models if not _is_vision(m)]
        candidates = _prefer_sorted(non_vis) or _prefer_sorted(list(models))

    if not candidates:
        return ResolvedModel(
            preferred or "(none)",
            True,
            f"no local model suitable for {role}; configured {preferred!r} missing",
        )

    chosen = candidates[0]
    note = (
        f"configured {preferred!r} unavailable for {role}; using {chosen!r}"
        if preferred else f"using {chosen!r} for {role}"
    )
    return ResolvedModel(chosen, True, note)
