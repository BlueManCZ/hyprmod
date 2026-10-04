"""Projection preset store and default layouts."""

import json
from collections.abc import Iterable
from typing import Any, cast

from hyprland_config import atomic_write
from hyprland_monitors.monitors import MonitorState

from hyprmod.core.config import HYPRMOD_DIR

_PRESET_FILE = HYPRMOD_DIR / "projection_preset"

# "No preset selected": presets leave the layout alone and it never gets a slot.
NO_PRESET = "none"

Layout = dict[str, dict[str, Any]]


class PresetStore:
    """Handles the storage and retrieval of monitor layout presets.
    Persisted as JSON at ``HYPRMOD_DIR/projection_preset``::
    Example:
        {
            "active": "only:DP-1",          # preset key selected in the dropdown
            "layouts": {                    # preset key -> Layout
                "extend": {                 # monitor name -> restorable fields
                    "DP-1": {
                        "width": 2560, "height": 1440, "refresh_rate": 144.0,
                        "x": 0, "y": 0, "scale": 1.0, "transform": 0,
                        "mode": None, "position": None,
                        "mirror_of": None, "disabled": False,
                        ...                 # extras (vrr, bit_depth, sdr_*, ...)
                    },
                    "DP-2": {
                        "width": 1920, "height": 1080, "refresh_rate": 60.0,
                        "x": 2560, "y": 0, ...
                    },
                },
                "only:DP-1": {
                    "DP-1": {"disabled": False, ...},
                    "DP-2: {"disabled": True, ...},
                },
            },
        }
    """

    def __init__(self, active: str, layouts: dict[str, Layout]):
        self.active = active
        self.layouts = layouts

    @classmethod
    def load(cls) -> "PresetStore":
        try:
            data: object = json.loads(_PRESET_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data = cast(dict[str, object], data)

        active = data.get("active")
        raw_layouts = data.get("layouts")
        layouts: dict[str, Layout] = {}
        if isinstance(raw_layouts, dict):
            for key, layout in cast(dict[str, object], raw_layouts).items():
                if not isinstance(layout, dict):
                    continue
                layouts[key] = {
                    name: cast(dict[str, Any], fields)
                    for name, fields in cast(dict[str, object], layout).items()
                    if isinstance(fields, dict)
                }
        return cls(active if isinstance(active, str) else NO_PRESET, layouts)

    def save(self) -> None:
        data = {"active": self.active, "layouts": self.layouts}
        _PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(_PRESET_FILE, json.dumps(data, indent=2) + "\n")

    def remember(self, key: str, monitors: list[MonitorState], fields: Iterable[str]) -> None:
        if key == NO_PRESET:
            return
        fields = tuple(fields)
        layout: Layout = {}
        for m in monitors:
            snapshot = {f: getattr(m, f) for f in fields}
            # The IPC resync has already written the coordinates Hyprland picked for
            # "auto" into x/y. Store those instead of the keyword so restoring this
            # layout puts the monitor back exactly where it was.
            if snapshot.get("position") == "auto" and not m.disabled and not m.mirror_of:
                snapshot["position"] = None
            layout[m.name] = snapshot
        self.layouts[key] = layout


# Monitor layout presets


def _only(selected: str, monitors: list[MonitorState]) -> Layout:
    return {m.name: {"disabled": m.name != selected} for m in monitors}


def _extend(monitors: list[MonitorState]) -> Layout:
    saved = PresetStore.load().layouts.get("extend", {})
    layout: Layout = {}
    for m in monitors:
        layout[m.name] = {"disabled": False, "mirror_of": None}
        stored = saved.get(m.name, {})
        if "x" in stored and "y" in stored and stored.get("position") != "auto":
            # The user positioned this monitor manually while extending.
            layout[m.name].update(x=stored["x"], y=stored["y"])
        else:
            layout[m.name]["position"] = "auto"
    return layout


def _mirror(monitors: list[MonitorState]) -> Layout:
    """NOT IMPLEMENTED:
    Mirror preset is not implemented yet due to a bug in hyprland-monitors.
    See https://github.com/BlueManCZ/hyprmod/issues/90"""

    return {}
    # if not monitors:
    #     return {}
    # primary = next((m for m in monitors if m.focused), monitors[0])
    # return {
    #     m.name: {"disabled": False, "mirror_of": None if m is primary else primary.name}
    #     for m in monitors
    # }


def default_layout(key: str, monitors: list[MonitorState]) -> Layout:
    """Layout to apply for a preset that has never been used before."""
    action, _, arg = key.partition(":")
    if action == "only":
        return _only(arg, monitors)
    if action == "extend":
        return _extend(monitors)
    if action == "mirror":
        return _mirror(monitors)
    return {}
