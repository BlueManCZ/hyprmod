"""Keybind management page — categorized list with override support."""

import copy
from contextlib import contextmanager
from html import escape as html_escape

from gi.repository import Adw, GLib, Gtk
from hyprland_config import BindData, parse_bind_line
from hyprland_socket import HyprlandError, modmask_to_str

from hyprmod.binds import (
    CATEGORY_BY_ID,
    DISPATCHER_CATEGORIES,
    OverrideTracker,
    categorize_dispatcher,
    format_action,
)
from hyprmod.binds.dialog import BindEditDialog
from hyprmod.core import config
from hyprmod.core.ownership import SavedList
from hyprmod.core.undo import BindsUndoEntry
from hyprmod.ui import clear_children, make_page_layout
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# BindsPage
# ---------------------------------------------------------------------------


class BindsPage:
    """Builds the keybinds management page with categorized layout."""

    def __init__(self, window, on_dirty_changed=None, push_undo=None, saved_sections=None):
        self._window = window
        self._on_dirty_changed = on_dirty_changed
        self._push_undo = push_undo
        self._hypr_binds: list[BindData] = []
        self._search_term: str = ""
        self._group_widgets: dict[str, Adw.PreferencesGroup] = {}
        self._row_widgets: list[tuple[Adw.ActionRow, BindData, bool]] = []
        self._content_box: Gtk.Box
        self._scrolled: Gtk.ScrolledWindow
        self._search_entry: Gtk.SearchEntry
        self._search_bar: Gtk.SearchBar
        self._search_btn: Gtk.ToggleButton
        self._overrides: OverrideTracker
        self._owned_binds: SavedList[BindData]
        self._load_binds(saved_sections)

    def _apply_bind_live(self, bind: BindData) -> bool:
        """Register a bind in the running Hyprland instance."""
        try:
            self._window.hypr.keyword(
                bind.bind_type,
                f"{bind.mods_str}, {bind.key}, {bind.dispatcher}, {bind.arg}",
            )
            return True
        except HyprlandError as e:
            self._window.show_toast(f"Bind failed — {e}", timeout=5)
            return False

    def _unbind_live(self, bind: BindData) -> bool:
        """Remove a bind from the running Hyprland instance."""
        try:
            self._window.hypr.keyword(config.KEYWORD_UNBIND, f"{bind.mods_str}, {bind.key}")
            return True
        except HyprlandError as e:
            self._window.show_toast(f"Unbind failed — {e}", timeout=5)
            return False

    def _load_binds(self, saved_sections=None):
        live_binds = self._window.hypr.get_binds() or []
        all_hypr_binds = []
        for b in live_binds:
            all_hypr_binds.append(
                BindData(
                    mods=modmask_to_str(b.modmask).split(" + ") if b.modmask else [],
                    key=b.key,
                    dispatcher=b.dispatcher,
                    arg=b.arg,
                    bind_type="bind",
                )
            )

        parsed_binds: list[BindData] = []
        if saved_sections is not None:
            bind_lines = config.collect_bind_section(saved_sections)
        else:
            _, sections = config.read_all_sections()
            bind_lines = config.collect_bind_section(sections)
        for line in bind_lines:
            parsed = parse_bind_line(line)
            if parsed:
                parsed_binds.append(parsed)

        self._overrides = OverrideTracker(all_hypr_binds, document=self._window.hypr.document)
        self._overrides.parse_saved_overrides(parsed_binds)
        self._owned_binds = SavedList(parsed_binds, key=lambda b: b.to_line())

    # -- Undo / Redo --

    def _binds_key(self) -> list[str]:
        """Serialized representation of current binds state for comparison."""
        return [b.to_line() for b in self._owned_binds]

    @contextmanager
    def _undo_track(self):
        """Capture before/after binds state and push an undo entry."""
        old_items, old_baselines = self._owned_binds.snapshot()
        old_overrides = self._overrides.snapshot_session()
        old_key = self._binds_key()
        yield
        if self._push_undo and self._binds_key() != old_key:
            new_items, new_baselines = self._owned_binds.snapshot()
            self._push_undo(
                BindsUndoEntry(
                    old_items=old_items,
                    new_items=new_items,
                    old_baselines=old_baselines,
                    new_baselines=new_baselines,
                    old_session_overrides=old_overrides,
                    new_session_overrides=self._overrides.snapshot_session(),
                )
            )

    def restore_snapshot(self, items, baselines, session_overrides):
        """Restore binds state from an undo/redo snapshot."""
        # Undo live: unbind all current owned, restore overridden originals
        for b in self._owned_binds:
            self._unbind_live(b)
        for orig in self._overrides.snapshot_session().values():
            self._apply_bind_live(orig)

        # Restore internal state
        self._owned_binds.restore(items, baselines)
        self._overrides.restore_session(session_overrides)

        # Redo live: unbind restored override originals, bind all restored
        for orig in session_overrides.values():
            self._unbind_live(orig)
        for b in items:
            self._apply_bind_live(b)

        self._rebuild_list()
        self._notify_dirty()

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add keybind")
        add_btn.connect("clicked", self._on_add)
        page_header.pack_start(add_btn)

        self._search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self._search_btn.set_tooltip_text("Search keybinds")
        self._search_btn.connect("toggled", self._on_search_toggled)
        page_header.pack_end(self._search_btn)

        toolbar_view, page_box, self._content_box, self._scrolled = make_page_layout(
            header=page_header
        )

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Filter keybinds\u2026")
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        page_box.prepend(self._search_bar)

        self._rebuild_list()

        return toolbar_view

    # -- List building --

    def _refilter_hypr_binds(self):
        self._hypr_binds = self._overrides.filter_hypr_binds(self._owned_binds)  # type: ignore[arg-type]

    def _rebuild_list(self):
        self._refilter_hypr_binds()
        has_hypr_binds = bool(self._hypr_binds)

        vadj = self._scrolled.get_vadjustment()
        scroll_pos = vadj.get_value() if vadj else 0

        clear_children(self._content_box)

        self._group_widgets.clear()
        self._row_widgets.clear()

        categories: dict[str, list[tuple[BindData, bool, int]]] = {}
        for cat in DISPATCHER_CATEGORIES:
            categories[cat["id"]] = []

        for i, bind in enumerate(self._owned_binds):
            cat_id = categorize_dispatcher(bind.dispatcher)
            if cat_id not in categories:
                cat_id = "advanced"
            categories[cat_id].append((bind, True, i))

        owned_combos = {b.combo for b in self._owned_binds}
        for bind in self._hypr_binds:
            if bind.combo in owned_combos:
                continue
            cat_id = categorize_dispatcher(bind.dispatcher)
            if cat_id not in categories:
                cat_id = "advanced"
            categories[cat_id].append((bind, False, -1))

        # Info note for locked binds
        if has_hypr_binds:
            info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            info_box.set_margin_start(4)
            info_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
            info_icon.set_opacity(0.5)
            info_icon.set_valign(Gtk.Align.CENTER)
            info_box.append(info_icon)
            info_label = Gtk.Label(
                label="Locked keybinds come from your hyprland.conf. "
                "Click the edit button to override them.",
            )
            info_label.set_wrap(True)
            info_label.set_xalign(0)
            info_label.add_css_class("dim-label")
            info_label.add_css_class("caption")
            info_box.append(info_label)
            self._content_box.append(info_box)

        for cat in DISPATCHER_CATEGORIES:
            binds_in_cat = categories.get(cat["id"], [])
            if not binds_in_cat:
                continue

            group = Adw.PreferencesGroup(title=cat["label"])
            group.set_description(
                f"{len(binds_in_cat)} keybind{'s' if len(binds_in_cat) != 1 else ''}"
            )

            add_btn = Gtk.Button(icon_name="list-add-symbolic")
            add_btn.set_valign(Gtk.Align.CENTER)
            add_btn.add_css_class("flat")
            add_btn.set_tooltip_text(f"Add keybind to {cat['label']}")
            add_btn.connect("clicked", lambda _btn, cid=cat["id"]: self._on_add(category=cid))
            group.set_header_suffix(add_btn)

            for bind, editable, index in binds_in_cat:
                row = self._make_bind_row(bind, editable=editable, index=index, icon=cat["icon"])
                group.add(row)
                self._row_widgets.append((row, bind, editable))

            self._group_widgets[cat["id"]] = group
            self._content_box.append(group)

        if not self._row_widgets:
            empty = Adw.StatusPage(
                title="No Keybinds",
                description="Click the + button to add your first keybind.",
                icon_name="keyboard-shortcuts-symbolic",
            )
            self._content_box.append(empty)

        if vadj and scroll_pos > 0:
            GLib.idle_add(lambda: vadj.set_value(scroll_pos) or False)

        self._apply_filter()

    def _make_bind_row(
        self, bind: BindData, editable: bool, index: int = -1, icon: str = ""
    ) -> Adw.ActionRow:
        shortcut = bind.format_shortcut()
        action_str = format_action(bind.dispatcher, bind.arg)

        row = Adw.ActionRow(
            title=html_escape(shortcut),
            subtitle=html_escape(action_str),
        )

        if icon:
            prefix_icon = Gtk.Image.new_from_icon_name(icon)
            prefix_icon.set_opacity(0.6)
            row.add_prefix(prefix_icon)

        if not editable:
            row.add_css_class("option-default")

            override_btn = Gtk.Button(icon_name="document-edit-symbolic")
            override_btn.set_valign(Gtk.Align.CENTER)
            override_btn.add_css_class("flat")
            override_btn.set_tooltip_text("Override this keybind")
            override_btn.connect("clicked", lambda _btn, b=bind: self._on_override(b))
            row.add_suffix(override_btn)

            lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
            lock_icon.set_opacity(0.4)
            row.add_suffix(lock_icon)
            row.set_opacity(0.65)
        else:
            row.set_activatable(True)
            row.connect("activated", lambda _row, idx=index: self._on_edit_at(idx))

            is_dirty = self._owned_binds.is_item_dirty(index)
            is_saved = self._owned_binds.get_baseline(index) is not None

            actions = RowActions(
                row,
                on_discard=lambda idx=index: self._discard_bind_at(idx),
                on_reset=lambda idx=index: self._on_delete_at(idx),
            )
            row.add_suffix(actions.box)

            actions.update(
                is_managed=True,
                is_dirty=is_dirty,
                is_saved=is_saved,
            )

            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))

        return row

    # -- Search --

    def _on_search_toggled(self, btn):
        self._search_bar.set_search_mode(btn.get_active())
        if btn.get_active():
            self._search_entry.grab_focus()
        else:
            self._search_entry.set_text("")

    def _on_search_changed(self, entry):
        self._search_term = entry.get_text().strip().lower()
        self._apply_filter()

    def _apply_filter(self):
        term = self._search_term
        for cat_id, group in self._group_widgets.items():
            visible_count = 0
            for row, bind, _editable in self._row_widgets:
                if row.get_parent() is None:
                    continue
                row_cat = categorize_dispatcher(bind.dispatcher)
                if row_cat != cat_id:
                    continue
                if not term:
                    row.set_visible(True)
                    visible_count += 1
                else:
                    shortcut = bind.format_shortcut().lower()
                    action = format_action(bind.dispatcher, bind.arg).lower()
                    cat_label = CATEGORY_BY_ID.get(cat_id, {}).get("label", "").lower()
                    if term in shortcut or term in action or term in cat_label:
                        row.set_visible(True)
                        visible_count += 1
                    else:
                        row.set_visible(False)
            group.set_visible(visible_count > 0)

    # -- Duplicate detection --

    def _find_conflicts(self, bind: BindData, exclude_idx: int = -1) -> list[BindData]:
        target = bind.combo
        conflicts = []
        for i, b in enumerate(self._owned_binds):
            if i == exclude_idx:
                continue
            if b.combo == target:
                conflicts.append(b)
        for b in self._hypr_binds:
            if b.combo == target:
                conflicts.append(b)
        return conflicts

    # -- Add / Edit / Delete --

    def _on_add(self, _button=None, category: str = ""):
        owned_binds = self._owned_binds

        def on_apply(bind):
            with self._undo_track():
                self._apply_bind_live(bind)
                owned_binds.append_new(bind)
            self._notify_dirty()
            self._rebuild_list()

        dialog = BindEditDialog(
            window=self._window,
            initial_category=category,
            on_apply=on_apply,
            conflict_finder=lambda candidate: self._find_conflicts(candidate),
        )
        dialog.present(self._window)

    def _on_edit_at(self, idx):
        owned_binds = self._owned_binds
        if idx < 0 or idx >= len(owned_binds):
            return
        bind = owned_binds[idx]

        def on_apply(new_bind):
            with self._undo_track():
                self._unbind_live(bind)
                self._apply_bind_live(new_bind)
                owned_binds[idx] = new_bind
            self._notify_dirty()
            self._rebuild_list()

        dialog = BindEditDialog(
            bind=bind,
            window=self._window,
            on_apply=on_apply,
            conflict_finder=lambda candidate: self._find_conflicts(candidate, exclude_idx=idx),
        )
        dialog.present(self._window)

    def _on_override(self, hypr_bind):
        owned_binds = self._owned_binds
        overrides = self._overrides
        owned = copy.deepcopy(hypr_bind)
        hypr_c = hypr_bind.combo

        def on_apply(new_bind):
            with self._undo_track():
                self._unbind_live(hypr_bind)
                self._apply_bind_live(new_bind)
                owned_binds.append_new(new_bind)
                idx = len(owned_binds) - 1
                overrides.add_override(idx, hypr_bind)
            self._notify_dirty()
            self._rebuild_list()

        dialog = BindEditDialog(
            bind=owned,
            window=self._window,
            on_apply=on_apply,
            conflict_finder=lambda candidate: [
                c for c in self._find_conflicts(candidate) if c.combo != hypr_c
            ],
        )
        dialog.present(self._window)

    def _on_delete_at(self, idx):
        if idx < 0 or idx >= len(self._owned_binds):
            return
        with self._undo_track():
            removed = self._owned_binds.pop_at(idx)
            self._unbind_live(removed)
            original = self._overrides.remove_at(idx, removed_bind=removed)
            if original:
                self._apply_bind_live(original)
        self._notify_dirty()
        self._rebuild_list()

    # -- Dirty state --

    def _notify_dirty(self):
        if self._on_dirty_changed:
            self._on_dirty_changed()

    def _discard_bind_at(self, idx: int):
        """Revert a single bind to its saved state."""
        baseline = self._owned_binds.get_baseline(idx)
        if baseline is None:
            # New bind — discard means delete
            self._on_delete_at(idx)
            return
        # Revert to saved version — _undo_track handles pop-or-push
        with self._undo_track():
            current = self._owned_binds[idx]
            self._unbind_live(current)
            self._apply_bind_live(baseline)
            self._owned_binds.discard_at(idx)
        self._notify_dirty()
        self._rebuild_list()

    def is_dirty(self) -> bool:
        return self._owned_binds.is_dirty()

    def mark_saved(self):
        self._owned_binds.mark_saved()
        self._overrides.mark_saved(self._owned_binds)  # type: ignore[arg-type]
        self._rebuild_list()

    def reload_from_live(self):
        """Re-read binds from Hyprland and reset baselines.

        Used after profile activation to sync with the new live state.
        """
        self._load_binds()
        self._rebuild_list()

    def discard(self):
        saved_lines = self._owned_binds.saved_set
        current_lines = {b.to_line() for b in self._owned_binds}

        for b in self._owned_binds:
            if b.to_line() not in saved_lines:
                self._unbind_live(b)
        for b in self._owned_binds.saved:
            if b.to_line() not in current_lines:
                self._apply_bind_live(b)

        self._owned_binds.discard_all()

        for original in self._overrides.clear_session_overrides():
            self._apply_bind_live(original)

        self._rebuild_list()

    def get_bind_lines(self) -> list[str]:
        return self._overrides.get_bind_lines(self._owned_binds)  # type: ignore[arg-type]
