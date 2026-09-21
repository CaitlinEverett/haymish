"""Typed states for the incremental-processing catalog substrate.

This is intentionally not wired into ``sweep.py`` yet. The catalog contract needs
to prove observation, invalidation, and effect idempotency independently before it
changes the behavior of the existing Photos action pipeline.
"""

from enum import StrEnum


class ObservationResult(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class ProcessorState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EffectState(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"
