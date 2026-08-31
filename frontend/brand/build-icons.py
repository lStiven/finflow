"""Derives the app's icon set from the source artwork.

Run by hand when the artwork changes, not by the build: the outputs are
committed, so a clone needs no image toolchain to serve them.

    uv run python frontend/brand/build-icons.py

Nothing here redraws the mark. Every output is the same pixels cropped and
resampled — the only edits are framing (square up the tile) and, for iOS, a
zoom that keeps Apple's own corner mask from biting into the neon rim.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


BRAND = Path(__file__).parent
PUBLIC = BRAND.parent / "public"
ASSETS = BRAND.parent / "src" / "assets"
SOURCE = BRAND / "finflow-logo.png"
LOCKUP = BRAND / "finflow.png"

# The social card, at the ratio every scraper crops to anyway.
CARD = (1200, 630)
# How much of the card's height the lockup should take. Under half and it
# reads as lost; over two thirds and it reads as cropped.
LOCKUP_HEIGHT = 0.63

# The tab bar this sits in, and the ground iOS composites an opaque icon on.
DARK = (11, 11, 16)


def square_tile(image: Image.Image) -> Image.Image:
    """The artwork's tile, cropped out of its canvas and padded to a square.

    The generator left a few faint speckles outside the tile and centred it a
    little high, so the bounds come from the alpha channel at a threshold
    rather than from the canvas. The tile is 6% wider than it is tall; padding
    to the larger side keeps it undistorted.
    """
    mask = image.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    box = mask.getbbox()
    assert box is not None
    tile = image.crop(box)

    side = max(tile.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(tile, ((side - tile.width) // 2, (side - tile.height) // 2))

    return out


def resample(image: Image.Image, size: int) -> Image.Image:
    """Downscale without the dark fringe a naive RGBA resize leaves behind.

    Outside the tile the source is transparent *black*. Resampling the colour
    channels on their own averages that black into every edge pixel, which at
    16px is most of the mark. Premultiplying first weights each pixel by its
    own alpha, so fully transparent pixels contribute nothing.
    """
    r, g, b, a = image.split()
    premultiplied = Image.merge(
        "RGBA",
        (
            Image.composite(r, Image.new("L", image.size), a),
            Image.composite(g, Image.new("L", image.size), a),
            Image.composite(b, Image.new("L", image.size), a),
            a,
        ),
    )
    small = premultiplied.resize((size, size), Image.Resampling.LANCZOS)

    r, g, b, a = small.split()
    lut = [0] + [min(255, round(255 * 255 / v)) for v in range(1, 256)]

    # Per-pixel rather than a channel-wide point(): the divisor is that
    # pixel's own alpha, which point() has no way to see.
    out = small.load()
    for y in range(size):
        for x in range(size):
            pr, pg, pb, pa = out[x, y]
            if pa == 0:
                continue
            k = lut[pa]
            out[x, y] = (
                min(255, pr * k // 255),
                min(255, pg * k // 255),
                min(255, pb * k // 255),
                pa,
            )

    return small


def flatten(image: Image.Image, zoom: float = 1.0) -> Image.Image:
    """An opaque square, optionally zoomed so the tile bleeds off the edges.

    iOS masks whatever it is given with its own squircle, and this tile is
    rounder than that mask (27.4% corner radius against Apple's 22.4%). Left
    alone it would show four dark notches where the fill peeks out past the
    artwork. Zooming past the frame crops the tile's own corners away, so the
    mask has solid colour to cut.
    """
    side = image.width
    ground = Image.new("RGBA", (side, side), (*DARK, 255))

    if zoom != 1.0:
        grown = round(side * zoom)
        image = image.resize((grown, grown), Image.Resampling.LANCZOS)
        offset = (side - grown) // 2
        ground.alpha_composite(image, (offset, offset))
    else:
        ground.alpha_composite(image)

    return ground.convert("RGB")


def social_card() -> Image.Image:
    """The link preview, composed from the lockup artwork's own pixels.

    The artwork is square and the card is not, so something has to give. A
    straight centre-crop to 1200x630 leaves the wordmark 38px from the edge,
    which reads as an accident. Instead the whole square is scaled down until
    the lockup sits at a comfortable height, and the gap either side is filled
    by stretching the artwork's own outermost columns — the backdrop there is a
    smooth near-black vignette, so the seam has nothing to show.
    """
    source = Image.open(LOCKUP).convert("RGB")

    # Where the eye actually sees the mark, rather than where the canvas ends.
    box = source.convert("L").point(lambda v: 255 if v > 90 else 0).getbbox()
    assert box is not None
    lockup_height = box[3] - box[1]

    scale = CARD[1] * LOCKUP_HEIGHT / lockup_height
    size = round(source.width * scale)
    scaled = source.resize((size, size), Image.Resampling.LANCZOS)

    card = Image.new("RGB", CARD)
    left = (CARD[0] - size) // 2
    top = (CARD[1] - size) // 2
    card.paste(scaled, (left, top))

    # Stretch the edge columns outward to fill what the square does not cover.
    if left > 0:
        card.paste(scaled.crop((0, 0, 1, size)).resize((left, size)), (0, top))
        card.paste(
            scaled.crop((size - 1, 0, size, size)).resize(
                (CARD[0] - left - size, size)
            ),
            (left + size, top),
        )

    return card


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    tile = square_tile(source)
    print(f"source {source.size} -> tile {tile.size}")

    # The in-app mark, imported by components so Vite hashes and cache-busts
    # it. One size for every use: the rail at 32px, the login panel at 44, the
    # welcome dialog at 56.
    ASSETS.mkdir(exist_ok=True)
    resample(tile, 128).save(ASSETS / "mark.png", optimize=True)

    # JPEG, not PNG: it is a smooth gradient with no flat colour to run-length
    # away, and lossless costs half a megabyte for a picture scrapers downsample.
    social_card().save(PUBLIC / "og.jpg", optimize=True, quality=88, subsampling=0)

    # Transparent-cornered, for browser chrome of any colour.
    for size in (192, 512):
        resample(tile, size).save(PUBLIC / f"icon-{size}.png", optimize=True)

    ico = [resample(tile, size) for size in (48, 32, 16)]
    ico[0].save(
        PUBLIC / "favicon.ico",
        format="ICO",
        sizes=[(48, 48), (32, 32), (16, 16)],
        append_images=ico[1:],
    )

    # Opaque, because iOS composites transparency onto black and Android's
    # maskable slot crops to a circle on some launchers.
    flatten(resample(tile, 180), zoom=1.22).save(
        PUBLIC / "apple-touch-icon.png",
        optimize=True,
    )
    flatten(resample(tile, 512), zoom=0.78).save(
        PUBLIC / "icon-maskable-512.png",
        optimize=True,
    )

    outputs = [
        *sorted(PUBLIC.glob("*.png")),
        PUBLIC / "og.jpg",
        PUBLIC / "favicon.ico",
        ASSETS / "mark.png",
    ]
    for path in outputs:
        print(f"  {path.name:26} {path.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
