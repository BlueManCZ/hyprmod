"""Editable card widget for a single monitor."""

from dataclasses import dataclass

from gi.repository import Adw, Gtk
from hyprland_monitors.monitors import (
    TRANSFORMS,
    MonitorState,
    compute_valid_scales,
    nearest_scale_index,
    parse_mode,
)

from hyprmod.ui import RowActions
from hyprmod.ui.signals import SignalBlocker

# UI display constants
BITDEPTHS = ["Auto", "8-bit", "10-bit"]
BITDEPTH_VALUES = [None, "8", "10"]

VRR_MODES = ["Off", "On", "Fullscreen only", "Fullscreen + Gaming"]
VRR_VALUES = [None, "1", "2", "3"]

CM_MODES = ["Auto", "sRGB", "Adobe", "Wide", "EDID", "HDR", "HDR (EDID)"]
CM_VALUES = [None, "srgb", "adobe", "wide", "edid", "hdr", "hdredid"]

# Hyprland applies SDR/HDR luminance controls only when HDR output is active.
_HDR_CM_VALUES = frozenset({"hdr", "hdredid"})

# UI range for the SDR brightness/saturation sliders.
SLIDER_VALUE_WIDTH_CHARS = 7
SDR_VALUE_MIN = 0.0
SDR_VALUE_MAX = 2.0
SDR_VALUE_STEP = 0.05
SDR_VALUE_DIGITS = 2
SDR_VALUE_DEFAULT = 1.0


@dataclass(frozen=True)
class _HdrSliderSpec:
    field: str
    title: str
    subtitle: str
    default: float
    minimum: float
    maximum: float
    step: float
    page: float
    digits: int
    display_digits: int | None = None
    auto_default: bool = False


HDR_SLIDER_SPECS = (
    _HdrSliderSpec(
        "sdr_brightness",
        "SDR Brightness",
        "Brightness for SDR content in HDR mode",
        SDR_VALUE_DEFAULT,
        SDR_VALUE_MIN,
        SDR_VALUE_MAX,
        SDR_VALUE_STEP,
        SDR_VALUE_STEP * 4,
        SDR_VALUE_DIGITS,
        2,
    ),
    _HdrSliderSpec(
        "sdr_saturation",
        "SDR Saturation",
        "Saturation for SDR content in HDR mode",
        SDR_VALUE_DEFAULT,
        SDR_VALUE_MIN,
        SDR_VALUE_MAX,
        SDR_VALUE_STEP,
        SDR_VALUE_STEP * 4,
        SDR_VALUE_DIGITS,
        2,
    ),
    _HdrSliderSpec(
        "sdr_min_luminance",
        "SDR Min Luminance",
        "Minimum luminance for SDR to HDR mapping",
        0.0,
        0.0,
        1.0,
        0.05,
        0.1,
        2,
    ),
    _HdrSliderSpec(
        "sdr_max_luminance",
        "SDR Max Luminance",
        "Maximum luminance for SDR content",
        800.0,
        0.0,
        2000.0,
        10.0,
        100.0,
        0,
    ),
    _HdrSliderSpec(
        "min_luminance",
        "HDR Min Luminance",
        "Minimum luminance for HDR output",
        0.0,
        0.0,
        1.0,
        0.05,
        0.1,
        2,
    ),
    _HdrSliderSpec(
        "max_luminance",
        "HDR Max Luminance",
        "Maximum luminance for HDR output",
        800.0,
        0.0,
        2000.0,
        10.0,
        100.0,
        0,
    ),
    _HdrSliderSpec(
        "max_avg_luminance",
        "Max Avg Luminance",
        "Monitor maximum average luminance",
        500.0,
        0.0,
        2000.0,
        10.0,
        100.0,
        0,
        None,
        True,
    ),
)
HDR_SLIDER_FIELDS = tuple(spec.field for spec in HDR_SLIDER_SPECS)
HDR_SLIDER_SPEC_BY_FIELD = {spec.field: spec for spec in HDR_SLIDER_SPECS}
HDR_RESET_FIELDS = HDR_SLIDER_FIELDS
LOCKED_LUMINANCE_PAIRS = {
    "sdr_min_luminance": "min_luminance",
    "min_luminance": "sdr_min_luminance",
    "sdr_max_luminance": "max_luminance",
    "max_luminance": "sdr_max_luminance",
}
LOCKED_LUMINANCE_FIELD_SET = frozenset(LOCKED_LUMINANCE_PAIRS)
HDR_LOCKED_ICON = "changes-prevent-symbolic"
HDR_UNLOCKED_ICON = "changes-allow-symbolic"


def _parse_sdr(value: str | None) -> float:
    """Parse a stored sdr_brightness/sdr_saturation string into a slider value."""
    return _parse_hdr_value(value, SDR_VALUE_DEFAULT)


def _parse_hdr_value(value: str | None, default: float) -> float:
    """Parse a stored HDR numeric value for a slider."""
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _format_sdr(value: float) -> str | None:
    """Format a slider value back into config-line form, or None at the default.

    Keeps at least one integer digit so ``0`` renders as ``"0"`` rather than ``""``.
    """
    return _format_hdr_value(value, SDR_VALUE_DEFAULT, SDR_VALUE_DIGITS)


def _format_hdr_value(value: float, default: float, digits: int) -> str | None:
    """Format a slider value back into config-line form, or None at the default."""
    # Epsilon handles FP jitter from the slider; picking the exact default resets.
    if abs(value - default) < 1e-3:
        return None
    int_part, _, frac = f"{value:.{digits}f}".partition(".")
    frac = frac.rstrip("0")
    return f"{int_part}.{frac}" if frac else int_part


def _format_hdr_raw_value(value: float, digits: int) -> str:
    int_part, _, frac = f"{value:.{digits}f}".partition(".")
    frac = frac.rstrip("0")
    return f"{int_part}.{frac}" if frac else int_part


def _format_hdr_display_value(value: float, spec: _HdrSliderSpec) -> str:
    """Format the visible slider value label."""
    if spec.auto_default and abs(value - spec.default) < 1e-3:
        return "Auto"
    digits = spec.display_digits if spec.display_digits is not None else spec.digits
    return f"{value:.{digits}f}"


class MonitorCard(Gtk.Box):
    """Editable card for a single monitor."""

    def __init__(
        self,
        monitor: MonitorState,
        index: int = 0,
        on_changed=None,
        on_discard=None,
        on_remove=None,
        caps: dict | None = None,
        mirror_choices: list[tuple[str, str]] | None = None,
        desc_unique: bool = False,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._monitor = monitor
        self._on_changed = on_changed
        self._on_discard = on_discard
        self._on_remove = on_remove
        self._caps = caps or {"hdr": False, "ten_bit": False, "vrr": False}
        self._mirror_choices = mirror_choices or []
        self._desc_unique = desc_unique

        connector = monitor.name
        make = monitor.make
        model = monitor.model
        display_name = f"{make} {model}".strip() or connector

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.add_css_class("monitor-header")
        header_box.set_margin_bottom(8)

        title_label = Gtk.Label(label=f"{index}. {display_name}")
        title_label.set_xalign(0)
        title_label.add_css_class("title-4")
        header_box.append(title_label)

        badges_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badges_box.set_valign(Gtk.Align.CENTER)
        has_caps = any(self._caps.get(k) for k in ("hdr", "ten_bit", "vrr"))
        if has_caps:
            supports_label = Gtk.Label(label="Supports:")
            supports_label.add_css_class("caption")
            supports_label.add_css_class("dim-label")
            badges_box.append(supports_label)
        for cap_key, cap_label in [("hdr", "HDR"), ("ten_bit", "10-bit"), ("vrr", "VRR")]:
            if self._caps.get(cap_key):
                badge = Gtk.Label(label=cap_label)
                badge.add_css_class("caption")
                badge.add_css_class("monitor-cap-badge")
                badges_box.append(badge)
        badges_box.set_hexpand(True)
        header_box.append(badges_box)

        # Action buttons (discard / remove override) — hover-revealed
        self._actions_box = Gtk.Box(spacing=2)
        self._actions_box.set_valign(Gtk.Align.CENTER)
        self._actions_box.add_css_class("reset-button")

        self._discard_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self._discard_btn.set_valign(Gtk.Align.CENTER)
        self._discard_btn.set_tooltip_text("Discard changes")
        self._discard_btn.add_css_class("flat")
        self._discard_btn.set_visible(False)
        self._discard_btn.connect("clicked", self._on_discard_clicked)
        self._actions_box.append(self._discard_btn)

        self._remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        self._remove_btn.set_valign(Gtk.Align.CENTER)
        self._remove_btn.set_tooltip_text("Remove override")
        self._remove_btn.add_css_class("flat")
        self._remove_btn.set_visible(False)
        self._remove_btn.connect("clicked", self._on_remove_clicked)
        self._actions_box.append(self._remove_btn)

        header_box.append(self._actions_box)

        self._managed_badge = Gtk.Label(label="Managed")
        self._managed_badge.add_css_class("caption")
        self._managed_badge.add_css_class("monitor-managed-badge")
        self._managed_badge.set_visible(False)
        self._managed_badge.set_valign(Gtk.Align.CENTER)
        header_box.append(self._managed_badge)

        connector_label = Gtk.Label(label=connector)
        connector_label.add_css_class("dim-label")
        header_box.append(connector_label)

        self._signals = SignalBlocker()

        self._enabled_switch = Gtk.Switch()
        self._enabled_switch.set_active(not monitor.disabled)
        self._enabled_switch.set_valign(Gtk.Align.CENTER)
        self._signals.connect(self._enabled_switch, "notify::active", self._on_enabled_changed)
        header_box.append(self._enabled_switch)

        self.append(header_box)
        self.set_margin_bottom(12)

        self._baseline: MonitorState | None = None
        self._row_actions: dict[Gtk.Widget, RowActions] = {}
        self._searchable: list[tuple[str, str]] = []

        # -- Display group (essentials) --
        display_group = Adw.PreferencesGroup()

        modes = monitor.available_modes
        mode_labels = [m.replace("Hz", " Hz") for m in modes]
        self._mode_row = Adw.ComboRow(
            title="Resolution",
            subtitle="Resolution and refresh rate",
            model=Gtk.StringList.new(mode_labels),
        )
        best_idx = 0
        for i, m in enumerate(modes):
            if m.startswith(f"{monitor.width}x{monitor.height}"):
                best_idx = i
                if f"{monitor.refresh_rate:.2f}" in m:
                    best_idx = i
                    break
        self._mode_row.set_selected(best_idx)
        self._modes = modes
        self._signals.connect(self._mode_row, "notify::selected", self._on_mode_changed)
        self._attach_row_actions(
            self._mode_row, lambda: self._discard_fields("width", "height", "refresh_rate")
        )
        display_group.add(self._mode_row)

        w, h = monitor.width, monitor.height
        self._valid_scales = compute_valid_scales(w, h)
        scale_labels = [label for _, label in self._valid_scales]
        self._scale_row = Adw.ComboRow(
            title="Scale",
            subtitle="Display scaling factor",
            model=Gtk.StringList.new(scale_labels),
        )
        self._scale_row.set_selected(nearest_scale_index(self._valid_scales, monitor.scale))
        self._signals.connect(self._scale_row, "notify::selected", self._on_scale_changed)
        self._attach_row_actions(self._scale_row, lambda: self._discard_fields("scale"))
        display_group.add(self._scale_row)

        transform_labels = list(TRANSFORMS.values())
        self._transform_row = Adw.ComboRow(
            title="Transform",
            subtitle="Screen rotation",
            model=Gtk.StringList.new(transform_labels),
        )
        self._transform_row.set_selected(monitor.transform)
        self._signals.connect(self._transform_row, "notify::selected", self._on_transform_changed)
        self._attach_row_actions(self._transform_row, lambda: self._discard_fields("transform"))
        display_group.add(self._transform_row)

        self.append(display_group)

        # -- Advanced group (expander) --
        advanced_group = Adw.PreferencesGroup()
        advanced_group.set_margin_top(12)
        self._advanced_expander = Adw.ExpanderRow(title="Advanced")
        advanced_group.add(self._advanced_expander)

        # Position
        self._pos_x = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                value=monitor.x,
                lower=-10000,
                upper=10000,
                step_increment=10,
                page_increment=100,
            ),
            digits=0,
        )
        self._pos_x.set_valign(Gtk.Align.CENTER)
        self._pos_y = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                value=monitor.y,
                lower=-10000,
                upper=10000,
                step_increment=10,
                page_increment=100,
            ),
            digits=0,
        )
        self._pos_y.set_valign(Gtk.Align.CENTER)

        self._pos_row = pos_row = Adw.ActionRow(title="Position")
        for label_text, widget, margin_start, margin_end in [
            ("X", self._pos_x, 0, 4),
            ("Y", self._pos_y, 12, 4),
        ]:
            lbl = Gtk.Label(label=label_text)
            lbl.add_css_class("dim-label")
            lbl.set_valign(Gtk.Align.CENTER)
            lbl.set_margin_start(margin_start)
            lbl.set_margin_end(margin_end)
            pos_row.add_suffix(lbl)
            pos_row.add_suffix(widget)

        self._signals.connect(self._pos_x, "value-changed", self._on_position_changed)
        self._signals.connect(self._pos_y, "value-changed", self._on_position_changed)
        self._attach_row_actions(pos_row, lambda: self._discard_fields("x", "y"))
        self._advanced_expander.add_row(pos_row)

        # Mirror
        mirror_labels = ["Off"] + [f"{name} \u2014 {label}" for name, label in self._mirror_choices]
        self._mirror_values: list[str | None] = [None] + [name for name, _ in self._mirror_choices]
        self._mirror_row = Adw.ComboRow(
            title="Mirror",
            subtitle="Clone another monitor's content",
            model=Gtk.StringList.new(mirror_labels),
        )
        current_idx = 0
        if monitor.mirror_of in self._mirror_values:
            current_idx = self._mirror_values.index(monitor.mirror_of)
        self._mirror_row.set_selected(current_idx)
        self._signals.connect(self._mirror_row, "notify::selected", self._on_mirror_changed)
        self._attach_row_actions(self._mirror_row, lambda: self._discard_fields("mirror_of"))
        self._advanced_expander.add_row(self._mirror_row)
        # Disable position when mirroring
        if monitor.mirror_of is not None:
            pos_row.set_sensitive(False)

        # Identify by description — survives moving the monitor between ports.
        # Use ActionRow + manual Switch (rather than SwitchRow) so the warning
        # icon sits *before* the switch and the switch stays flush right.
        self._identify_row = Adw.ActionRow(title="Identify by description")
        self._identify_warning = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        self._identify_warning.set_valign(Gtk.Align.CENTER)
        self._identify_warning.add_css_class("warning")
        self._identify_warning.set_tooltip_text(
            "Another connected monitor matches the same description prefix — "
            "Hyprland may apply this config to the wrong monitor"
        )
        self._identify_row.add_suffix(self._identify_warning)
        self._identify_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._identify_switch.set_active(monitor.identify_by_description)
        self._identify_row.add_suffix(self._identify_switch)
        self._identify_row.set_activatable_widget(self._identify_switch)
        self._signals.connect(self._identify_switch, "notify::active", self._on_identify_changed)
        self._attach_row_actions(
            self._identify_row, lambda: self._discard_fields("identify_by_description")
        )
        self._advanced_expander.add_row(self._identify_row)
        self._refresh_identify_state(monitor)

        # Optional extras (only shown if hardware supports them)
        self._cm_row = self._build_extra_combo(
            "hdr",
            "Color Management",
            "Color space mode",
            CM_MODES,
            CM_VALUES,
            monitor.color_management,
            self._on_cm_changed,
            lambda: self._discard_fields("color_management"),
        )
        self._hdr_reset_row = self._build_hdr_reset_row()
        self._hdr_slider_rows = self._build_hdr_slider_rows(monitor)
        self._bitdepth_row = self._build_extra_combo(
            "ten_bit",
            "Bit Depth",
            "Color depth per channel",
            BITDEPTHS,
            BITDEPTH_VALUES,
            monitor.bit_depth,
            self._on_bitdepth_changed,
            lambda: self._discard_fields("bit_depth"),
        )
        self._vrr_row = self._build_extra_combo(
            "vrr",
            "VRR",
            "Per-monitor variable refresh rate",
            VRR_MODES,
            VRR_VALUES,
            monitor.vrr,
            self._on_vrr_changed,
            lambda: self._discard_fields("vrr"),
        )
        for row in (
            self._cm_row,
            self._hdr_reset_row,
            *self._hdr_slider_rows,
            self._bitdepth_row,
            self._vrr_row,
        ):
            if row is not None:
                self._advanced_expander.add_row(row)
        self._refresh_hdr_slider_visibility()

        self.append(advanced_group)

        self._setting_rows = [
            self._mode_row,
            self._scale_row,
            self._transform_row,
            self._advanced_expander,
        ]
        if monitor.disabled:
            for row in self._setting_rows:
                row.set_sensitive(False)

        for row in (
            self._mode_row,
            self._scale_row,
            self._transform_row,
            self._pos_row,
            self._mirror_row,
            self._cm_row,
            self._hdr_reset_row,
            self._bitdepth_row,
            self._vrr_row,
            self._identify_row,
            *self._hdr_slider_rows,
        ):
            if row is not None:
                self._searchable.append((row.get_title(), row.get_subtitle() or ""))

    @property
    def searchable_fields(self) -> list[tuple[str, str]]:
        """Return (title, subtitle) pairs for all visible rows."""
        return self._searchable

    def _refresh_identify_state(self, mon: MonitorState):
        """Update the identify-by-description row's subtitle, sensitivity, and warning.

        Five possible states:

        - description empty → row disabled, explains why
        - description present, unique → row enabled, subtitle shows the match string
        - description present, not unique, toggle off → row disabled, collision warning
        - description present, not unique, toggle on → row stays enabled (so the
          user can toggle it off), warning icon visible explaining the ambiguity
        """
        if not mon.description:
            self._identify_row.set_subtitle("Description not reported by this monitor")
            self._identify_row.set_sensitive(False)
            self._identify_row.set_tooltip_text("This monitor does not report a description")
            self._identify_warning.set_visible(False)
            return

        prefix = mon.description.split(",", 1)[0].strip()
        on = mon.identify_by_description
        match_text = f"Match by description: “{prefix}”"

        if self._desc_unique:
            self._identify_row.set_subtitle(match_text)
            self._identify_row.set_sensitive(True)
            self._identify_row.set_tooltip_text(None)
            self._identify_warning.set_visible(False)
        elif on:
            self._identify_row.set_subtitle(
                f"{match_text} — ambiguous, another monitor matches the same prefix"
            )
            self._identify_row.set_sensitive(True)
            self._identify_row.set_tooltip_text(None)
            self._identify_warning.set_visible(True)
        else:
            self._identify_row.set_subtitle(match_text)
            self._identify_row.set_sensitive(False)
            self._identify_row.set_tooltip_text(
                "Another connected monitor shares the same description prefix"
            )
            self._identify_warning.set_visible(False)

    # -- Helpers --

    def _build_extra_combo(
        self,
        cap_key,
        title,
        subtitle,
        labels,
        values,
        current,
        on_changed,
        on_discard,
    ) -> Adw.ComboRow | None:
        if not self._caps.get(cap_key):
            return None
        row = Adw.ComboRow(
            title=title,
            subtitle=subtitle,
            model=Gtk.StringList.new(labels),
        )
        idx = values.index(current) if current in values else 0
        row.set_selected(idx)
        self._signals.connect(row, "notify::selected", on_changed)
        self._attach_row_actions(row, on_discard)
        return row

    def _build_hdr_reset_row(self) -> Adw.ActionRow | None:
        self._hdr_reset_button: Gtk.Button | None = None
        if not self._caps.get("hdr"):
            return None

        row = Adw.ActionRow(title="Safe Defaults", subtitle="Restore conservative HDR values")
        self._hdr_reset_button = Gtk.Button(icon_name="edit-undo-symbolic")
        self._hdr_reset_button.set_valign(Gtk.Align.CENTER)
        self._hdr_reset_button.set_tooltip_text("Apply safe defaults")
        self._hdr_reset_button.add_css_class("flat")
        row.add_suffix(self._hdr_reset_button)
        self._signals.connect(self._hdr_reset_button, "clicked", self._on_hdr_reset_clicked)
        return row

    def _build_hdr_slider_rows(self, monitor: MonitorState) -> list[Adw.ActionRow]:
        """Build one slider row for each HDR-related monitor value."""
        self._hdr_sliders: dict[str, Gtk.Scale] = {}
        self._hdr_value_labels: dict[str, Gtk.Label] = {}
        self._hdr_lock_active = False
        self._hdr_lock_buttons: dict[str, Gtk.ToggleButton] = {}
        if not self._caps.get("hdr"):
            return []

        rows: list[Adw.ActionRow] = []
        for spec in HDR_SLIDER_SPECS:
            value = _parse_hdr_value(getattr(monitor, spec.field), spec.default)
            row = Adw.ActionRow(title=spec.title, subtitle=spec.subtitle)
            scale = self._make_hdr_slider(spec, value)
            value_label = Gtk.Label(
                label=self._format_hdr_label(value, spec),
                width_chars=SLIDER_VALUE_WIDTH_CHARS,
                xalign=1,
            )
            value_label.add_css_class("dim-label")
            value_label.set_valign(Gtk.Align.CENTER)
            lock_button = self._build_hdr_lock_button(spec)
            if lock_button is not None:
                row.add_suffix(lock_button)
            row.add_suffix(scale)
            row.add_suffix(value_label)

            self._hdr_sliders[spec.field] = scale
            self._hdr_value_labels[spec.field] = value_label
            self._signals.connect(scale, "value-changed", self._on_hdr_slider_changed, spec)
            self._attach_row_actions(row, lambda f=spec.field: self._discard_fields(f))
            rows.append(row)
        return rows

    def _build_hdr_lock_button(self, spec: _HdrSliderSpec) -> Gtk.ToggleButton | None:
        if spec.field not in LOCKED_LUMINANCE_FIELD_SET:
            return None

        peer = HDR_SLIDER_SPEC_BY_FIELD[LOCKED_LUMINANCE_PAIRS[spec.field]]
        button = Gtk.ToggleButton()
        button.set_child(Gtk.Image.new_from_icon_name(HDR_UNLOCKED_ICON))
        button.set_valign(Gtk.Align.CENTER)
        button.set_active(self._hdr_lock_active)
        button.set_tooltip_text(f"Lock {spec.title} with {peer.title}")
        button.add_css_class("flat")
        self._hdr_lock_buttons[spec.field] = button
        self._signals.connect(button, "toggled", self._on_hdr_lock_toggled, spec.field)
        return button

    def _make_hdr_slider(self, spec: _HdrSliderSpec, value: float) -> Gtk.Scale:
        scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(
                value=value,
                lower=spec.minimum,
                upper=spec.maximum,
                step_increment=spec.step,
                page_increment=spec.page,
            ),
        )
        scale.set_digits(spec.digits)
        scale.set_draw_value(False)
        scale.set_size_request(220, -1)
        scale.set_valign(Gtk.Align.CENTER)
        if spec.field in {"sdr_brightness", "sdr_saturation"}:
            scale.add_mark(SDR_VALUE_DEFAULT, Gtk.PositionType.BOTTOM, None)
        return scale

    def _is_hdr_cm_active(self) -> bool:
        """Whether the current cm preset is one that makes sdr* values effective."""
        if self._cm_row is None:
            return False
        idx = self._cm_row.get_selected()
        if idx < 0 or idx >= len(CM_VALUES):
            return False
        return CM_VALUES[idx] in _HDR_CM_VALUES

    def _refresh_hdr_slider_visibility(self):
        visible = self._is_hdr_cm_active()
        if self._hdr_reset_row is not None:
            self._hdr_reset_row.set_visible(visible)
        for row in self._hdr_slider_rows:
            row.set_visible(visible)

    def _format_hdr_label(self, value: float, spec: _HdrSliderSpec) -> str:
        return _format_hdr_display_value(value, spec)

    def _attach_row_actions(self, row: Adw.ActionRow | Adw.ComboRow, discard_handler):
        """Attach a RowActions strip (discard only, no per-row remove)."""
        actions = RowActions(
            row,
            on_discard=discard_handler,
        )
        row.add_suffix(actions.box)
        actions.reorder_first()
        self._row_actions[row] = actions

    # -- Public methods --

    def set_position_silent(self, x: int, y: int):
        """Update position spinners without triggering change callbacks."""
        with self._signals:
            self._pos_x.set_value(x)
            self._pos_y.set_value(y)

    def push_from_monitor(self, mon: MonitorState):
        """Update all card widgets from Monitor values."""
        with self._signals:
            self._monitor = mon

            self._enabled_switch.set_active(not mon.disabled)
            for row in self._setting_rows:
                row.set_sensitive(not mon.disabled)

            self._pos_x.set_value(mon.x)
            self._pos_y.set_value(mon.y)

            new_scales = compute_valid_scales(mon.width, mon.height)
            if new_scales != self._valid_scales:
                self._valid_scales = new_scales
                self._scale_row.set_model(Gtk.StringList.new([label for _, label in new_scales]))
            self._scale_row.set_selected(nearest_scale_index(self._valid_scales, mon.scale))
            self._transform_row.set_selected(mon.transform)

            if self._bitdepth_row:
                bd = mon.bit_depth
                self._bitdepth_row.set_selected(
                    BITDEPTH_VALUES.index(bd) if bd in BITDEPTH_VALUES else 0
                )
            if self._vrr_row:
                v = mon.vrr
                self._vrr_row.set_selected(VRR_VALUES.index(v) if v in VRR_VALUES else 0)
            if self._cm_row:
                c = mon.color_management
                self._cm_row.set_selected(CM_VALUES.index(c) if c in CM_VALUES else 0)
            for spec in HDR_SLIDER_SPECS:
                slider = self._hdr_sliders.get(spec.field)
                if slider is None:
                    continue
                value = _parse_hdr_value(getattr(mon, spec.field), spec.default)
                slider.set_value(value)
                label = self._hdr_value_labels.get(spec.field)
                if label is not None:
                    label.set_label(self._format_hdr_label(value, spec))
            self._refresh_hdr_slider_visibility()

            mirror_idx = 0
            if mon.mirror_of in self._mirror_values:
                mirror_idx = self._mirror_values.index(mon.mirror_of)
            self._mirror_row.set_selected(mirror_idx)
            self._pos_row.set_sensitive(mon.mirror_of is None and not mon.disabled)

            self._identify_switch.set_active(mon.identify_by_description)
            self._refresh_identify_state(mon)

            for i, m in enumerate(self._modes):
                parsed = parse_mode(m)
                if (
                    parsed["width"] == mon.width
                    and parsed["height"] == mon.height
                    and abs(parsed["refresh_rate"] - mon.refresh_rate) < 0.02
                ):
                    self._mode_row.set_selected(i)
                    break

    def update_managed_state(self, baseline: MonitorState | None, is_managed: bool, is_saved: bool):
        """Update dirty/managed visual indicators.

        When a monitor is managed, ALL options are overrides (monitor= is
        all-or-nothing), so every row shows the same managed state.
        """
        self._baseline = baseline
        self._managed_badge.set_visible(is_saved)
        managed = is_saved and is_managed

        any_dirty = False
        if baseline is None:
            all_dirty = is_managed
            any_dirty = all_dirty
            for row in (
                self._mode_row,
                self._scale_row,
                self._transform_row,
                self._pos_row,
                self._mirror_row,
                self._cm_row,
                self._bitdepth_row,
                self._vrr_row,
                self._identify_row,
                *self._hdr_slider_rows,
            ):
                if row is not None:
                    self._update_row(row, all_dirty, managed)
        else:
            mon = self._monitor
            fields: list[tuple[Gtk.Widget | None, bool]] = [
                (
                    self._mode_row,
                    mon.width != baseline.width
                    or mon.height != baseline.height
                    or abs(mon.refresh_rate - baseline.refresh_rate) > 0.02,
                ),
                (self._scale_row, mon.scale != baseline.scale),
                (self._transform_row, mon.transform != baseline.transform),
                (self._pos_row, mon.x != baseline.x or mon.y != baseline.y),
                (self._mirror_row, mon.mirror_of != baseline.mirror_of),
                (self._cm_row, mon.color_management != baseline.color_management),
                (self._bitdepth_row, mon.bit_depth != baseline.bit_depth),
                (self._vrr_row, mon.vrr != baseline.vrr),
                (
                    self._identify_row,
                    mon.identify_by_description != baseline.identify_by_description,
                ),
            ]
            # Rows are built only when the monitor reports HDR capability;
            # skip when absent so the strict zip stays meaningful for the HDR path.
            if self._hdr_slider_rows:
                for row, field in zip(self._hdr_slider_rows, HDR_SLIDER_FIELDS, strict=True):
                    fields.append((row, getattr(mon, field) != getattr(baseline, field)))
            for row, dirty in fields:
                if row is None:
                    continue
                self._update_row(row, dirty, managed)
                if dirty:
                    any_dirty = True

        self._discard_btn.set_visible(any_dirty)
        self._remove_btn.set_visible(is_saved and is_managed)

    def _update_row(self, row: Gtk.Widget, dirty: bool, managed: bool):
        actions = self._row_actions.get(row)
        if actions is not None:
            actions.update(
                is_managed=managed,
                is_dirty=dirty,
                is_saved=managed,
                show_reset=False,
            )

    def _is_hdr_lock_active(self) -> bool:
        return getattr(self, "_hdr_lock_active", False)

    def _set_hdr_lock_active(self, active: bool):
        self._hdr_lock_active = active
        with self._signals:
            for button in self._hdr_lock_buttons.values():
                button.set_active(active)
                image = button.get_child()
                if isinstance(image, Gtk.Image):
                    image.set_from_icon_name(HDR_LOCKED_ICON if active else HDR_UNLOCKED_ICON)

    def _format_hdr_field_value(self, field: str, value: float) -> str | None:
        spec = HDR_SLIDER_SPEC_BY_FIELD[field]
        return _format_hdr_value(value, spec.default, spec.digits)

    def _format_hdr_safe_value(self, field: str) -> str | None:
        spec = HDR_SLIDER_SPEC_BY_FIELD[field]
        if spec.auto_default:
            return None
        return _format_hdr_raw_value(spec.default, spec.digits)

    def _default_hdr_values(self) -> dict[str, str | None]:
        return {field: self._format_hdr_safe_value(field) for field in HDR_RESET_FIELDS}

    def _missing_hdr_default_values(self) -> dict[str, str | None]:
        return {
            field: self._format_hdr_safe_value(field)
            for field in HDR_RESET_FIELDS
            if getattr(self._monitor, field) is None
        }

    def _set_hdr_slider_value(self, field: str, value: float) -> float:
        spec = HDR_SLIDER_SPEC_BY_FIELD[field]
        clamped = max(spec.minimum, min(spec.maximum, value))
        slider = self._hdr_sliders.get(field)
        if slider is not None and abs(slider.get_value() - clamped) > 1e-6:
            slider.set_value(clamped)
        label = self._hdr_value_labels.get(field)
        if label is not None:
            label.set_label(self._format_hdr_label(clamped, spec))
        return clamped

    # -- Signal handlers --

    def _emit(self, new_vals: dict):
        if self._on_changed:
            self._on_changed(self._monitor, new_vals)

    def _on_mode_changed(self, *_args):
        idx = self._mode_row.get_selected()
        if 0 <= idx < len(self._modes):
            self._emit(parse_mode(self._modes[idx]))  # type: ignore[arg-type]  # ParsedMode is a TypedDict subclass of dict

    def _on_position_changed(self, *_args):
        self._emit({"x": int(self._pos_x.get_value()), "y": int(self._pos_y.get_value())})

    def _on_scale_changed(self, *_args):
        idx = self._scale_row.get_selected()
        scale = self._valid_scales[idx][0] if 0 <= idx < len(self._valid_scales) else 1.0
        self._emit({"scale": scale})

    def _on_transform_changed(self, *_args):
        self._emit({"transform": self._transform_row.get_selected()})

    def _on_bitdepth_changed(self, row: Adw.ComboRow, *_args):
        idx = row.get_selected()
        self._emit({"bit_depth": BITDEPTH_VALUES[idx] if idx < len(BITDEPTH_VALUES) else None})

    def _on_vrr_changed(self, row: Adw.ComboRow, *_args):
        idx = row.get_selected()
        self._emit({"vrr": VRR_VALUES[idx] if idx < len(VRR_VALUES) else None})

    def _on_cm_changed(self, row: Adw.ComboRow, *_args):
        idx = row.get_selected()
        value = CM_VALUES[idx] if idx < len(CM_VALUES) else None
        new_vals = {"color_management": value}
        if value in _HDR_CM_VALUES:
            new_vals.update(self._missing_hdr_default_values())
        self._emit(new_vals)
        self._refresh_hdr_slider_visibility()

    def _on_hdr_lock_toggled(self, button: Gtk.ToggleButton, field: str):
        self._set_hdr_lock_active(button.get_active())
        if not self._is_hdr_lock_active():
            return

        new_vals: dict[str, str | None] = {}
        with self._signals:
            for sdr_field, hdr_field in (
                ("sdr_min_luminance", "min_luminance"),
                ("sdr_max_luminance", "max_luminance"),
            ):
                source_field = field if field in {sdr_field, hdr_field} else sdr_field
                target_field = hdr_field if source_field == sdr_field else sdr_field
                source_slider = self._hdr_sliders.get(source_field)
                if source_slider is None:
                    continue
                value = self._set_hdr_slider_value(target_field, source_slider.get_value())
                new_vals[target_field] = self._format_hdr_field_value(target_field, value)
        if new_vals:
            self._emit(new_vals)

    def _on_hdr_reset_clicked(self, *_args):
        with self._signals:
            for spec in HDR_SLIDER_SPECS:
                self._set_hdr_slider_value(spec.field, spec.default)
        self._emit(self._default_hdr_values())

    def _on_hdr_slider_changed(self, scale: Gtk.Scale, spec: _HdrSliderSpec):
        value = scale.get_value()
        self._set_hdr_slider_value(spec.field, value)
        new_vals = {spec.field: _format_hdr_value(value, spec.default, spec.digits)}

        peer_field = LOCKED_LUMINANCE_PAIRS.get(spec.field)
        if self._is_hdr_lock_active() and peer_field is not None:
            with self._signals:
                peer_value = self._set_hdr_slider_value(peer_field, value)
            new_vals[peer_field] = self._format_hdr_field_value(peer_field, peer_value)

        self._emit(new_vals)

    def _on_mirror_changed(self, *_args):
        idx = self._mirror_row.get_selected()
        target = self._mirror_values[idx] if idx < len(self._mirror_values) else None
        self._pos_row.set_sensitive(target is None)
        self._emit({"mirror_of": target})

    def _on_enabled_changed(self, *_args):
        disabled = not self._enabled_switch.get_active()
        for row in self._setting_rows:
            row.set_sensitive(not disabled)
        self._emit({"disabled": disabled})

    def _on_identify_changed(self, *_args):
        self._emit({"identify_by_description": self._identify_switch.get_active()})

    # -- Per-field discard --

    def _discard_fields(self, *fields: str):
        """Revert one or more fields to their baseline values."""
        if self._baseline:
            self._emit({f: getattr(self._baseline, f) for f in fields})

    def _on_discard_clicked(self, _btn):
        if self._on_discard:
            self._on_discard(self._monitor)

    def _on_remove_clicked(self, _btn):
        if self._on_remove:
            self._on_remove(self._monitor)
