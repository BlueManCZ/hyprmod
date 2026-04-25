"""Color and gradient option rows plus their parse/format helpers."""

from gi.repository import Adw, Gdk, Gtk
from hyprland_config import Color

from hyprmod.ui.options.base import OptionRow
from hyprmod.ui.signals import SignalBlocker


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
