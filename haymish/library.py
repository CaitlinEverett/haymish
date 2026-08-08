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


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".gif", ".webp"}


def image_source(photo) -> str | None:
    """Best local IMAGE representing this asset, or None.

    For photos: smallest derivative first (faster to caption/thumbnail), original
    as fallback. For videos: the poster-frame image derivative Photos generates —
    never the video file itself, which vision models and PIL can't open. This is
    what makes captioning, classify, and review thumbnails work uniformly for
    both photos and videos.
    """
    derivatives = getattr(photo, "path_derivatives", None) or []
    for d in derivatives:
        if Path(d).suffix.lower() in _IMAGE_SUFFIXES:
            return d
    if getattr(photo, "ismovie", False):
        return None  # a video's original is not an image; no poster frame -> no image
    return getattr(photo, "path", None)


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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Used for `near = {...}` geo queries."""
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def place_name(photo) -> str:
    """Flattened place text ("Chicago, Illinois, United States") or "".

    osxphotos' PlaceInfo shape varies across macOS versions, so this reads
    several plausible attributes rather than trusting one.
    """
    place = getattr(photo, "place", None)
    if place is None:
        return ""
    for attr in ("address_str", "name"):
        value = getattr(place, attr, None)
        if value:
            return str(value)
    names = getattr(place, "names", None)
    if names is not None:
        parts = []
        for attr in ("city", "sub_administrative_area", "state_province", "country"):
            value = getattr(names, attr, None)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value:
                parts.append(str(value))
        if parts:
            return ", ".join(parts)
    return ""


def coordinates(photo) -> tuple[float, float] | None:
    lat = getattr(photo, "latitude", None)
    lon = getattr(photo, "longitude", None)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _as_aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _parse_date(value) -> dt.datetime | None:
    """Accept a TOML date/datetime or an ISO 'YYYY-MM-DD' string."""
    if isinstance(value, dt.datetime):
        return _as_aware(value)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        try:
            return _as_aware(dt.datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _camera_text(photo) -> str:
    exif = getattr(photo, "exif_info", None)
    if exif is None:
        return ""
    parts = [getattr(exif, attr, None) for attr in ("camera_make", "camera_model")]
    return " ".join(str(p) for p in parts if p)


def _lens_text(photo) -> str:
    exif = getattr(photo, "exif_info", None)
    return str(getattr(exif, "lens_model", None) or "") if exif else ""


def matches_query(photo, query: dict, now: dt.datetime | None = None) -> bool:
    """Apply a rule's query table to one photo.

    Everything here reads metadata osxphotos has already loaded -- no network,
    no inference -- so these filters are effectively free compared to the
    semantic and classify stages that run after them. That's why the expensive
    stages should always be paired with a query that narrows first.
    """
    if query.get("screenshot") is not None and bool(getattr(photo, "screenshot", False)) != query["screenshot"]:
        return False
    if query.get("movie") is not None and bool(getattr(photo, "ismovie", False)) != query["movie"]:
        return False
    if query.get("screen_recording") is not None and \
            bool(getattr(photo, "screen_recording", False)) != query["screen_recording"]:
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
    # An explicit photo set. Mainly for ephemeral rules built in code (e.g. the
    # per-event rules `haymish galleries` constructs), so that work still flows
    # through the normal preview -> review -> apply path instead of a side door.
    if "uuids" in query and photo.uuid not in set(query["uuids"]):
        return False

    # -- absolute dates ------------------------------------------------------
    # min/max_age_days is relative and drifts every run; a shoot or a trip is a
    # fixed window, so professional rules need real dates. `before` is exclusive
    # of the following day only if a bare date was given (TOML dates parse to
    # midnight), so "before = 2026-03-07" includes all of the 6th and nothing of
    # the 7th -- which is what people mean.
    taken = getattr(photo, "date", None)
    if "after" in query or "before" in query:
        if taken is None:
            return False
        taken_aware = _as_aware(taken)
        after = _parse_date(query.get("after"))
        before = _parse_date(query.get("before"))
        if after is not None and taken_aware < after:
            return False
        if before is not None and taken_aware >= before:
            return False

    # -- place and coordinates ------------------------------------------------
    if "place" in query:
        wanted = str(query["place"]).lower()
        if wanted not in place_name(photo).lower():
            return False
    if query.get("has_location") is not None:
        if (coordinates(photo) is not None) != bool(query["has_location"]):
            return False
    if "near" in query:
        spec = query["near"]
        here = coordinates(photo)
        if here is None:
            return False
        radius = float(spec.get("km", 25))
        if haversine_km(here[0], here[1], float(spec["lat"]), float(spec["lon"])) > radius:
            return False

    # -- people ---------------------------------------------------------------
    persons = [p for p in (getattr(photo, "persons", None) or [])
               if p and p != "_UNKNOWN_"]  # osxphotos' sentinel for unnamed faces
    if "persons" in query and not set(query["persons"]) & set(persons):
        return False
    if query.get("has_faces") is not None:
        has_faces = bool(getattr(photo, "face_info", None) or persons)
        if has_faces != bool(query["has_faces"]):
            return False

    # -- quality (culling) ----------------------------------------------------
    # Apple's own curation scores. Absent on some libraries/macOS versions, so a
    # missing score fails the filter rather than silently passing everything.
    if "min_score" in query:
        overall = score(photo, "overall")
        if overall is None or overall < float(query["min_score"]):
            return False
    if "max_failure" in query:
        failure = score(photo, "failure")
        if failure is None or failure > float(query["max_failure"]):
            return False
    if "min_rating" in query:
        rating = getattr(photo, "rating", None)
        if rating is None or int(rating) < int(query["min_rating"]):
            return False

    # -- capture --------------------------------------------------------------
    if "camera" in query and str(query["camera"]).lower() not in _camera_text(photo).lower():
        return False
    if "lens" in query and str(query["lens"]).lower() not in _lens_text(photo).lower():
        return False
    for flag, attr in (("raw", "israw"), ("burst", "burst"), ("live_photo", "live_photo")):
        if query.get(flag) is not None and bool(getattr(photo, attr, False)) != query[flag]:
            return False
    return True
