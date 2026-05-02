"""Profile management — self-contained with IPC activate().

Profiles are stored in HYPRMOD_DIR/<profile_id>/ directories.
"""

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from hyprland_config import atomic_write
from hyprland_state import HyprlandState

from hyprmod.core.config import HYPRMOD_DIR, gui_conf, parse_conf

_PROFILES_DIR = HYPRMOD_DIR / "profiles"
_META_FILE = "meta.json"
_ACTIVE_FILE = HYPRMOD_DIR / "active_profile"

# Profile IDs are 12 hex chars (48 bits) — long enough that collisions are
# astronomically unlikely with the dozens of profiles a single user creates,
# short enough that the directory name stays readable.
_PROFILE_ID_LEN = 12


def _profile_dir(profile_id: str) -> Path:
    return _PROFILES_DIR / profile_id


def _read_meta(profile_id: str) -> dict:
    meta_path = _profile_dir(profile_id) / _META_FILE
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"name": profile_id}


def _write_meta(profile_id: str, meta: dict) -> None:
    meta_path = _profile_dir(profile_id) / _META_FILE
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(meta_path, json.dumps(meta, indent=2) + "\n")


def _copy_file_atomic(src: Path, dest: Path) -> None:
    """Copy a file atomically — read content, then write via atomic_write."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(dest, src.read_text())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_active_id() -> str | None:
    """Return the ID of the currently active profile, or ``None``."""
    if _ACTIVE_FILE.exists():
        try:
            return _ACTIVE_FILE.read_text().strip() or None
        except OSError:
            pass
    return None


def set_active_id(profile_id: str | None) -> None:
    """Set (or clear) the active profile ID."""
    _ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_ACTIVE_FILE, (profile_id or "") + "\n")


def list_profiles_and_active() -> tuple[list[dict], str | None]:
    """Return (profiles_list, active_id).

    Each profile dict has keys: id, name, description, created_at, modified_at.
    """
    profiles = []
    if _PROFILES_DIR.exists():
        for d in sorted(_PROFILES_DIR.iterdir()):
            if d.is_dir() and (d / _META_FILE).exists():
                meta = _read_meta(d.name)
                profiles.append(
                    {
                        "id": d.name,
                        "name": meta.get("name", d.name),
                        "description": meta.get("description", ""),
                        "created_at": meta.get("created_at", ""),
                        "modified_at": meta.get("modified_at", ""),
                    }
                )
    return profiles, get_active_id()


def read_profile_values(profile_id: str) -> dict[str, str]:
    """Read the saved config values for a profile."""
    conf_path = _profile_dir(profile_id) / "hyprland-gui.conf"
    return parse_conf(conf_path)


def save_current_as(name: str, description: str = "") -> str:
    """Snapshot the current managed config as a new profile. Returns the profile ID."""
    profile_id = uuid.uuid4().hex[:_PROFILE_ID_LEN]
    pdir = _profile_dir(profile_id)
    pdir.mkdir(parents=True, exist_ok=True)
    conf_dest = pdir / "hyprland-gui.conf"
    if gui_conf().exists():
        _copy_file_atomic(gui_conf(), conf_dest)
    now = _now_iso()
    _write_meta(
        profile_id,
        {
            "name": name,
            "description": description,
            "created_at": now,
            "modified_at": now,
        },
    )
    set_active_id(profile_id)
    return profile_id


def update(profile_id: str) -> None:
    """Update an existing profile with the current config."""
    pdir = _profile_dir(profile_id)
    if not pdir.exists():
        return
    conf_dest = pdir / "hyprland-gui.conf"
    if gui_conf().exists():
        _copy_file_atomic(gui_conf(), conf_dest)
    meta = _read_meta(profile_id)
    meta["modified_at"] = _now_iso()
    _write_meta(profile_id, meta)


def activate_meta(profile_id: str) -> bool:
    """Set a profile as active and copy its snapshot to the managed config path."""
    pdir = _profile_dir(profile_id)
    conf_src = pdir / "hyprland-gui.conf"
    if not conf_src.exists():
        return False
    _copy_file_atomic(conf_src, gui_conf())
    set_active_id(profile_id)
    return True


def activate(profile_id: str, hypr: HyprlandState) -> bool:
    """Load a profile: apply all values via IPC, copy to the managed config, reload."""
    values = read_profile_values(profile_id)
    if values:
        hypr.apply_batch(list(values.items()), validate=False)

    if not activate_meta(profile_id):
        return False

    hypr.reload_compositor()
    return True


def delete(profile_id: str) -> None:
    """Delete a profile directory."""
    pdir = _profile_dir(profile_id)
    if pdir.exists():
        shutil.rmtree(pdir)
    if get_active_id() == profile_id:
        set_active_id(None)


def rename(profile_id: str, new_name: str) -> None:
    """Rename a profile (display name only, not the directory)."""
    meta = _read_meta(profile_id)
    meta["name"] = new_name
    _write_meta(profile_id, meta)


def update_description(profile_id: str, description: str) -> None:
    """Update a profile's description."""
    meta = _read_meta(profile_id)
    meta["description"] = description
    _write_meta(profile_id, meta)


def duplicate(profile_id: str) -> str:
    """Duplicate a profile. Returns the new profile ID."""
    meta = _read_meta(profile_id)
    new_id = uuid.uuid4().hex[:_PROFILE_ID_LEN]
    src = _profile_dir(profile_id)
    dst = _profile_dir(new_id)
    if src.exists():
        shutil.copytree(src, dst)
    now = _now_iso()
    _write_meta(
        new_id,
        {
            "name": f"{meta.get('name', 'Untitled')} (copy)",
            "description": meta.get("description", ""),
            "created_at": now,
            "modified_at": now,
        },
    )
    return new_id
