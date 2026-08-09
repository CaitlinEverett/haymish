"""Gallery persistence: the human judgments that survive a recompute.

Galleries are derived from photo timestamps and locations every run, so a
rename, a removed photo, or a rejected grouping has nowhere to live unless the
catalog keeps it. Without these, every decision evaporates on the next click.
"""

from __future__ import annotations

import pytest

from haymish.catalog import Catalog


@pytest.fixture()
def catalog(tmp_path):
    cat = Catalog(path=tmp_path / "catalog.db")
    yield cat
    cat.close()


def test_declines_survive_reopen(tmp_path):
    path = tmp_path / "catalog.db"
    first = Catalog(path=path)
    first.decline_gallery("2025-04-03", "Apr 3–8, 2025")
    first.close()

    second = Catalog(path=path)
    try:
        assert second.declined_galleries() == {"2025-04-03": "Apr 3–8, 2025"}
        second.undecline_gallery("2025-04-03")
        assert second.declined_galleries() == {}
    finally:
        second.close()


def test_gallery_name_is_remembered(catalog):
    catalog.set_gallery_name("2024-08-05-savannah", "Trips/Savannah Wedding")
    assert catalog.gallery_names()["2024-08-05-savannah"] == "Trips/Savannah Wedding"

    # Renaming replaces rather than accumulating.
    catalog.set_gallery_name("2024-08-05-savannah", "Trips/Savannah 2024")
    assert catalog.gallery_names()["2024-08-05-savannah"] == "Trips/Savannah 2024"


def test_excluded_photos_are_scoped_to_their_gallery(catalog):
    catalog.exclude_from_gallery("gallery-a", ["u1", "u2"])
    catalog.exclude_from_gallery("gallery-b", ["u3"])

    assert catalog.gallery_exclusions("gallery-a") == {"gallery-a": {"u1", "u2"}}
    everything = catalog.gallery_exclusions()
    assert everything["gallery-a"] == {"u1", "u2"}
    assert everything["gallery-b"] == {"u3"}
    # A photo removed from one gallery must stay available to others.
    assert "u1" not in everything["gallery-b"]


def test_excluding_the_same_photo_twice_is_idempotent(catalog):
    catalog.exclude_from_gallery("g", ["u1"])
    catalog.exclude_from_gallery("g", ["u1", "u2"])
    assert catalog.gallery_exclusions("g")["g"] == {"u1", "u2"}


def test_unknown_gallery_has_no_exclusions(catalog):
    assert catalog.gallery_exclusions("never-seen") == {}
