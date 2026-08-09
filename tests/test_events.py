"""Event clustering, significance ranking, and place-name borrowing.

These pin behaviors that were each wrong at least once against a real 28,000-photo
library: ranking by date buried the notable trips, borrowing at the clustering
radius made neighbouring cities adopt each other's names, and a thin multi-day
trip outranked a 347-photo wedding.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from haymish.events import Event, cluster_events, haversine_km, pick_representative

CHICAGO = (41.88, -87.63)
NAPERVILLE = (41.75, -88.15)   # ~45 km from Chicago
PARIS = (48.85, 2.35)


@dataclass
class FakeScore:
    overall: float = 0.5
    failure: float = 0.0


@dataclass
class FakePhoto:
    uuid: str
    date: dt.datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    place: object = None
    favorite: bool = False
    score: object = None
    width: int = 1000
    height: int = 1000
    albums: list = field(default_factory=list)


def _run(start, count, coords=(None, None), place=None, step_hours=2, prefix="p"):
    lat, lon = coords
    return [
        FakePhoto(f"{prefix}{i}", start + dt.timedelta(hours=i * step_hours), lat, lon, place)
        for i in range(count)
    ]


def test_haversine_matches_known_distance():
    assert 1100 < haversine_km(41.88, -87.63, 40.71, -74.01) < 1200


def test_contiguous_run_is_one_event():
    events = cluster_events(_run(dt.datetime(2024, 5, 1, 9), 8, CHICAGO, "Chicago"), min_photos=5)
    assert len(events) == 1
    assert events[0].photo_count == 8


def test_trips_weeks_apart_split():
    photos = _run(dt.datetime(2024, 5, 1, 9), 8, CHICAGO, "Chicago", prefix="a")
    photos += _run(dt.datetime(2024, 7, 1, 9), 8, CHICAGO, "Chicago", prefix="b")
    assert len(cluster_events(photos, min_photos=5)) == 2


def test_distance_splits_same_day_but_missing_gps_never_does():
    same_day = dt.datetime(2024, 5, 1, 9)
    far = _run(same_day, 6, CHICAGO, "Chicago", step_hours=1, prefix="chi")
    far += _run(same_day + dt.timedelta(hours=7), 6, PARIS, "Paris", step_hours=1, prefix="par")
    assert len(cluster_events(far, min_photos=5)) == 2

    # Same timing, no coordinates at all: must stay one event rather than
    # splitting on unknown location -- most libraries lack GPS on most photos.
    no_gps = _run(same_day, 12, step_hours=1, prefix="x")
    assert len(cluster_events(no_gps, min_photos=5)) == 1


def test_photos_without_dates_are_excluded():
    photos = _run(dt.datetime(2024, 5, 1, 9), 6, CHICAGO, "Chicago")
    photos.append(FakePhoto("undated", None))
    events = cluster_events(photos, min_photos=5)
    assert sum(e.photo_count for e in events) == 6


def test_min_photos_drops_small_clusters():
    photos = _run(dt.datetime(2024, 5, 1, 9), 3, CHICAGO, "Chicago")
    assert cluster_events(photos, min_photos=5) == []


def test_significance_puts_a_busy_day_above_a_thin_long_trip():
    """The weights were wrong once: a 20-photo three-day trip outranked a
    347-photo single day. Shooting 347 photos in a day is an occasion."""
    def event(n, days=1, place=None, lat=None):
        start = dt.datetime(2024, 1, 1)
        return Event("k", "l", start, start + dt.timedelta(days=days - 1),
                     ["u"] * n, place, lat, None, n)

    busy_day = event(347)
    thin_trip = event(20, days=3, place="Chicago", lat=41.9)
    assert busy_day.significance > thin_trip.significance


def test_place_borrowing_is_city_scale_not_trip_scale():
    """Borrowing used the 60 km clustering radius, so Naperville (45 km away)
    became '~Chicago'. Naming is a tighter judgment than trip membership."""
    photos = _run(dt.datetime(2024, 5, 1, 9), 8, CHICAGO, "Chicago", prefix="chi")
    photos += _run(dt.datetime(2025, 4, 3, 9), 8, NAPERVILLE, None, prefix="nap")
    photos += _run(dt.datetime(2025, 6, 1, 9), 8, (41.89, -87.62), None, prefix="dt")

    by_start = {e.start.date(): e for e in cluster_events(photos, min_photos=5)}
    assert by_start[dt.date(2025, 4, 3)].place is None, "borrowed across cities"
    assert by_start[dt.date(2025, 6, 1)].place == "~Chicago", "failed to borrow next door"


def test_borrowed_names_do_not_seed_further_borrowing():
    """Otherwise one guess propagates outward and hardens into apparent fact."""
    photos = _run(dt.datetime(2024, 5, 1, 9), 8, CHICAGO, "Chicago", prefix="chi")
    photos += _run(dt.datetime(2025, 1, 1, 9), 8, (41.90, -87.62), None, prefix="near")
    photos += _run(dt.datetime(2025, 2, 1, 9), 8, (42.02, -87.67), None, prefix="far")
    events = cluster_events(photos, min_photos=5, borrow_km=15)
    borrowed = [e.place for e in events if e.place and e.place.startswith("~")]
    assert all(p == "~Chicago" for p in borrowed)


def test_pick_representative_prefers_a_human_favorite_over_any_score():
    photos = [
        FakePhoto("low", score=FakeScore(0.2)),
        FakePhoto("faved", favorite=True, score=FakeScore(0.3)),
        FakePhoto("high", score=FakeScore(0.9)),
    ]
    assert pick_representative(photos).uuid == "faved"


def test_pick_representative_penalises_failed_shots_and_handles_empty():
    photos = [
        FakePhoto("ok", score=FakeScore(0.7, 0.0)),
        FakePhoto("sharp_but_failed", score=FakeScore(0.95, 0.9)),
    ]
    assert pick_representative(photos).uuid == "ok"
    assert pick_representative([]) is None


def test_mixed_naive_and_aware_datetimes_do_not_crash():
    utc = dt.timezone.utc
    photos = [
        FakePhoto("a", dt.datetime(2024, 5, 1, 9)),
        FakePhoto("b", dt.datetime(2024, 5, 1, 10, tzinfo=utc)),
        FakePhoto("c", dt.datetime(2024, 5, 1, 11)),
        FakePhoto("d", dt.datetime(2024, 5, 1, 12, tzinfo=utc)),
        FakePhoto("e", dt.datetime(2024, 5, 1, 13)),
    ]
    assert cluster_events(photos, min_photos=5)[0].photo_count == 5
