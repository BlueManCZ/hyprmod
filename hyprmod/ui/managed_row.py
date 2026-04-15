"""Reusable row builders and a state-tracking wrapper.

Bespoke pages (cursor, etc.) that can't fit the schema-driven ``OptionRow``
flow use these helpers to get the same visuals and bug-resistant behavior:

- ``make_*_row``: thin factories for Adw rows + their control widgets.
- ``ManagedRow``: wraps a row with a ``RowActions`` strip and automatic
  dirty/managed/saved style management against a baseline + default.
"""

from collections.abc import Callable
from typing import Any

from gi.repository import Adw, Gtk

from hyprmod.ui.row_actions import RowActions

# Adw row types that expose ``add_suffix`` — used by ManagedRow.
_SuffixRow = Adw.ActionRow | Adw.ComboRow | Adw.SpinRow | Adw.EntryRow | Adw.ExpanderRow


def make_spin_int_row(
    title: str,
    *,
    value: int,
    lower: int = 0,
    upper: int = 9999,
    step: int = 1,
    page_step: int = 5,
    subtitle: str = "",
) -> tuple[Adw.ActionRow, Gtk.SpinButton]:
    """Action row with a trailing integer ``Gtk.SpinButton`` suffix."""
    adj = Gtk.Adjustment(
        value=value,
        lower=lower,
        upper=upper,
        step_increment=step,
        page_increment=page_step,
    )
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    spin = Gtk.SpinButton(adjustment=adj, digits=0)
    spin.set_valign(Gtk.Align.CENTER)
    row.add_suffix(spin)
    return row, spin


def make_spin_float_row(
    title: str,
    *,
    value: float,
    lower: float = 0.0,
    upper: float = 100.0,
    step: float = 0.01,
    page_step: float | None = None,
    digits: int = 2,
    subtitle: str = "",
) -> tuple[Adw.ActionRow, Gtk.SpinButton]:
    """Action row with a trailing float ``Gtk.SpinButton`` suffix."""
    adj = Gtk.Adjustment(
        value=value,
        lower=lower,
        upper=upper,
        step_increment=step,
        page_increment=step * 10 if page_step is None else page_step,
    )
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    spin = Gtk.SpinButton(adjustment=adj, digits=digits)
    spin.set_valign(Gtk.Align.CENTER)
    row.add_suffix(spin)
    return row, spin


def make_combo_row(
    title: str,
    *,
    model,
    factory: Gtk.ListItemFactory | None = None,
    selected: int = 0,
    subtitle: str = "",
) -> Adw.ComboRow:
    """Combo row with an optional custom factory."""
    row = Adw.ComboRow(title=title, subtitle=subtitle, model=model)
    if factory is not None:
        row.set_factory(factory)
    row.set_selected(selected)
    return row


class ManagedRow:
    """Wraps an Adw row with a RowActions strip and dirty/managed/saved styles.

    Derives indicator state from the current value versus the baseline (saved
    value) and default (unmanaged value). Callers are responsible for keeping
    the widget's value in sync with ``get_value()`` — call :py:meth:`refresh`
    from the value-changed signal handler.

    Parameters
    ----------
    row:
        The Adw row to decorate.
    default:
        Value considered "unmanaged" (no override).
    baseline:
        Last saved value. Defaults to *default*.
    get_value:
        Callable returning the row's current value.
    set_value_silent:
        Callable taking a new value and updating the widget without triggering
        the caller's value-changed handler (typically uses ``SignalBlocker`` or
        ``handler_block`` around the widget's setter).
    on_value_set:
        Optional callback fired after discard/reset (value, is_reset) so the
        caller can trigger live-apply, undo-push, etc.
    """

    def __init__(
        self,
        row: _SuffixRow,
        *,
        default: Any,
        baseline: Any = None,
        get_value: Callable[[], Any],
        set_value_silent: Callable[[Any], None],
        on_value_set: Callable[[Any], None] | None = None,
        is_managed: Callable[[], bool] | None = None,
        is_saved: Callable[[], bool] | None = None,
    ):
        self.row = row
        self._default = default
        self._baseline = default if baseline is None else baseline
        self._get = get_value
        self._set_silent = set_value_silent
        self._on_value_set = on_value_set
        self._is_managed_override = is_managed
        self._is_saved_override = is_saved

        self._actions = RowActions(row, on_discard=self.discard, on_reset=self.reset)
        row.add_suffix(self._actions.box)
        self._actions.reorder_first()
        self.refresh()

    # ── state ──

    @property
    def value(self) -> Any:
        return self._get()

    @property
    def is_dirty(self) -> bool:
        return self.value != self._baseline

    @property
    def is_managed(self) -> bool:
        if self._is_managed_override is not None:
            return self._is_managed_override()
        return self.value != self._default

    @property
    def is_saved(self) -> bool:
        if self._is_saved_override is not None:
            return self._is_saved_override()
        return self._baseline != self._default

    def refresh(self) -> None:
        """Recompute indicator state from current value vs baseline/default."""
        self._actions.update(
            is_managed=self.is_managed,
            is_dirty=self.is_dirty,
            is_saved=self.is_saved,
        )

    def set_baseline(self, value: Any) -> None:
        """Update the saved baseline (call on save or profile reload)."""
        self._baseline = value
        self.refresh()

    # ── actions ──

    def discard(self) -> None:
        self._apply(self._baseline)

    def reset(self) -> None:
        self._apply(self._default)

    def _apply(self, value: Any) -> None:
        self._set_silent(value)
        self.refresh()
        if self._on_value_set is not None:
            self._on_value_set(value)
