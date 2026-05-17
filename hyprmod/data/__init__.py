"""Bundled data assets and persistence helpers (bezier curves, user data)."""

from pathlib import Path

_PACKAGE_DATA = Path(__file__).parent
_REPO_DATA = _PACKAGE_DATA.parent.parent / "data"


def bundled_data_dir(*parts: str) -> Path:
    """Path to bundled data inside the wheel, with a dev-tree fallback.

    Wheel builds force-include ``data/applications`` and ``data/metainfo``
    into the package, but source checkouts only have them at repo root.
    The fallback lets ``uv run hyprmod --install`` work without first
    building a wheel.
    """
    pkg_path = _PACKAGE_DATA.joinpath(*parts)
    if pkg_path.exists():
        return pkg_path
    repo_path = _REPO_DATA.joinpath(*parts)
    if repo_path.exists():
        return repo_path
    return pkg_path
