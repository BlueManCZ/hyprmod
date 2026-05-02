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

# Bind types selectable when the dialog's trigger mode is "Key combination".
# ``bindm`` is excluded because it's reached via the dedicated "Mouse button"
# trigger mode instead.
KEY_BIND_TYPES: dict[str, BindTypeInfo] = {k: v for k, v in BIND_TYPES.items() if k != "bindm"}

# ---------------------------------------------------------------------------
# Mouse buttons & bindm-specific dispatchers
# ---------------------------------------------------------------------------


# Friendly preset list for the mouse-drag button picker. The bare
# ``mouse:N`` strings on the left are Hyprland's wire format.
MOUSE_BUTTON_PRESETS: list[tuple[str, str]] = [
    ("mouse:272", "Left button"),
    ("mouse:273", "Right button"),
    ("mouse:274", "Middle button"),
    ("mouse:275", "Back"),
    ("mouse:276", "Forward"),
]

MOUSE_BUTTON_LABELS: dict[str, str] = dict(MOUSE_BUTTON_PRESETS)


# Linux input event button codes for ``GtkGestureClick`` (button 1 = left,
# 2 = middle, 3 = right, 8 = back, 9 = forward) mapped onto Hyprland's
# ``mouse:NNN`` evdev codes (272 + N - 1 for the standard buttons,
# 275/276 for the side buttons). Used by the Record button to translate a
# captured click into the wire format.
GDK_BUTTON_TO_MOUSE_KEY: dict[int, str] = {
    1: "mouse:272",  # Left
    2: "mouse:274",  # Middle
    3: "mouse:273",  # Right
    8: "mouse:275",  # Back
    9: "mouse:276",  # Forward
}


# Dispatchers that make sense for ``bindm`` (mouse drag). These overlap with
# entries in "Window Management" and "Focus and Move Windows" — the same
# dispatcher name behaves differently as a key vs mouse bind, so the
# bindm-only label set is kept separate from ``DISPATCHER_INFO`` to avoid
# clobbering the keyboard variant in the flat lookup.
BINDM_DISPATCHERS: dict[str, str] = {
    "movewindow": "Move window",
    "resizewindow": "Resize window",
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
        "id": "mouse_button",
        "label": "Mouse Button",
        "icon": "input-mouse-symbolic",
        # Empty: ``movewindow`` / ``resizewindow`` already live in
        # "Focus and Move Windows" / "Window Management", and the flat
        # ``DISPATCHER_INFO`` lookup can only hold one category per
        # dispatcher name. The dialog reads ``BINDM_DISPATCHERS`` directly
        # when the trigger mode is "Mouse button".
        "dispatchers": {},
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


# Flat lookups derived from DISPATCHER_CATEGORIES at import time.
CATEGORY_BY_ID: dict[str, DispatcherCategory] = {cat["id"]: cat for cat in DISPATCHER_CATEGORIES}
DISPATCHER_INFO: dict[str, DispatcherInfoWithCategory] = {
    dname: {**dinfo, "category_id": cat["id"]}
    for cat in DISPATCHER_CATEGORIES
    for dname, dinfo in cat["dispatchers"].items()
}


def categorize_dispatcher(dispatcher: str) -> str:
    """Return category id for a dispatcher, defaulting to 'advanced'."""
    info = DISPATCHER_INFO.get(dispatcher)
    return info["category_id"] if info else "advanced"


def categorize_bind(bind_type: str, dispatcher: str) -> str:
    """Return category id for a bind.

    ``bindm`` always categorises as ``mouse_button`` regardless of dispatcher,
    because ``movewindow``/``resizewindow`` also exist as keyboard
    dispatchers (with a directional argument) and would otherwise be
    bucketed alongside their key-mode siblings.
    """
    if bind_type == "bindm":
        return "mouse_button"
    return categorize_dispatcher(dispatcher)


def dispatcher_label(dispatcher: str) -> str:
    """Human-readable label for a dispatcher."""
    info = DISPATCHER_INFO.get(dispatcher)
    return info["label"] if info else dispatcher


def bind_dispatcher_label(bind_type: str, dispatcher: str) -> str:
    """Human-readable label for a bind's dispatcher.

    Uses ``BINDM_DISPATCHERS`` when ``bind_type == "bindm"`` so mouse-drag
    binds read as "Move window" / "Resize window" instead of the
    direction-flavoured keyboard label.
    """
    if bind_type == "bindm" and dispatcher in BINDM_DISPATCHERS:
        return BINDM_DISPATCHERS[dispatcher]
    return dispatcher_label(dispatcher)


def format_action(dispatcher: str, arg: str) -> str:
    """Human-readable action string: ``'Run command: firefox'`` or ``'Close window'``."""
    label = dispatcher_label(dispatcher)
    if arg:
        return f"{label}: {arg}"
    return label


def format_bind_action(bind_type: str, dispatcher: str, arg: str) -> str:
    """Human-readable action string for a bind, with bindm-specific labels."""
    label = bind_dispatcher_label(bind_type, dispatcher)
    if arg:
        return f"{label}: {arg}"
    return label
