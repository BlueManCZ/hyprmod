"""Bezier curve data management — user curves, external curves, and lookup API."""

import functools
import json
from pathlib import Path

from hyprland_config import atomic_write
from hyprland_state import HYPRLAND_NATIVE_CURVES

from hyprmod.core.config import HYPRMOD_DIR
from hyprmod.data.bezier_presets import BUILTIN_PRESETS


class BezierCurveStore:
    """Manages user-defined and external bezier curves.

    User curves are persisted to disk; external curves (from Hyprland IPC)
    are held in memory only.
    """

    def __init__(self, curves_path: Path):
        self._path = curves_path
        self._user_curves: dict[str, tuple] | None = None
        self._external_curves: dict[str, tuple] = {}
        self._native_curves: dict[str, tuple] = {}

    def _ensure_user_curves(self) -> dict[str, tuple]:
        """Load user curves from the disk on first access."""
        if self._user_curves is None:
            self._user_curves = self._read_from_disk()
        return self._user_curves

    def _read_from_disk(self) -> dict[str, tuple]:
        """Read user curves from disk."""
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                return {k: tuple(v) for k, v in raw.items()}
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return {}

    def _save_to_disk(self) -> None:
        """Persist user curves to disk atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: list(v) for k, v in self._ensure_user_curves().items()}
        atomic_write(self._path, json.dumps(data, indent=2) + "\n")

    def load_user_curves(self) -> dict[str, tuple]:
        """Load (or reload) user curves from disk."""
        self._user_curves = self._read_from_disk()
        return self._user_curves

    def save_user_curve(self, name: str, points: tuple) -> None:
        """Save or update a user curve."""
        self._ensure_user_curves()[name] = tuple(points)
        self._save_to_disk()

    def delete_user_curve(self, name: str) -> None:
        """Delete a user curve."""
        self._ensure_user_curves().pop(name, None)
        self._save_to_disk()

    def rename_user_curve(self, old_name: str, new_name: str) -> None:
        """Rename a user curve."""
        curves = self._ensure_user_curves()
        if old_name in curves:
            curves[new_name] = curves.pop(old_name)
            self._save_to_disk()

    def get_user_curve_names(self) -> list[str]:
        """Return a sorted list of user curve names."""
        return sorted(self._ensure_user_curves().keys())

    def set_hyprland_curves(self, curves: dict[str, tuple[float, float, float, float]]) -> None:
        """Set curves from ``Animations.get_curves()`` result."""
        self._native_curves = {k: v for k, v in curves.items() if k in HYPRLAND_NATIVE_CURVES}
        self._external_curves = {k: v for k, v in curves.items() if k not in HYPRLAND_NATIVE_CURVES}

    def get_external_curves(self) -> dict[str, tuple]:
        """Return external curves (from Hyprland, not user-defined)."""
        return dict(self._external_curves)

    def is_builtin_curve(self, name: str) -> bool:
        """Check if a name is a builtin preset."""
        return name in BUILTIN_PRESETS

    def get_curve_points(self, name: str) -> tuple | None:
        """Look up control points for a curve by name.

        Search order: user curves, builtin presets, external curves.
        Returns None if not found.
        """
        curves = self._ensure_user_curves()
        if name in curves:
            return curves[name]
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]
        if name in self._external_curves:
            return self._external_curves[name]
        if name in self._native_curves:
            return self._native_curves[name]
        return None

    def get_all_curve_names(self) -> list[str]:
        """Return all known curve names: user + external + native + builtin."""
        names: list[str] = []
        seen: set[str] = set()
        for source in (
            sorted(self._ensure_user_curves()),
            sorted(self._external_curves),
            sorted(HYPRLAND_NATIVE_CURVES),
            sorted(BUILTIN_PRESETS),
        ):
            for name in source:
                if name not in seen:
                    names.append(name)
                    seen.add(name)
        return names

    def get_all_presets(self) -> dict[str, tuple]:
        """Return all presets: builtin + user."""
        result = dict(BUILTIN_PRESETS)
        result.update(self._ensure_user_curves())
        return result

    def get_curve_definitions(self, used_curves: set[str]) -> list[str]:
        """Return bezier config lines for the given set of curve names."""
        lines = []
        for name in sorted(used_curves):
            if name in HYPRLAND_NATIVE_CURVES:
                continue
            pts = self.get_curve_points(name)
            if pts:
                lines.append(f"bezier = {name}, {pts[0]}, {pts[1]}, {pts[2]}, {pts[3]}")
        return lines

    def next_custom_name(self) -> str:
        """Generate the next unique custom curve name."""
        existing = set(self._ensure_user_curves().keys())
        i = 1
        while True:
            name = f"custom{i}"
            if name not in existing:
                return name
            i += 1


@functools.cache
def get_curve_store() -> BezierCurveStore:
    """Return the singleton BezierCurveStore, creating it on the first call."""
    return BezierCurveStore(HYPRMOD_DIR / "user_curves.json")
