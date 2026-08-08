"""Generates the menu-bar icon: a broom head, drawn as a macOS template image.

Template images are pure black + alpha; macOS recolors them automatically for
light/dark menu bars, active/inactive state, and accessibility contrast
settings. That's why this is greyscale-by-construction rather than a colored
glyph or an emoji -- an emoji renders at a fixed color and reads poorly against
a dark menu bar or a busy wallpaper.

Run `python -m haymish.static.make_icon` to regenerate after editing.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Menu bar icons are ~18pt; we draw at 4x and downsample for clean antialiasing,
# then emit @1x and @2x so Retina and non-Retina both look sharp.
BASE = 18
SCALE = 8


def draw_broom(size: int) -> Image.Image:
    """A broom head: tapered ferrule up top, bristles fanning below."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 18.0  # one "point" in this image's pixels

    # Everything is nudged up slightly (dy) so the drawn mass sits optically
    # centered in the square rather than low, which reads as misaligned next to
    # other menu bar items.
    dy = -0.5 * u

    # Ferrule (the metal band) -- a trapezoid, wider at the bottom.
    ferrule = [
        (6.6 * u, 3.2 * u + dy),
        (11.4 * u, 3.2 * u + dy),
        (12.6 * u, 8.2 * u + dy),
        (5.4 * u, 8.2 * u + dy),
    ]
    d.polygon(ferrule, fill=(0, 0, 0, 255))

    # Bristle block -- a wider trapezoid fanning out under the ferrule.
    bristles = [
        (5.4 * u, 8.8 * u + dy),
        (12.6 * u, 8.8 * u + dy),
        (15.2 * u, 15.6 * u + dy),
        (2.8 * u, 15.6 * u + dy),
    ]
    d.polygon(bristles, fill=(0, 0, 0, 255))

    # Notch the bristle ends so it reads as a broom, not a bucket. Cutting with
    # transparent wedges keeps the silhouette crisp when downsampled.
    n = 5
    for i in range(1, n):
        x = 2.8 * u + (15.2 - 2.8) * u * i / n
        half = 0.42 * u
        d.polygon(
            [(x - half, 15.8 * u + dy), (x + half, 15.8 * u + dy), (x, 12.4 * u + dy)],
            fill=(0, 0, 0, 0),
        )
    return img


def write_icons(dest_dir: Path | None = None) -> list[Path]:
    dest_dir = dest_dir or Path(__file__).resolve().parent
    big = draw_broom(BASE * SCALE)
    written = []
    for suffix, px in (("", BASE), ("@2x", BASE * 2)):
        out = dest_dir / f"broom{suffix}.png"
        big.resize((px, px), Image.LANCZOS).save(out)
        written.append(out)
    return written


if __name__ == "__main__":
    for path in write_icons():
        print(f"wrote {path}")
