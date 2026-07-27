"""GTK-dependent keybind helpers — modifier-keysym tracking and key capture utilities."""

from gi.repository import Gdk

# Hyprland modifier names mapped to the X11/Wayland keysyms that produce them.
#
# Tracking pressed keysyms is more reliable than reading GDK's modifier
# bitmask: the bitmask depends on the current keymap defining the right
# virtual modifier (e.g. ``Hyper``), which Wayland compositors and GTK do
# not always agree on — a user with ``caps:hyper`` may press Caps and never
# see ``HYPER_MASK`` in the event state. The keysym a key produces is
# unambiguous and matches what Hyprland resolves binds against.
MOD_NAME_TO_KEYSYMS: dict[str, frozenset[str]] = {
    "SUPER": frozenset({"Super_L", "Super_R"}),
    "SHIFT": frozenset({"Shift_L", "Shift_R"}),
    "CTRL": frozenset({"Control_L", "Control_R"}),
    "ALT": frozenset({"Alt_L", "Alt_R", "Meta_L", "Meta_R"}),
    "MOD3": frozenset({"Hyper_L", "Hyper_R"}),
    "MOD5": frozenset({"ISO_Level3_Shift"}),
}

# Flat set of every keysym that counts as a modifier — used to skip
# modifier-only presses when capturing the bind's "key" part.
MODIFIER_KEYVALS: frozenset[str] = frozenset(
    ks for keysyms in MOD_NAME_TO_KEYSYMS.values() for ks in keysyms
)


# Bounds of the vendor keysym block (``XF86XK_*`` in X11's keysymdef), the
# only range where GDK's spelling of a keysym differs from xkb's.
_XF86_KEYVAL_MIN = 0x10080000
_XF86_KEYVAL_MAX = 0x1008FFFF

# The two vendor keysyms GDK names after their pre-xkb spelling, where
# restoring the prefix alone would not reach a name xkb knows.
_GDK_LEGACY_KEYSYM_NAMES = {
    "WindowClear": "XF86Clear",
    "SelectButton": "XF86Select",
}


def keysym_name(keyval: int) -> str | None:
    """Return *keyval*'s keysym name in the spelling Hyprland parses.

    GDK's generator strips the ``XF86`` prefix off the vendor block, so
    ``Gdk.keyval_name`` answers ``AudioRaiseVolume`` where xkb (and with it
    Hyprland's ``xkb_keysym_from_name``) resolves only
    ``XF86AudioRaiseVolume``. Handing Hyprland the GDK spelling gets the
    whole bind rejected with "Unknown keysym".

    ``None`` for a keyval GDK cannot name at all.
    """
    name = Gdk.keyval_name(keyval)
    if name is None or not _XF86_KEYVAL_MIN <= keyval <= _XF86_KEYVAL_MAX:
        return name
    # Vendor keysyms GDK has no name for come back as a "0x..." literal,
    # which xkb accepts verbatim. Prefixing one would break it.
    if name.startswith("0x"):
        return name
    return _GDK_LEGACY_KEYSYM_NAMES.get(name, f"XF86{name}")


def keysyms_to_mods(held: set[str]) -> list[str]:
    """Return canonical Hyprland modifier names for the held modifier keysyms.

    Result order follows ``MOD_NAME_TO_KEYSYMS`` insertion order so the
    capture preview reads consistently.
    """
    return [name for name, ks in MOD_NAME_TO_KEYSYMS.items() if held & ks]


def base_keyval(
    display: Gdk.Display,
    keycode: int,
    group: int,
    fallback: int,
) -> int:
    """Resolve the keyval the keycode produces with no modifier applied.

    Hyprland matches binds against the base-level keysym: the xkb state it
    translates key events through for bind lookup is created fresh and never
    receives a modifier mask, so Shift, AltGr and every other level chooser
    are ignored. A bind recorded from the modified keysym (``SUPER SHIFT,
    exclam`` on US, ``MOD5, backslash`` for AltGr+Q on Czech) can never fire.
    GDK hands us the modified keyval, so re-translate the keycode from
    scratch.
    """
    ok, kv, *_ = display.translate_key(keycode, Gdk.ModifierType(0), group)
    return kv if ok and kv else fallback
