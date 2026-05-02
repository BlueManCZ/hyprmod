"""Animation configuration page — flat list with detail dialogs."""

from dataclasses import replace

from gi.repository import Adw, GLib, Gtk
from hyprland_state import (
    ANIM_CHILDREN,
    ANIM_FLAT,
    ANIM_LOOKUP,
    AnimState,
    get_styles_for,
)

from hyprmod.core import config
from hyprmod.core.ownership import OwnershipSet
from hyprmod.core.undo import AnimationUndoEntry
from hyprmod.data.bezier_data import get_curve_store
from hyprmod.pages.section import SectionPage
from hyprmod.ui.bezier_editor import BezierEditorDialog
from hyprmod.ui.row_actions import RowActions
from hyprmod.ui.signals import SignalBlocker

ANIM_LABELS: dict[str, str] = {
    "global": "Global",
    "windows": "Windows",
    "windowsIn": "Open",
    "windowsOut": "Close",
    "windowsMove": "Move",
    "layers": "Layers",
    "layersIn": "Open",
    "layersOut": "Close",
    "fade": "Fade",
    "fadeIn": "Fade In",
    "fadeOut": "Fade Out",
    "fadeSwitch": "Switch",
    "fadeShadow": "Shadow",
    "fadeDim": "Dim",
    "fadeLayers": "Layers",
    "fadeLayersIn": "Layers In",
    "fadeLayersOut": "Layers Out",
    "fadePopups": "Popups",
    "fadePopupsIn": "Popups In",
    "fadePopupsOut": "Popups Out",
    "fadeDpms": "DPMS",
    "border": "Border",
    "borderangle": "Border Angle",
    "workspaces": "Workspaces",
    "workspacesIn": "Switch In",
    "workspacesOut": "Switch Out",
    "specialWorkspace": "Special",
    "specialWorkspaceIn": "Special In",
    "specialWorkspaceOut": "Special Out",
    "zoomFactor": "Zoom Factor",
    "monitorAdded": "Monitor Added",
}

GROUP_ORDER = [
    "Global",
    "Windows & Layers",
    "Fading",
    "Workspaces",
    "Other",
]

CAT_TO_GROUP: dict[str, str] = {
    "global": "Global",
    "windows": "Windows & Layers",
    "layers": "Windows & Layers",
    "fade": "Fading",
    "border": "Other",
    "borderangle": "Other",
    "workspaces": "Workspaces",
    "zoomFactor": "Other",
    "monitorAdded": "Other",
}


def _curve_display_names(curve_names: list[str]) -> list[str]:
    """Build display labels for curve names, starring user curves."""
    user_set = set(get_curve_store().get_user_curve_names())
    return [f"\u2605 {n}" if n in user_set else n for n in curve_names]


class AnimationsPage(SectionPage):
    def __init__(self, window, on_dirty_changed=None, push_undo=None, saved_sections=None):
        super().__init__(window, on_dirty_changed, push_undo)
        self._rows: dict[str, _AnimRow] = {}
        self._anims = window.hypr.animations

        # Initialize library cache from IPC
        self._anims.sync()
        self.load_owned_names(saved_sections)
        self.load_hyprland_curves()

        # Subscribe to reactive change notifications
        window.hypr.on_change(self._on_hypr_change)

    def _on_hypr_change(self, category: str, key: str | None):
        """React to library state changes (e.g. after sync)."""
        if category == "animations" and key:
            self._refresh_row(key)
            self._refresh_children(key)
            self._notify_dirty()

    @property
    def window(self):
        return self._window

    @property
    def anims(self):
        return self._anims

    def is_owned(self, name: str) -> bool:
        return self._ownership.is_owned(name)

    def is_saved(self, name: str) -> bool:
        return self._ownership.is_saved(name)

    def get_curve_usage(self, curve_name: str) -> int:
        """Count how many overridden animations use a given curve."""
        return sum(
            1
            for s in self._anims.get_all_cached().values()
            if s.overridden and s.curve == curve_name
        )

    def _apply_with_curves(self, state: AnimState) -> bool:
        """Apply an AnimState with automatic curve point resolution."""
        points = get_curve_store().get_curve_points(state.curve) if state.curve else None
        return self._anims.apply_state(state, curve_points=points)

    def replace_curve(self, old_name: str, new_name: str):
        """Replace all references to a curve name and re-apply affected animations."""
        for name, state in self._anims.get_all_cached().items():
            if state.curve == old_name:
                updated = replace(state, curve=new_name)
                self._anims.update_cached(name, updated)
                self._apply_with_curves(updated)

    def reset_curve_to_default(self, deleted_name: str):
        """Reset animations using a deleted curve to 'default' and re-apply."""
        for name, state in self._anims.get_all_cached().items():
            if state.overridden and state.curve == deleted_name:
                updated = replace(state, curve="default")
                self._anims.update_cached(name, updated)
                self._apply_with_curves(updated)

    def load_owned_names(self, saved_sections=None):
        """Load which animation names HyprMod owns from the config file."""
        if saved_sections is not None:
            saved_lines = config.collect_section(saved_sections, config.KEYWORD_ANIMATION)
        else:
            _, sections = config.read_all_sections()
            saved_lines = config.collect_section(sections, config.KEYWORD_ANIMATION)
        names: set[str] = set()
        for line in saved_lines:
            _, _, val = line.partition("=")
            parts = [p.strip() for p in val.split(",")]
            if parts:
                names.add(parts[0])
        self._ownership = OwnershipSet(names)

    def load_hyprland_curves(self):
        """Load bezier curves from Hyprland."""
        get_curve_store().set_hyprland_curves(self._anims.get_curves())

    def _promote_to_overridden(self, name: str) -> AnimState:
        """Promote an inherited animation to overridden, copying effective values.

        Returns the new overridden AnimState (updates cache).
        """
        eff_en, eff_sp, eff_cu, eff_st = self._anims.get_effective(name)
        state = self._anims.get_cached(name)
        promoted = (
            replace(
                state, overridden=True, enabled=eff_en, speed=eff_sp, curve=eff_cu, style=eff_st
            )
            if state
            else AnimState(
                name=name, overridden=True, enabled=eff_en, speed=eff_sp, curve=eff_cu, style=eff_st
            )
        )
        self._anims.update_cached(name, promoted)
        return promoted

    def set_overridden(self, name: str, overridden: bool):
        """Toggle whether an animation has explicit settings."""
        state = self._anims.get_cached(name)
        if not state:
            return
        old_copy = state
        if overridden and not state.overridden:
            self._promote_to_overridden(name)
            self._apply_live(name)
        elif not overridden and state.overridden:
            # Reload to get the actual state (user's own config may still override)
            self._window.app_state.reload_preserving_dirty()
            refreshed = self._anims.get(name)
            if refreshed:
                self._anims.update_cached(name, refreshed)
            else:
                inherited = AnimState(name=name)
                self._anims.update_cached(name, inherited)
        new_copy = self._anims.get_cached(name)
        if self._push_undo:
            entry = AnimationUndoEntry(anim_name=name, anim_old=old_copy, anim_new=new_copy)
            self._push_undo(entry)
        self._refresh_row(name)
        self._refresh_children(name)
        self._notify_dirty()

    def set_field(self, name: str, field_name: str, value):
        """Set a specific field on an animation, making it overridden."""
        state = self._anims.get_cached(name)
        if not state:
            return
        old_copy = state
        if not state.overridden:
            state = self._promote_to_overridden(name)

        updated = replace(state, **{field_name: value})
        self._anims.update_cached(name, updated)
        if self._push_undo:
            self._push_undo(AnimationUndoEntry(anim_name=name, anim_old=old_copy, anim_new=updated))
        self._apply_live(name)
        self._refresh_row(name)
        self._refresh_children(name)

    def restore_state(self, name: str, state_copy):
        """Restore an animation to a given state (used by undo/redo)."""
        if state_copy.overridden:
            self._apply_with_curves(state_copy)
        self._anims.update_cached(name, state_copy)
        self._refresh_row(name)
        self._refresh_children(name)
        self._notify_dirty()

    def _apply_live(self, name: str):
        """Apply animation to Hyprland via IPC."""
        state = self._anims.get_cached(name)
        if state:
            self._apply_with_curves(state)
        self._notify_dirty()

    def _refresh_row(self, name: str):
        """Update the UI row for an animation."""
        row = self._rows.get(name)
        if row:
            row.refresh()

    def _refresh_children(self, name: str):
        """Refresh all descendant rows that inherit from this animation."""
        for child_name in ANIM_CHILDREN.get(name, []):
            self._refresh_row(child_name)
            self._refresh_children(child_name)

    def get_animation_lines(self) -> tuple[list[str], set[str]]:
        """Return config lines for animations owned by HyprMod or newly changed,
        and the set of curve names referenced."""
        lines = []
        used_curves: set[str] = set()
        for name, _, _, _ in ANIM_FLAT:
            state = self._anims.get_cached(name)
            if not state or not state.overridden:
                continue
            # Only include if HyprMod owns it (was in our config) or the user changed it
            if not self._ownership.is_owned(name) and not self.is_anim_dirty(name):
                continue
            parts = [name, "1" if state.enabled else "0"]
            if state.enabled:
                parts.extend([str(state.speed), state.curve])
                if state.curve:
                    used_curves.add(state.curve)
                if state.style:
                    parts.append(state.style)
            line = "animation = " + ", ".join(parts)
            lines.append(line)
        return lines, used_curves

    def is_anim_dirty(self, name: str) -> bool:
        """Check if a single animation has unsaved changes."""
        if self._anims.is_dirty(name):
            return True
        return self._ownership.is_item_dirty(name)

    def is_dirty(self) -> bool:
        """Check if any animation has unsaved changes."""
        if self._anims.is_dirty():
            return True
        return self._ownership.is_dirty()

    def _apply_fallback(self, name: str):
        """Apply the fallback animation state via IPC and update the cache.

        Resolves what the animation would be without our managed config:
        either a user-defined override from their own config or the
        inherited value from the parent animation chain.
        """
        fallback = self._anims.get_fallback(name, config.gui_conf())
        if fallback:
            # User has their own animation line — apply it
            self._apply_with_curves(fallback)
            self._anims.update_cached(name, fallback)
        else:
            # No user override — resolve inherited from a parent chain
            inherited = AnimState(name=name)
            self._anims.update_cached(name, inherited)
            eff_en, eff_sp, eff_cu, eff_st = self._anims.get_effective(name)
            live = AnimState(
                name=name,
                overridden=True,
                enabled=eff_en,
                speed=eff_sp,
                curve=eff_cu,
                style=eff_st,
            )
            self._apply_with_curves(live)
            self._anims.update_cached(name, inherited)

    def revert_anim(self, name: str):
        """Revert a single animation to its saved state (value + ownership)."""
        baseline = self._anims.get_baseline(name)
        if not baseline:
            return
        if baseline.overridden:
            self._apply_with_curves(baseline)
            self._anims.update_cached(name, baseline)
        else:
            self._apply_fallback(name)
        self._ownership.discard(name)
        self._refresh_row(name)
        self._refresh_children(name)
        self._notify_dirty()

    def unmanage_anim(self, name: str):
        """Pending removal of an animation override.

        Like option reset_to_value: applies the fallback value live and
        clears ownership, but does NOT modify the config file or baseline.
        The actual config removal happens at save time.
        """
        self._ownership.disown(name)
        self._apply_fallback(name)
        self._refresh_row(name)
        self._refresh_children(name)
        self._notify_dirty()

    def mark_saved(self):
        # Promote newly overridden animations to owned, but skip
        # explicitly disowned ones (pending "remove override").
        for name in ANIM_LOOKUP:
            if not self._anims.is_dirty(name):
                continue
            state = self._anims.get_cached(name)
            if not state or not state.overridden:
                continue
            # Skip if explicitly disowned (ownership dirty + not owned)
            if self._ownership.is_item_dirty(name) and not self._ownership.is_owned(name):
                continue
            self._ownership.own(name)
        self._ownership.mark_saved()
        self._anims.mark_saved()
        for name in ANIM_LOOKUP:
            self._refresh_row(name)

    def discard(self):
        """Revert to saved state and re-apply via IPC."""
        self._ownership.discard_all()
        self._anims.discard()
        # Match ``revert_anim``: refresh every row + tell the window the page is
        # clean again. Without this the rows render with stale dirty indicators
        # until the next change forces a paint.
        for name in ANIM_LOOKUP:
            self._refresh_row(name)
        self._notify_dirty()

    # -- UI building --

    def build_widget(self) -> Gtk.Box:
        """Build the animation list with ExpanderRows for categories with children."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)

        # Group animations by top-level category, then merge into UI groups.
        categories = {}
        current_cat = None
        for name, parent, depth, styles in ANIM_FLAT:
            if name == "global":
                current_cat = "global"
                categories.setdefault(current_cat, [])
                categories[current_cat].append((name, parent, depth, styles))
            elif depth == 1:
                current_cat = name
                categories.setdefault(current_cat, [])
                categories[current_cat].append((name, parent, depth, styles))
            else:
                if current_cat:
                    categories[current_cat].append((name, parent, depth, styles))

        # Merge categories into broader groups in defined order.
        groups: dict[str, list[tuple[str, list]]] = {}
        for cat_name, entries in categories.items():
            grp = CAT_TO_GROUP.get(cat_name, "Other")
            groups.setdefault(grp, []).append((cat_name, entries))

        for grp_name in GROUP_ORDER:
            cat_list = groups.get(grp_name)
            if not cat_list:
                continue
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(grp_name))

            for _, entries in cat_list:
                has_children = any(depth > 1 for _, _, depth, _ in entries)
                parent_row = None

                for name, _parent, depth, _styles in entries:
                    avail_styles = get_styles_for(name)
                    if depth <= 1 and has_children:
                        row = _AnimRow(self, name, depth, avail_styles, is_parent=True)
                        parent_row = row
                        group.add(row.widget)
                    elif depth > 1 and parent_row is not None:
                        row = _AnimRow(self, name, depth, avail_styles)
                        parent_row.add_child(row)
                    else:
                        row = _AnimRow(self, name, depth, avail_styles)
                        group.add(row.widget)
                    self._rows[name] = row

            box.append(group)

        return box

    def build_curve_editor_widget(self) -> Adw.PreferencesGroup:
        """Build the Bezier Curve Editor row as a standalone group."""
        group = Adw.PreferencesGroup()
        curves_row = Adw.ActionRow(
            title="Bezier Curve Editor",
            subtitle="Create and manage animation curves",
        )
        curves_row.add_prefix(Gtk.Image.new_from_icon_name("draw-arc-symbolic"))
        curves_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        curves_row.set_activatable(True)
        curves_row.connect("activated", self._on_open_curve_editor)
        group.add(curves_row)
        return group

    def _on_open_curve_editor(self, _row):
        BezierEditorDialog(
            self._window,
            animation_page=self,
            get_curve_usage=self.get_curve_usage,
            animations=self._anims,
        )


class _AnimRow:
    """A single animation row.

    Parent rows (is_parent=True) use ExpanderRow to reveal child sub-animations.
    Child/standalone rows use ActionRow — click to open the detail dialog.
    """

    def __init__(
        self,
        page: AnimationsPage,
        name: str,
        depth: int,
        styles: tuple[str, ...] | list[str],
        is_parent: bool = False,
    ):
        self._page = page
        self._name = name
        self._depth = depth
        self._styles = styles
        self._is_parent = is_parent
        self._signals = SignalBlocker()

        if is_parent:
            self._row = Adw.ExpanderRow(title=ANIM_LABELS.get(name, name))
        else:
            self._row = Adw.ActionRow(title=ANIM_LABELS.get(name, name))
            self._row.set_activatable(True)
            self._row.connect("activated", self._on_activated)

        # Enable switch as a prefix — consistent left-edge alignment across all rows
        self._switch = Gtk.Switch()
        self._switch.set_valign(Gtk.Align.CENTER)
        self._signals.connect(self._switch, "notify::active", self._on_switch)
        self._row.add_prefix(self._switch)

        if is_parent:
            # Edit button to open the detail dialog (clicking the row expands children)
            edit_btn = Gtk.Button(icon_name="menu-symbolic")
            edit_btn.set_valign(Gtk.Align.CENTER)
            edit_btn.add_css_class("flat")
            edit_btn.set_tooltip_text("Edit animation settings")
            edit_btn.connect("clicked", lambda _b: self._on_activated(None))
            self._row.add_suffix(edit_btn)

        self._actions = RowActions(
            self._row,
            on_discard=self._on_discard,
            on_reset=self._on_reset,
        )
        self._row.add_suffix(self._actions.box)

        self.refresh()

    def add_child(self, child_row: "_AnimRow"):
        """Add a child row to this parent's ExpanderRow."""
        self._row.add_row(child_row.widget)  # type: ignore[union-attr]

    @property
    def widget(self):
        return self._row

    def refresh(self):
        """Update the row subtitle and switch from the current state."""
        with self._signals:
            state = self._page.anims.get_cached(self._name)
            if not state:
                return

            eff_en, eff_sp, eff_cu, eff_st = self._page.anims.get_effective(self._name)
            is_override = state.overridden
            is_dirty = self._page.is_anim_dirty(self._name)

            self._switch.set_active(eff_en)
            is_owned = self._page.is_owned(self._name)
            is_saved = self._page.is_saved(self._name)
            self._actions.update(is_managed=is_owned, is_dirty=is_dirty, is_saved=is_saved)

            # Build subtitle showing effective values
            parts = []
            if not eff_en:
                parts.append("disabled")
            parts.append(f"{eff_sp:.1f}ds")
            parts.append(eff_cu)
            if eff_st:
                parts.append(eff_st)

            if is_override:
                self._row.set_subtitle(" \u00b7 ".join(parts))
            else:
                self._row.set_subtitle("inherited \u00b7 " + " \u00b7 ".join(parts))

            # Dim inherited values
            self._switch.set_opacity(1.0 if is_override else 0.5)

    def _on_switch(self, switch, _pspec):
        self._page.set_field(self._name, "enabled", switch.get_active())

    def _on_discard(self):
        self._page.revert_anim(self._name)

    def _on_reset(self):
        self._page.unmanage_anim(self._name)

    def _on_activated(self, _row):
        """Open the detail dialog for this animation."""
        _AnimDetailDialog(self._page, self._name, self._styles)


class _AnimDetailDialog:
    """Dialog for editing a single animation's speed, curve, and style."""

    def __init__(self, page: AnimationsPage, name: str, styles: tuple[str, ...] | list[str]):
        self._page = page
        self._name = name
        self._styles = styles
        self._signals = SignalBlocker()

        state = page.anims.get_cached(name)
        eff_en, eff_sp, eff_cu, eff_st = page.anims.get_effective(name)

        self._dialog = Adw.Dialog()
        self._dialog.set_title(ANIM_LABELS.get(name, name))
        self._dialog.set_content_width(400)
        self._dialog.set_follows_content_size(True)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_margin_top(12)
        content.set_margin_bottom(24)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # Inherited note
        if state and not state.overridden:
            note = Gtk.Label(
                label="Inherits from parent. Editing will create an override.",
            )
            note.set_wrap(True)
            note.add_css_class("dim-label")
            note.set_margin_bottom(12)
            content.append(note)

        group = Adw.PreferencesGroup()

        # Speed
        speed_adj = Gtk.Adjustment(
            value=eff_sp,
            lower=0.5,
            upper=30,
            step_increment=0.5,
            page_increment=1,
        )
        self._speed_spin = Gtk.SpinButton(adjustment=speed_adj, digits=1)
        self._speed_spin.set_valign(Gtk.Align.CENTER)
        self._speed_row = Adw.ActionRow(
            title="Speed",
            subtitle="Duration in deciseconds (1 = 100ms)",
        )
        self._speed_row.add_suffix(self._speed_spin)
        self._signals.connect(self._speed_spin, "value-changed", self._on_speed)
        group.add(self._speed_row)

        # Curve with the edit button
        self._curve_names = get_curve_store().get_all_curve_names()
        curve_model = Gtk.StringList.new(_curve_display_names(self._curve_names))
        self._curve_row = Adw.ComboRow(
            title="Curve",
            subtitle="Bezier easing curve",
            model=curve_model,
        )
        if eff_cu in self._curve_names:
            self._curve_row.set_selected(self._curve_names.index(eff_cu))
        self._signals.connect(self._curve_row, "notify::selected", self._on_curve)

        edit_btn = Gtk.Button(icon_name="draw-arc-symbolic")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.add_css_class("flat")
        edit_btn.set_tooltip_text("Edit curve")
        edit_btn.connect("clicked", self._on_edit_curve)
        self._curve_row.add_suffix(edit_btn)
        group.add(self._curve_row)

        # Style (only if styles available)
        self._style_row = None
        if styles:
            style_options = ["default"] + list(styles)
            self._style_names = style_options
            style_model = Gtk.StringList.new(style_options)
            self._style_row = Adw.ComboRow(
                title="Style",
                subtitle="Animation style",
                model=style_model,
            )
            base_style = eff_st.split()[0] if eff_st else ""
            if base_style in self._style_names:
                self._style_row.set_selected(self._style_names.index(base_style))
            self._signals.connect(self._style_row, "notify::selected", self._on_style)
            group.add(self._style_row)

        content.append(group)
        toolbar.set_content(content)
        self._dialog.set_child(toolbar)
        self._dialog.present(page.window)

    def _on_speed(self, btn):
        self._page.set_field(self._name, "speed", btn.get_value())

    def _on_curve(self, row, _pspec):
        idx = row.get_selected()
        if 0 <= idx < len(self._curve_names):
            self._page.set_field(self._name, "curve", self._curve_names[idx])

    def _on_style(self, row, _pspec):
        idx = row.get_selected()
        if 0 <= idx < len(self._style_names):
            val = self._style_names[idx]
            self._page.set_field(self._name, "style", "" if val == "default" else val)

    def _on_edit_curve(self, _btn):
        idx = self._curve_row.get_selected()
        curve_name = self._curve_names[idx] if 0 <= idx < len(self._curve_names) else None

        BezierEditorDialog(
            self._page.window,
            on_curve_saved=self._refresh_curves,
            initial_curve=curve_name,
            animation_name=self._name,
            animation_page=self._page,
            get_curve_usage=self._page.get_curve_usage,
            animations=self._page.anims,
        )

    def _refresh_curves(self, curve_name=None):
        """Refresh curve dropdown after curve changes."""
        self._curve_names = get_curve_store().get_all_curve_names()
        model = Gtk.StringList.new(_curve_display_names(self._curve_names))
        with self._signals:
            self._curve_row.set_model(model)
            target = curve_name or self._page.anims.get_effective(self._name)[2]
            if target in self._curve_names:
                self._curve_row.set_selected(self._curve_names.index(target))
