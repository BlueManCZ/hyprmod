"""Editable card widget for a single monitor."""

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

CM_MODES = ["Off", "sRGB", "HDR"]
CM_VALUES = [None, "srgb", "hdr"]


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
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._monitor = monitor
        self._on_changed = on_changed
        self._on_discard = on_discard
        self._on_remove = on_remove
        self._caps = caps or {"hdr": False, "ten_bit": False, "vrr": False}
        self._mirror_choices = mirror_choices or []

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
        for row in (self._cm_row, self._bitdepth_row, self._vrr_row):
            if row is not None:
                self._advanced_expander.add_row(row)

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
            self._bitdepth_row,
            self._vrr_row,
        ):
            if row is not None:
                self._searchable.append((row.get_title(), row.get_subtitle() or ""))

    @property
    def searchable_fields(self) -> list[tuple[str, str]]:
        """Return (title, subtitle) pairs for all visible rows."""
        return self._searchable

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

            mirror_idx = 0
            if mon.mirror_of in self._mirror_values:
                mirror_idx = self._mirror_values.index(mon.mirror_of)
            self._mirror_row.set_selected(mirror_idx)
            self._pos_row.set_sensitive(mon.mirror_of is None and not mon.disabled)

            for i, m in enumerate(self._modes):
                parsed = parse_mode(m)
                if (
                    parsed["width"] == mon.width
                    and parsed["height"] == mon.height
                    and abs(parsed["refresh_rate"] - mon.refresh_rate) < 0.02
                ):
                    self._mode_row.set_selected(i)
                    break

    def update_state(  # type: ignore[override]  # not related to Accessible.update_state
        self, baseline: MonitorState | None, is_managed: bool, is_saved: bool
    ):
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
            ):
                if row is not None:
                    self._update_row(row, all_dirty, managed)
        else:
            mon = self._monitor
            fields = [
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
            ]
            if self._cm_row:
                fields.append(
                    (
                        self._cm_row,
                        mon.color_management != baseline.color_management,
                    )
                )
            if self._bitdepth_row:
                fields.append(
                    (
                        self._bitdepth_row,
                        mon.bit_depth != baseline.bit_depth,
                    )
                )
            if self._vrr_row:
                fields.append((self._vrr_row, mon.vrr != baseline.vrr))
            for row, dirty in fields:
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
        self._emit({"color_management": CM_VALUES[idx] if idx < len(CM_VALUES) else None})

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
