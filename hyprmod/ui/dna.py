"""Config DNA — deterministic visual fingerprint generated from configuration values.

Produces a small abstract graphic (bar/wave pattern) from a SHA256 hash
of the sorted key=value pairs. Same config always produces the same graphic.
"""

import colorsys
import hashlib
import math
from typing import NamedTuple

from gi.repository import Gtk


def compute_hash(values: dict[str, str]) -> bytes:
    """Compute SHA256 of sorted key=value pairs."""
    content = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    return hashlib.sha256(content.encode()).digest()


class DnaParams(NamedTuple):
    """Visual parameters extracted from a config hash."""

    bar_heights: list[float]
    hue_base: float
    hue_shift: float
    saturation: float
    lightness: float
    wave_amplitude: float
    wave_freq: float


def _bytes_to_params(digest: bytes) -> DnaParams:
    """Extract visual parameters from hash bytes."""
    return DnaParams(
        bar_heights=[b / 255.0 for b in digest[:16]],
        hue_base=digest[16] / 255.0 * 360.0,
        hue_shift=digest[17] / 255.0 * 60.0 - 30.0,  # -30 to +30
        saturation=0.5 + (digest[18] / 255.0) * 0.4,  # 0.5 - 0.9
        lightness=0.45 + (digest[19] / 255.0) * 0.2,  # 0.45 - 0.65
        wave_amplitude=0.1 + (digest[20] / 255.0) * 0.3,  # 0.1 - 0.4
        wave_freq=1.0 + (digest[21] / 255.0) * 2.0,  # 1.0 - 3.0
    )


class DnaWidget(Gtk.DrawingArea):
    """Renders a Config DNA fingerprint graphic."""

    def __init__(self, width: int = 128, height: int = 48):
        super().__init__()
        self._digest = b"\x00" * 32
        self._params = _bytes_to_params(self._digest)

        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self._draw)

    def set_values(self, values: dict[str, str]):
        """Update the DNA graphic from config values."""
        self._digest = compute_hash(values)
        self._params = _bytes_to_params(self._digest)
        self.queue_draw()

    def set_digest(self, digest: bytes):
        """Set the hash directly (e.g. from a profile)."""
        self._digest = digest
        self._params = _bytes_to_params(digest)
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        p = self._params
        bars = p.bar_heights
        n = len(bars)
        pad_x = 4
        pad_y = 4
        bar_area_w = width - 2 * pad_x
        bar_area_h = height - 2 * pad_y
        bar_w = bar_area_w / n
        gap = max(1, bar_w * 0.15)
        actual_bar_w = bar_w - gap

        for i, h in enumerate(bars):
            hue = p.hue_base + p.hue_shift * (i / n)
            r, g, b = colorsys.hls_to_rgb(hue / 360.0, p.lightness, p.saturation)

            wave = math.sin(i / n * math.pi * 2 * p.wave_freq) * p.wave_amplitude
            bar_h = max(2, (h + wave) * bar_area_h)
            bar_h = min(bar_h, bar_area_h)

            x = pad_x + i * bar_w
            y = pad_y + (bar_area_h - bar_h)

            radius = min(2, actual_bar_w / 2)
            cr.set_source_rgba(r, g, b, 0.85)
            self._rounded_rect(cr, x, y, actual_bar_w, bar_h, radius)
            cr.fill()

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r):
        """Draw a rounded rectangle path."""
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
