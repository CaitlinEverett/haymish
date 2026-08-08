"""Build the per-photo AI index: a caption from a small local vision model plus a
text embedding of everything known about the photo (caption, Photos' own OCR text
and ML labels, filename, date). Cached in the catalog by uuid; incremental — a
second `haymish index` only pays for new photos.

The embedded "document" deliberately leans on signals Photos already computed
(detected_text, labels) so the index is useful even for photos where no vision
caption could be generated (iCloud-only originals with no local derivative).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import library
from ..catalog import Catalog
from ..config import Config
from . import ollama_client
from .ollama_client import AIError

CAPTION_PROMPT = (
    "Describe this photo in 1-2 short sentences for a search index. Mention the kind "
    "of image (photo, screenshot, document, receipt, meme), the main subject, any "
    "visible brand or product, and quote a few words of any prominent text. "
    "No preamble, just the description."
)

EMBED_BATCH = 16
_MAX_TEXT_CHARS = 1500  # OCR text can be huge; embeddings don't need all of it


@dataclass
class IndexStats:
    captioned: int = 0
    caption_failed: int = 0
    caption_skipped_no_image: int = 0
    embedded: int = 0
    already_indexed: int = 0
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
        config.ollama_host, config.ai_vision_model, CAPTION_PROMPT,
        image_bytes=image_bytes, timeout=120,
    ).strip()


def vector_to_blob(vector: list[float]) -> tuple[bytes, int]:
    arr = np.asarray(vector, dtype=np.float32)
    return arr.tobytes(), arr.shape[0]


def index_photos(config: Config, catalog: Catalog, photos: list, captions: bool = True,
                 limit: int | None = None, progress=None) -> IndexStats:
    """Captions (optional) then embeddings, incremental against the catalog.
    progress(done, total, phase) is called per photo for UI. Caption failures are
    per-photo (logged, photo still gets embedded from OCR/labels); embedding
    failures abort — without the embedding model there's no index to build."""
    stats = IndexStats()
    embedded = catalog.embedded_uuids(config.ai_embed_model)
    captioned = catalog.captioned_uuids()

    todo = [p for p in photos if p.uuid not in embedded or (captions and p.uuid not in captioned)]
    stats.already_indexed = len(photos) - len(todo)
    if limit is not None:
        todo = todo[:limit]
    total = len(todo)

    if captions:
        vision_ok = ollama_client.model_available(config.ollama_host, config.ai_vision_model)
        if not vision_ok:
            stats.errors.append(
                f"vision model {config.ai_vision_model!r} not available — captions skipped "
                f"(index still built from Photos' own OCR text and labels). "
                f"Fix: ollama pull {config.ai_vision_model}"
            )
            captions = False

    for i, photo in enumerate(todo):
        if captions and photo.uuid not in captioned:
            try:
                caption = caption_photo(config, photo)
                if caption is None:
                    stats.caption_skipped_no_image += 1
                else:
                    catalog.put_caption(photo.uuid, caption, config.ai_vision_model)
                    stats.captioned += 1
            except AIError as e:
                stats.caption_failed += 1
                if len(stats.errors) < 5:
                    stats.errors.append(f"caption failed for {photo.uuid}: {e}")
        if progress:
            progress(i + 1, total, "caption")

    to_embed = [p for p in todo if p.uuid not in embedded]
    for start in range(0, len(to_embed), EMBED_BATCH):
        batch = to_embed[start:start + EMBED_BATCH]
        docs = [build_document(p, catalog.get_caption(p.uuid)) for p in batch]
        vectors = ollama_client.embed(config.ollama_host, config.ai_embed_model, docs)
        for photo, vector in zip(batch, vectors):
            blob, dim = vector_to_blob(vector)
            catalog.put_embedding(photo.uuid, config.ai_embed_model, blob, dim)
            stats.embedded += 1
        if progress:
            progress(min(start + EMBED_BATCH, len(to_embed)), len(to_embed), "embed")

    return stats
