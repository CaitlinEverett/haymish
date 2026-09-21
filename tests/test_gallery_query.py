"""Gallery semantic query: scoring and ranking galleries by AI index match.

All embedding and model operations are faked or use temporary databases.
Nothing can reach Ollama, Photos, the real catalog, or a backup volume.
"""

from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass, field

import numpy as np
import pytest

from haymish.ai.search import gallery_scores, semantic_scores, top_matches
from haymish.catalog import Catalog
from haymish.events import Event


# -- helpers ------------------------------------------------------------------

def _make_event(key, uuids, label="test", place=None,
                start=None, end=None):
    start = start or dt.datetime(2025, 6, 1)
    end = end or start
    return Event(
        key=key, label=label, start=start, end=end,
        uuids=uuids, place=place,
        lat=None, lon=None, photo_count=len(uuids),
    )


def _vec(values):
    """float32 bytes from a list of floats."""
    return struct.pack(f"{len(values)}f", *values)


def _unit_vec(values):
    """Normalize then pack to float32 bytes."""
    arr = np.array(values, dtype=np.float32)
    arr /= np.linalg.norm(arr) or 1.0
    return arr.tobytes()


@pytest.fixture()
def catalog(tmp_path):
    cat = Catalog(path=tmp_path / "catalog.db")
    yield cat
    cat.close()


# -- gallery_scores -----------------------------------------------------------

class TestGalleryScores:
    """gallery_scores ranks events by mean member score."""

    def test_ranks_by_mean_member_score(self):
        scores = {"u1": 0.9, "u2": 0.8, "u3": 0.2, "u4": 0.1}
        beach = _make_event("beach", ["u1", "u2"])    # mean 0.85
        city = _make_event("city", ["u3", "u4"])       # mean 0.15

        ranked = gallery_scores(scores, [city, beach])
        assert ranked[0][0].key == "beach"
        assert ranked[1][0].key == "city"
        assert abs(ranked[0][1] - 0.85) < 0.01
        assert abs(ranked[1][1] - 0.15) < 0.01

    def test_unindexed_members_get_zero_score(self):
        scores = {"u1": 0.7}
        ev = _make_event("mixed", ["u1", "u2", "u3"])

        ranked = gallery_scores(scores, [ev])
        _event, mean, indexed, total = ranked[0]
        assert indexed == 1
        assert total == 3
        assert abs(mean - 0.7) < 0.01

    def test_fully_unindexed_event_has_zero_score(self):
        scores = {"other": 0.9}
        ev = _make_event("dark", ["u1", "u2"])

        ranked = gallery_scores(scores, [ev])
        _, mean, indexed, total = ranked[0]
        assert indexed == 0
        assert mean == 0.0

    def test_empty_events_list(self):
        assert gallery_scores({"u1": 0.5}, []) == []

    def test_empty_scores_dict(self):
        ev = _make_event("lonely", ["u1"])
        ranked = gallery_scores({}, [ev])
        assert len(ranked) == 1
        assert ranked[0][1] == 0.0

    def test_returns_coverage_counts(self):
        scores = {"u1": 0.5, "u2": 0.6}
        ev = _make_event("partial", ["u1", "u2", "u3"])

        ranked = gallery_scores(scores, [ev])
        _, _, indexed, total = ranked[0]
        assert indexed == 2
        assert total == 3


class TestSemanticScoresIntegration:
    """semantic_scores against a real temp catalog with stored embeddings."""

    def test_returns_scores_for_indexed_photos(self, catalog, monkeypatch):
        dim = 4
        # Store embeddings for two photos.
        catalog.put_embedding("u1", "test-embed", _unit_vec([1, 0, 0, 0]), dim)
        catalog.put_embedding("u2", "test-embed", _unit_vec([0, 1, 0, 0]), dim)

        # Mock the Ollama embed call to return a query vector.
        query_vec = [1.0, 0.0, 0.0, 0.0]  # should match u1 closely
        monkeypatch.setattr(
            "haymish.ai.search.ollama_client.embed",
            lambda _host, _model, _texts: [query_vec],
        )

        from types import SimpleNamespace
        config = SimpleNamespace(
            ollama_host="http://offline.invalid",
            ai_embed_model="test-embed",
        )

        result = semantic_scores(config, catalog, "test query")
        assert "u1" in result
        assert "u2" in result
        # u1 should score much higher (aligned with query vector)
        assert result["u1"] > result["u2"]
        assert result["u1"] > 0.9

    def test_empty_index_returns_empty(self, catalog, monkeypatch):
        from types import SimpleNamespace
        config = SimpleNamespace(
            ollama_host="http://offline.invalid",
            ai_embed_model="test-embed",
        )
        result = semantic_scores(config, catalog, "anything")
        assert result == {}


class TestGalleryQueryEndToEnd:
    """End-to-end: score galleries via catalog embeddings, then rank events."""

    def test_query_ranks_galleries_by_semantic_relevance(self, catalog, monkeypatch):
        dim = 4
        # "beach" photos: embeddings aligned with [1, 0, 0, 0]
        for i in range(5):
            catalog.put_embedding(f"beach-{i}", "test-embed",
                                  _unit_vec([1, 0, 0.1 * i, 0]), dim)
        # "city" photos: embeddings aligned with [0, 1, 0, 0]
        for i in range(5):
            catalog.put_embedding(f"city-{i}", "test-embed",
                                  _unit_vec([0, 1, 0.1 * i, 0]), dim)

        beach_event = _make_event("beach-trip", [f"beach-{i}" for i in range(5)],
                                   label="Beach · Jun 1–3")
        city_event = _make_event("city-trip", [f"city-{i}" for i in range(5)],
                                  label="City · Jul 10–12")

        # Query for "beach" — vector aligned with beach embeddings
        query_vec = [1.0, 0.0, 0.0, 0.0]
        monkeypatch.setattr(
            "haymish.ai.search.ollama_client.embed",
            lambda _host, _model, _texts: [query_vec],
        )

        from types import SimpleNamespace
        config = SimpleNamespace(
            ollama_host="http://offline.invalid",
            ai_embed_model="test-embed",
        )

        scores = semantic_scores(config, catalog, "beach vacation")
        ranked = gallery_scores(scores, [city_event, beach_event])

        assert ranked[0][0].key == "beach-trip"
        assert ranked[1][0].key == "city-trip"
        assert ranked[0][1] > ranked[1][1]

        # All members indexed
        for _, _, indexed, total in ranked:
            assert indexed == total == 5

    def test_partially_indexed_gallery_still_scored(self, catalog, monkeypatch):
        dim = 4
        catalog.put_embedding("u1", "test-embed", _unit_vec([1, 0, 0, 0]), dim)
        # u2 is NOT indexed

        ev = _make_event("partial", ["u1", "u2"])

        query_vec = [1.0, 0.0, 0.0, 0.0]
        monkeypatch.setattr(
            "haymish.ai.search.ollama_client.embed",
            lambda _host, _model, _texts: [query_vec],
        )

        from types import SimpleNamespace
        config = SimpleNamespace(
            ollama_host="http://offline.invalid",
            ai_embed_model="test-embed",
        )

        scores = semantic_scores(config, catalog, "test")
        ranked = gallery_scores(scores, [ev])
        _, mean, indexed, total = ranked[0]
        assert indexed == 1
        assert total == 2
        assert mean > 0.9
