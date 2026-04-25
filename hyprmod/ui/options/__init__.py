"""Widget factory — creates the right Adw widget for each schema option type.

Each widget row gets:
- A modified-from-default visual indicator (accent left border)
- A reset-to-default button that appears on hover
- Error shake/flash on IPC failure
- Scale pulse on reset

The package is split by widget family (``base``, ``numeric``, ``text``,
``color``, ``combo``, ``multi``, ``factory``); this module re-exports the
public surface so existing ``from hyprmod.ui.options import …`` imports
keep working.
"""

from hyprmod.ui.options.base import OptionRow, digits_for_step
from hyprmod.ui.options.factory import create_option_row

__all__ = ["OptionRow", "create_option_row", "digits_for_step"]
