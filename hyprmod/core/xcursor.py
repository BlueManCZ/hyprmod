"""Minimal parser for Xcursor binary files.

Xcursor format (little-endian throughout):
  Header:  "Xcur" | header_size:u32 | version:u32 | ntoc:u32
  TOC[ntoc]:  type:u32 | subtype:u32 | position:u32
  Chunk at position:
    header_size:u32 | type:u32 | subtype:u32 | version:u32
    (image chunks only) width:u32 | height:u32 | xhot:u32 | yhot:u32
                       delay:u32 | pixels[width*height]:u32 (ARGB, premultiplied)
"""

import struct
from dataclasses import dataclass
from pathlib import Path

from hyprmod.core.cursor_themes import read_index_theme, search_dirs

_MAGIC = b"Xcur"
_IMAGE_TYPE = 0xFFFD0002


@dataclass(frozen=True)
class CursorImage:
    width: int
    height: int
    nominal_size: int
    bgra: bytes  # premultiplied, width*height*4


def parse(path: Path | str) -> list[CursorImage]:
    """Parse an Xcursor file, returning all image frames (any size, first frame per size)."""
    data = Path(path).read_bytes()
    if len(data) < 16 or data[:4] != _MAGIC:
        return []

    _, _, ntoc = struct.unpack_from("<III", data, 4)
    images: list[CursorImage] = []
    seen_sizes: set[int] = set()

    for i in range(ntoc):
        toc_off = 16 + i * 12
        if toc_off + 12 > len(data):
            break
        ctype, subtype, pos = struct.unpack_from("<III", data, toc_off)
        if ctype != _IMAGE_TYPE or subtype in seen_sizes:
            continue
        if pos + 36 > len(data):
            continue

        # chunk header (16) + image header (20)
        width, height, _xhot, _yhot, _delay = struct.unpack_from("<IIIII", data, pos + 16)
        pixel_off = pos + 36
        pixel_len = width * height * 4
        if pixel_off + pixel_len > len(data):
            continue

        images.append(
            CursorImage(
                width=width,
                height=height,
                nominal_size=subtype,
                bgra=bytes(data[pixel_off : pixel_off + pixel_len]),
            )
        )
        seen_sizes.add(subtype)

    return images


def crop_to_content(image: CursorImage, alpha_threshold: int = 8) -> CursorImage:
    """Return *image* cropped to the bounding box of pixels with alpha > threshold."""
    w, h = image.width, image.height
    bgra = image.bgra
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        row = y * w * 4
        for x in range(w):
            if bgra[row + x * 4 + 3] > alpha_threshold:
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
    if max_x < 0:
        return image

    new_w, new_h = max_x - min_x + 1, max_y - min_y + 1
    out = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        src = ((min_y + y) * w + min_x) * 4
        out[y * new_w * 4 : (y + 1) * new_w * 4] = bgra[src : src + new_w * 4]
    return CursorImage(new_w, new_h, image.nominal_size, bytes(out))


def scale_nearest(image: CursorImage, size: int) -> CursorImage:
    """Scale *image* to ``size × size`` pixels using nearest-neighbor sampling."""
    if image.width == size and image.height == size:
        return image
    sw, sh, src = image.width, image.height, image.bgra
    out = bytearray(size * size * 4)
    for y in range(size):
        src_row = (y * sh // size) * sw * 4
        dst_row = y * size * 4
        for x in range(size):
            s = src_row + (x * sw // size) * 4
            out[dst_row + x * 4 : dst_row + x * 4 + 4] = src[s : s + 4]
    return CursorImage(size, size, image.nominal_size, bytes(out))


def pad_to_square(image: CursorImage) -> CursorImage:
    """Return *image* centered on a transparent square canvas (max of w, h)."""
    w, h = image.width, image.height
    if w == h:
        return image
    side = max(w, h)
    ox, oy = (side - w) // 2, (side - h) // 2
    out = bytearray(side * side * 4)
    for y in range(h):
        src = y * w * 4
        dst = ((oy + y) * side + ox) * 4
        out[dst : dst + w * 4] = image.bgra[src : src + w * 4]
    return CursorImage(side, side, image.nominal_size, bytes(out))


def pick_closest(images: list[CursorImage], target: int) -> CursorImage | None:
    """Return the image whose nominal_size is closest to *target* (prefer >= target)."""
    if not images:
        return None
    ge = [im for im in images if im.nominal_size >= target]
    pool = ge or images
    return min(pool, key=lambda im: (abs(im.nominal_size - target), im.nominal_size))


def load_pointer(theme_dir: Path, target_size: int = 48) -> CursorImage | None:
    """Load the theme's primary pointer cursor at the closest size to *target_size*.

    Walks the XCursor inheritance chain via ``index.theme`` if the pointer cursor
    is not directly present in *theme_dir*.
    """
    for name in ("default", "left_ptr", "arrow", "top_left_arrow"):
        img = _load_from_theme(theme_dir, name, target_size, visited=set())
        if img is not None:
            return img
    return None


def _load_from_theme(
    theme_dir: Path, cursor_name: str, target_size: int, visited: set[Path]
) -> CursorImage | None:
    theme_dir = theme_dir.resolve()
    if theme_dir in visited:
        return None
    visited.add(theme_dir)

    path = theme_dir / "cursors" / cursor_name
    if path.is_file():
        img = pick_closest(parse(path), target_size)
        if img is not None:
            return img

    for parent in _inherited_themes(theme_dir):
        for base in (theme_dir.parent, *search_dirs()):
            candidate = base / parent
            if candidate.is_dir() and candidate.resolve() not in visited:
                img = _load_from_theme(candidate, cursor_name, target_size, visited)
                if img is not None:
                    return img
    return None


def _inherited_themes(theme_dir: Path) -> list[str]:
    raw = read_index_theme(theme_dir).get("Inherits", "")
    return [p.strip() for p in raw.split(",") if p.strip()]
