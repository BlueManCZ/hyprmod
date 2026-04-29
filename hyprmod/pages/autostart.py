"""Autostart page — manage ``exec`` and ``exec-once`` entries.

Hyprland runs every ``exec-once = …`` once at startup and every
``exec = …`` on every config reload. This page is a list editor for
those entries: add, edit, remove, reorder (load order is preserved on
save).

Unlike the keybinds page, autostart edits are *not* live-applied —
``hyprctl keyword exec foo`` would actually launch ``foo`` immediately,
which is rarely what someone editing the list wants (you'd get a second
``waybar`` while tweaking the existing entry). Instead, edits land in
hyprmod's managed config and take effect on the next Hyprland reload. A
per-row "Run now" action lets users test a command without firing
everything else on the page.

Reusable dialogs live in ``hyprmod.ui``:

- ``ui.autostart_edit_dialog.AutostartEditDialog`` for add/edit.
- ``ui.app_picker.AppPickerDialog`` for picking from installed
  ``.desktop`` apps without having to remember CLI binary names.
"""

import shlex
import subprocess
from html import escape as html_escape

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from hyprmod.core import config
from hyprmod.core.autostart import (
    EXEC_KEYWORDS,
    KEYWORD_LABELS,
    ExecData,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
    parse_exec_lines,
    serialize,
)
from hyprmod.core.desktop_apps import DesktopApp, list_apps, match_command
from hyprmod.core.ownership import SavedList
from hyprmod.core.undo import AutostartUndoEntry
from hyprmod.pages.section import SectionPage
from hyprmod.ui import clear_children, make_page_layout
from hyprmod.ui.app_picker import AppPickerDialog
from hyprmod.ui.autostart_edit_dialog import AutostartEditDialog
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# AutostartPage
# ---------------------------------------------------------------------------


class AutostartPage(SectionPage):
    """List editor for ``exec`` / ``exec-once`` config entries."""

    def __init__(
        self,
        window,
        on_dirty_changed=None,
        push_undo=None,
        saved_sections: dict[str, list[str]] | None = None,
    ):
        super().__init__(window, on_dirty_changed, push_undo)
        self._content_box: Gtk.Box
        self._scrolled: Gtk.ScrolledWindow
        self._owned: SavedList[ExecData]
        # Snapshot installed apps once at startup. The list is small
        # (few hundred at most) and the page lives for the app session,
        # so per-row matching is just a linear scan over a cached list.
        # If users install new apps mid-session the page won't reflect
        # them until restart — acceptable for now; we can hook into
        # ``Gio.AppInfoMonitor`` later if it becomes a complaint.
        self._installed_apps: list[DesktopApp] = list_apps()
        # Maps each owned-list index to the ``Adw.ActionRow`` widget
        # currently representing it. Rebuilt on every ``_rebuild_list``
        # call: pre-sized with ``None`` slots and filled in as
        # ``_make_row`` runs, so a freshly-rebuilt list briefly has
        # ``None`` entries before all rows are constructed. Used by
        # the keyboard reorder path (Alt+Up/Down) to refocus the
        # moved row post-rebuild for chained shortcuts.
        self._rows_by_idx: list[Adw.ActionRow | None] = []
        # Index of the row currently being dragged, ``None`` when
        # no drag is in progress. Read by ``motion`` to refuse
        # cross-keyword drops without waiting for the async drop
        # value to resolve.
        self._dragging_idx: int | None = None
        # ``(x, y)`` of the press that started the current drag, in
        # source-row-local coords. Stashed by ``drag-prepare`` for
        # ``drag-begin`` to use as the icon's hot spot. ``None``
        # when no drag is active.
        self._drag_press: tuple[float, float] | None = None
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            _, saved_sections = config.read_all_sections()
        raw_lines = config.collect_section(saved_sections, *EXEC_KEYWORDS)
        items = parse_exec_lines(raw_lines)
        self._owned = SavedList(items, key=lambda e: e.to_line())

    # ── Undo / Redo ──

    def _capture_undo(self):
        return self._owned.snapshot()

    def _undo_key(self) -> list[str]:
        return [e.to_line() for e in self._owned]

    def _build_undo_entry(self, old, new):
        old_items, old_baselines = old
        new_items, new_baselines = new
        return AutostartUndoEntry(
            old_items=old_items,
            new_items=new_items,
            old_baselines=old_baselines,
            new_baselines=new_baselines,
        )

    def restore_snapshot(self, items: list[ExecData], baselines: list[ExecData | None]) -> None:
        """Restore state from an undo/redo snapshot."""
        self._owned.restore(items, baselines)
        self._rebuild_list()
        self._notify_dirty()

    # ── Build ──

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add autostart entry")
        add_btn.connect("clicked", lambda _b: self._on_add())
        page_header.pack_start(add_btn)

        toolbar_view, _, self._content_box, self._scrolled = make_page_layout(header=page_header)

        self._rebuild_list()
        return toolbar_view

    # ── List rendering ──

    def _rebuild_list(self, focus_idx: int = -1) -> None:
        clear_children(self._content_box)
        # Pre-size with ``None`` slots; ``_make_row`` fills each as it
        # runs. Sparse list is fine while rows are still being built —
        # the type annotation in ``__init__`` admits ``None``.
        self._rows_by_idx = [None] * len(self._owned)

        # Reorder hint shown only when there are at least two entries
        # — with one or zero rows there's nothing to reorder, so the
        # hint would just be noise.
        if len(self._owned) >= 2:
            self._content_box.append(self._build_reorder_hint())

        # Group by keyword so users can scan startup vs. reload separately.
        by_keyword: dict[str, list[tuple[int, ExecData]]] = {kw: [] for kw in EXEC_KEYWORDS}
        for idx, item in enumerate(self._owned):
            by_keyword.setdefault(item.keyword, []).append((idx, item))

        any_rows = False
        for kw in EXEC_KEYWORDS:
            entries = by_keyword.get(kw, [])
            if not entries:
                continue
            any_rows = True
            self._content_box.append(self._build_group(kw, entries))

        # Surface deleted rows so the user can restore or re-confirm them.
        deleted = self._deleted_baselines()
        if deleted:
            any_rows = True
            self._content_box.append(self._build_deleted_group(deleted))

        if not any_rows:
            self._content_box.append(self._build_empty_state())

        if 0 <= focus_idx < len(self._rows_by_idx):
            target = self._rows_by_idx[focus_idx]
            if target is not None:
                # Defer to idle so the row has actually been mapped
                # before grab_focus runs. CRITICAL: the callback must
                # return ``GLib.SOURCE_REMOVE`` — ``Widget.grab_focus``
                # returns ``True`` on success, which an idle handler
                # interprets as "fire me again," producing an infinite
                # focus-grab loop that freezes Tab navigation.
                GLib.idle_add(self._grab_focus_once, target)

    @staticmethod
    def _grab_focus_once(widget: Gtk.Widget) -> bool:
        widget.grab_focus()
        return GLib.SOURCE_REMOVE  # one-shot

    def _build_empty_state(self) -> Adw.StatusPage:
        """Empty-state page with action buttons.

        Two actions: "Pick installed app" (opens the app picker
        directly) and "Add custom command" (opens the edit dialog).
        Surfaces both paths upfront so users don't have to discover
        the picker hidden inside the edit dialog.
        """
        empty = Adw.StatusPage(
            title="No Autostart Entries",
            description=("Add programs that should launch automatically when Hyprland starts."),
            icon_name="media-playback-start-symbolic",
        )

        button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
        )

        pick_btn = Gtk.Button(label="Pick from Installed Apps")
        pick_btn.add_css_class("suggested-action")
        pick_btn.add_css_class("pill")
        pick_btn.connect("clicked", lambda _b: self._on_quick_pick())
        button_box.append(pick_btn)

        add_btn = Gtk.Button(label="Custom Command…")
        add_btn.add_css_class("pill")
        add_btn.connect("clicked", lambda _b: self._on_add())
        button_box.append(add_btn)

        empty.set_child(button_box)
        return empty

    def _build_reorder_hint(self) -> Gtk.Widget:
        """Inline note teaching the two reorder gestures.

        Same shape as the keybinds page's "locked binds" info row:
        dim icon + dim caption-styled label. The hint is the *only*
        place either interaction is advertised, so the two gestures
        (drag, Alt+arrows) are spelled out explicitly rather than
        implied via tooltip.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(4)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.5)
        icon.set_valign(Gtk.Align.START)
        box.append(icon)

        label = Gtk.Label(
            label=(
                "Reorder entries by dragging them within their group, "
                "or with Alt+↑ / Alt+↓ on a focused row."
            ),
        )
        label.set_wrap(True)
        label.set_xalign(0)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
        return box

    def _build_group(
        self, keyword: str, entries: list[tuple[int, ExecData]]
    ) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=KEYWORD_LABELS.get(keyword, keyword))
        group.set_description(f"{len(entries)} entr{'ies' if len(entries) != 1 else 'y'}")

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        label = KEYWORD_LABELS.get(keyword, keyword).lower()
        add_btn.set_tooltip_text(f"Add another entry that runs {label}")
        add_btn.connect(
            "clicked",
            lambda _b, kw=keyword: self._on_add(default_advanced=kw == config.KEYWORD_EXEC),
        )
        group.set_header_suffix(add_btn)

        for idx, item in entries:
            group.add(self._make_row(idx, item))
        return group

    def _build_deleted_group(self, deleted: list[ExecData]) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Removed (pending save)")
        group.set_description(
            f"{len(deleted)} entr{'ies' if len(deleted) != 1 else 'y'} will be removed on save"
        )
        for item in deleted:
            matched = match_command(item.command, self._installed_apps)
            keyword_label = KEYWORD_LABELS.get(item.keyword, item.keyword)
            if matched is not None:
                title = matched.name
                subtitle = f"{keyword_label} · {item.command}"
            else:
                title = item.command
                subtitle = keyword_label

            row = Adw.ActionRow(
                title=html_escape(title),
                subtitle=html_escape(subtitle),
            )
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            row.add_css_class("option-default")
            row.set_opacity(0.65)

            if matched is not None and matched.icon_name:
                prefix = Gtk.Image.new_from_icon_name(matched.icon_name)
                prefix.set_pixel_size(32)
                row.add_prefix(prefix)

            restore_btn = Gtk.Button(icon_name="edit-undo-symbolic")
            restore_btn.set_valign(Gtk.Align.CENTER)
            restore_btn.add_css_class("flat")
            restore_btn.set_tooltip_text("Restore this entry")
            restore_btn.connect("clicked", lambda _b, e=item: self._on_restore_deleted(e))
            row.add_suffix(restore_btn)

            group.add(row)
        return group

    def _make_row(self, idx: int, item: ExecData) -> Adw.ActionRow:
        # Match against installed apps so a row picked from the picker
        # (or a manually-typed command that happens to match an app)
        # renders with the app's friendly name + icon, with the raw
        # command demoted to subtitle for transparency.
        matched = match_command(item.command, self._installed_apps)
        if matched is not None:
            title = matched.name
            subtitle = item.command  # keep the raw command visible
        else:
            title = item.command
            # Group header already shows "Once at startup" / "On every reload",
            # so a per-row keyword subtitle would be redundant noise.
            subtitle = ""

        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        # Single-line wrap with end-ellipsize keeps long Chrome-style
        # commands from blowing up row height.
        row.set_title_lines(1)
        row.set_subtitle_lines(1)

        if matched is not None and matched.icon_name:
            prefix = Gtk.Image.new_from_icon_name(matched.icon_name)
            prefix.set_pixel_size(32)
        else:
            prefix = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            prefix.set_opacity(0.6)
        row.add_prefix(prefix)

        # Whole-row drag-and-drop reorder. The DragSource sits on the
        # entire row so users can grab anywhere — the natural "I want
        # to move this" gesture. ``Gtk.DragSource`` only claims the
        # press if motion crosses its threshold, so a plain click
        # still routes to the row's ``activated`` signal (edit dialog).
        # Keyboard parallel: Alt+Up / Alt+Down on the focused row,
        # advertised via the page-top hint.
        self._attach_drag_source(row, idx)
        self._attach_drop_target(row, idx)
        self._attach_keyboard_reorder(row, idx)
        if idx < len(self._rows_by_idx):
            self._rows_by_idx[idx] = row

        is_dirty = self._owned.is_item_dirty(idx)
        is_saved = self._owned.get_baseline(idx) is not None

        actions = RowActions(
            row,
            on_discard=lambda i=idx: self._discard_at(i),
            on_reset=lambda i=idx: self._on_delete_at(i),
            reset_icon="user-trash-symbolic",
            reset_tooltip="Remove this entry",
        )
        row.add_suffix(actions.box)
        actions.update(is_managed=True, is_dirty=is_dirty, is_saved=is_saved)

        # "Run now" — a low-friction way to test a command without
        # reloading Hyprland or duplicating exec-once on every save.
        run_btn = Gtk.Button(icon_name="system-run-symbolic")
        run_btn.set_valign(Gtk.Align.CENTER)
        run_btn.add_css_class("flat")
        run_btn.set_tooltip_text("Run this command now")
        run_btn.connect("clicked", lambda _b, e=item: self._run_now(e))
        row.add_suffix(run_btn)

        row.set_activatable(True)
        row.connect("activated", lambda _r, i=idx: self._on_edit_at(i))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    def _deleted_baselines(self) -> list[ExecData]:
        """Return saved entries that are no longer in the owned list."""
        current = {e.to_line() for e in self._owned}
        return [b for b in self._owned.saved if b.to_line() not in current]

    # ── Reorder (drag-and-drop + Alt+arrow keyboard shortcut) ──

    def _attach_drag_source(self, row: Adw.ActionRow, idx: int) -> None:
        """Make *row* the source of a same-group reorder drag.

        ``Gtk.DragSource`` only claims the input sequence once motion
        has crossed its threshold, so a plain click on the row still
        routes to ``activated`` (the edit dialog). The whole row is
        the grab area — no separate handle to discover.
        """
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, idx)
        source.connect("drag-begin", self._on_drag_begin, idx)
        source.connect("drag-end", self._on_drag_end)
        row.add_controller(source)

    def _attach_drop_target(self, row: Adw.ActionRow, idx: int) -> None:
        """Make *row* a drop target for same-group reorder.

        Uses the cursor's vertical position within the row to choose
        an above/below insertion point, so users can drop *between*
        rows without needing pixel-perfect aim. Cross-keyword drops
        (e.g. dragging an exec-once over an exec row) are silently
        refused — no indicator shown.
        """
        target = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        target.connect("motion", self._on_drop_motion, idx)
        target.connect("leave", self._on_drop_leave)
        target.connect("drop", self._on_drop, idx)
        row.add_controller(target)

    def _on_drag_prepare(
        self, _source: Gtk.DragSource, x: float, y: float, idx: int
    ) -> Gdk.ContentProvider | None:
        # Stash the press coords so ``drag-begin`` can use them as the
        # icon's hot spot. Setting the icon in ``prepare`` doesn't
        # always stick — some compositors apply the icon only after
        # the drag is fully initialised, which happens between the
        # ``prepare`` and ``drag-begin`` signals.
        self._drag_press = (x, y)
        val = GObject.Value(GObject.TYPE_INT, idx)
        return Gdk.ContentProvider.new_for_value(val)

    def _on_drag_begin(self, source: Gtk.DragSource, drag: Gdk.Drag, idx: int) -> None:
        self._dragging_idx = idx
        press = self._drag_press or (0.0, 0.0)
        hot_x, hot_y = int(press[0]), int(press[1])
        # ``Adw.ActionRow`` has no intrinsic background — the visible
        # "card" appearance comes from the parent ``PreferencesGroup``'s
        # ``boxed-list`` styling. Painted in isolation the row would be
        # transparent, so we add a short-lived CSS class that gives it
        # a solid background + corner radius for the duration of the
        # drag. ``Gtk.WidgetPaintable`` is a *live* view, so it picks
        # up the new CSS class on the next paint.
        widget = source.get_widget()
        if widget is not None:
            widget.add_css_class("autostart-drag-source")
            paintable = Gtk.WidgetPaintable.new(widget)
            source.set_icon(paintable, hot_x, hot_y)
        # Belt-and-suspenders: also set the hot spot directly on the
        # ``Gdk.Drag``. ``GtkDragSource.set_icon`` calls this internally
        # but at least one Wayland compositor (Hyprland) appears to
        # ignore the hot spot at that point — repeating the call
        # against the live ``Gdk.Drag`` after it's been initialised is
        # harmless if redundant and effective when the earlier call
        # was lost.
        drag.set_hotspot(hot_x, hot_y)

    def _on_drag_end(
        self,
        source: Gtk.DragSource,
        _drag: Gdk.Drag,
        _delete: bool,
    ) -> None:
        self._dragging_idx = None
        self._drag_press = None
        widget = source.get_widget()
        if widget is not None:
            widget.remove_css_class("autostart-drag-source")
        # Defensive: if the drop completed and rebuilt the list before
        # ``leave`` fired, dangling indicator classes would carry over
        # to other rows that happen to land at the same widget pointer.
        self._clear_drop_indicators()

    def _on_drop_motion(
        self,
        target: Gtk.DropTarget,
        _x: float,
        y: float,
        hover_idx: int,
    ) -> Gdk.DragAction:
        # ``motion`` doesn't have access to the dragged value — that's
        # only resolved at drop time — so we read ``_dragging_idx``
        # set in ``drag-begin`` to validate the move synchronously.
        src = self._dragging_idx
        if src is None or src == hover_idx:
            return Gdk.DragAction(0)
        if not self._is_valid_move(src, hover_idx):
            return Gdk.DragAction(0)

        widget = target.get_widget()
        if widget is None:
            return Gdk.DragAction(0)

        before = self._is_above_half(widget, y)
        # Top-edge or bottom-edge insertion line via inset box-shadow.
        # Only one class at a time per row, so flicking across the
        # midpoint cleanly swaps the indicator.
        if before:
            widget.add_css_class("autostart-drop-above")
            widget.remove_css_class("autostart-drop-below")
        else:
            widget.add_css_class("autostart-drop-below")
            widget.remove_css_class("autostart-drop-above")
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, target: Gtk.DropTarget) -> None:
        widget = target.get_widget()
        if widget is not None:
            widget.remove_css_class("autostart-drop-above")
            widget.remove_css_class("autostart-drop-below")

    def _on_drop(
        self,
        target: Gtk.DropTarget,
        value: object,
        _x: float,
        y: float,
        hover_idx: int,
    ) -> bool:
        # PyGObject normally unwraps ``GObject.TYPE_INT`` to a plain
        # ``int``, but the signal contract is ``object`` so the type
        # checker can't see that. Fall back to ``int(value)`` for the
        # rare wrapper case; the ``type: ignore`` covers the int()
        # call against an arbitrary object.
        src_idx = value if isinstance(value, int) else int(value)  # type: ignore[arg-type]
        if not self._is_valid_move(src_idx, hover_idx):
            return False
        widget = target.get_widget()
        if widget is None:
            return False
        before = self._is_above_half(widget, y)

        target_idx = drop_target_idx(src_idx, hover_idx, before)

        # ``move()`` itself rejects out-of-range targets, but compute
        # cleanly here so a same-position no-op doesn't push an empty
        # undo entry.
        if target_idx == src_idx:
            return False
        n = len(self._owned)
        if not 0 <= target_idx < n:
            return False

        with self._undo_track():
            self._owned.move(src_idx, target_idx)
        self._notify_dirty()
        self._rebuild_list()
        return True

    @staticmethod
    def _is_above_half(widget: Gtk.Widget, y: float) -> bool:
        """True if *y* falls in the upper half of *widget*.

        Used to choose between ``insert-above`` and ``insert-below``
        for a drop on this widget. Falls back to "above" for zero-
        height widgets (shouldn't happen, but cheap to handle).
        """
        height = widget.get_height() or widget.get_allocated_height()
        if height <= 0:
            return True
        return y < height / 2

    def _clear_drop_indicators(self) -> None:
        """Remove insertion-line classes from every tracked row.

        Belt-and-suspenders: ``leave`` should clear them per row, but
        if the drop completed and ``_rebuild_list`` ran before the
        leave signal fired, the freshly-rebuilt rows shouldn't
        inherit any stale state. Iterating the rows we already
        track avoids a recursive widget-tree walk.
        """
        for row in self._rows_by_idx:
            if row is not None:
                row.remove_css_class("autostart-drop-above")
                row.remove_css_class("autostart-drop-below")

    def _attach_keyboard_reorder(self, row: Adw.ActionRow, idx: int) -> None:
        """Bind Alt+Up / Alt+Down on *row* to move it within its keyword group.

        Keyboard parallel to drag-and-drop — same ``_move_relative``
        path, same validation, same undo entry, same focus-restore
        for chained shortcuts.
        """
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_row_key_pressed, idx)
        row.add_controller(controller)

    def _on_row_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        idx: int,
    ) -> bool:
        # Require Alt, reject if Shift/Ctrl/Super are also held. Those
        # combos are reserved for future shortcuts (e.g. Alt+Shift+Up
        # to move-to-top) that we may add later.
        wanted = Gdk.ModifierType.ALT_MASK
        relevant = (
            Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if state & relevant != wanted:
            return False

        if keyval == Gdk.KEY_Up:
            delta = -1
        elif keyval == Gdk.KEY_Down:
            delta = 1
        else:
            return False
        return self._move_relative(idx, delta)

    def _move_relative(self, idx: int, delta: int) -> bool:
        """Move the entry at *idx* by *delta* slots (typically ±1).

        Returns ``True`` when the move was performed, ``False`` when
        the move would have been illegal (out of range, or crossing
        a keyword-group boundary). The keyboard handler propagates
        this return value as its "event consumed" flag so unhandled
        Up/Down arrows fall through to default focus traversal.
        """
        target = idx + delta
        if not self._is_valid_move(idx, target):
            return False
        with self._undo_track():
            self._owned.move(idx, target)
        self._notify_dirty()
        self._rebuild_list(focus_idx=target)
        return True

    def _is_valid_move(self, src_idx: int, dst_idx: int) -> bool:
        """True if moving *src_idx* to *dst_idx* is a legal reorder.

        Restricts moves to within the same keyword group: turning an
        ``exec-once`` into an ``exec`` (or vice versa) by reordering
        would silently change the entry's behaviour, so we don't
        allow it. Users who need to flip the trigger edit the entry.
        """
        n = len(self._owned)
        if src_idx < 0 or dst_idx < 0:
            return False
        if src_idx == dst_idx:
            return False
        if src_idx >= n or dst_idx >= n:
            return False
        return self._owned[src_idx].keyword == self._owned[dst_idx].keyword

    # ── Add / Edit / Remove ──

    def _on_add(self, default_advanced: bool = False) -> None:
        owned = self._owned

        def on_apply(new_item: ExecData) -> None:
            with self._undo_track():
                owned.append_new(new_item)
            self._notify_dirty()
            self._rebuild_list()

        AutostartEditDialog.present_singleton(
            self._window,
            initial_advanced=default_advanced,
            on_apply=on_apply,
        )

    def _on_quick_pick(self) -> None:
        """Empty-state shortcut: open the app picker directly.

        Apps picked this way always become ``exec-once`` entries — that's
        what 95% of autostart usage actually wants, and the user can
        still flip on "Re-run on every reload" by editing the entry
        afterwards if they need ``exec`` behaviour.
        """
        owned = self._owned

        def on_pick(app: DesktopApp) -> None:
            new_item = ExecData(keyword=config.KEYWORD_EXEC_ONCE, command=app.command)
            with self._undo_track():
                owned.append_new(new_item)
            self._notify_dirty()
            self._rebuild_list()

        AppPickerDialog.present_singleton(self._window, on_pick=on_pick)

    def _on_edit_at(self, idx: int) -> None:
        owned = self._owned
        if idx < 0 or idx >= len(owned):
            return
        current = owned[idx]

        def on_apply(new_item: ExecData) -> None:
            if new_item == current:
                return
            with self._undo_track():
                owned[idx] = new_item
            self._notify_dirty()
            self._rebuild_list()

        AutostartEditDialog.present_singleton(
            self._window,
            entry=current,
            on_apply=on_apply,
        )

    def _on_delete_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._owned):
            return
        with self._undo_track():
            self._owned.pop_at(idx)
        self._notify_dirty()
        self._rebuild_list()

    def _discard_at(self, idx: int) -> None:
        """Revert a single entry to its saved value (or remove if unsaved)."""
        baseline = self._owned.get_baseline(idx)
        if baseline is None:
            self._on_delete_at(idx)
            return
        with self._undo_track():
            self._owned.discard_at(idx)
        self._notify_dirty()
        self._rebuild_list()

    def _on_restore_deleted(self, item: ExecData) -> None:
        """Re-add a previously-deleted entry as a new owned row."""
        with self._undo_track():
            self._owned.append_new(item)
        self._notify_dirty()
        self._rebuild_list()

    # ── Reorder helpers (queried by pages/pending.py) ──

    def is_reordered(self) -> bool:
        """True if the *common* items between saved and current differ in order.

        Pure pass-through to ``core.autostart.detect_reorder``; lives
        on the page so callers (notably the pending-changes view)
        don't need to know about ``_owned`` internals.
        """
        return detect_reorder(self._owned.saved, list(self._owned))

    def pending_change_count(self) -> int:
        """Number of distinct pending-change entries the page would surface.

        Pure pass-through to ``core.autostart.count_pending_changes``
        so the sidebar badge agrees with the pending-changes list by
        construction — both ultimately call the same helper.
        """
        if not self.is_dirty():
            return 0
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        return count_pending_changes(self._owned.saved, list(self._owned), baselines)

    def revert_reorder(self) -> None:
        """Restore the saved order while preserving other dirty changes.

        - Items that exist in both saved and current are repositioned
          to their saved-order slots (any in-flight edits to those
          items are kept — only the position is reverted).
        - Newly-added items (no baseline) keep their values and slot
          in at the end.
        - Items the user removed stay removed; this revert isn't a
          general "undo all".

        Pushes a single undo entry so Ctrl+Z restores the pre-revert
        order in one step.
        """
        # Map saved-line -> (current_item, baseline) for items that
        # originated from the saved snapshot (have a non-None baseline).
        # ``baseline.to_line()`` is the stable identity even if the
        # user has since edited the item — that's how we keep edits
        # while reverting position.
        by_saved_line: dict[str, tuple[ExecData, ExecData | None]] = {}
        new_pairs: list[tuple[ExecData, ExecData | None]] = []

        for idx in range(len(self._owned)):
            item = self._owned[idx]
            baseline = self._owned.get_baseline(idx)
            if baseline is None:
                new_pairs.append((item, baseline))
            else:
                by_saved_line[baseline.to_line()] = (item, baseline)

        rebuilt_items: list[ExecData] = []
        rebuilt_baselines: list[ExecData | None] = []
        for saved in self._owned.saved:
            pair = by_saved_line.get(saved.to_line())
            if pair is None:
                # User removed this entry; not coming back from a
                # reorder revert.
                continue
            item, baseline = pair
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)
        # Newly-added rows keep their existing positions at the end of
        # the list — they have no saved-order to revert to.
        for item, baseline in new_pairs:
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)

        with self._undo_track():
            self._owned.restore(rebuilt_items, rebuilt_baselines)
        self._notify_dirty()
        self._rebuild_list()

    # ── Run-now ──

    def _run_now(self, item: ExecData) -> None:
        """Best-effort fire-and-forget launch of a command for testing.

        Errors during ``Popen`` (most commonly: shell parse failures)
        are surfaced as a toast; runtime errors after spawn are the
        user's problem — same behaviour as Hyprland itself.
        """
        cmd = item.command.strip()
        if not cmd:
            return
        try:
            tokens = shlex.split(cmd)
        except ValueError as e:
            self._window.show_toast(f"Couldn't parse command: {e}", timeout=4)
            return
        try:
            subprocess.Popen(  # noqa: S603 — user-supplied autostart command, by design
                tokens,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, FileNotFoundError) as e:
            self._window.show_toast(f"Failed to run: {e}", timeout=5)
            return
        self._window.show_toast(f"Started: {cmd}")

    # ── SectionPage protocol ──

    def is_dirty(self) -> bool:
        return self._owned.is_dirty()

    def mark_saved(self) -> None:
        self._owned.mark_saved()
        self._rebuild_list()

    def discard(self) -> None:
        self._owned.discard_all()
        self._rebuild_list()

    def reload_from_saved(self, saved_sections: dict[str, list[str]]) -> None:
        """Re-load baseline from the given saved sections (after profile switch)."""
        self._load(saved_sections)
        self._rebuild_list()

    # ── Save plumbing ──

    def get_exec_lines(self) -> list[str]:
        """Serialize the current entries for ``config.write_all``.

        Order is preserved as-is — users may rely on, e.g., ``swaybg``
        being listed before ``waybar`` so the wallpaper is up before
        the bar starts. Within each keyword group the relative order
        is what the user saw in the UI.
        """
        return serialize(list(self._owned))

    @staticmethod
    def has_managed_section(sections: dict[str, list[str]]) -> bool:
        """True if the saved config already contains any exec/exec-once lines."""
        return any(sections.get(kw) for kw in EXEC_KEYWORDS)


__all__ = ["AutostartPage"]
