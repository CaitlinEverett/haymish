"""Pure domain substrate for Haymish's future browser and routine engine.

These immutable values describe what evidence selects a collection and what
should happen to it.  They deliberately do not execute rules or access Photos,
the catalog, or any other runtime service.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Rule


def _freeze(value: Any) -> Any:
    """Copy supported criterion data into deeply immutable values."""
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            frozen[key] = _freeze(child)
        return MappingProxyType(dict(sorted(frozen.items())))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, (set, frozenset)):
        children = [_freeze(child) for child in value]
        return tuple(sorted(children, key=lambda child: _canonical_json(child)))
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical values cannot contain NaN or infinity")
    if value is None or isinstance(value, (str, bool, int, float, date, datetime, Enum)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonicalize(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _canonicalize(value.to_dict())
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Mapping):
        return {
            key: _canonicalize(child)
            for key, child in sorted(value.items(), key=lambda item: item[0])
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(child) for child in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("canonical values cannot contain NaN or infinity")
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _revision(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class LensOperator(str, Enum):
    ATOM = "atom"
    ALL = "all"
    ANY = "any"
    NOT = "not"


@dataclass(frozen=True, slots=True)
class Lens:
    """An atomic evidence source or a logical composition of other lenses."""

    operator: LensOperator
    source: str | None = None
    criteria: Mapping[str, Any] = field(default_factory=dict)
    children: tuple["Lens", ...] = ()

    def __post_init__(self) -> None:
        try:
            operator = LensOperator(self.operator)
        except ValueError as error:
            raise ValueError(f"unknown lens operator: {self.operator!r}") from error
        object.__setattr__(self, "operator", operator)

        if not isinstance(self.criteria, Mapping):
            raise TypeError("lens criteria must be a mapping")
        criteria = _freeze(self.criteria)
        children = tuple(self.children)

        if operator is LensOperator.ATOM:
            if not isinstance(self.source, str) or not self.source.strip():
                raise ValueError("an atomic lens requires a non-empty evidence source")
            if children:
                raise ValueError("an atomic lens cannot have children")
            object.__setattr__(self, "source", self.source.strip())
        else:
            if self.source is not None:
                raise ValueError("a composed lens cannot have an evidence source")
            if criteria:
                raise ValueError("a composed lens cannot have criteria")
            if not children:
                raise ValueError(f"{operator.value.upper()} requires at least one child")
            if not all(isinstance(child, Lens) for child in children):
                raise TypeError("lens children must be Lens values")
            if operator is LensOperator.NOT and len(children) != 1:
                raise ValueError("NOT requires exactly one child")
            if operator in {LensOperator.ALL, LensOperator.ANY}:
                children = tuple(sorted(children, key=lambda child: child.to_json()))

        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(self, "children", children)

    @classmethod
    def atom(
        cls,
        source: str,
        criteria: Mapping[str, Any] | None = None,
        **criterion: Any,
    ) -> "Lens":
        merged = dict(criteria or {})
        duplicate = merged.keys() & criterion.keys()
        if duplicate:
            raise ValueError(f"duplicate lens criteria: {sorted(duplicate)}")
        merged.update(criterion)
        return cls(LensOperator.ATOM, source=source, criteria=merged)

    @classmethod
    def all(cls, *children: "Lens") -> "Lens":
        return cls(LensOperator.ALL, children=children)

    @classmethod
    def all_of(cls, *children: "Lens") -> "Lens":
        return cls.all(*children)

    @classmethod
    def any(cls, *children: "Lens") -> "Lens":
        return cls(LensOperator.ANY, children=children)

    @classmethod
    def any_of(cls, *children: "Lens") -> "Lens":
        return cls.any(*children)

    @classmethod
    def not_(cls, child: "Lens") -> "Lens":
        return cls(LensOperator.NOT, children=(child,))

    @classmethod
    def negate(cls, child: "Lens") -> "Lens":
        return cls.not_(child)

    def to_dict(self) -> dict[str, Any]:
        if self.operator is LensOperator.ATOM:
            return {
                "type": self.operator.value,
                "source": self.source,
                "criteria": _canonicalize(self.criteria),
            }
        return {
            "type": self.operator.value,
            "children": [child.to_dict() for child in self.children],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def revision(self) -> str:
        return _revision(self.to_dict())

    def __hash__(self) -> int:
        return hash(self.revision)


def _names(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{label} must be a sequence of names, not a string")
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{label} names must be strings")
        name = value.strip()
        if not name:
            raise ValueError(f"{label} names cannot be empty")
        normalized.add(name)
    return tuple(sorted(normalized))


def _age(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer number of days")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    return value


@dataclass(frozen=True, slots=True)
class Disposition:
    """Tag/view additions and optional, explicitly delayed state changes."""

    keywords: tuple[str, ...] = ()
    albums: tuple[str, ...] = ()
    protect: bool = False
    hide_after_days: int | None = None
    archive_after_days: int | None = None
    stage_delete_after_days: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "keywords", _names(self.keywords, "keyword"))
        object.__setattr__(self, "albums", _names(self.albums, "album"))
        if not isinstance(self.protect, bool):
            raise TypeError("protect must be a boolean")

        for field_name in (
            "hide_after_days",
            "archive_after_days",
            "stage_delete_after_days",
        ):
            object.__setattr__(self, field_name, _age(getattr(self, field_name), field_name))

        if (
            self.archive_after_days is not None
            and self.stage_delete_after_days is not None
            and self.archive_after_days >= self.stage_delete_after_days
        ):
            raise ValueError("archive must occur before stage-delete")
        if self.protect and self.has_destructive_stages:
            raise ValueError("protect cannot be combined with hide or stage-delete")

    @property
    def has_destructive_stages(self) -> bool:
        """Visibility loss or deletion staging; making a backup copy is not destructive."""
        return self.hide_after_days is not None or self.stage_delete_after_days is not None

    @property
    def has_delayed_stages(self) -> bool:
        return any(
            age is not None
            for age in (
                self.hide_after_days,
                self.archive_after_days,
                self.stage_delete_after_days,
            )
        )

    @property
    def is_additive_only(self) -> bool:
        return not self.has_delayed_stages

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "disposition",
            "keywords": list(self.keywords),
            "albums": list(self.albums),
            "protect": self.protect,
            "hide_after_days": self.hide_after_days,
            "archive_after_days": self.archive_after_days,
            "stage_delete_after_days": self.stage_delete_after_days,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def revision(self) -> str:
        return _revision(self.to_dict())

    @property
    def target_fingerprint(self) -> str:
        targets = {
            "type": "disposition-targets",
            "keywords": list(self.keywords),
            "albums": list(self.albums),
            "protect": self.protect,
        }
        return _revision(targets)


class AutomationLevel(str, Enum):
    EXPLORE = "explore"
    HAND_PICK = "hand-pick"
    REVIEW_GROUP = "review-group"
    REVIEW_NEW = "review-new"
    SAMPLE_THEN_APPLY = "sample-then-apply"
    TRUSTED_ADDITIVE = "trusted-additive"
    DELAYED_STATE = "delayed-state"
    STAGE_ONLY = "stage-only"

    @property
    def is_review_or_manual(self) -> bool:
        return self in {
            AutomationLevel.EXPLORE,
            AutomationLevel.HAND_PICK,
            AutomationLevel.REVIEW_GROUP,
            AutomationLevel.REVIEW_NEW,
            AutomationLevel.SAMPLE_THEN_APPLY,
        }


class CadenceKind(str, Enum):
    MANUAL = "manual"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class Cadence:
    """A deliberately small schedule vocabulary; weekdays use Monday=0."""

    kind: CadenceKind
    interval_hours: int | None = None
    hour: int | None = None
    weekday: int | None = None

    def __post_init__(self) -> None:
        try:
            kind = CadenceKind(self.kind)
        except ValueError as error:
            raise ValueError(f"unknown cadence kind: {self.kind!r}") from error
        object.__setattr__(self, "kind", kind)

        if kind is CadenceKind.MANUAL:
            if any(value is not None for value in (self.interval_hours, self.hour, self.weekday)):
                raise ValueError("manual cadence cannot have schedule fields")
        elif kind is CadenceKind.HOURLY:
            if (
                isinstance(self.interval_hours, bool)
                or not isinstance(self.interval_hours, int)
                or self.interval_hours <= 0
            ):
                raise ValueError("hourly cadence requires a positive interval_hours")
            if self.hour is not None or self.weekday is not None:
                raise ValueError("hourly cadence only accepts interval_hours")
        elif kind is CadenceKind.DAILY:
            self._validate_hour()
            if self.interval_hours is not None or self.weekday is not None:
                raise ValueError("daily cadence only accepts hour")
        else:
            self._validate_hour()
            if (
                isinstance(self.weekday, bool)
                or not isinstance(self.weekday, int)
                or not 0 <= self.weekday <= 6
            ):
                raise ValueError("weekly cadence weekday must be 0 (Monday) through 6 (Sunday)")
            if self.interval_hours is not None:
                raise ValueError("weekly cadence does not accept interval_hours")

    def _validate_hour(self) -> None:
        if (
            isinstance(self.hour, bool)
            or not isinstance(self.hour, int)
            or not 0 <= self.hour <= 23
        ):
            raise ValueError("cadence hour must be between 0 and 23")

    @classmethod
    def manual(cls) -> "Cadence":
        return cls(CadenceKind.MANUAL)

    @classmethod
    def hourly(cls, interval: int = 1, *, interval_hours: int | None = None) -> "Cadence":
        if interval_hours is not None:
            if interval != 1:
                raise ValueError("provide either interval or interval_hours, not both")
            interval = interval_hours
        return cls(CadenceKind.HOURLY, interval_hours=interval)

    @classmethod
    def daily(cls, hour: int) -> "Cadence":
        return cls(CadenceKind.DAILY, hour=hour)

    @classmethod
    def weekly(cls, weekday: int, hour: int) -> "Cadence":
        return cls(CadenceKind.WEEKLY, weekday=weekday, hour=hour)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind.value,
            "interval_hours": self.interval_hours,
            "hour": self.hour,
            "weekday": self.weekday,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def revision(self) -> str:
        return _revision(self.to_dict())


@dataclass(frozen=True, slots=True)
class Collection:
    """A named, revisioned lens and its intended disposition."""

    id: str
    name: str
    lens: Lens
    disposition: Disposition
    description: str = ""

    def __post_init__(self) -> None:
        for field_name in ("id", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"collection {field_name} cannot be empty")
            object.__setattr__(self, field_name, value.strip())
        if not isinstance(self.lens, Lens):
            raise TypeError("collection lens must be a Lens")
        if not isinstance(self.disposition, Disposition):
            raise TypeError("collection disposition must be a Disposition")
        if not isinstance(self.description, str):
            raise TypeError("collection description must be a string")

    @classmethod
    def from_rule(
        cls,
        rule: "Rule",
        *,
        collection_id: str | None = None,
        description: str = "",
    ) -> "Collection":
        return collection_from_rule(
            rule,
            collection_id=collection_id,
            description=description,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "collection",
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "lens": self.lens.to_dict(),
            "disposition": self.disposition.to_dict(),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def revision(self) -> str:
        return _revision(self.to_dict())


@dataclass(frozen=True, slots=True)
class Routine:
    """A future execution policy; defining one does not schedule or run it."""

    collection: Collection
    automation_level: AutomationLevel
    cadence: Cadence = field(default_factory=Cadence.manual)

    def __post_init__(self) -> None:
        if not isinstance(self.collection, Collection):
            raise TypeError("routine collection must be a Collection")
        try:
            level = AutomationLevel(self.automation_level)
        except ValueError as error:
            raise ValueError(f"unknown automation level: {self.automation_level!r}") from error
        object.__setattr__(self, "automation_level", level)
        if not isinstance(self.cadence, Cadence):
            raise TypeError("routine cadence must be a Cadence")

        disposition = self.collection.disposition
        if (
            level is AutomationLevel.DELAYED_STATE
            and disposition.stage_delete_after_days is not None
        ):
            raise ValueError("delayed-state cannot include stage-delete")
        stage_delete_levels = {
            AutomationLevel.HAND_PICK,
            AutomationLevel.REVIEW_GROUP,
            AutomationLevel.REVIEW_NEW,
            AutomationLevel.STAGE_ONLY,
        }
        if (
            disposition.stage_delete_after_days is not None
            and level not in stage_delete_levels
        ):
            raise ValueError(
                "stage-delete requires hand-pick, full review, or stage-only automation"
            )
        if level is AutomationLevel.STAGE_ONLY and disposition.stage_delete_after_days is None:
            raise ValueError("stage-only requires a stage-delete disposition")
        if level is AutomationLevel.TRUSTED_ADDITIVE and not disposition.is_additive_only:
            raise ValueError("trusted-additive requires an additive-only disposition")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "routine",
            "collection": self.collection.to_dict(),
            "automation_level": self.automation_level.value,
            "cadence": self.cadence.to_dict(),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def revision(self) -> str:
        return _revision(self.to_dict())


def _legacy_names(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        names: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("legacy file target names must be strings")
            names.append(item)
        return tuple(names)
    raise TypeError("legacy file target must be a name or sequence of names")


def collection_from_rule(
    rule: "Rule",
    *,
    collection_id: str | None = None,
    description: str = "",
) -> Collection:
    """Convert one current config.Rule without changing or executing it."""
    atoms: list[Lens] = []
    if rule.query:
        atoms.append(Lens.atom("query", rule.query))
    if rule.detector:
        atoms.append(Lens.atom("detector", name=rule.detector))
    if rule.semantic:
        atoms.append(Lens.atom("semantic", rule.semantic))
    if rule.classify:
        atoms.append(Lens.atom("classify", rule.classify))
    if not atoms:
        raise ValueError(f"legacy rule {rule.name!r} has no lens evidence")

    file_targets = rule.file or {}
    disposition = Disposition(
        keywords=_legacy_names(file_targets.get("keyword")),
        albums=_legacy_names(file_targets.get("album")),
        hide_after_days=rule.hide.after_days if rule.hide is not None else None,
        archive_after_days=rule.archive.after_days if rule.archive is not None else None,
        stage_delete_after_days=rule.delete.after_days if rule.delete is not None else None,
    )
    return Collection(
        id=collection_id or rule.name,
        name=rule.name,
        lens=Lens.all(*atoms),
        disposition=disposition,
        description=description,
    )
