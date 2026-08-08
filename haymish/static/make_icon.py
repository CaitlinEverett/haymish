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
    """A broom head, seen straight on: handle stub, binding band, bristles.

    Readability notes for 18pt, learned the hard way -- the first version read
    as a badminton shuttlecock:
      * The handle stub is what distinguishes a broom from a shuttlecock or a
        bucket. It has to be present even though this is a "head only" mark.
      * Modest flare, not a wide fan. A wide triangular skirt is the exact
        shuttlecock silhouette.
      * A transparent gap between band and bristles reads at small size far
        better than a drawn outline, which fills in when downsampled.
      * Bristle gaps run the full height of the block, so they survive as
        distinct strokes rather than blurring into a solid mass.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 18.0  # one "point" in this image's pixels
    black = (0, 0, 0, 255)
    clear = (0, 0, 0, 0)

    # Handle stub -- narrow, clearly narrower than the band beneath it.
    d.rectangle([7.9 * u, 1.6 * u, 10.1 * u, 5.0 * u], fill=black)

    # Binding band -- the widest solid horizontal element; anchors the shape.
    d.rectangle([5.6 * u, 5.0 * u, 12.4 * u, 7.6 * u], fill=black)

    # Bristle block -- gentle flare (6.8u wide at top, 10.4u at bottom), flat
    # bottom edge. Starts below a transparent gap so the band stays distinct.
    top_l, top_r = 5.6 * u, 12.4 * u
    bot_l, bot_r = 3.8 * u, 14.2 * u
    top_y, bot_y = 8.6 * u, 16.0 * u
    d.polygon([(top_l, top_y), (top_r, top_y), (bot_r, bot_y), (bot_l, bot_y)], fill=black)

    # Bristle separations: full-height wedges that follow the flare, so each
    # gap stays open at the bottom where the block is widest.
    # Gap width is tuned for the @1x (18px) render, where each gap is barely a
    # pixel: wider cuts hollow the block out and it reads as a fence or crown.
    for i in (1, 2, 3):
        t = i / 4.0
        xt = top_l + (top_r - top_l) * t
        xb = bot_l + (bot_r - bot_l) * t
        half_t, half_b = 0.16 * u, 0.28 * u
        d.polygon(
            [(xt - half_t, top_y - 0.1 * u), (xt + half_t, top_y - 0.1 * u),
             (xb + half_b, bot_y + 0.1 * u), (xb - half_b, bot_y + 0.1 * u)],
            fill=clear,
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
