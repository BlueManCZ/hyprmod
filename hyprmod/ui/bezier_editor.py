"""Bezier curve editor widget and dialog for Hyprland animations.

Combines a BezierCanvas, AnimationPreview, numeric inputs, preset management,
and context-sensitive actions into a complete editing experience.
"""

from gi.repository import Adw, Gtk

from hyprmod.data.bezier_data import get_curve_store
from hyprmod.data.bezier_presets import BUILTIN_PRESETS
from hyprmod.ui import confirm
from hyprmod.ui.bezier_canvas import AnimationPreview, BezierCanvas
from hyprmod.ui.signals import SignalBlocker


class BezierEditor(Gtk.Box):
    """Complete bezier curve editor with canvas, preview, inputs, and context-sensitive actions."""

    def __init__(
        self,
        on_curve_changed=None,
        on_curve_saved=None,
        on_curve_deleted=None,
        on_curve_renamed=None,
        on_apply=None,
        get_curve_usage=None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        self._on_curve_changed = on_curve_changed
        self._on_curve_saved = on_curve_saved
        self._on_curve_deleted = on_curve_deleted
        self._on_curve_renamed = on_curve_renamed
        self._on_apply = on_apply
        self._get_curve_usage = get_curve_usage
        self._spin_signals = SignalBlocker()

        # Build preset data first so we can initialise base from index 0
        self._build_preset_list()

        # Base curve tracking — match dropdown's initial selection (index 0)
        self._base_curve_name = self._presets[0][0]
        self._base_points = self._presets[0][2]
        self._last_action_state = None

        self.set_margin_top(8)
        self.set_margin_bottom(8)

        # Preset selector
        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preset_box.set_halign(Gtk.Align.CENTER)

        preset_label = Gtk.Label(label="Preset")
        preset_label.add_css_class("dim-label")
        preset_box.append(preset_label)

        self._preset_dropdown = Gtk.DropDown.new_from_strings(
            [label for _, label, _ in self._presets]
        )
        self._preset_dropdown.set_selected(0)
        self._preset_dropdown.connect("notify::selected", self._on_preset_selected)
        preset_box.append(self._preset_dropdown)

        self.append(preset_box)

        # Canvas
        self._actions_dirty = False

        self._canvas = BezierCanvas(
            on_change=self._on_canvas_changed,
            on_drag_end=self._on_canvas_drag_end,
        )
        self._canvas.set_hexpand(True)
        self._canvas.set_halign(Gtk.Align.CENTER)
        self._canvas.set_points(*self._base_points)

        frame = Gtk.Frame()
        frame.set_child(self._canvas)

        canvas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        canvas_box.append(frame)
        hint_label = Gtk.Label(label="Drag control points to adjust the curve")
        hint_label.add_css_class("dim-label")
        hint_label.set_margin_top(4)
        canvas_box.append(hint_label)
        self.append(canvas_box)

        # Animation preview
        self._preview = AnimationPreview()
        self._preview.set_hexpand(True)
        self.append(self._preview)

        # Numeric inputs — two compact rows, one per control point
        inputs_group = Adw.PreferencesGroup(title="Control Points")

        self._spin_x1, self._spin_y1, row1 = self._make_point_row(
            "Point 1", self._canvas.x1, self._canvas.y1, 0.0, 1.0, -10.0, 10.0
        )
        self._spin_x2, self._spin_y2, row2 = self._make_point_row(
            "Point 2", self._canvas.x2, self._canvas.y2, 0.0, 1.0, -10.0, 10.0
        )

        inputs_group.add(row1)
        inputs_group.add(row2)
        self.append(inputs_group)

        # Action bar (simple button row, Figma-style)
        self._build_action_bar()

        # Start preview animation with the initial preset points
        self._preview.set_points(*self._base_points)
        self._preview.start()

        # Initial visibility
        self._update_actions()

    def _build_preset_list(self):
        """Build preset dropdown data as (name, label, points) tuples.

        Order: user curves (starred), then external (includes native), then builtins.
        """
        store = get_curve_store()
        user_curves = store.load_user_curves()
        ext_curves = store.get_external_curves()
        self._presets: list[tuple[str, str, tuple]] = []
        for name, points in user_curves.items():
            self._presets.append((name, f"\u2605 {name}", points))
        for name, points in ext_curves.items():
            self._presets.append((name, name, points))
        for name, points in BUILTIN_PRESETS.items():
            self._presets.append((name, name, points))

    def _make_point_row(self, label, x_val, y_val, x_lo, x_hi, y_lo, y_hi):
        """Build an ActionRow with two labelled SpinButtons for X and Y."""
        row = Adw.ActionRow(title=label)

        def _spin(val, lo, hi):
            adj = Gtk.Adjustment(
                value=val, lower=lo, upper=hi, step_increment=0.01, page_increment=0.1
            )
            btn = Gtk.SpinButton(adjustment=adj, digits=3, width_chars=6)
            btn.set_valign(Gtk.Align.CENTER)
            self._spin_signals.connect(btn, "value-changed", self._on_spin_changed)
            return btn

        x_label = Gtk.Label(label="X")
        x_label.add_css_class("dim-label")
        x_label.set_valign(Gtk.Align.CENTER)
        x_label.set_margin_end(4)

        spin_x = _spin(x_val, x_lo, x_hi)

        y_label = Gtk.Label(label="Y")
        y_label.add_css_class("dim-label")
        y_label.set_valign(Gtk.Align.CENTER)
        y_label.set_margin_start(12)
        y_label.set_margin_end(4)

        spin_y = _spin(y_val, y_lo, y_hi)

        row.add_suffix(x_label)
        row.add_suffix(spin_x)
        row.add_suffix(y_label)
        row.add_suffix(spin_y)

        return spin_x, spin_y, row

    def _sync_from_points(self, x1, y1, x2, y2):
        """Common sync: update preview, rebuild actions, notify callback."""
        self._preview.set_points(x1, y1, x2, y2)
        self._maybe_rebuild_actions()
        if self._on_curve_changed:
            self._on_curve_changed(x1, y1, x2, y2)

    def _on_canvas_changed(self, x1, y1, x2, y2):
        """Canvas handles dragged — sync inputs, preview, and actions."""
        with self._spin_signals:
            self._spin_x1.set_value(x1)
            self._spin_y1.set_value(y1)
            self._spin_x2.set_value(x2)
            self._spin_y2.set_value(y2)
        self._sync_from_points(x1, y1, x2, y2)

    def _on_canvas_drag_end(self):
        """Flush deferred action rebuild after drag completes."""
        if self._actions_dirty:
            self._actions_dirty = False
            self._last_action_state = None
            self._update_actions()

    def _on_spin_changed(self, _button):
        """Numeric inputs changed — sync canvas, preview, and actions."""
        x1 = round(self._spin_x1.get_value(), 3)
        y1 = round(self._spin_y1.get_value(), 3)
        x2 = round(self._spin_x2.get_value(), 3)
        y2 = round(self._spin_y2.get_value(), 3)
        self._canvas.set_points(x1, y1, x2, y2)
        self._sync_from_points(x1, y1, x2, y2)

    def _on_preset_selected(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        if 0 <= idx < len(self._presets):
            name, _, points = self._presets[idx]
            self._base_curve_name = name
            self._base_points = points
            x1, y1, x2, y2 = points
            self._canvas.set_points(x1, y1, x2, y2)
            self._on_canvas_changed(x1, y1, x2, y2)
            # Force rebuild since base changed
            self._last_action_state = None
            self._update_actions()

    # -- State tracking --

    def _is_modified(self):
        if self._base_points is None:
            return False
        return self.get_points() != self._base_points

    def _is_base_custom(self):
        return self._base_curve_name in get_curve_store().get_user_curve_names()

    def _maybe_rebuild_actions(self):
        if self._canvas.is_dragging:
            self._actions_dirty = True
            return
        state = (self._is_base_custom(), self._is_modified())
        if state != self._last_action_state:
            self._last_action_state = state
            self._update_actions()

    # -- Action bar --

    def _build_action_bar(self):
        """Build a simple, persistent button bar (Figma-style)."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_halign(Gtk.Align.CENTER)
        bar.set_margin_top(4)

        # Revert — flat text button
        self._revert_btn = Gtk.Button(label="Revert")
        self._revert_btn.add_css_class("flat")
        self._revert_btn.set_tooltip_text("Revert to saved curve")
        self._revert_btn.connect("clicked", lambda _b: self._do_revert())
        bar.append(self._revert_btn)

        # Save — for custom curves: update in place
        self._save_btn = Gtk.Button(label="Save")
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.add_css_class("pill")
        self._save_btn.set_tooltip_text("Save changes")
        self._save_btn.connect("clicked", lambda _b: self._do_update())
        bar.append(self._save_btn)

        # Save as — opens name dialog (for both builtin and custom)
        self._save_as_btn = Gtk.Button(label="Save as\u2026")
        self._save_as_btn.add_css_class("pill")
        self._save_as_btn.set_tooltip_text("Save as new custom curve")
        self._save_as_btn.connect("clicked", lambda _b: self._show_save_as_dialog())
        bar.append(self._save_as_btn)

        # Apply — use this curve for the animation
        self._apply_btn = Gtk.Button(label="Apply")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.add_css_class("pill")
        self._apply_btn.set_tooltip_text("Apply curve to animation")
        self._apply_btn.connect(
            "clicked",
            lambda _b: self._on_apply(self._base_curve_name) if self._on_apply else None,
        )
        bar.append(self._apply_btn)

        # Spacer
        spacer = Gtk.Separator()
        spacer.add_css_class("spacer")
        bar.append(spacer)

        # Rename — icon button
        self._rename_btn = Gtk.Button(icon_name="pencil-symbolic")
        self._rename_btn.add_css_class("flat")
        self._rename_btn.set_tooltip_text("Rename curve")
        self._rename_btn.connect("clicked", lambda _b: self._show_rename_dialog())
        bar.append(self._rename_btn)

        # Delete — icon button
        self._delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        self._delete_btn.add_css_class("flat")
        self._delete_btn.add_css_class("error")
        self._delete_btn.set_tooltip_text("Delete curve")
        self._delete_btn.connect("clicked", lambda _b: self._do_delete(self._base_curve_name))
        bar.append(self._delete_btn)

        self._action_bar = bar
        self.append(bar)

    def _update_actions(self):
        """Show/hide action buttons based on current state."""
        modified = self._is_modified()
        is_custom = self._is_base_custom()

        # Revert + Save: only when modified
        self._revert_btn.set_visible(modified)
        # Save (update in place): only for modified custom curves
        self._save_btn.set_visible(modified and is_custom)
        # Save as: when modified (works for both builtin and custom)
        self._save_as_btn.set_visible(modified)
        # Apply: when not modified and callback exists
        self._apply_btn.set_visible(not modified and self._on_apply is not None)
        # Rename/Delete: only for custom curves
        self._rename_btn.set_visible(is_custom)
        self._delete_btn.set_visible(is_custom)

    def _get_usage_count(self, name):
        if self._get_curve_usage:
            return self._get_curve_usage(name)
        return 0

    def _show_save_as_dialog(self):
        """Show a dialog to enter a name for the new curve."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Save as Custom Curve")
        dialog.set_body("Enter a name for your curve.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_text(get_curve_store().next_custom_name())
        entry.set_hexpand(True)
        entry.set_margin_top(8)
        dialog.set_extra_child(entry)

        def on_activate(_e):
            name = entry.get_text().strip()
            if name:
                dialog.force_close()
                self._do_save_new(name)

        entry.connect("activate", on_activate)
        dialog.connect("response", self._on_save_as_response, entry)
        dialog.present(self.get_root())  # type: ignore[arg-type]

    def _on_save_as_response(self, dialog, response, entry):
        if response == "save":
            name = entry.get_text().strip()
            if name:
                self._do_save_new(name)

    def _show_rename_dialog(self):
        """Show a dialog to rename the current custom curve."""
        old_name = self._base_curve_name
        dialog = Adw.AlertDialog()
        dialog.set_heading("Rename Curve")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_text(old_name)
        entry.set_hexpand(True)
        entry.set_margin_top(8)
        dialog.set_extra_child(entry)

        def on_activate(_e):
            new_name = entry.get_text().strip()
            if new_name:
                dialog.force_close()
                self._do_rename(old_name, new_name)

        entry.connect("activate", on_activate)
        dialog.connect("response", self._on_rename_response, old_name, entry)
        dialog.present(self.get_root())  # type: ignore[arg-type]
        entry.grab_focus()

    def _on_rename_response(self, dialog, response, old_name, entry):
        if response == "rename":
            new_name = entry.get_text().strip()
            if new_name:
                self._do_rename(old_name, new_name)

    def _do_revert(self):
        """Revert to the base curve points."""
        x1, y1, x2, y2 = self._base_points
        self._canvas.set_points(x1, y1, x2, y2)
        self._on_canvas_changed(x1, y1, x2, y2)

    # -- Actions --

    def _save_and_refresh(self, name: str):
        """Save current points under *name*, refresh presets, and notify."""
        points = self.get_points()
        get_curve_store().save_user_curve(name, points)

        self._base_curve_name = name
        self._base_points = points

        self._rebuild_presets()
        self.select_curve(name)

        if self._on_curve_saved:
            self._on_curve_saved(name)

    def _do_save_new(self, name):
        """Save current points as a new custom curve."""
        if not name:
            return
        if get_curve_store().is_builtin_curve(name):
            return  # Can't overwrite built-in
        self._save_and_refresh(name)

    def _do_update(self):
        """Update the current custom curve with new points."""
        self._save_and_refresh(self._base_curve_name)

    def _do_rename(self, old_name, new_name):
        """Rename a custom curve."""
        if not new_name or new_name == old_name:
            return
        store = get_curve_store()
        if store.is_builtin_curve(new_name) or new_name in store.get_user_curve_names():
            return  # Name taken

        store.rename_user_curve(old_name, new_name)
        self._base_curve_name = new_name

        self._rebuild_presets()
        self.select_curve(new_name)

        if self._on_curve_renamed:
            self._on_curve_renamed(old_name, new_name)

    def _do_delete(self, name):
        """Delete a custom curve with confirmation."""
        usage = self._get_usage_count(name)

        if usage > 0:
            body = (
                f"This curve is used by {usage} animation{'s' if usage != 1 else ''}. "
                "Those animations will fall back to their parent\u2019s curve."
            )
        else:
            body = "This action cannot be undone."

        confirm(
            self.get_root(),  # type: ignore[arg-type]
            f"Delete \u201c{name}\u201d?",
            body,
            "Delete",
            lambda: self._on_delete_confirmed(name),
        )

    def _on_delete_confirmed(self, name):
        store = get_curve_store()
        store.delete_user_curve(name)

        # Fall back to first available preset (native curves like "default"
        # have no explicit control points and can't be displayed).
        self._rebuild_presets()
        fallback_name = self._presets[0][0] if self._presets else "ease"
        self._base_curve_name = fallback_name
        self._base_points = store.get_curve_points(fallback_name) or BUILTIN_PRESETS["ease"]

        self.select_curve(fallback_name)

        x1, y1, x2, y2 = self._base_points
        self._canvas.set_points(x1, y1, x2, y2)
        self._on_canvas_changed(x1, y1, x2, y2)

        if self._on_curve_deleted:
            self._on_curve_deleted(name)

    def _rebuild_presets(self):
        self._build_preset_list()
        self._preset_dropdown.set_model(
            Gtk.StringList.new([label for _, label, _ in self._presets])
        )

    def cleanup(self):
        """Stop animation preview and release resources."""
        self._preview.stop()

    def get_points(self):
        return self._canvas.x1, self._canvas.y1, self._canvas.x2, self._canvas.y2

    def select_curve(self, name: str):
        """Select a curve by name in the preset dropdown."""
        preset_names = [n for n, _, _ in self._presets]
        if name in preset_names:
            idx = preset_names.index(name)
            # Set base state before triggering the dropdown signal
            self._base_curve_name = name
            self._base_points = self._presets[idx][2]
            self._preset_dropdown.set_selected(idx)
        else:
            pts = get_curve_store().get_curve_points(name)
            if pts:
                self._base_curve_name = name
                self._base_points = pts
                self._canvas.set_points(*pts)
                self._on_canvas_changed(*pts)
        self._last_action_state = None
        self._update_actions()


class BezierEditorDialog:
    """Adw.Dialog wrapping the BezierEditor for on-demand curve editing.

    When opened from an animation detail dialog (animation_name is set),
    the edited curve is applied live to Hyprland. On close without saving,
    the animation is reverted to its previous curve.
    """

    def __init__(
        self,
        parent,
        on_curve_saved=None,
        initial_curve=None,
        animation_name=None,
        animation_page=None,
        get_curve_usage=None,
        animations=None,
    ):
        self._parent = parent
        self._anims = animations
        self._on_curve_saved = on_curve_saved
        self._animation_name = None  # Set after select_curve to avoid init callbacks
        self._animation_page = animation_page
        self._original_eff = None
        self._user_saved = False
        self._preview_active = False

        self._dialog = Adw.Dialog()
        self._dialog.set_title("Bezier Curve Editor")
        self._dialog.set_follows_content_size(True)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_propagate_natural_width(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(400)
        clamp.set_tightening_threshold(360)

        self._editor = BezierEditor(
            on_curve_changed=self._on_curve_changed,
            on_curve_saved=self._on_editor_saved,
            on_curve_deleted=self._on_editor_deleted,
            on_curve_renamed=self._on_editor_renamed,
            on_apply=self._on_apply if animation_name else None,
            get_curve_usage=get_curve_usage,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(500, -1)
        box.set_margin_top(12)
        box.set_margin_bottom(24)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(self._editor)

        clamp.set_child(box)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)
        self._dialog.set_child(toolbar)

        # Select initial curve before enabling animation_name, so the
        # select_curve callback doesn't trigger _start_preview during init.
        if initial_curve:
            self._editor.select_curve(initial_curve)

        # Now enable live preview support
        if animation_name and animation_page:
            self._animation_name = animation_name
            self._original_eff = animation_page.get_effective(animation_name)

        self._dialog.connect("closed", self._on_closed)
        self._dialog.present(parent)

    def _start_preview(self):
        """Switch the animation to hyprmodCurve for live preview."""
        if self._preview_active or not self._animation_name or not self._original_eff:
            return
        if self._anims is None:
            return
        self._preview_active = True
        eff_en, eff_sp, _, eff_st = self._original_eff
        self._anims.define_bezier("hyprmodCurve", self._editor.get_points())
        self._anims.preview(self._animation_name, eff_en, eff_sp, "hyprmodCurve", eff_st)

    def _on_curve_changed(self, x1, y1, x2, y2):
        """Apply bezier curve live to Hyprland for preview."""
        if self._animation_name and self._anims is not None:
            self._start_preview()
            self._anims.define_bezier("hyprmodCurve", (x1, y1, x2, y2))

    def _on_editor_saved(self, curve_name):
        """Called when the editor saves a curve — apply to the animation."""
        self._user_saved = True
        if self._animation_name and self._animation_page and self._anims is not None:
            self._animation_page.set_field(self._animation_name, "curve", curve_name)
            pts = get_curve_store().get_curve_points(curve_name)
            if pts:
                self._anims.define_bezier(curve_name, pts)
            # Re-apply hyprmodCurve for continued live preview
            eff = self._animation_page.get_effective(self._animation_name)
            self._anims.define_bezier("hyprmodCurve", self._editor.get_points())
            self._anims.preview(self._animation_name, eff[0], eff[1], "hyprmodCurve", eff[3])
        if self._on_curve_saved:
            self._on_curve_saved(curve_name)

    def _on_editor_deleted(self, curve_name):
        """Called when a curve is deleted — update animations that used it."""
        if self._animation_page:
            self._animation_page.reset_curve_to_default(curve_name)
        if self._on_curve_saved:
            self._on_curve_saved(None)

    def _on_editor_renamed(self, old_name, new_name):
        """Called when a curve is renamed — update animation references."""
        if self._animation_page:
            # Define the renamed curve in Hyprland before updating references
            pts = get_curve_store().get_curve_points(new_name)
            if pts and self._anims is not None:
                self._anims.define_bezier(new_name, pts)
            self._animation_page.replace_curve(old_name, new_name)
        if self._on_curve_saved:
            self._on_curve_saved(new_name)

    def _on_apply(self, curve_name):
        """Apply the selected (unmodified) curve to the animation and close."""
        self._user_saved = True
        if self._animation_name and self._animation_page:
            self._animation_page.set_field(self._animation_name, "curve", curve_name)
        if self._on_curve_saved:
            self._on_curve_saved(curve_name)
        self._dialog.close()

    def _on_closed(self, dialog):
        """Revert animation to its actual state on close."""
        self._editor.cleanup()
        if not self._animation_name or not self._animation_page or self._anims is None:
            return
        if self._user_saved:
            # User explicitly saved/applied — restore Hyprland to the model state
            state = self._animation_page.get_state(self._animation_name)
            if state and state.overridden:
                self._anims.apply(
                    self._animation_name,
                    state.enabled,
                    state.speed,
                    state.curve,
                    state.style,
                )
        elif self._preview_active and self._original_eff:
            # Preview didn't touch cache — just revert Hyprland
            self._anims.preview(self._animation_name, *self._original_eff)
