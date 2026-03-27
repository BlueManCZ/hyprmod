"""Dispatcher categories, bind type metadata, and display helpers.

UI presentation data for Hyprland dispatchers and bind types — icons,
labels, categories, and human-readable formatting.
"""

from typing import TypedDict

# ---------------------------------------------------------------------------
# Bind types
# ---------------------------------------------------------------------------


class BindTypeInfo(TypedDict):
    """Metadata for a bind type (e.g. ``bind``, ``binde``)."""

    label: str
    desc: str


BIND_TYPES: dict[str, BindTypeInfo] = {
    "bind": {"label": "Normal", "desc": "Triggers on key press"},
    "binde": {"label": "Repeat", "desc": "Repeats while held (volume, resize)"},
    "bindm": {"label": "Mouse", "desc": "Mouse button bind (move/resize)"},
    "bindl": {"label": "Locked", "desc": "Works even when screen is locked"},
    "bindr": {"label": "Release", "desc": "Triggers on key release"},
    "bindn": {"label": "Non-consuming", "desc": "Key event passes through to windows"},
}

# ---------------------------------------------------------------------------
# Dispatcher category system
# ---------------------------------------------------------------------------


class DispatcherInfo(TypedDict):
    """Metadata for a single dispatcher."""

    label: str
    arg_type: str


class DispatcherInfoWithCategory(DispatcherInfo):
    """Dispatcher metadata augmented with its category id."""

    category_id: str


class DispatcherCategory(TypedDict):
    """A group of related dispatchers."""

    id: str
    label: str
    icon: str
    dispatchers: dict[str, DispatcherInfo]


DISPATCHER_CATEGORIES: list[DispatcherCategory] = [
    {
        "id": "apps",
        "label": "Launch Application",
        "icon": "system-run-symbolic",
        "dispatchers": {
            "exec": {"label": "Run command", "arg_type": "command"},
            "execr": {"label": "Run raw command", "arg_type": "command"},
        },
    },
    {
        "id": "window_mgmt",
        "label": "Window Management",
        "icon": "overlapping-windows-symbolic",
        "dispatchers": {
            "killactive": {"label": "Close window", "arg_type": "none"},
            "forcekillactive": {"label": "Force kill window", "arg_type": "none"},
            "togglefloating": {"label": "Toggle floating", "arg_type": "none"},
            "fullscreen": {"label": "Toggle fullscreen", "arg_type": "fullscreen_mode"},
            "pin": {"label": "Pin window", "arg_type": "none"},
            "centerwindow": {"label": "Center window", "arg_type": "none"},
            "pseudo": {"label": "Toggle pseudo-tiling", "arg_type": "none"},
            "layoutmsg": {"label": "Layout message", "arg_type": "text"},
        },
    },
    {
        "id": "workspace_nav",
        "label": "Workspace Navigation",
        "icon": "shell-overview-symbolic",
        "dispatchers": {
            "workspace": {"label": "Switch workspace", "arg_type": "workspace"},
            "movetoworkspace": {
                "label": "Move window to workspace",
                "arg_type": "workspace",
            },
            "movetoworkspacesilent": {
                "label": "Move window silently",
                "arg_type": "workspace",
            },
            "togglespecialworkspace": {
                "label": "Toggle scratchpad",
                "arg_type": "optional_text",
            },
        },
    },
    {
        "id": "window_focus",
        "label": "Focus and Move Windows",
        "icon": "move-to-window-symbolic",
        "dispatchers": {
            "movefocus": {"label": "Move focus", "arg_type": "direction"},
            "movewindow": {"label": "Move window", "arg_type": "direction"},
            "swapwindow": {"label": "Swap window", "arg_type": "direction"},
            "movewindoworgroup": {
                "label": "Move window or group",
                "arg_type": "direction",
            },
            "resizeactive": {"label": "Resize window", "arg_type": "text"},
            "cyclenext": {"label": "Cycle focus next", "arg_type": "none"},
            "swapnext": {"label": "Swap with next", "arg_type": "none"},
            "focuscurrentorlast": {"label": "Focus last window", "arg_type": "none"},
            "focusurgentorlast": {"label": "Focus urgent/last", "arg_type": "none"},
        },
    },
    {
        "id": "grouping",
        "label": "Window Grouping",
        "icon": "group-symbolic",
        "dispatchers": {
            "togglegroup": {"label": "Toggle group", "arg_type": "none"},
            "changegroupactive": {
                "label": "Cycle group member",
                "arg_type": "group_dir",
            },
            "moveoutofgroup": {"label": "Remove from group", "arg_type": "none"},
            "moveintogroup": {"label": "Move into group", "arg_type": "direction"},
            "movegroupwindow": {"label": "Reorder in group", "arg_type": "group_dir"},
            "lockgroups": {"label": "Lock all groups", "arg_type": "text"},
            "lockactivegroup": {"label": "Lock active group", "arg_type": "text"},
            "denywindowfromgroup": {
                "label": "Deny window from group",
                "arg_type": "text",
            },
        },
    },
    {
        "id": "monitor",
        "label": "Monitor Control",
        "icon": "preferences-desktop-display-symbolic",
        "dispatchers": {
            "focusmonitor": {"label": "Focus monitor", "arg_type": "text"},
            "movecurrentworkspacetomonitor": {
                "label": "Move workspace to monitor",
                "arg_type": "text",
            },
            "moveworkspacetomonitor": {
                "label": "Move specific workspace to monitor",
                "arg_type": "text",
            },
            "swapactiveworkspaces": {
                "label": "Swap workspaces between monitors",
                "arg_type": "text",
            },
            "focusworkspaceoncurrentmonitor": {
                "label": "Focus workspace on current monitor",
                "arg_type": "workspace",
            },
            "dpms": {"label": "Screen on/off", "arg_type": "dpms"},
        },
    },
    {
        "id": "session",
        "label": "Session",
        "icon": "computer-symbolic",
        "dispatchers": {
            "exit": {"label": "Exit Hyprland", "arg_type": "none"},
            "pass": {"label": "Pass key to window", "arg_type": "text"},
            "global": {"label": "Global shortcut", "arg_type": "text"},
            "submap": {"label": "Enter submap", "arg_type": "text"},
        },
    },
    {
        "id": "advanced",
        "label": "Other",
        "icon": "terminal-symbolic",
        "dispatchers": {},
    },
]


def _build_lookups() -> tuple[dict[str, DispatcherInfoWithCategory], dict[str, DispatcherCategory]]:
    """Build flat lookup dicts from DISPATCHER_CATEGORIES."""
    dispatcher_info: dict[str, DispatcherInfoWithCategory] = {}
    category_by_id: dict[str, DispatcherCategory] = {}
    for cat in DISPATCHER_CATEGORIES:
        category_by_id[cat["id"]] = cat
        for dname, dinfo in cat["dispatchers"].items():
            dispatcher_info[dname] = {**dinfo, "category_id": cat["id"]}
    return dispatcher_info, category_by_id


DISPATCHER_INFO, CATEGORY_BY_ID = _build_lookups()


def categorize_dispatcher(dispatcher: str) -> str:
    """Return category id for a dispatcher, defaulting to 'advanced'."""
    info = DISPATCHER_INFO.get(dispatcher)
    return info["category_id"] if info else "advanced"


def dispatcher_label(dispatcher: str) -> str:
    """Human-readable label for a dispatcher."""
    info = DISPATCHER_INFO.get(dispatcher)
    return info["label"] if info else dispatcher


def format_action(dispatcher: str, arg: str) -> str:
    """Human-readable action string: ``'Run command: firefox'`` or ``'Close window'``."""
    label = dispatcher_label(dispatcher)
    if arg:
        return f"{label}: {arg}"
    return label
