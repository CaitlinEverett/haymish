"""Shared dataclasses for detectors, rules, and reports."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Candidate:
    """A photo matched by a detector, with the evidence for why."""

    uuid: str
    filename: str
    date: dt.datetime | None
    reason: str
    confidence: float = 1.0
    extra: dict = field(default_factory=dict)


@dataclass
class DetectorResult:
    name: str
    title: str
    candidates: list[Candidate]
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.candidates)
