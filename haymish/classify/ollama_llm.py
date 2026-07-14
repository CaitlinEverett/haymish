"""Local vision LLM classify backend via Ollama's HTTP API. Default backend for
custom-prompt rules.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

import httpx

from .base import ClassifyError, ClassifyResult, require_local_path

_JPEG_READY_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _read_as_jpeg_bytes(path: str) -> bytes:
    """Ollama vision models are unreliable on HEIC; convert via sips (always
    present on macOS) rather than round-tripping through a Python image lib."""
    src = Path(path)
    if src.suffix.lower() in _JPEG_READY_SUFFIXES:
        return src.read_bytes()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        dst = Path(tmp.name)
    try:
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise ClassifyError(
                f"sips failed converting {src.name} to jpeg: {result.stderr.strip()[:200]}"
            )
        return dst.read_bytes()
    finally:
        dst.unlink(missing_ok=True)


def _parse_verdict(text: str) -> tuple[bool, float]:
    normalized = text.strip().lower()
    if normalized.startswith("yes"):
        return True, 0.85
    if normalized.startswith("no"):
        return False, 0.85
    return False, 0.3


def classify(photo, prompt: str, config) -> ClassifyResult:
    path = require_local_path(photo)
    image_bytes = _read_as_jpeg_bytes(path)
    b64 = base64.b64encode(image_bytes).decode("ascii")

    try:
        response = httpx.post(
            f"{config.ollama_host}/api/generate",
            json={
                "model": config.ollama_model,
                "prompt": prompt,
                "images": [b64],
                "stream": False,
            },
            timeout=120,
        )
    except httpx.ConnectError as e:
        raise ClassifyError(
            f"could not connect to Ollama at {config.ollama_host} — is Ollama running? ({e})"
        ) from e
    except httpx.HTTPError as e:
        raise ClassifyError(f"Ollama request failed: {e}") from e

    if response.status_code != 200:
        raise ClassifyError(
            f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
        )

    body = response.json()
    if "response" not in body:
        raise ClassifyError(f"Ollama response missing 'response' key: {body!r}"[:300])

    text = body["response"]
    verdict, confidence = _parse_verdict(text)
    detail = text.strip()[:150]
    return ClassifyResult(verdict=verdict, confidence=confidence, detail=detail)
