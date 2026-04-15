"""Discover installed cursor themes (XCursor and Hyprcursor)."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CursorTheme:
    name: str  # internal name (directory basename)
    display_name: str  # human-readable from index.theme/manifest, fallback to name
    path: Path  # absolute theme dir
    has_xcursor: bool
    has_hyprcursor: bool


def search_dirs() -> list[Path]:
    """Return XDG icon search directories in priority order."""
    dirs: list[Path] = [
        Path.home() / ".icons",
        Path.home() / ".local" / "share" / "icons",
    ]
    for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(Path(d) / "icons")
    return dirs


def discover() -> list[CursorTheme]:
    """Scan icon dirs for cursor themes. First occurrence of a given name wins."""
    seen: dict[str, CursorTheme] = {}
    for base in search_dirs():
        if not base.is_dir():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name in seen:
                continue
            theme = _classify(entry)
            if theme is not None:
                seen[entry.name] = theme
    return sorted(seen.values(), key=lambda t: t.display_name.lower())


def _classify(path: Path) -> CursorTheme | None:
    has_x = (path / "cursors").is_dir()
    has_hy = _has_hyprcursor(path)
    if not (has_x or has_hy):
        return None
    return CursorTheme(
        name=path.name,
        display_name=_read_display_name(path) or path.name,
        path=path,
        has_xcursor=has_x,
        has_hyprcursor=has_hy,
    )


def _has_hyprcursor(path: Path) -> bool:
    if (path / "manifest.hl").is_file():
        return True
    # Some themes ship a hyprcursors/ subdir alongside cursors/
    if (path / "hyprcursors").is_dir():
        return True
    return False


def _read_display_name(path: Path) -> str | None:
    """Extract Name= from index.theme's [Icon Theme] section."""
    index = path / "index.theme"
    if not index.is_file():
        return None
    try:
        text = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_section = line == "[Icon Theme]"
            continue
        if in_section and line.startswith("Name"):
            key, _, val = line.partition("=")
            if key.strip() == "Name":
                return val.strip()
    return None
