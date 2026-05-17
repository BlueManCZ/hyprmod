"""Install/uninstall XDG entries for pipx/uv users.

Wheel installs (``pipx install hyprmod``, ``uv tool install hyprmod``)
place the binary on ``$PATH`` but never touch ``$XDG_DATA_HOME``, so the
desktop launcher and icon don't appear in app menus. This module copies
those assets out of the bundled ``hyprmod/data`` tree into the user's
data directory and removes them again on request.

Distros that package hyprmod install the same files under ``/usr/share``;
the first-launch safety net checks ``Gio.DesktopAppInfo`` first and
skips work if anything in ``XDG_DATA_DIRS`` already provides the entry,
so the two install paths don't collide. Explicit ``hyprmod --install``
always reinstalls to ``$XDG_DATA_HOME`` (useful when bundled assets
update with a new release).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from gi.repository import Gio

from hyprmod.constants import APPLICATION_ID
from hyprmod.data import bundled_data_dir

DESKTOP_FILE = f"{APPLICATION_ID}.desktop"
METAINFO_FILE = f"{APPLICATION_ID}.metainfo.xml"
APP_ICON_FILE = f"{APPLICATION_ID}.svg"


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def _shortpath(p: Path) -> str:
    """Replace the user's home prefix with ``~`` for display."""
    home = Path.home()
    try:
        return f"~/{p.relative_to(home)}"
    except ValueError:
        return str(p)


def _user_dest_map() -> list[tuple[Path, Path]]:
    """Source-in-wheel → destination-under-$XDG_DATA_HOME pairs."""
    home = _xdg_data_home()
    return [
        (
            bundled_data_dir("applications", DESKTOP_FILE),
            home / "applications" / DESKTOP_FILE,
        ),
        (
            bundled_data_dir("metainfo", METAINFO_FILE),
            home / "metainfo" / METAINFO_FILE,
        ),
        (
            bundled_data_dir("icons", "hicolor", "scalable", "apps", APP_ICON_FILE),
            home / "icons" / "hicolor" / "scalable" / "apps" / APP_ICON_FILE,
        ),
    ]


def is_registered() -> bool:
    """Whether a .desktop entry for hyprmod is visible to the desktop."""
    return Gio.DesktopAppInfo.new(DESKTOP_FILE) is not None


def install_user_files(*, quiet: bool = False) -> list[Path]:
    """Copy bundled XDG assets into the user's data home."""
    placed: list[Path] = []
    for src, dest in _user_dest_map():
        if not src.exists():
            if not quiet:
                print(f" ! source missing: {src}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        placed.append(dest)
        if not quiet:
            print(f" + {_shortpath(dest)}")
    return placed


def uninstall_user_files(*, quiet: bool = False) -> list[Path]:
    """Remove user-scope XDG assets owned by hyprmod. Leaves system files alone."""
    removed: list[Path] = []
    home = _xdg_data_home()
    for _, dest in _user_dest_map():
        # Guard: never delete anything outside $XDG_DATA_HOME (e.g. /usr/share).
        try:
            dest.relative_to(home)
        except ValueError:
            continue
        if dest.exists():
            dest.unlink()
            removed.append(dest)
            if not quiet:
                print(f" - {_shortpath(dest)}")
    return removed


def ensure_registered_silently() -> None:
    """First-launch safety net: install only if no entry is visible anywhere."""
    if is_registered():
        return
    # Source-checkout runs (``uv run hyprmod``) don't put the binary on PATH,
    # so dropping a launcher with ``Exec=hyprmod`` would point at nothing.
    if shutil.which("hyprmod") is None:
        return
    install_user_files(quiet=True)
