"""Pluggable classify backends: apple (free, on-device), ollama (local LLM, default
for custom prompts), claude (opt-in, API). All implement the same contract in base.py.
"""

from .base import ClassifyError, ClassifyResult, get_backend

__all__ = ["ClassifyError", "ClassifyResult", "get_backend"]
