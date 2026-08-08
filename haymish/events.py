"""Time-and-place event clustering: group photos into trips, shoots, and outings.

Uses only metadata Photos already gives us — date, latitude, longitude, place.
No AI, no network, no local originals needed, so this runs over a whole library
in milliseconds and is the shared primitive behind auto-galleries (a business
traveler's "Chicago, Mar 3-6", a photographer's shoot, an afternoon out).

The rule is a single pass over date-sorted photos: cut a new event when the gap
to the previous photo is too long, or when the photo lands too far from the
running centroid of the current cluster. Distance only ever *splits* — it never
merges — and it is skipped entirely unless both sides have real coordinates,
because most photos in a real library have no GPS at all and a naive distance
test would shatter every event into singletons.

Attribute access is defensive (getattr with fallbacks) to match library.py:
Photos' schema churns across macOS versions and `place` in particular is a rich
object on some versions, a bare string on others, and absent on many photos.

Timezones: photo dates arrive naive or aware depending on the library and the
macOS version. Gap math is done in UTC so it is always absolute, but Event.start
and Event.end are timezone-naive *local wall clock* — the time the photographer
experienced. That keeps "Mar 3-6" from sliding to "Mar 4-7" for evening photos
in a western timezone, and guarantees a caller can subtract two Event datetimes
without a naive/aware TypeError.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

EARTH_RADIUS_KM = 6371.0088

# Photos sometimes stores a missing fix as 0.0/0.0 ("Null Island") rather than
# NULL. Treating that as a real location would drag centroids into the Atlantic.
_NULL_ISLAND = (0.0, 0.0)

# Candidate attributes on a PlaceInfo, most specific first — a city beats a
# country for labelling an event.
_PLACE_NAME_FIELDS = ("city", "sub_administrative_area", "state_province", "country")


@dataclass
class Event:
    """A contiguous run of photos in one place and one stretch of time."""

    key: str                 # stable slug, e.g. "2026-03-03-chicago"
    label: str               # human label, e.g. "Chicago · Mar 3–6, 2026"
    start: dt.datetime       # naive, local wall clock
    end: dt.datetime         # naive, local wall clock
    uuids: list[str]
    place: str | None        # best-guess place name if any photo had one
    lat: float | None        # centroid of located photos, if any
    lon: float | None
    photo_count: int

    @property
    def days(self) -> int:
        """Calendar days the event spans, inclusive (a single day is 1)."""
        return (self.end.date() - self.start.date()).days + 1


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres. Pure stdlib math."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


# --------------------------------------------------------------------------
# metadata extraction (defensive — see module docstring)
# --------------------------------------------------------------------------

def _as_datetime(value) -> dt.datetime | None:
    """Coerce a photo's date to a datetime, or None if it hasn't got a usable one."""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):  # bare date: treat as midnight rather than drop
        return dt.datetime.combine(value, dt.time.min)
    return None


def _as_utc(date: dt.datetime) -> dt.datetime:
    """Absolute time for gap math. Naive is assumed UTC, matching photo_age_days."""
    if date.tzinfo is None:
        return date.replace(tzinfo=dt.timezone.utc)
    return date.astimezone(dt.timezone.utc)


def _as_wall(date: dt.datetime) -> dt.datetime:
    """Local wall clock for display. Drops the offset without shifting the clock."""
    return date.replace(tzinfo=None)


def _coords(photo) -> tuple[float | None, float | None]:
    """(lat, lon) if the photo carries a plausible fix, else (None, None)."""
    lat = getattr(photo, "latitude", None)
    lon = getattr(photo, "longitude", None)
    if lat is None or lon is None:
        return None, None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if math.isnan(lat) or math.isnan(lon) or math.isinf(lat) or math.isinf(lon):
        return None, None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None, None
    if (lat, lon) == _NULL_ISLAND:
        return None, None
    return lat, lon


def place_name(photo) -> str | None:
    """Best short place name for a photo.

    `photo.place` is an osxphotos PlaceInfo on most macOS versions (with a
    `.names` record of city/state/country), a plain string on some, and None on
    photos with no reverse-geocode. Prefer the most specific name available so
    an event reads "Chicago" rather than "United States".
    """
    place = getattr(photo, "place", None)
    if place is None:
        return None
    if isinstance(place, str):
        return _first_segment(place)

    names = getattr(place, "names", None)
    if names is not None:
        for attr in _PLACE_NAME_FIELDS:
            value = getattr(names, attr, None)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value:
                return str(value).strip() or None

    for attr in ("name", "address_str", "country"):
        value = getattr(place, attr, None)
        if value:
            return _first_segment(str(value))
    return None


def _first_segment(text: str) -> str | None:
    """"Chicago, Illinois, United States" -> "Chicago"."""
    head = text.split(",")[0].strip()
    return head or None


def slugify(text: str) -> str:
    """Lowercase ascii hyphen slug, safe for filenames and Photos album names."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------

@dataclass
class _Cluster:
    """Mutable accumulator; becomes an Event once it survives the min_photos cut."""

    uuids: list[str] = field(default_factory=list)
    places: Counter = field(default_factory=Counter)
    lat_sum: float = 0.0
    lon_sum: float = 0.0
    located: int = 0
    start_wall: dt.datetime | None = None
    end_wall: dt.datetime | None = None
    last_utc: dt.datetime | None = None

    @property
    def centroid(self) -> tuple[float | None, float | None]:
        if not self.located:
            return None, None
        return self.lat_sum / self.located, self.lon_sum / self.located

    def add(self, uuid: str, utc: dt.datetime, wall: dt.datetime,
            lat: float | None, lon: float | None, place: str | None) -> None:
        self.uuids.append(uuid)
        if self.start_wall is None or wall < self.start_wall:
            self.start_wall = wall
        if self.end_wall is None or wall > self.end_wall:
            self.end_wall = wall
        self.last_utc = utc
        if lat is not None and lon is not None:
            self.lat_sum += lat
            self.lon_sum += lon
            self.located += 1
        if place:
            self.places[place] += 1


def cluster_events(photos, *, max_gap_hours: float = 14.0, max_km: float = 60.0,
                   min_photos: int = 5) -> list[Event]:
    """Group photos into events by time proximity and, where known, location.

    Args:
        photos: any iterable of photo-like objects exposing uuid/date and
            optionally latitude/longitude/place. Photos with no usable date are
            skipped and appear in no event.
        max_gap_hours: a gap longer than this starts a new event. The 14h default
            spans a night's sleep without merging two separate days out.
        max_km: a photo further than this from the current cluster's running
            centroid starts a new event — but only when both the photo and the
            cluster have real coordinates. Location never merges, only splits.
        min_photos: clusters smaller than this are dropped as noise.

    Returns:
        Events in chronological order.
    """
    dated: list[tuple[dt.datetime, dt.datetime, object]] = []
    for photo in photos:
        date = _as_datetime(getattr(photo, "date", None))
        if date is None:
            continue
        dated.append((_as_utc(date), _as_wall(date), photo))

    # Sort on absolute time; the wall clock is only ever used for display.
    dated.sort(key=lambda row: row[0])

    clusters: list[_Cluster] = []
    current: _Cluster | None = None
    for utc, wall, photo in dated:
        lat, lon = _coords(photo)
        if current is not None:
            gap_hours = (utc - current.last_utc).total_seconds() / 3600.0
            split = gap_hours > max_gap_hours
            if not split and lat is not None:
                c_lat, c_lon = current.centroid
                if c_lat is not None and haversine_km(lat, lon, c_lat, c_lon) > max_km:
                    split = True
            if split:
                clusters.append(current)
                current = None
        if current is None:
            current = _Cluster()
        current.add(
            uuid=str(getattr(photo, "uuid", "") or ""),
            utc=utc, wall=wall, lat=lat, lon=lon, place=place_name(photo),
        )
    if current is not None:
        clusters.append(current)

    events: list[Event] = []
    used_keys: set[str] = set()
    for cluster in clusters:
        if len(cluster.uuids) < min_photos:
            continue
        place = cluster.places.most_common(1)[0][0] if cluster.places else None
        lat, lon = cluster.centroid
        start, end = cluster.start_wall, cluster.end_wall
        events.append(Event(
            key=_unique_key(start, place, used_keys),
            label=format_label(start, end, place),
            start=start,
            end=end,
            uuids=list(cluster.uuids),
            place=place,
            lat=lat,
            lon=lon,
            photo_count=len(cluster.uuids),
        ))
    return events


def _unique_key(start: dt.datetime, place: str | None, used: set[str]) -> str:
    """Stable slug; disambiguated with -2, -3 if a day+place repeats."""
    base = f"{start:%Y-%m-%d}"
    if place:
        slug = slugify(place)
        if slug:
            base = f"{base}-{slug}"
    key, n = base, 1
    while key in used:
        n += 1
        key = f"{base}-{n}"
    used.add(key)
    return key


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------

EN_DASH = "–"


def format_date_range(start: dt.datetime, end: dt.datetime) -> str:
    """"Mar 3, 2026" / "Mar 3–6, 2026" / "Mar 30 – Apr 2, 2026" / cross-year."""
    if start.date() == end.date():
        return f"{start:%b} {start.day}, {start.year}"
    if start.year != end.year:
        return (f"{start:%b} {start.day}, {start.year} {EN_DASH} "
                f"{end:%b} {end.day}, {end.year}")
    if start.month != end.month:
        return f"{start:%b} {start.day} {EN_DASH} {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}{EN_DASH}{end.day}, {end.year}"


def format_label(start: dt.datetime, end: dt.datetime, place: str | None) -> str:
    dates = format_date_range(start, end)
    return f"{place} · {dates}" if place else dates


# --------------------------------------------------------------------------
# display
# --------------------------------------------------------------------------

def summarize(events: list[Event], limit: int = 20) -> str:
    """Short plain-text table for CLI display."""
    if not events:
        return "No events found."

    shown = events[:limit] if limit and limit > 0 else events
    rows = [(e.label, f"{e.photo_count}", e.key) for e in shown]
    headers = ("EVENT", "PHOTOS", "KEY")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    lines = [f"{headers[0]:<{widths[0]}}  {headers[1]:>{widths[1]}}  {headers[2]}"]
    lines += [f"{label:<{widths[0]}}  {count:>{widths[1]}}  {key}"
              for label, count, key in rows]

    total_photos = sum(e.photo_count for e in events)
    footer = f"{len(events)} event{'s' if len(events) != 1 else ''}, {total_photos} photos"
    if len(shown) < len(events):
        footer += f" (showing first {len(shown)})"
    lines.append(footer)
    return "\n".join(lines)
