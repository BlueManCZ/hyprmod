"""Widget factory — creates the right Adw widget for each schema option type.

Each widget row gets:
- A modified-from-default visual indicator (accent left border)
- A reset-to-default button that appears on hover
- Error shake/flash on IPC failure
- Scale pulse on reset
"""

import math

from gi.repository import Adw, Gdk, GLib, Gtk
from hyprland_config import Color

from hyprmod.ui.row_actions import RowActions
from hyprmod.ui.signals import SignalBlocker
from hyprmod.ui.sources import get_source_values
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


class SwitchOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        row = Adw.SwitchRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        super().__init__(row, option, on_change, on_reset, on_discard)
        row.set_active(bool(value))

        def on_active_changed(row_, _pspec):
            self._emit_change(row_.get_active())

        self._change_handler_id = row.connect("notify::active", on_active_changed)

    def _set_widget_value(self, value):
        self.row.set_active(bool(value))  # type: ignore[attr-defined]


class SpinIntOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        min_val = option.get("min", 0)
        max_val = option.get("max", 9999)
        adjustment = Gtk.Adjustment(
            value=int(value) if value is not None else option.get("default", 0),
            lower=min_val,
            upper=max_val,
            step_increment=1,
            page_increment=5,
        )
        row = Adw.ActionRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        self._spin = Gtk.SpinButton(adjustment=adjustment, digits=0)
        self._spin.set_valign(Gtk.Align.CENTER)
        row.add_suffix(self._spin)
        super().__init__(row, option, on_change, on_reset, on_discard)
        self._signal_widget = self._spin

        def on_value_changed(btn):
            self._emit_change(int(btn.get_value()))

        self._change_handler_id = self._spin.connect("value-changed", on_value_changed)

    def _set_widget_value(self, value):
        self._spin.set_value(int(value))


class SpinFloatOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        min_val = option.get("min", 0.0)
        max_val = option.get("max", 100.0)
        step = option.get("step", 0.01)

        adjustment = Gtk.Adjustment(
            value=float(value) if value is not None else option.get("default", 0.0),
            lower=min_val,
            upper=max_val,
            step_increment=step,
            page_increment=step * 10,
        )

        self._digits = digits_for_step(step)

        row = Adw.ActionRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        self._spin = Gtk.SpinButton(adjustment=adjustment, digits=self._digits)
        self._spin.set_valign(Gtk.Align.CENTER)
        row.add_suffix(self._spin)
        super().__init__(row, option, on_change, on_reset, on_discard)
        self._signal_widget = self._spin

        def on_value_changed(btn):
            self._emit_change(round(btn.get_value(), self._digits))

        self._change_handler_id = self._spin.connect("value-changed", on_value_changed)

    def _set_widget_value(self, value):
        self._spin.set_value(float(value))


class Vec2OptionRow(OptionRow):
    """Two-spinbutton row for vec2 values like shadow offset ('x y')."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        x_val, y_val = self._parse_vec2(value, option.get("default", "0 0"))
        min_val = option.get("min", -10000)
        max_val = option.get("max", 10000)

        self._spin_x = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                value=x_val,
                lower=min_val,
                upper=max_val,
                step_increment=1,
                page_increment=5,
            ),
            digits=0,
        )
        self._spin_x.set_valign(Gtk.Align.CENTER)
        self._spin_y = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                value=y_val,
                lower=min_val,
                upper=max_val,
                step_increment=1,
                page_increment=5,
            ),
            digits=0,
        )
        self._spin_y.set_valign(Gtk.Align.CENTER)

        row = Adw.ActionRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        for label_text, widget, margin_start, margin_end in [
            ("X", self._spin_x, 0, 4),
            ("Y", self._spin_y, 12, 4),
        ]:
            lbl = Gtk.Label(label=label_text)
            lbl.add_css_class("dim-label")
            lbl.set_valign(Gtk.Align.CENTER)
            lbl.set_margin_start(margin_start)
            lbl.set_margin_end(margin_end)
            row.add_suffix(lbl)
            row.add_suffix(widget)

        super().__init__(row, option, on_change, on_reset, on_discard)

        self._signals = SignalBlocker()
        self._change_handler_id = self._signals.connect(
            self._spin_x,
            "value-changed",
            lambda _: self._emit_vec2(),
        )
        self._signals.connect(
            self._spin_y,
            "value-changed",
            lambda _: self._emit_vec2(),
        )

    @staticmethod
    def _parse_vec2(value, default: str) -> tuple[int, int]:
        raw = str(value) if value is not None else default
        parts = raw.split()
        try:
            return int(float(parts[0])), int(float(parts[1]))
        except (ValueError, IndexError):
            return 0, 0

    def _emit_vec2(self):
        x = int(self._spin_x.get_value())
        y = int(self._spin_y.get_value())
        self._emit_change(f"{x} {y}")

    def _set_widget_value(self, value):
        x, y = self._parse_vec2(value, self.option.get("default", "0 0"))
        self._spin_x.set_value(x)
        self._spin_y.set_value(y)


class EntryOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        row = Adw.EntryRow(
            title=option.get("label", option["key"]),
        )
        row.set_text(str(value) if value is not None else option.get("default", ""))
        row.set_tooltip_text(option.get("description", ""))
        row.set_show_apply_button(True)
        super().__init__(row, option, on_change, on_reset, on_discard)

        def on_apply(row_):
            self._emit_change(row_.get_text())

        self._change_handler_id = row.connect("apply", on_apply)

    def _set_widget_value(self, value):
        self.row.set_text(str(value) if value is not None else "")  # type: ignore[attr-defined]


def _hypr_color_to_rgba(value: str) -> Gdk.RGBA:
    """Convert Hyprland AARRGGBB or 0xAARRGGBB hex color to Gdk.RGBA."""
    rgba = Gdk.RGBA()
    try:
        c = Color.parse(value)
        rgba.red = c.r / 255.0
        rgba.green = c.g / 255.0
        rgba.blue = c.b / 255.0
        rgba.alpha = c.a / 255.0
    except (ValueError, TypeError):
        rgba.red = rgba.green = rgba.blue = rgba.alpha = 1.0
    return rgba


def _rgba_to_hypr_color(rgba: Gdk.RGBA) -> str:
    """Convert Gdk.RGBA to Hyprland 0xAARRGGBB hex string."""
    return Color(
        r=round(rgba.red * 255),
        g=round(rgba.green * 255),
        b=round(rgba.blue * 255),
        a=round(rgba.alpha * 255),
    ).to_hex()


def _parse_gradient(value: str) -> tuple[list[str], int]:
    """Parse a gradient string into (color_hex_list, angle_degrees).

    Input format (from IPC): 'AARRGGBB AARRGGBB 45deg'
    Input format (from config): '0xAARRGGBB 0xAARRGGBB 45deg'
    """
    parts = str(value).split()
    colors = []
    angle = 0
    for part in parts:
        if part.endswith("deg"):
            try:
                angle = int(part[:-3])
            except ValueError:
                pass
        else:
            colors.append(part)
    if not colors:
        colors = ["ffffffff"]
    return colors, angle


def _build_gradient(colors: list[str], angle: int) -> str:
    """Build a gradient string for IPC (0x-prefixed)."""
    parts = [c if c.startswith("0x") else f"0x{c}" for c in colors]
    parts.append(f"{angle}deg")
    return " ".join(parts)


class ColorOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        row = Adw.ActionRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )

        self._color_button = Gtk.ColorDialogButton()
        self._color_button.set_dialog(Gtk.ColorDialog())
        self._color_button.set_valign(Gtk.Align.CENTER)
        initial = value or option.get("default") or "0xffffffff"
        self._color_button.set_rgba(_hypr_color_to_rgba(initial))
        row.add_suffix(self._color_button)

        super().__init__(row, option, on_change, on_reset, on_discard)
        self._signal_widget = self._color_button

        def on_color_changed(btn, _pspec):
            self._emit_change(_rgba_to_hypr_color(btn.get_rgba()))

        self._change_handler_id = self._color_button.connect("notify::rgba", on_color_changed)

    def _set_widget_value(self, value):
        self._color_button.set_rgba(_hypr_color_to_rgba(value or "0xffffffff"))


class GradientOptionRow(OptionRow):
    """Row with color picker(s) + angle spinner for Hyprland gradient values."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        row = Adw.ActionRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )

        initial = value or option.get("default") or "0xffffffff"
        colors, angle = _parse_gradient(initial)

        self._signals = SignalBlocker()

        # Angle spinner
        adj = Gtk.Adjustment(
            value=angle,
            lower=0,
            upper=360,
            step_increment=5,
        )
        self._spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        self._spin.set_valign(Gtk.Align.CENTER)
        self._spin.set_tooltip_text("Angle (degrees)")
        self._spin.set_width_chars(4)

        # Container for color stops
        self._stops_box = Gtk.Box(spacing=4)
        self._stops_box.set_valign(Gtk.Align.CENTER)

        # Each stop is (color_button, remove_button) tracked together
        self._color_buttons: list[Gtk.ColorDialogButton] = []
        self._stop_boxes: list[Gtk.Box] = []

        suffix_box = Gtk.Box(spacing=6)
        suffix_box.set_valign(Gtk.Align.CENTER)

        for c in colors:
            self._add_color_stop(c, emit=False)

        suffix_box.append(self._stops_box)
        suffix_box.append(self._spin)

        # Add color button
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.set_tooltip_text("Add color stop")
        add_btn.add_css_class("flat")
        add_btn.connect("clicked", lambda _: self._on_add_color())
        suffix_box.append(add_btn)
        self._add_btn = add_btn

        row.add_suffix(suffix_box)

        super().__init__(row, option, on_change, on_reset, on_discard)
        self._signal_widget = self._spin

        self._change_handler_id = self._signals.connect(
            self._spin,
            "value-changed",
            lambda _spin: self._emit_gradient(),
        )
        self._update_remove_visibility()

    def _add_color_stop(self, color: str, *, emit: bool = True):
        """Add a color stop with its color button and remove button."""
        stop_box = Gtk.Box(spacing=0)
        stop_box.set_valign(Gtk.Align.CENTER)

        btn = Gtk.ColorDialogButton()
        btn.set_dialog(Gtk.ColorDialog())
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_rgba(_hypr_color_to_rgba(color))
        self._signals.connect(
            btn,
            "notify::rgba",
            lambda *_: self._emit_gradient(),
        )

        rm_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        rm_btn.set_valign(Gtk.Align.CENTER)
        rm_btn.set_tooltip_text("Remove color stop")
        rm_btn.add_css_class("flat")
        rm_btn.add_css_class("circular")

        stop_box.append(btn)
        stop_box.append(rm_btn)

        rm_btn.connect(
            "clicked",
            lambda _, sb=stop_box: self._on_remove_stop(sb),
        )

        self._color_buttons.append(btn)
        self._stop_boxes.append(stop_box)
        self._stops_box.append(stop_box)

        if emit:
            self._update_remove_visibility()
            self._emit_gradient()

    def _on_add_color(self):
        """Add a new white color stop (max 10)."""
        if len(self._color_buttons) >= 10:
            return
        self._add_color_stop("ffffffff")

    def _on_remove_stop(self, stop_box: Gtk.Box):
        """Remove a color stop by its container widget."""
        if len(self._color_buttons) <= 1:
            return
        idx = self._stop_boxes.index(stop_box)
        self._stops_box.remove(stop_box)
        self._color_buttons.pop(idx)
        self._stop_boxes.pop(idx)
        self._update_remove_visibility()
        self._emit_gradient()

    def _update_remove_visibility(self):
        """Hide remove buttons when only one stop; hide add at max."""
        single = len(self._color_buttons) <= 1
        for box in self._stop_boxes:
            rm = box.get_last_child()
            if rm is not None:
                rm.set_visible(not single)
        self._add_btn.set_visible(len(self._color_buttons) < 10)

    def _emit_gradient(self):
        colors = [_rgba_to_hypr_color(btn.get_rgba()) for btn in self._color_buttons]
        angle = int(self._spin.get_value())
        self._emit_change(_build_gradient(colors, angle))

    def _set_widget_value(self, value):
        colors, angle = _parse_gradient(value or "ffffffff")
        self._spin.set_value(angle)
        # Remove excess stops
        while len(self._color_buttons) > len(colors):
            self._stops_box.remove(self._stop_boxes.pop())
            self._color_buttons.pop()
        # Update existing / add new
        for i, c in enumerate(colors):
            if i < len(self._color_buttons):
                self._color_buttons[i].set_rgba(_hypr_color_to_rgba(c))
            else:
                self._add_color_stop(c, emit=False)
        self._update_remove_visibility()


def _is_int(s: str) -> bool:
    """Return True if *s* looks like an integer (including negative)."""
    return s.lstrip("-").isdigit() if s else False


class ComboOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        values = option.get("values", [])
        self._labels = [v["label"] for v in values]
        self._ids = [v.get("id", str(i)) for i, v in enumerate(values)]

        string_list = Gtk.StringList.new(self._labels)
        row = Adw.ComboRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
            model=string_list,
        )

        current_str = str(value) if value is not None else str(option.get("default", ""))
        if current_str in self._ids:
            row.set_selected(self._ids.index(current_str))

        # IDs are always strings (from JSON schema), but live values from IPC
        # may be int.  Coerce emitted IDs to match so dirty checks work.
        self._coerce = int if all(_is_int(i) for i in self._ids) else None

        super().__init__(row, option, on_change, on_reset, on_discard)

        def on_selected_changed(row_, _pspec):
            idx = row_.get_selected()
            if 0 <= idx < len(self._ids):
                val = self._ids[idx]
                if self._coerce is not None:
                    try:
                        val = self._coerce(val)
                    except (ValueError, TypeError):
                        pass
                self._emit_change(val)

        self._change_handler_id = row.connect("notify::selected", on_selected_changed)

    def _set_widget_value(self, value):
        val_str = str(value) if value is not None else ""
        if val_str in self._ids:
            self.row.set_selected(self._ids.index(val_str))  # type: ignore[attr-defined]


def _wrapping_label_setup(_factory, list_item):
    """Factory setup callback: create a wrapping label for dropdown items."""
    label = Gtk.Label(xalign=0)
    label.set_wrap(True)
    label.set_margin_top(6)
    label.set_margin_bottom(6)
    label.set_margin_start(6)
    label.set_margin_end(6)
    list_item.set_child(label)


class SourceComboOptionRow(OptionRow):
    """ComboRow whose values come from a dynamic source, with search enabled."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        self._source_name = option["source"]
        self._source_args = dict(option.get("source_args", {}))

        self._labels = []
        self._ids = []

        row = Adw.ComboRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )
        row.set_enable_search(True)
        row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        row.add_css_class("wide-dropdown")
        row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))

        # Custom list factory so dropdown labels don't truncate
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", _wrapping_label_setup)
        factory.connect("bind", self._on_factory_bind)
        row.set_list_factory(factory)

        super().__init__(row, option, on_change, on_reset, on_discard)

        self._populate(value)

        def on_selected_changed(row_, _pspec):
            idx = row_.get_selected()
            if 0 <= idx < len(self._ids):
                self._emit_change(self._ids[idx])

        self._change_handler_id = row.connect("notify::selected", on_selected_changed)

    def _populate(self, select_value=None):
        """Rebuild the dropdown items from the source."""
        values = get_source_values(self._source_name, **self._source_args)
        self._labels = [v["label"] for v in values]
        self._ids = [v["id"] for v in values]

        self.row.set_model(Gtk.StringList.new(self._labels))  # type: ignore[attr-defined]

        current_str = str(select_value) if select_value is not None else ""
        if current_str in self._ids:
            self.row.set_selected(self._ids.index(current_str))  # type: ignore[attr-defined]

    def refresh_source(self, **kwargs):
        """Re-populate the dropdown with updated source args."""
        self._source_args.update(kwargs)
        self._populate(select_value=self.option.get("default", ""))

    @staticmethod
    def _on_factory_bind(_factory, list_item):
        label = list_item.get_child()
        item = list_item.get_item()
        label.set_label(item.get_string())

    def _set_widget_value(self, value):
        val_str = str(value) if value is not None else ""
        if val_str in self._ids:
            self.row.set_selected(self._ids.index(val_str))  # type: ignore[attr-defined]


_MULTI_SEP = "\x1f"  # separator between group and label in model strings


class MultiSourceOptionRow(OptionRow):
    """Row for comma-separated multi-value options with a searchable add dropdown."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        self._source_name = option["source"]
        source_values = get_source_values(self._source_name)
        self._all_items = {v["id"]: v for v in source_values}
        self._selected: list[str] = []
        self._selected_rows: list[Adw.ActionRow] = []

        # Main expander row
        row = Adw.ExpanderRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )

        super().__init__(row, option, on_change, on_reset, on_discard)

        # Picker row with searchable combo inside the expander
        self._picker_row = Adw.ActionRow(title="Add option…")
        self._picker_row.add_css_class("option-default")

        self._combo = Gtk.DropDown()
        self._combo.set_enable_search(True)
        self._combo.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._combo.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self._combo.set_valign(Gtk.Align.CENTER)

        # Factory for the selected item display (strip encoded group prefix)
        selected_factory = Gtk.SignalListItemFactory()
        selected_factory.connect("setup", _wrapping_label_setup)
        selected_factory.connect("bind", self._on_selected_bind)
        self._combo.set_factory(selected_factory)

        # List item factory (just the label, no group — headers handle that)
        list_factory = Gtk.SignalListItemFactory()
        list_factory.connect("setup", self._on_list_setup)
        list_factory.connect("bind", self._on_list_bind)
        self._combo.set_list_factory(list_factory)

        # Header factory for section separators
        header_factory = Gtk.SignalListItemFactory()
        header_factory.connect("setup", self._on_header_setup)
        header_factory.connect("bind", self._on_header_bind)
        self._combo.set_header_factory(header_factory)

        self._combo.add_css_class("wide-dropdown")

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add selected option")
        add_btn.connect("clicked", self._on_add_clicked)

        self._picker_row.add_suffix(self._combo)
        self._picker_row.add_suffix(add_btn)
        row.add_row(self._picker_row)

        # Parse initial value and populate
        current = str(value) if value else ""
        if current:
            for item_id in current.split(","):
                item_id = item_id.strip()
                if item_id and item_id in self._all_items:
                    self._selected.append(item_id)
        self._rebuild_selected_rows()
        self._rebuild_picker_model()

    @staticmethod
    def _section_sort(a, b, _user_data):
        """Section sorter: group items by the group prefix."""
        ga = a.get_string().split(_MULTI_SEP)[0]
        gb = b.get_string().split(_MULTI_SEP)[0]
        return (ga > gb) - (ga < gb)

    def _rebuild_picker_model(self):
        """Rebuild the dropdown model, excluding already-selected items.

        Each model string is encoded as "group SEP item_id SEP display_label".
        """
        selected_set = set(self._selected)
        model_strings = []
        for item_id, item in sorted(
            self._all_items.items(),
            key=lambda kv: (kv[1].get("group", ""), kv[1]["label"].casefold()),
        ):
            if item_id not in selected_set:
                group = item.get("group", "")
                label = f"{item['label']} ({item_id})"
                model_strings.append(f"{group}{_MULTI_SEP}{item_id}{_MULTI_SEP}{label}")
        string_list = Gtk.StringList.new(model_strings)
        section_model = Gtk.SortListModel(model=string_list)
        section_model.set_section_sorter(Gtk.CustomSorter.new(self._section_sort))
        self._combo.set_model(section_model)
        if model_strings:
            self._combo.set_selected(0)

    def _rebuild_selected_rows(self):
        """Rebuild the child rows showing selected options."""
        for r in self._selected_rows:
            self.row.remove(r)
        self._selected_rows = []

        for item_id in self._selected:
            item = self._all_items.get(item_id, {})
            label = item.get("label", item_id)
            group = item.get("group", "")

            child_row = Adw.ActionRow(title=label)
            if group:
                child_row.set_subtitle(group)

            code_label = Gtk.Label(label=item_id)
            code_label.add_css_class("dim-label")
            code_label.add_css_class("caption")
            child_row.add_suffix(code_label)

            remove_btn = Gtk.Button(icon_name="edit-clear-symbolic")
            remove_btn.set_valign(Gtk.Align.CENTER)
            remove_btn.add_css_class("flat")
            remove_btn.set_tooltip_text("Remove")
            remove_btn.connect("clicked", self._on_remove_clicked, item_id)
            child_row.add_suffix(remove_btn)

            self.row.add_row(child_row)  # type: ignore[attr-defined]
            self._selected_rows.append(child_row)

        self._update_subtitle()

    def _update_subtitle(self):
        """Update the expander subtitle with count of selected options."""
        if self._selected:
            self.row.set_subtitle(f"{len(self._selected)} option(s) selected")  # type: ignore[attr-defined]
        else:
            self.row.set_subtitle(self.option.get("description", ""))  # type: ignore[attr-defined]

    def _on_add_clicked(self, _button):
        idx = self._combo.get_selected()
        model = self._combo.get_model()
        if model is None or idx == Gtk.INVALID_LIST_POSITION or idx >= model.get_n_items():
            return
        item = model.get_item(idx)
        if item is None:
            return
        raw: str = item.get_string()  # type: ignore[attr-defined]
        parts = raw.split(_MULTI_SEP, 2)
        if len(parts) == 3:
            item_id = parts[1]
            self._selected.append(item_id)
            self._rebuild_selected_rows()
            self._rebuild_picker_model()
            self._emit_value()

    def _on_remove_clicked(self, _button, item_id):
        if item_id in self._selected:
            self._selected.remove(item_id)
            self._rebuild_selected_rows()
            self._rebuild_picker_model()
            self._emit_value()

    @staticmethod
    def _on_selected_bind(_factory, list_item):
        """Show just the display label for the selected item."""
        label = list_item.get_child()
        raw = list_item.get_item().get_string()
        parts = raw.split(_MULTI_SEP, 2)
        label.set_label(parts[2] if len(parts) == 3 else raw)

    _on_list_setup = staticmethod(_wrapping_label_setup)

    @staticmethod
    def _on_list_bind(_factory, list_item):
        label = list_item.get_child()
        raw = list_item.get_item().get_string()
        parts = raw.split(_MULTI_SEP, 2)
        label.set_label(parts[2] if len(parts) == 3 else raw)

    @staticmethod
    def _on_header_setup(_factory, list_header):
        label = Gtk.Label(xalign=0)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        label.set_margin_top(8)
        label.set_margin_bottom(4)
        label.set_margin_start(6)
        list_header.set_child(label)

    @staticmethod
    def _on_header_bind(_factory, list_header):
        label = list_header.get_child()
        raw = list_header.get_item().get_string()
        group = raw.split(_MULTI_SEP)[0]
        label.set_label(group)

    def _emit_value(self):
        self._emit_change(",".join(self._selected))

    def refresh_source(self, **kwargs):
        """Re-populate the available items from the source."""
        source_values = get_source_values(self._source_name, **kwargs)
        self._all_items = {v["id"]: v for v in source_values}
        self._rebuild_selected_rows()
        self._rebuild_picker_model()

    def _set_widget_value(self, value):
        self._selected.clear()
        val_str = str(value) if value else ""
        if val_str:
            for item_id in val_str.split(","):
                item_id = item_id.strip()
                if item_id and item_id in self._all_items:
                    self._selected.append(item_id)
        self._rebuild_selected_rows()
        self._rebuild_picker_model()


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
