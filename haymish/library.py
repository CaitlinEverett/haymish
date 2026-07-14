"""Thin wrapper around osxphotos: load once, query with plain predicates.

Loading PhotosDB takes seconds-to-minutes on big libraries, so everything in a
command shares one instance. Queries are in-memory predicate filters rather than
osxphotos QueryOptions — libraries in the tens of thousands filter instantly and
this keeps rule semantics in one place.

Attribute access is defensive (getattr with fallbacks) because Photos' database
schema churns across macOS versions and some fields (search index, scores) can
be absent — see doctor's schema check.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

FRONT_CAMERA_MARKERS = ("front", "truedepth")


def load_photosdb(library: Path):
    import osxphotos

    return osxphotos.PhotosDB(dbfile=str(library))


def all_photos(photosdb) -> list:
    """Everything not in the trash, including hidden (lifecycle stages need them)."""
    return photosdb.photos(intrash=False)


def photo_age_days(photo, now: dt.datetime | None = None) -> float | None:
    date = getattr(photo, "date", None)
    if date is None:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=dt.timezone.utc)
    return (now - date).total_seconds() / 86400


def is_selfie(photo) -> bool:
    """Photos' own selfie flag, with EXIF front-camera fallback."""
    if getattr(photo, "selfie", False):
        return True
    exif = getattr(photo, "exif_info", None)
    lens = (getattr(exif, "lens_model", None) or "") if exif else ""
    return any(m in lens.lower() for m in FRONT_CAMERA_MARKERS)


def labels(photo) -> list[str]:
    return [l.lower() for l in (getattr(photo, "labels_normalized", None) or [])]


def detected_text(photo) -> str:
    """Photos' own indexed OCR text (free — no Vision pass needed).

    Availability varies by macOS version; returns "" when the search index
    doesn't expose it. scan reports coverage so we know whether to fall back
    to our own Vision OCR.
    """
    si = getattr(photo, "search_info", None)
    if si is None:
        return ""
    text = getattr(si, "detected_text", None)
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        return " ".join(str(t) for t in text)
    return str(text)


def score(photo, field: str) -> float | None:
    s = getattr(photo, "score", None)
    if s is None:
        return None
    v = getattr(s, field, None)
    return float(v) if v is not None else None


def matches_query(photo, query: dict, now: dt.datetime | None = None) -> bool:
    """Apply a rule's query table to one photo."""
    if query.get("screenshot") is not None and bool(getattr(photo, "screenshot", False)) != query["screenshot"]:
        return False
    if query.get("selfie") is not None and is_selfie(photo) != query["selfie"]:
        return False
    if query.get("favorite") is not None and bool(getattr(photo, "favorite", False)) != query["favorite"]:
        return False
    if query.get("hidden") is not None and bool(getattr(photo, "hidden", False)) != query["hidden"]:
        return False
    age = photo_age_days(photo, now)
    if "min_age_days" in query and (age is None or age < query["min_age_days"]):
        return False
    if "max_age_days" in query and (age is None or age > query["max_age_days"]):
        return False
    if "albums" in query and not set(query["albums"]) & set(photo.albums or []):
        return False
    if "exclude_albums" in query and set(query["exclude_albums"]) & set(photo.albums or []):
        return False
    if "keywords" in query and not set(query["keywords"]) & set(photo.keywords or []):
        return False
    return True
