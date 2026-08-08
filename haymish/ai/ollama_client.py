"""Shared Ollama HTTP helpers for the AI layer (generate, embed, availability).

classify/ollama_llm.py predates this module and keeps its own call; new AI-layer
code should use these helpers so error handling stays consistent.
"""

from __future__ import annotations

import base64

import httpx


class AIError(Exception):
    """The AI layer couldn't produce a result (Ollama down, model missing, bad output)."""


def _post(host: str, path: str, payload: dict, timeout: float) -> dict:
    try:
        response = httpx.post(f"{host}{path}", json=payload, timeout=timeout)
    except httpx.ConnectError as e:
        raise AIError(f"could not connect to Ollama at {host} — is Ollama running? ({e})") from e
    except httpx.HTTPError as e:
        raise AIError(f"Ollama request failed: {e}") from e
    if response.status_code == 404:
        raise AIError(
            f"Ollama returned 404 for {payload.get('model')!r} — model not pulled? "
            f"Try: ollama pull {payload.get('model')}"
        )
    if response.status_code != 200:
        raise AIError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
    return response.json()


def generate(host: str, model: str, prompt: str, image_bytes: bytes | None = None,
             format_json: bool = False, think: bool | None = None,
             timeout: float = 180) -> str:
    """think=False disables thinking mode — required for reasoning-family models
    (qwen3 etc.) with format_json, where thinking otherwise consumes the whole
    output and 'response' comes back empty. Retries without the option for models
    that reject it."""
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if image_bytes is not None:
        payload["images"] = [base64.b64encode(image_bytes).decode("ascii")]
    if format_json:
        payload["format"] = "json"
    if think is not None:
        payload["think"] = think
    try:
        body = _post(host, "/api/generate", payload, timeout)
    except AIError:
        if think is None:
            raise
        payload.pop("think")
        body = _post(host, "/api/generate", payload, timeout)
    if "response" not in body:
        raise AIError(f"Ollama response missing 'response' key: {str(body)[:200]}")
    return body["response"]


def embed(host: str, model: str, texts: list[str], timeout: float = 120) -> list[list[float]]:
    """Batch embedding via /api/embed. Returns one vector per input text."""
    if not texts:
        return []
    body = _post(host, "/api/embed", {"model": model, "input": texts}, timeout)
    vectors = body.get("embeddings")
    if not vectors or len(vectors) != len(texts):
        raise AIError(
            f"Ollama /api/embed returned {len(vectors or [])} vectors for {len(texts)} inputs"
        )
    return vectors


def _norm(name: str) -> str:
    return name[:-7] if name.endswith(":latest") else name


def available_models(host: str) -> set[str]:
    """Full model names Ollama has locally (':latest' suffix normalized away), or
    empty set if Ollama is unreachable — callers treat that as 'nothing available'."""
    try:
        response = httpx.get(f"{host}/api/tags", timeout=5)
        response.raise_for_status()
    except httpx.HTTPError:
        return set()
    return {_norm(m["name"]) for m in response.json().get("models", []) if m.get("name")}


def model_available(host: str, model: str) -> bool:
    """Exact-tag matching: 'gemma3:27b' does NOT count as available just because
    'gemma3:4b' is pulled — a tag mismatch 404s at request time, so a loose base-name
    check here would just defer the failure to the worst moment. An untagged name
    matches any pulled tag of that model."""
    models = available_models(host)
    target = _norm(model)
    if ":" in target:
        return target in models
    return target in models or any(m.split(":")[0] == target for m in models)
