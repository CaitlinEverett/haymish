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
