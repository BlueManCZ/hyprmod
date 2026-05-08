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


def keysyms_to_mods(held: set[str]) -> list[str]:
    """Return canonical Hyprland modifier names for the held modifier keysyms.

    Result order follows ``MOD_NAME_TO_KEYSYMS`` insertion order so the
    capture preview reads consistently.
    """
    return [name for name, ks in MOD_NAME_TO_KEYSYMS.items() if held & ks]


def unshifted_keyval(
    display: Gdk.Display,
    keycode: int,
    state: Gdk.ModifierType,
    group: int,
    fallback: int,
) -> int:
    """Resolve the keyval the keycode would produce without SHIFT.

    Hyprland binds use the unshifted keysym when ``SHIFT`` is in the modifier
    mask (e.g. ``SUPER SHIFT, 1`` rather than ``SUPER SHIFT, exclam`` on US,
    or ``SUPER SHIFT, plus`` on Czech). GDK already applied shift to give us
    the level-1+ symbol, so re-translate the keycode with shift cleared.
    Other modifiers (AltGr/level3) are preserved so layered layouts still get
    the right keysym.
    """
    ok, kv, *_ = display.translate_key(keycode, state & ~Gdk.ModifierType.SHIFT_MASK, group)
    return kv if ok and kv else fallback
