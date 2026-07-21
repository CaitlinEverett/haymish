#!/usr/bin/env python3
"""One-shot recovery for a photo left hidden during hide testing.

Usage (from Terminal.app — needs Photos TCC on Terminal):
  cd ~/dev/photosweep && uv run python scripts/recover_hidden.py [uuid]
"""
from __future__ import annotations

import sys

from haymish.actions.hide import unhide_photos
from haymish.config import load_config
from haymish import library

DEFAULT_UUID = "4BD24541-9964-40BF-9D0B-637E1775B3DD"  # IMG_6067.PNG from hide test


def main() -> int:
    uuid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UUID
    print(f"unhide {uuid}…")
    results = unhide_photos([uuid])
    print("result:", results)
    cfg = load_config()
    photo = next(
        (p for p in library.all_photos(library.load_photosdb(cfg.library)) if p.uuid == uuid),
        None,
    )
    if photo is None:
        print("osxphotos: not found")
        return 1
    print(f"osxphotos: hidden={photo.hidden} ismissing={photo.ismissing} file={photo.original_filename}")
    return 0 if results.get(uuid) == "ok" and not photo.hidden else 2


if __name__ == "__main__":
    raise SystemExit(main())
