"""Dispatch from schema option dict to the appropriate ``OptionRow`` subclass."""

from hyprmod.ui.options.base import OptionRow
from hyprmod.ui.options.color import ColorOptionRow, GradientOptionRow
from hyprmod.ui.options.combo import ComboOptionRow, SourceComboOptionRow
from hyprmod.ui.options.multi import MultiSourceOptionRow
from hyprmod.ui.options.numeric import (
    SpinFloatOptionRow,
    SpinIntOptionRow,
    SwitchOptionRow,
    Vec2OptionRow,
)
from hyprmod.ui.options.text import EntryOptionRow

_ROW_CLASSES = {
    "bool": SwitchOptionRow,
    "int": SpinIntOptionRow,
    "float": SpinFloatOptionRow,
    "string": EntryOptionRow,
    "color": ColorOptionRow,
    "gradient": GradientOptionRow,
    "choice": ComboOptionRow,
    "vec2": Vec2OptionRow,
}


def create_option_row(
    option: dict, value, on_change, on_reset, on_discard=None
) -> OptionRow | None:
    """Create an OptionRow for the given schema option.

    Returns an OptionRow wrapper (access .row for the Gtk widget), or None if unsupported.
    """
    if option.get("source") and option.get("multi"):
        return MultiSourceOptionRow(option, value, on_change, on_reset, on_discard)
    if option.get("source"):
        return SourceComboOptionRow(option, value, on_change, on_reset, on_discard)
    cls = _ROW_CLASSES.get(option.get("type", ""))
    if cls is None:
        return None
    return cls(option, value, on_change, on_reset, on_discard)
