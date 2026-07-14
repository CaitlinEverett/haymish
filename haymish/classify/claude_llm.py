"""Opt-in classify backend using the Anthropic API. Only used when a rule's
classify.backend is explicitly set to "claude" in rules.toml.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from .base import ClassifyError, ClassifyResult, require_local_path

_HEIC_SUFFIXES = {".heic", ".heif"}
_SUPPORTED_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _convert_heic_to_jpeg(src_path: str) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", src_path, "--out", tmp_path],
            check=True, capture_output=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        os.unlink(tmp_path)
        raise ClassifyError(f"HEIC to JPEG conversion failed for {src_path}: {e}") from e
    return tmp_path


def classify(photo, prompt: str, config) -> ClassifyResult:
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise ClassifyError(
            "anthropic package not installed -- run `uv sync --extra claude` to use the "
            "claude classify backend"
        ) from e

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ClassifyError(
            "ANTHROPIC_API_KEY is not set -- set it in the environment to use the "
            "claude classify backend"
        )

    src_path = require_local_path(photo)
    suffix = Path(src_path).suffix.lower()

    converted_path: str | None = None
    try:
        if suffix in _HEIC_SUFFIXES:
            converted_path = _convert_heic_to_jpeg(src_path)
            image_path = converted_path
            media_type = "image/jpeg"
        else:
            image_path = src_path
            media_type = _SUPPORTED_MEDIA_TYPES.get(suffix, "image/jpeg")

        with open(image_path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode("ascii")

        client = Anthropic(api_key=api_key)
        try:
            response = client.messages.create(
                model=config.claude_model,
                max_tokens=20,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": f"{prompt}\n\nAnswer with only a single word: yes or no.",
                            },
                        ],
                    }
                ],
            )
        except Exception as e:
            raise ClassifyError(f"{getattr(photo, 'uuid', '?')}: claude API call failed: {e}") from e
    finally:
        if converted_path:
            try:
                os.unlink(converted_path)
            except OSError:
                pass

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    lowered = text.lower()

    if lowered.startswith("yes"):
        verdict, confidence = True, 0.9
    elif lowered.startswith("no"):
        verdict, confidence = False, 0.9
    else:
        verdict, confidence = False, 0.3

    detail = text[:200]
    return ClassifyResult(verdict=verdict, confidence=confidence, detail=detail)
