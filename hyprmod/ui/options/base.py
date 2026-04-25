"""Base ``OptionRow`` class and shared utilities for option widgets.

Every concrete row subclass wraps an Adw row widget and inherits:

- A modified-from-default visual indicator (accent left border)
- A reset-to-default button that appears on hover
- Error shake/flash on IPC failure
- Scale pulse on reset
"""

import math

from gi.repository import Adw, GLib

from hyprmod.ui.row_actions import RowActions
from hyprmod.ui.timer import Timer

_SHAKE_OFFSETS = (0, -4, 4, -3, 3, -1, 0)


def digits_for_step(step: float) -> int:
    """Return the number of decimal digits needed to display a given step size."""
    if step <= 0:
        return 2
    return max(0, -math.floor(math.log10(step)))


class OptionRow:
    """Wraps an Adw row widget with modification tracking and reset support."""

    def __init__(
        self,
        row: Adw.ActionRow | Adw.SwitchRow | Adw.ComboRow | Adw.EntryRow | Adw.ExpanderRow,
        option: dict,
        on_change,
        on_reset,
        on_discard=None,
    ):
        self.row = row
        self.option = option
        self.key = option["key"]
        self.default_value = option.get("default")
        self._on_change = on_change
        self._on_reset = on_reset
        self._on_discard_single = on_discard
        self._change_handler_id = None
        self._signal_widget = None  # widget carrying the change signal (defaults to row)

        self._actions = RowActions(
            row,
            on_discard=self._do_discard,
            on_reset=self._do_reset,
        )
        row.add_suffix(self._actions.box)
        self._actions.reorder_first()

        self._error_timer = Timer()
        self._highlight_timer = Timer()
        self._shake_timer = Timer()
        self._shake_step_idx = 0

    def update_modified_state(
        self, is_managed: bool, is_dirty: bool = False, is_saved: bool = False
    ):
        """Update visual indicator and button visibility via shared RowActions."""
        self._actions.update(is_managed=is_managed, is_dirty=is_dirty, is_saved=is_saved)

    def flash_error(self):
        """Play the error red flash + shake animation."""
        self.row.set_margin_start(0)
        self.row.add_css_class("option-error")
        self._shake_step_idx = 0
        self._shake_timer.schedule(50, self._shake_tick)
        self._error_timer.schedule(600, self._remove_class, "option-error")

    def flash_highlight(self, duration_ms: int = 800):
        """Brief highlight glow to draw attention (search navigation, reset, etc.)."""
        self.row.add_css_class("option-highlight")
        self._highlight_timer.schedule(duration_ms, self._remove_class, "option-highlight")

    def _shake_tick(self):
        """Programmatic shake via margin-start offsets, driven by Timer."""
        if self._shake_step_idx < len(_SHAKE_OFFSETS):
            self.row.set_margin_start(_SHAKE_OFFSETS[self._shake_step_idx])
            self._shake_step_idx += 1
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_REMOVE

    def set_value_silent(self, value):
        """Set the widget value without triggering the change callback.

        Subclasses that use a SignalBlocker should store it as ``_signals``
        — this method will use it to block all registered signals at once.
        Otherwise falls back to blocking ``_change_handler_id`` on the
        signal widget.
        """
        signals = getattr(self, "_signals", None)
        if signals is not None:
            with signals:
                self._set_widget_value(value)
        else:
            w = self._signal_widget or self.row
            if self._change_handler_id is not None:
                w.handler_block(self._change_handler_id)
            try:
                self._set_widget_value(value)
            finally:
                if self._change_handler_id is not None:
                    w.handler_unblock(self._change_handler_id)

    def _set_widget_value(self, value):
        """Override in subclasses to update the widget without triggering signals."""
        raise NotImplementedError

    def _do_reset(self):
        """Remove override — pending removal from config."""
        self._on_reset(self.key, self.default_value)

    def _do_discard(self):
        """Discard changes — revert to saved value."""
        if self._on_discard_single:
            self._on_discard_single(self.key)

    def _remove_class(self, css_class):
        self.row.remove_css_class(css_class)
        return GLib.SOURCE_REMOVE

    def refresh_source(self, **kwargs):
        """Refresh dynamic source values. Override in source-backed rows."""

    def _emit_change(self, value):
        self._on_change(self.key, value)
