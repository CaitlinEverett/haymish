"""M0 spike: benchmark Apple Vision (classify + OCR) and Ollama vision from Python.

Uses system wallpaper images (no Photos/TCC access needed), so it can run anywhere.

Usage:
    uv run python spikes/vision_bench.py                # Vision only
    uv run python spikes/vision_bench.py --ollama       # also benchmark Ollama model
    uv run python spikes/vision_bench.py --ollama --model gemma3:27b
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import objc
import Quartz  # noqa: F401  (registers CGImage helpers Vision needs)
import Vision
from Foundation import NSURL

WALLPAPER_DIRS = [
    Path("/System/Library/Desktop Pictures"),
    Path("/Library/Desktop Pictures"),
]
N_IMAGES = 8


def sample_images() -> list[Path]:
    imgs: list[Path] = []
    for d in WALLPAPER_DIRS:
        if d.exists():
            imgs += sorted(d.glob("*.heic")) + sorted(d.glob("*.jpg")) + sorted(d.glob("*.png"))
    return imgs[:N_IMAGES]


def run_vision(paths: list[Path]):
    classify_times, ocr_times = [], []
    for i, path in enumerate(paths):
        with objc.autorelease_pool():
            url = NSURL.fileURLWithPath_(str(path))
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)

            t0 = time.perf_counter()
            req = Vision.VNClassifyImageRequest.alloc().init()
            handler.performRequests_error_([req], None)
            classify_times.append(time.perf_counter() - t0)
            top = [(r.identifier(), round(r.confidence(), 2)) for r in (req.results() or [])[:3]]

            t0 = time.perf_counter()
            ocr = Vision.VNRecognizeTextRequest.alloc().init()
            ocr.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            handler.performRequests_error_([ocr], None)
            ocr_times.append(time.perf_counter() - t0)
            n_text = len(ocr.results() or [])

        print(f"  {path.name[:40]:42} classify {classify_times[-1]*1000:6.0f}ms top={top}  ocr {ocr_times[-1]*1000:6.0f}ms ({n_text} regions)")

    def avg(xs):  # skip first call (model load)
        xs = xs[1:] if len(xs) > 1 else xs
        return sum(xs) / len(xs) * 1000

    print(f"\nVision classify avg (warm): {avg(classify_times):.0f}ms/img")
    print(f"Vision OCR accurate avg (warm): {avg(ocr_times):.0f}ms/img")


def run_ollama(paths: list[Path], model: str, host: str = "http://localhost:11434"):
    import httpx

    print(f"\nOllama {model} (first call includes model load)…")
    times = []
    for path in paths[:3]:
        if path.suffix == ".heic":  # ollama wants jpg/png
            continue
        b64 = base64.b64encode(path.read_bytes()).decode()
        t0 = time.perf_counter()
        r = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": "Is this a screenshot of a product ad? Answer yes or no.",
                  "images": [b64], "stream": False},
            timeout=300,
        )
        times.append(time.perf_counter() - t0)
        answer = r.json().get("response", "").strip()[:60]
        print(f"  {path.name[:40]:42} {times[-1]:6.1f}s → {answer!r}")
    if times:
        print(f"Ollama avg: {sum(times)/len(times):.1f}s/img")
    else:
        print("  (no jpg/png samples found for Ollama)")


def main():
    paths = sample_images()
    if not paths:
        print("No sample images found in system wallpaper dirs.")
        sys.exit(1)
    print(f"Benchmarking on {len(paths)} system images:\n")
    run_vision(paths)
    if "--ollama" in sys.argv:
        model = "gemma3:27b"
        if "--model" in sys.argv:
            model = sys.argv[sys.argv.index("--model") + 1]
        run_ollama(paths, model)


if __name__ == "__main__":
    main()
