"""Tests for role-based Ollama model resolution."""

from __future__ import annotations

from haymish.ai.model_resolve import resolve_model


def test_resolve_uses_preferred_when_available(monkeypatch):
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"qwen3-vl:8b", "nomic-embed-text"},
    )
    monkeypatch.setattr(
        "haymish.ai.model_resolve.model_available",
        lambda host, model: model == "qwen3-vl:8b",
    )
    r = resolve_model("http://localhost:11434", "qwen3-vl:8b", "caption")
    assert r.model == "qwen3-vl:8b"
    assert r.fallback_used is False


def test_resolve_falls_back_to_vision_model(monkeypatch):
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"qwen3-vl:8b", "nomic-embed-text", "llama3.2:3b"},
    )

    def avail(host, model):
        return model in {"qwen3-vl:8b", "nomic-embed-text", "llama3.2:3b"} and model != "gemma3:4b"

    monkeypatch.setattr("haymish.ai.model_resolve.model_available", avail)
    r = resolve_model("http://localhost:11434", "gemma3:4b", "classify")
    assert r.fallback_used is True
    assert r.model == "qwen3-vl:8b"
    assert "gemma3:4b" in r.note


def test_resolve_embed_prefers_embed_family(monkeypatch):
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"nomic-embed-text", "qwen3-vl:8b"},
    )
    monkeypatch.setattr(
        "haymish.ai.model_resolve.model_available",
        lambda host, model: model != "missing-embed",
    )
    r = resolve_model("http://localhost:11434", "missing-embed", "embed")
    assert r.model == "nomic-embed-text"
    assert r.fallback_used is True


def test_resolve_with_no_models_available(monkeypatch):
    """When Ollama is unreachable (no models), return the preferred with a note."""
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: set(),
    )
    r = resolve_model("http://localhost:11434", "gemma3:4b", "classify")
    assert r.model == "gemma3:4b"
    assert r.fallback_used is True
    assert "unreachable" in r.note.lower()


def test_resolve_with_empty_preferred(monkeypatch):
    """An empty preferred string should still resolve to something useful."""
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"qwen3-vl:8b"},
    )
    monkeypatch.setattr(
        "haymish.ai.model_resolve.model_available",
        lambda host, model: model == "qwen3-vl:8b",
    )
    r = resolve_model("http://localhost:11434", "", "classify")
    assert r.model == "qwen3-vl:8b"
    assert r.fallback_used is True


def test_resolve_planner_prefers_non_vision(monkeypatch):
    """Planner role should prefer non-vision models when available."""
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"qwen3-vl:8b", "llama3.2:3b", "qwen3.6:27b"},
    )
    monkeypatch.setattr(
        "haymish.ai.model_resolve.model_available",
        lambda host, model: model != "missing-planner",
    )
    r = resolve_model("http://localhost:11434", "missing-planner", "planner")
    assert r.fallback_used is True
    # Should prefer non-vision model
    assert r.model in {"llama3.2:3b", "qwen3.6:27b"}
    assert "qwen3-vl" not in r.model


def test_resolve_no_candidates_for_role(monkeypatch):
    """When no model fits the role, note should say so."""
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"llama3.2:3b"},  # no vision model
    )
    monkeypatch.setattr(
        "haymish.ai.model_resolve.model_available",
        lambda host, model: model != "gemma3:4b",
    )
    r = resolve_model("http://localhost:11434", "gemma3:4b", "classify")
    # classify needs a vision model; llama3.2:3b isn't vision
    # but resolve should still return something (the best available)
    assert r.fallback_used is True


def test_resolve_prefer_sorted_picks_smaller_models(monkeypatch):
    """Smaller models should be preferred for interactive latency."""
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"gemma3:4b", "gemma3:27b", "qwen3-vl:8b"},
    )

    def avail(host, model):
        return model in {"gemma3:4b", "gemma3:27b", "qwen3-vl:8b"}

    monkeypatch.setattr("haymish.ai.model_resolve.model_available", avail)

    # Missing model so it falls back
    r = resolve_model("http://localhost:11434", "llava:70b", "classify")
    assert r.fallback_used is True
    # Should pick 4b over 27b
    assert r.model == "gemma3:4b"


def test_resolve_note_mentions_preferred_when_falling_back(monkeypatch):
    """The note should mention what was configured so the user knows why."""
    monkeypatch.setattr(
        "haymish.ai.model_resolve.available_models",
        lambda host: {"nomic-embed-text"},
    )
    monkeypatch.setattr(
        "haymish.ai.model_resolve.model_available",
        lambda host, model: model == "nomic-embed-text",
    )
    r = resolve_model("http://localhost:11434", "mxbai-embed-large", "embed")
    assert "mxbai-embed-large" in r.note
    assert r.model == "nomic-embed-text"
