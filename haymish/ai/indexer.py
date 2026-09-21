"""Build the per-photo AI index: a caption from a small local vision model plus a
text embedding of everything known about the photo (caption, Photos' own OCR text
and ML labels, filename, date). Cached in the catalog by uuid; incremental — a
second `haymish index` only pays for new photos.

The embedded "document" deliberately leans on signals Photos already computed
(detected_text, labels) so the index is useful even for photos where no vision
caption could be generated (iCloud-only originals with no local derivative).
"""

from __future__ import annotations

import datetime as dt
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import library
from ..catalog import Catalog
from ..config import Config
from ..hardware import recommended_caption_workers
from . import ollama_client
from .ollama_client import AIError

# Bump when CAPTION_PROMPT or metadata hints change materially. It's part of the
# caption identity, so old and new descriptions never mix silently.
CAPTION_PROMPT_VERSION = 6

# Prose remains deliberate: structured KIND/SENSITIVE fields made small models'
# guesses look like facts. The p3 wording comes from the 2026-09-02 qwen3-vl:8b
# pilot: it grounded all 18 outputs structurally but confused email vs chat,
# named the wrong social platform twice, called one screenshot a photo, and may
# have inferred a person's identity. p6 makes generation deterministic and
# bounded, rejects empty output, removes invitations to guess social/person
# identity, and uses Qwen's explicit /no_think control. Inference options are
# part of the revision just like prompt wording.
CAPTION_PROMPT = (
    "Describe this image in 1-2 short sentences for a photo search index. "
    "Begin with what kind of image it is: photo, screenshot, document, or receipt. "
    "If it is a screenshot, distinguish a video call, text or chat conversation, "
    "email, web page, map, receipt, social media post, app, or a displayed photo. "
    "Say whether human figures are visible in the main content and roughly how many; "
    "do not count tiny profile or avatar icons as people in the scene. "
    "For social content, say social media post without guessing or naming the platform "
    "from its layout or logo. Never identify or name a person; if a name is legible, "
    "quote it only as visible text without asserting whose face is shown. Name another "
    "app, brand, or product only when that exact name is clearly printed in the image. "
    "Quote only a few useful words of prominent text. "
    "No preamble, just the description."
)


def caption_key(config: Config) -> str:
    """The identity a caption is stored under: the vision model plus the prompt
    version. Either changing means existing captions describe the library
    differently from new ones, which the catalog needs to be able to see."""
    return f"{config.ai_vision_model}+p{CAPTION_PROMPT_VERSION}"


def caption_prompt(photo: object) -> str:
    """Ground the model with cheap Photos metadata it should not second-guess."""
    hints: list[str] = []
    if getattr(photo, "screenshot", False):
        hints.append(
            "Apple Photos marks this asset as a screenshot. Call the asset a screenshot "
            "even when most of it is a photograph. "
        )
    if getattr(photo, "ismovie", False):
        hints.append("This image is a representative frame from a video. ")
    return "/no_think\n" + "".join(hints) + CAPTION_PROMPT


# Circuit breakers are deliberately smaller than a library run. The first real
# qwen3-vl pilot returned success after 12/28 timeouts because the old threshold
# was 25 and the whole 28-photo pool was one chunk. Bound both wasted time and
# the amount of durable work between health decisions.
CONSECUTIVE_FAILURE_LIMIT = 5
RECENT_FAILURE_WINDOW = 8
RECENT_FAILURE_LIMIT = 4
FINAL_FAILURE_RATE_LIMIT = 0.25
MAX_AUTO_CAPTION_WORKERS = 2
CAPTION_TIMEOUT_SECONDS = 180
CAPTION_GENERATION_OPTIONS: dict[str, int | float] = {
    "temperature": 0,
    "seed": 0,
    "num_predict": 512,
}

EMBED_BATCH = 16
CHUNK = 8
_MAX_TEXT_CHARS = 1500  # OCR text can be huge; embeddings don't need all of it


class _IndexLog:
    """Append-only record of index runs at ~/.haymish/index.log.

    Indexing a large library runs for hours, often unattended or in a terminal
    that gets closed. Without this, a run that dies at hour 12 leaves no trace
    of how far it got or what went wrong -- and per-photo caption failures are
    capped at 5 in IndexStats, so the rest would vanish entirely. Logging never
    raises: a failure to write a log line must not kill an indexing run.
    """

    def __init__(self, config):
        from ..paths import APP_DIR

        self.path = APP_DIR / "index.log"
        self.config = config
        self._failures = 0

    def _write(self, line: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.path.open("a") as f:
                f.write(f"{stamp}  {line}\n")
        except OSError:
            pass

    def start(self, total: int, workers: int, captions: bool) -> None:
        self._write(
            f"START  {total} photo(s) to index · vision={self.config.ai_vision_model if captions else 'off'} "
            f"· embed={self.config.ai_embed_model} · {workers} caption worker(s)"
        )

    def chunk(self, done: int, total: int, stats) -> None:
        self._write(f"  ..    {done}/{total} · captioned={stats.captioned} "
                     f"embedded={stats.embedded} failed={stats.caption_failed}")

    def failure(self, uuid: str, error) -> None:
        # Unlike IndexStats.errors (capped at 5 for display), every failure lands
        # here -- that's the point of a log.
        self._failures += 1
        self._write(f"  FAIL  {uuid}: {error}")

    def finish(self, stats, elapsed: float) -> None:
        self._write(
            f"DONE   captioned={stats.captioned} embedded={stats.embedded} "
            f"skipped_no_image={stats.caption_skipped_no_image} failed={stats.caption_failed} "
            f"already_indexed={stats.already_indexed} in {elapsed / 60:.1f} min"
        )

    def aborted(self, error) -> None:
        self._write(f"ABORT  {type(error).__name__}: {error}")


@dataclass
class IndexStats:
    captioned: int = 0
    caption_failed: int = 0
    caption_skipped_no_image: int = 0
    embedded: int = 0
    already_indexed: int = 0
    caption_workers: int = 1
    # Planned work, known before anything runs -- so the UI can say what it's
    # about to do instead of only what it did.
    needs_embedding: int = 0
    needs_caption: int = 0
    errors: list[str] = field(default_factory=list)


def _caption_source(photo) -> str | None:
    """Smallest local image we can feed the vision model — for videos, the
    poster-frame derivative (captioning one representative frame is what makes
    `find`/`ask` work on videos at all)."""
    return library.image_source(photo)


def build_document(photo, caption: str | None) -> str:
    """The text that gets embedded for this photo."""
    parts = []
    if caption:
        parts.append(caption)
    photo_labels = library.labels(photo)
    if photo_labels:
        parts.append("labels: " + ", ".join(photo_labels))
    text = library.detected_text(photo).strip()
    if text:
        parts.append("text in image: " + text[:_MAX_TEXT_CHARS])
    kind = []
    if getattr(photo, "screenshot", False):
        kind.append("screenshot")
    if library.is_selfie(photo):
        kind.append("selfie")
    if getattr(photo, "ismovie", False):
        kind.append("video")
    if kind:
        parts.append("type: " + ", ".join(kind))
    date = getattr(photo, "date", None)
    if date is not None:
        parts.append(f"date: {date:%Y-%m-%d}")
    filename = getattr(photo, "original_filename", None)
    if filename:
        parts.append(f"filename: {filename}")
    return "\n".join(parts)


def caption_photo(config: Config, photo) -> str | None:
    """One vision-LLM caption, or None when there's no local image to look at."""
    source = _caption_source(photo)
    if not source or not Path(source).is_file():
        return None
    image_bytes = Path(source).read_bytes()
    return ollama_client.generate(
        config.ollama_host,
        config.ai_vision_model,
        caption_prompt(photo),
        image_bytes=image_bytes,
        think=False,
        options=CAPTION_GENERATION_OPTIONS,
        timeout=CAPTION_TIMEOUT_SECONDS,
    ).strip()


def vector_to_blob(vector: list[float]) -> tuple[bytes, int]:
    arr = np.asarray(vector, dtype=np.float32)
    return arr.tobytes(), arr.shape[0]


def index_photos(config: Config, catalog: Catalog, photos: list[Any], captions: bool = True,
                 limit: int | None = None, progress=None,
                 concurrency: int | None = None, plan=None,
                 catch_up_captions: bool = False) -> IndexStats:
    """Captions (optional) then embeddings, incremental against the catalog.
    concurrency caps parallel caption requests; None uses a conservative hardware cap.
    progress(done, total, phase) is called per photo for UI. plan(stats, total,
    config) is called once before any work, so a caller can report what is about
    to happen -- a run that turns out to have nothing to do should say so, not
    show a progress bar that advances over photos it silently skips. Caption
    failures are per-photo (logged, photo still gets embedded from OCR/labels);
    embedding failures abort -- without the embedding model there's no index."""
    log = _IndexLog(config)
    stats = IndexStats()
    embedded = catalog.embedded_uuids(config.ai_embed_model)
    # Model-scoped: a photo captioned by a DIFFERENT vision model counts as
    # un-captioned for the current one, so upgrading the model re-captions
    # instead of silently reusing stale text forever.
    captioned = catalog.captioned_uuids(caption_key(config))

    if catch_up_captions:
        if not captions:
            raise ValueError("catch_up_captions requires captions enabled")
        todo = [p for p in photos if p.uuid not in captioned]
    else:
        todo = [p for p in photos if p.uuid not in embedded or (captions and p.uuid not in captioned)]
    stats.already_indexed = len(photos) - len(todo)
    if limit is not None:
        todo = todo[:limit]
    total = len(todo)
    stats.needs_embedding = sum(1 for p in todo if p.uuid not in embedded)
    stats.needs_caption = (sum(1 for p in todo if p.uuid not in captioned) if captions else 0)

    if captions and stats.needs_caption and not ollama_client.model_available(
        config.ollama_host, config.ai_vision_model
    ):
        from .model_resolve import resolve_model
        resolved = resolve_model(config.ollama_host, config.ai_vision_model, "caption")
        if not ollama_client.model_available(config.ollama_host, resolved.model):
            raise AIError(
                f"vision model {config.ai_vision_model!r} is not available — no captions "
                f"were attempted ({resolved.note}). Pull it or explicitly use "
                f"`haymish index --no-captions`."
            )
        from dataclasses import replace
        config = replace(config, ai_vision_model=resolved.model)
        log._write(f"WARN  {resolved.note}")
        # Recompute caption backlog under the effective model key.
        captioned = catalog.captioned_uuids(caption_key(config))
        if catch_up_captions:
            todo = [p for p in photos if p.uuid not in captioned]
        else:
            todo = [p for p in photos if p.uuid not in embedded or p.uuid not in captioned]
        if limit is not None:
            todo = todo[:limit]
        total = len(todo)
        stats.needs_caption = sum(1 for p in todo if p.uuid not in captioned)
        stats.needs_embedding = sum(1 for p in todo if p.uuid not in embedded)

    if stats.needs_embedding and not ollama_client.model_available(
        config.ollama_host, config.ai_embed_model
    ):
        from .model_resolve import resolve_model
        embed_resolved = resolve_model(config.ollama_host, config.ai_embed_model, "embed")
        if not ollama_client.model_available(config.ollama_host, embed_resolved.model):
            raise AIError(
                f"embed model {config.ai_embed_model!r} unavailable ({embed_resolved.note})"
            )
        from dataclasses import replace
        config = replace(config, ai_embed_model=embed_resolved.model)
        log._write(f"WARN  {embed_resolved.note}")
        embedded = catalog.embedded_uuids(config.ai_embed_model)
        todo = [p for p in photos if p.uuid not in embedded or (captions and p.uuid not in captioned)]
        if limit is not None:
            todo = todo[:limit]
        total = len(todo)
        stats.needs_embedding = sum(1 for p in todo if p.uuid not in embedded)

    if concurrency is not None and concurrency < 1:
        raise ValueError("caption concurrency must be at least 1")
    if captions:
        workers = (
            concurrency
            if concurrency is not None
            else min(recommended_caption_workers(), MAX_AUTO_CAPTION_WORKERS)
        )
    else:
        workers = 1
    stats.caption_workers = workers

    def caption_one(photo):
        """Returns (photo, caption_or_None, error_or_None). Runs on a worker
        thread: does the network call only -- the catalog write happens on
        the main thread, keeping sqlite access single-threaded here."""
        try:
            return photo, caption_photo(config, photo), None
        except AIError as e:
            return photo, None, e

    def embed_chunk(chunk: list[Any], recaptioned: set[str] | None = None) -> None:
        """Embed and commit a slice, so searchability grows during the run.

        A photo already embedded gets re-embedded if it just received a caption:
        its old vector was built from OCR/labels alone, so without this the new
        caption would never reach the search index. That's the
        `index --no-captions` (fast pass) then `index` (full pass) workflow --
        the second run must upgrade the vectors, not skip them.
        """
        recaptioned = recaptioned or set()
        pending = [p for p in chunk if p.uuid not in embedded or p.uuid in recaptioned]
        if not pending:
            return
        docs = [build_document(p, catalog.get_caption(p.uuid, caption_key(config)))
                for p in pending]
        vectors = ollama_client.embed(config.ollama_host, config.ai_embed_model, docs)
        for photo, vector in zip(pending, vectors):
            blob, dim = vector_to_blob(vector)
            catalog.put_embedding(photo.uuid, config.ai_embed_model, blob, dim)
            embedded.add(photo.uuid)
            stats.embedded += 1

    # Caption and embed in interleaved chunks rather than captioning everything
    # first. On a large library the caption pass can run many hours, and a
    # caption without its embedding is useless -- find/ask read embeddings. This
    # way an interrupted run leaves behind a smaller but fully working index
    # instead of hours of captions and nothing searchable.
    if plan is not None:
        plan(stats, total, config)
    log.start(total, workers, captions)
    started = time.monotonic()
    done = 0
    consecutive_failures = 0
    recent_failures: deque[bool] = deque(maxlen=RECENT_FAILURE_WINDOW)
    try:
        for start in range(0, len(todo), CHUNK):
            chunk = todo[start:start + CHUNK]
            fresh_captions: set[str] = set()
            if captions:
                needs_caption = [p for p in chunk if p.uuid not in captioned]
                if needs_caption:
                    # Workers only perform HTTP. Catalog writes stay on this thread.
                    # CHUNK bounds how many already-submitted calls can finish after
                    # the health threshold is crossed.
                    unhealthy: str | None = None
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        for photo, caption, error in pool.map(caption_one, needs_caption):
                            if error is not None:
                                stats.caption_failed += 1
                                consecutive_failures += 1
                                recent_failures.append(True)
                                log.failure(photo.uuid, error)
                                if len(stats.errors) < 5:
                                    stats.errors.append(f"caption failed for {photo.uuid}: {error}")
                            elif caption is None:
                                stats.caption_skipped_no_image += 1
                            else:
                                catalog.put_caption(photo.uuid, caption, caption_key(config))
                                fresh_captions.add(photo.uuid)
                                stats.captioned += 1
                                consecutive_failures = 0
                                recent_failures.append(False)

                            if unhealthy is None and (
                                consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT
                                or (
                                    len(recent_failures) == RECENT_FAILURE_WINDOW
                                    and sum(recent_failures) >= RECENT_FAILURE_LIMIT
                                )
                            ):
                                unhealthy = (
                                    f"caption backend unhealthy: {consecutive_failures} consecutive "
                                    f"failures and {sum(recent_failures)}/{len(recent_failures)} "
                                    f"recent requests failed. Check `ollama ps` for contention, "
                                    f"then re-run with `--concurrency 1`; completed chunks resume."
                                )

                    if unhealthy is not None:
                        embed_chunk(chunk, recaptioned=fresh_captions)
                        raise AIError(unhealthy)
            embed_chunk(chunk, recaptioned=fresh_captions)
            done += len(chunk)
            if progress:
                progress(min(done, total), total, "index")
            log.chunk(min(done, total), total, stats)
    except BaseException as e:
        # Includes KeyboardInterrupt: a Ctrl-C at hour 12 should leave a record of
        # where it stopped. Everything committed so far is already durable and
        # fully usable -- captions and their embeddings land together per chunk.
        log.aborted(e)
        raise

    caption_attempts = stats.captioned + stats.caption_failed
    if (
        caption_attempts
        and stats.caption_failed / caption_attempts >= FINAL_FAILURE_RATE_LIMIT
    ):
        error = AIError(
            f"caption run marked failed: {stats.caption_failed}/{caption_attempts} "
            f"requests failed ({stats.caption_failed / caption_attempts:.0%}). "
            f"Successful captions remain durable; inspect Ollama contention before retrying."
        )
        log.aborted(error)
        raise error

    log.finish(stats, time.monotonic() - started)
    return stats
