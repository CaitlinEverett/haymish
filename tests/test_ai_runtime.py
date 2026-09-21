"""AI runtime circuit breakers and caption request contracts.

All model, catalog, and image operations are faked or use temporary files. Nothing
can reach Ollama, Photos, the real catalog, or a backup volume.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from haymish.ai import indexer, ollama_client
from haymish.ai.ollama_client import AIError, OllamaHTTPError


def config():
    return SimpleNamespace(
        ollama_host="http://offline.invalid",
        ai_vision_model="qwen3-vl:8b",
        ai_embed_model="embed-test",
    )


def photo(uuid: str = "u1"):
    return SimpleNamespace(
        uuid=uuid,
        screenshot=True,
        ismovie=False,
        date=None,
        original_filename=f"{uuid}.png",
    )


def catalog():
    fake = MagicMock()
    fake.embedded_uuids.return_value = set()
    fake.captioned_uuids.return_value = set()
    fake.get_caption.return_value = None
    return fake


def test_generate_does_not_retry_a_timeout_when_think_is_explicit():
    with patch(
        "haymish.ai.ollama_client._post", side_effect=AIError("request timed out")
    ) as post:
        with pytest.raises(AIError, match="timed out"):
            ollama_client.generate(
                "http://offline.invalid", "model", "prompt", think=False
            )

    post.assert_called_once()


def test_generate_retries_only_an_explicit_think_field_rejection():
    payloads: list[dict] = []

    def post(_host, _path, payload, _timeout):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            raise OllamaHTTPError(400, 'json: unknown field "think"')
        return {"response": "ok"}

    with patch("haymish.ai.ollama_client._post", side_effect=post):
        assert ollama_client.generate(
            "http://offline.invalid", "model", "prompt", think=False
        ) == "ok"

    assert payloads[0]["think"] is False
    assert "think" not in payloads[1]


@pytest.mark.parametrize("status", [400, 422, 500])
def test_generate_does_not_retry_unrelated_http_errors(status: int):
    with patch(
        "haymish.ai.ollama_client._post",
        side_effect=OllamaHTTPError(status, "server overloaded"),
    ) as post:
        with pytest.raises(OllamaHTTPError, match="server overloaded"):
            ollama_client.generate(
                "http://offline.invalid", "model", "prompt", think=False
            )

    post.assert_called_once()


def test_generate_rejects_an_empty_response_as_failed_work():
    with patch(
        "haymish.ai.ollama_client._post",
        return_value={"response": "  ", "done_reason": "length"},
    ):
        with pytest.raises(AIError, match="spent its output budget on hidden reasoning"):
            ollama_client.generate("http://offline.invalid", "model", "prompt")


def test_caption_photo_disables_thinking_and_grounds_screenshot_kind(tmp_path):
    source = tmp_path / "preview.jpg"
    source.write_bytes(b"fake image bytes")
    asset = photo()

    with (
        patch("haymish.ai.indexer._caption_source", return_value=str(source)),
        patch("haymish.ai.indexer.ollama_client.generate", return_value=" caption ") as generate,
    ):
        assert indexer.caption_photo(config(), asset) == "caption"

    _, _, prompt = generate.call_args.args
    assert prompt.startswith("/no_think\nApple Photos marks this asset as a screenshot")
    assert "even when most of it is a photograph" in prompt
    assert "Never identify or name a person" in prompt
    assert "without guessing or naming the platform" in prompt
    assert "email" in prompt
    assert generate.call_args.kwargs == {
        "image_bytes": b"fake image bytes",
        "think": False,
        "options": indexer.CAPTION_GENERATION_OPTIONS,
        "timeout": indexer.CAPTION_TIMEOUT_SECONDS,
    }


def test_missing_required_vision_model_fails_instead_of_silently_degrading():
    fake_catalog = catalog()

    with (
        patch("haymish.ai.indexer._IndexLog"),
        patch("haymish.ai.indexer.ollama_client.model_available", return_value=False),
    ):
        with pytest.raises(AIError, match="explicitly use `haymish index --no-captions`"):
            indexer.index_photos(config(), fake_catalog, [photo()])

    fake_catalog.put_caption.assert_not_called()
    fake_catalog.put_embedding.assert_not_called()


def test_automatic_caption_concurrency_is_conservatively_capped():
    fake_catalog = catalog()

    with (
        patch("haymish.ai.indexer._IndexLog"),
        patch("haymish.ai.indexer.recommended_caption_workers", return_value=10),
    ):
        stats = indexer.index_photos(config(), fake_catalog, [])

    assert stats.caption_workers == indexer.MAX_AUTO_CAPTION_WORKERS == 2


@contextmanager
def index_patches(caption_side_effect):
    managers = (
        patch("haymish.ai.indexer._IndexLog"),
        patch("haymish.ai.indexer.ollama_client.model_available", return_value=True),
        patch("haymish.ai.indexer.caption_photo", side_effect=caption_side_effect),
        patch(
            "haymish.ai.indexer.ollama_client.embed",
            side_effect=lambda _host, _model, docs: [[0.1]] * len(docs),
        ),
        patch("haymish.ai.indexer.library.labels", return_value=[]),
        patch("haymish.ai.indexer.library.detected_text", return_value=""),
        patch("haymish.ai.indexer.library.is_selfie", return_value=False),
    )
    with ExitStack() as stack:
        for manager in managers:
            stack.enter_context(manager)
        yield


def test_backend_failure_is_bounded_to_one_small_chunk():
    fake_catalog = catalog()
    photos = [photo(f"u{i}") for i in range(20)]

    with index_patches(AIError("timed out")):
        with pytest.raises(AIError, match="backend unhealthy"):
            indexer.index_photos(
                config(), fake_catalog, photos, concurrency=1
            )

    assert fake_catalog.put_caption.call_count == 0
    assert fake_catalog.put_embedding.call_count == indexer.CHUNK == 8


def test_single_item_failure_does_not_abort_tiny_runs():
    """A one-photo sample must not trip the library-scale failure-rate abort."""
    fake_catalog = catalog()

    with index_patches(AIError("timed out")):
        stats = indexer.index_photos(
            config(), fake_catalog, [photo()], concurrency=1
        )

    assert stats.caption_failed == 1
    assert stats.captioned == 0


def test_high_final_failure_rate_returns_an_error_after_preserving_successes():
    fake_catalog = catalog()
    photos = [photo(f"u{i}") for i in range(32)]

    def caption(_config, asset):
        # 10/32 = 31% failures — above FINAL_FAILURE_RATE_LIMIT once the
        # minimum attempt floor is met.
        if int(asset.uuid[1:]) < 10:
            raise AIError("timed out")
        return f"Screenshot caption for {asset.uuid}"

    with index_patches(caption):
        with pytest.raises(AIError, match="10/32 requests failed"):
            indexer.index_photos(
                config(), fake_catalog, photos, concurrency=1
            )

    assert fake_catalog.put_caption.call_count == 22


def test_catch_up_high_failure_rate_warns_instead_of_aborting():
    fake_catalog = catalog()
    photos = [photo(f"u{i}") for i in range(32)]

    def caption(_config, asset):
        if int(asset.uuid[1:]) < 10:
            raise AIError("timed out")
        return f"Screenshot caption for {asset.uuid}"

    with index_patches(caption):
        stats = indexer.index_photos(
            config(), fake_catalog, photos, concurrency=1,
            catch_up_captions=True,
        )

    assert stats.caption_failed == 10
    assert stats.captioned == 22
    assert any("marked failed" in e for e in stats.errors)
