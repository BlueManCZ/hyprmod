"""GTK-dependent keybind helpers — GDK modifier mapping and key capture utilities."""

from gi.repository import Gdk

# GdkModifierType -> our modifier names
GDK_MOD_MAP = {
    Gdk.ModifierType.SUPER_MASK: "SUPER",
    Gdk.ModifierType.SHIFT_MASK: "SHIFT",
    Gdk.ModifierType.CONTROL_MASK: "CTRL",
    Gdk.ModifierType.ALT_MASK: "ALT",
}

# Keys that are modifier-only (should not be recorded as the "key" part)
MODIFIER_KEYVALS = {
    "Super_L",
    "Super_R",
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Alt_L",
    "Alt_R",
    "Meta_L",
    "Meta_R",
    "Hyper_L",
    "Hyper_R",
    "ISO_Level3_Shift",
}


def gdk_state_to_mods(state: Gdk.ModifierType) -> list[str]:
    """Extract modifier names from GdkModifierType bitmask."""
    mods = []
    for gdk_bit, name in GDK_MOD_MAP.items():
        if state & gdk_bit:
            mods.append(name)
    return mods
