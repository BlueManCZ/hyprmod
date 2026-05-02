"""Environment Variables page — manage ``env = NAME,value`` entries.

Hyprland's ``env`` keyword exports environment variables to processes
the compositor spawns (``exec``/``exec-once`` children, dispatcher
``exec`` calls, terminal launches). Lines look like
``env = QT_QPA_PLATFORM,wayland``; the first comma separates name
from value, further commas inside the value are preserved.

Like autostart, env edits are **not** live-applied — Hyprland reads
``env`` lines once at compositor startup and there's no IPC path to
retroactively patch the environment of already-running processes.
Edits land in hyprmod's managed config and take effect on the next
Hyprland session.

External entries (env vars defined in the user's ``hyprland.conf``
or any file it sources) are surfaced read-only at the bottom of the
page with an "override" button — same UX as locked keybinds. Clicking
the button opens the edit dialog pre-filled with the external var's
name and value; on apply, a new managed entry is added. Hyprland
sources files in order with last-write-wins semantics, and HyprMod's
first-run setup ensures our file is sourced after ``hyprland.conf``,
so a managed override always wins.

The cursor theme/size variables (``XCURSOR_THEME``, ``XCURSOR_SIZE``,
``HYPRCURSOR_THEME``, ``HYPRCURSOR_SIZE``) are owned by the Cursor
page — they're transparently filtered out of this page on read (both
managed and external), so there's only one place to edit each
variable. On save, both pages emit env lines independently and the
window concatenates them (cursor first, by convention).

Reusable dialog lives in ``hyprmod.ui``:

- ``ui.env_var_edit_dialog.EnvVarEditDialog`` for add/edit/override.
"""

from html import escape as html_escape
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from hyprmod.core import config
from hyprmod.core.env_vars import (
    RESERVED_NAMES,
    EnvVar,
    ExternalEnvVar,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
    load_external_env_vars,
    overridden_external_names,
    parse_env_lines,
    serialize,
)
from hyprmod.core.ownership import SavedList
from hyprmod.core.setup import HYPRLAND_CONF
from hyprmod.core.undo import EnvVarsUndoEntry
from hyprmod.pages.section import SectionPage
from hyprmod.ui import clear_children, display_path, make_page_layout
from hyprmod.ui.env_var_edit_dialog import EnvVarEditDialog
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# EnvVarsPage
# ---------------------------------------------------------------------------


class EnvVarsPage(SectionPage):
    """List editor for ``env = NAME,value`` config entries."""

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
        self._owned: SavedList[EnvVar]
        # Env vars from sourced config files outside our managed file —
        # surfaced read-only with an override button. Rebuilt on every
        # load (including profile switches), since their content can
        # change without our involvement.
        self._external: list[ExternalEnvVar] = []
        self._rows_by_idx: list[Adw.ActionRow | None] = []
        # Index of the row currently being dragged, ``None`` when no
        # drag is in progress. Read by ``motion`` to validate drops
        # synchronously.
        self._dragging_idx: int | None = None
        self._drag_press: tuple[float, float] | None = None
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            _, saved_sections = config.read_all_sections()
        raw_lines = config.collect_section(saved_sections, config.KEYWORD_ENV)
        items = parse_env_lines(raw_lines)
        # Strip out cursor-owned vars so they show up only on the Cursor
        # page. We deliberately do this on the OWNED list (not just the
        # display) so the page truly doesn't track them — that keeps
        # save/discard/undo from accidentally rewriting cursor vars
        # via this page's serializer.
        items = [item for item in items if item.name not in RESERVED_NAMES]
        self._owned = SavedList(items, key=lambda e: e.to_line())
        # External entries — those defined in the user's hyprland.conf
        # or any file it sources, excluding our managed file. The loader
        # also drops cursor-owned names so they're surfaced only on the
        # Cursor page.
        self._external = load_external_env_vars(HYPRLAND_CONF, config.gui_conf())

    # ── Undo / Redo ──

    def _capture_undo(self):
        return self._owned.snapshot()

    def _undo_key(self) -> list[str]:
        return [e.to_line() for e in self._owned]

    def _build_undo_entry(self, old, new):
        old_items, old_baselines = old
        new_items, new_baselines = new
        return EnvVarsUndoEntry(
            old_items=old_items,
            new_items=new_items,
            old_baselines=old_baselines,
            new_baselines=new_baselines,
        )

    def restore_snapshot(self, items: list[EnvVar], baselines: list[EnvVar | None]) -> None:
        """Restore state from an undo/redo snapshot."""
        self._owned.restore(items, baselines)
        self._rebuild_list()
        self._notify_dirty()

    # ── Build ──

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add environment variable")
        add_btn.connect("clicked", lambda _b: self._on_add())
        page_header.pack_start(add_btn)

        toolbar_view, _, self._content_box, self._scrolled = make_page_layout(header=page_header)

        self._rebuild_list()
        return toolbar_view

    # ── List rendering ──

    def _rebuild_list(self, focus_idx: int = -1) -> None:
        clear_children(self._content_box)
        self._rows_by_idx = [None] * len(self._owned)

        # Reorder hint shown only when there are at least two entries
        # — with one or zero rows there's nothing to reorder.
        if len(self._owned) >= 2:
            self._content_box.append(self._build_reorder_hint())

        if len(self._owned) > 0:
            self._content_box.append(self._build_group())

        # Surface deleted rows so the user can restore or re-confirm them.
        deleted = self._deleted_baselines()
        if deleted:
            self._content_box.append(self._build_deleted_group(deleted))

        # External vars from the user's hyprland.conf or sourced files —
        # locked rows with an override button. Always at the bottom: it's
        # reference info / starting point for overrides, not the primary
        # content.
        if self._external:
            for widget in self._build_external_section():
                self._content_box.append(widget)

        if len(self._owned) == 0 and not deleted and not self._external:
            self._content_box.append(self._build_empty_state())

        if 0 <= focus_idx < len(self._rows_by_idx):
            target = self._rows_by_idx[focus_idx]
            if target is not None:
                # Defer to idle so the row has actually been mapped
                # before grab_focus runs. Returns SOURCE_REMOVE since
                # ``grab_focus`` returns True (which an idle handler
                # would interpret as "keep firing").
                GLib.idle_add(self._grab_focus_once, target)

    @staticmethod
    def _grab_focus_once(widget: Gtk.Widget) -> bool:
        widget.grab_focus()
        return GLib.SOURCE_REMOVE  # one-shot

    def _build_empty_state(self) -> Adw.StatusPage:
        empty = Adw.StatusPage(
            title="No Environment Variables",
            description=(
                "Export variables to processes Hyprland spawns — "
                "toolkit hints (QT_QPA_PLATFORM), theme overrides, "
                "scaling settings, and so on."
            ),
            icon_name="utilities-terminal-symbolic",
        )

        button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
        )

        add_btn = Gtk.Button(label="Add Variable…")
        add_btn.add_css_class("suggested-action")
        add_btn.add_css_class("pill")
        add_btn.connect("clicked", lambda _b: self._on_add())
        button_box.append(add_btn)

        empty.set_child(button_box)
        return empty

    def _build_reorder_hint(self) -> Gtk.Widget:
        """Inline note teaching the two reorder gestures."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(4)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.5)
        icon.set_valign(Gtk.Align.START)
        box.append(icon)

        label = Gtk.Label(
            label=(
                "Reorder entries by dragging them, "
                "or with Alt+↑ / Alt+↓ on a focused row. "
                "Order matters when one variable references another (e.g. ‘PATH’)."
            ),
        )
        label.set_wrap(True)
        label.set_xalign(0)
        # Without ``hexpand=True`` the label settles at its preferred
        # narrow width so the copy wraps prematurely.
        label.set_hexpand(True)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
        return box

    def _build_group(self) -> Adw.PreferencesGroup:
        n = len(self._owned)
        group = Adw.PreferencesGroup(title="Variables")
        group.set_description(f"{n} variable{'' if n == 1 else 's'}")

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add another variable")
        add_btn.connect("clicked", lambda _b: self._on_add())
        group.set_header_suffix(add_btn)

        for idx in range(len(self._owned)):
            group.add(self._make_row(idx, self._owned[idx]))
        return group

    def _build_deleted_group(self, deleted: list[EnvVar]) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Removed (pending save)")
        n = len(deleted)
        group.set_description(f"{n} variable{'' if n == 1 else 's'} will be removed on save")
        for item in deleted:
            row = Adw.ActionRow(
                title=html_escape(item.name),
                subtitle=html_escape(item.value or "(empty)"),
            )
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            row.add_css_class("option-default")
            row.set_opacity(0.65)

            restore_btn = Gtk.Button(icon_name="edit-undo-symbolic")
            restore_btn.set_valign(Gtk.Align.CENTER)
            restore_btn.add_css_class("flat")
            restore_btn.set_tooltip_text("Restore this variable")
            restore_btn.connect("clicked", lambda _b, e=item: self._on_restore_deleted(e))
            row.add_suffix(restore_btn)

            group.add(row)
        return group

    def _make_row(self, idx: int, item: EnvVar) -> Adw.ActionRow:
        row = Adw.ActionRow(
            title=html_escape(item.name),
            # The value is the interesting part — show it as the subtitle
            # in monospace so users can scan long values (e.g. paths).
            subtitle=html_escape(item.value or "(empty)"),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)

        prefix = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        prefix.set_opacity(0.6)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        # Whole-row drag-and-drop reorder, mirroring the autostart page.
        # ``Gtk.DragSource`` only claims the press if motion crosses its
        # threshold, so a plain click still activates the row (edit dialog).
        # Keyboard parallel: Alt+Up / Alt+Down on the focused row.
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
            reset_tooltip="Remove this variable",
        )
        row.add_suffix(actions.box)
        actions.update(is_managed=True, is_dirty=is_dirty, is_saved=is_saved)

        row.set_activatable(True)
        row.connect("activated", lambda _r, i=idx: self._on_edit_at(i))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    def _deleted_baselines(self) -> list[EnvVar]:
        """Return saved entries that are no longer in the owned list."""
        current = {e.to_line() for e in self._owned}
        return [b for b in self._owned.saved if b.to_line() not in current]

    # ── External (read-only display + override flow) ──

    def _build_external_section(self) -> list[Gtk.Widget]:
        """Build the read-only external-vars display.

        Returns an inline hint + one PreferencesGroup per source file —
        same grouping pattern as the layer-rules page so users see one
        path-as-title per file instead of repeating it on every row.

        Entries already overridden by an owned line are visually muted
        and badged "Overridden" instead of carrying the override
        button — a second override would be redundant.
        """
        widgets: list[Gtk.Widget] = [self._build_external_hint()]

        # ``find_all`` returns env entries in source-traversal order, so
        # a plain (insertion-ordered) dict gives us the right grouping
        # for free.
        by_file: dict[Path, list[ExternalEnvVar]] = {}
        for ext in self._external:
            by_file.setdefault(ext.source_path, []).append(ext)

        overridden = overridden_external_names(self._external, list(self._owned))
        for source_path, entries in by_file.items():
            widgets.append(self._build_external_file_group(source_path, entries, overridden))
        return widgets

    def _build_external_hint(self) -> Gtk.Widget:
        """Inline note: explains override semantics + read-only nature."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(4)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.5)
        icon.set_valign(Gtk.Align.START)
        box.append(icon)

        label = Gtk.Label(
            label=(
                "Variables below come from your hyprland.conf or its "
                "sourced files. Click the edit button to override them — "
                "your managed entry will take precedence on the next "
                "Hyprland session."
            ),
        )
        label.set_wrap(True)
        label.set_xalign(0)
        # Without ``hexpand=True`` the label settles at its preferred
        # narrow width so the copy wraps prematurely.
        label.set_hexpand(True)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
        return box

    def _build_external_file_group(
        self,
        source_path: Path,
        entries: list[ExternalEnvVar],
        overridden: set[str],
    ) -> Adw.PreferencesGroup:
        """A PreferencesGroup containing every external var from one file."""
        group = Adw.PreferencesGroup(title=display_path(source_path))
        n = len(entries)
        group.set_description(f"{n} variable{'' if n == 1 else 's'}")
        for ext in entries:
            group.add(self._make_external_row(ext, is_overridden=ext.var.name in overridden))
        return group

    def _make_external_row(self, ext: ExternalEnvVar, *, is_overridden: bool) -> Adw.ActionRow:
        """One locked row representing an external env var."""
        # Subtitle = value + line number. Path is already in the group
        # title, so we don't repeat it on every row.
        subtitle = f"{ext.var.value or '(empty)'}  ·  line {ext.lineno}"

        row = Adw.ActionRow(
            title=html_escape(ext.var.name),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.add_css_class("option-default")
        row.set_opacity(0.65)
        row.set_tooltip_text(f"{ext.source_path}:{ext.lineno}")

        prefix = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        prefix.set_opacity(0.4)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        if is_overridden:
            # An owned entry with the same name is already in our managed
            # file — Hyprland will see ours last and use ours. Label the
            # external row so the user can see what they overrode.
            badge = Gtk.Label(label="Overridden")
            badge.add_css_class("pending-badge")
            badge.add_css_class("pending-badge-modified")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
            lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
            lock_icon.set_opacity(0.4)
            lock_icon.set_valign(Gtk.Align.CENTER)
            row.add_suffix(lock_icon)
            return row

        # Not yet overridden — offer the override action.
        override_btn = Gtk.Button(icon_name="document-edit-symbolic")
        override_btn.set_valign(Gtk.Align.CENTER)
        override_btn.add_css_class("flat")
        override_btn.set_tooltip_text("Override this variable")
        override_btn.connect("clicked", lambda _b, e=ext: self._on_override(e))
        row.add_suffix(override_btn)

        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock_icon.set_opacity(0.4)
        lock_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(lock_icon)
        return row

    def _on_override(self, ext: ExternalEnvVar) -> None:
        """Open the edit dialog pre-filled with *ext*'s name and value.

        The user can change either field before applying — for example,
        keep the same name but flip the value (the typical override
        case). On apply, a new managed entry is appended to ``_owned``
        and the page rebuilds with the external row newly badged
        "Overridden".

        Note that the dialog's normal "name in RESERVED_NAMES" guard
        still applies — but external rows for reserved names are
        already filtered out by :func:`load_external_env_vars`, so
        users can only land here with a non-reserved name.
        """
        owned = self._owned

        def on_apply(new_item: EnvVar) -> None:
            with self._undo_track():
                owned.append_new(new_item)
            self._notify_dirty()
            self._rebuild_list()

        EnvVarEditDialog.present_singleton(
            self._window,
            entry=ext.var,
            is_override=True,
            on_apply=on_apply,
        )

    # ── Reorder (drag-and-drop + Alt+arrow keyboard shortcut) ──

    def _attach_drag_source(self, row: Adw.ActionRow, idx: int) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, idx)
        source.connect("drag-begin", self._on_drag_begin, idx)
        source.connect("drag-end", self._on_drag_end)
        row.add_controller(source)

    def _attach_drop_target(self, row: Adw.ActionRow, idx: int) -> None:
        target = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        target.connect("motion", self._on_drop_motion, idx)
        target.connect("leave", self._on_drop_leave)
        target.connect("drop", self._on_drop, idx)
        row.add_controller(target)

    def _on_drag_prepare(
        self, _source: Gtk.DragSource, x: float, y: float, idx: int
    ) -> Gdk.ContentProvider | None:
        # Stash the press coords for ``drag-begin`` to use as the
        # icon's hot spot. Setting the icon in ``prepare`` doesn't
        # always stick — some compositors apply it only once the drag
        # is fully initialised, between ``prepare`` and ``drag-begin``.
        self._drag_press = (x, y)
        val = GObject.Value(GObject.TYPE_INT, idx)
        return Gdk.ContentProvider.new_for_value(val)

    def _on_drag_begin(self, source: Gtk.DragSource, drag: Gdk.Drag, idx: int) -> None:
        self._dragging_idx = idx
        press = self._drag_press or (0.0, 0.0)
        hot_x, hot_y = int(press[0]), int(press[1])
        # Same painter trick the autostart page uses — without the CSS
        # class the row paints transparently as a drag icon (the visible
        # "card" look comes from the parent ``PreferencesGroup``).
        widget = source.get_widget()
        if widget is not None:
            widget.add_css_class("autostart-drag-source")
            paintable = Gtk.WidgetPaintable.new(widget)
            source.set_icon(paintable, hot_x, hot_y)
        # Belt-and-suspenders: also set the hot spot on the live ``Gdk.Drag``.
        # Hyprland's drag implementation appears to drop the hot spot set
        # via ``GtkDragSource.set_icon`` in some cases.
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
        # If the drop completed and rebuilt the list before ``leave``
        # fired, dangling indicator classes would carry over.
        self._clear_drop_indicators()

    def _on_drop_motion(
        self,
        target: Gtk.DropTarget,
        _x: float,
        y: float,
        hover_idx: int,
    ) -> Gdk.DragAction:
        # ``motion`` doesn't have access to the dragged value — that's
        # only resolved at drop time — so we read ``_dragging_idx`` set
        # in ``drag-begin`` to validate the move synchronously.
        src = self._dragging_idx
        if src is None or src == hover_idx:
            return Gdk.DragAction(0)

        widget = target.get_widget()
        if widget is None:
            return Gdk.DragAction(0)

        before = self._is_above_half(widget, y)
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
        # rare wrapper case.
        src_idx = value if isinstance(value, int) else int(value)  # type: ignore[arg-type]
        widget = target.get_widget()
        if widget is None:
            return False
        before = self._is_above_half(widget, y)

        target_idx = drop_target_idx(src_idx, hover_idx, before)

        if target_idx == src_idx:
            return False
        n = len(self._owned)
        if not 0 <= target_idx < n:
            return False
        if not 0 <= src_idx < n:
            return False

        with self._undo_track():
            self._owned.move(src_idx, target_idx)
        self._notify_dirty()
        self._rebuild_list()
        return True

    @staticmethod
    def _is_above_half(widget: Gtk.Widget, y: float) -> bool:
        """True if *y* falls in the upper half of *widget*."""
        height = widget.get_height() or widget.get_allocated_height()
        if height <= 0:
            return True
        return y < height / 2

    def _clear_drop_indicators(self) -> None:
        """Remove insertion-line classes from every tracked row."""
        for row in self._rows_by_idx:
            if row is not None:
                row.remove_css_class("autostart-drop-above")
                row.remove_css_class("autostart-drop-below")

    def _attach_keyboard_reorder(self, row: Adw.ActionRow, idx: int) -> None:
        """Bind Alt+Up / Alt+Down on *row* to move it within the list."""
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
        # Require Alt only — Shift/Ctrl/Super combos are reserved for
        # future shortcuts (e.g. Alt+Shift+Up = move-to-top).
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
        """Move the entry at *idx* by *delta* slots."""
        target = idx + delta
        n = len(self._owned)
        if target < 0 or target >= n or idx == target:
            return False
        with self._undo_track():
            self._owned.move(idx, target)
        self._notify_dirty()
        self._rebuild_list(focus_idx=target)
        return True

    # ── Add / Edit / Remove ──

    def _on_add(self) -> None:
        owned = self._owned

        def on_apply(new_item: EnvVar) -> None:
            with self._undo_track():
                owned.append_new(new_item)
            self._notify_dirty()
            self._rebuild_list()

        EnvVarEditDialog.present_singleton(self._window, on_apply=on_apply)

    def _on_edit_at(self, idx: int) -> None:
        owned = self._owned
        if idx < 0 or idx >= len(owned):
            return
        current = owned[idx]

        def on_apply(new_item: EnvVar) -> None:
            if new_item == current:
                return
            with self._undo_track():
                owned[idx] = new_item
            self._notify_dirty()
            self._rebuild_list()

        EnvVarEditDialog.present_singleton(
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

    def _on_restore_deleted(self, item: EnvVar) -> None:
        """Restore a previously-deleted entry to its saved position.

        Routes through :meth:`SavedList.restore_deleted` so the row
        comes back with its saved baseline at the slot consistent with
        the saved order — a pure delete-then-restore round trip leaves
        the page non-dirty.
        """
        with self._undo_track():
            self._owned.restore_deleted(item)
        self._notify_dirty()
        self._rebuild_list()

    # ── Reorder helpers (queried by pages/pending.py) ──

    def is_reordered(self) -> bool:
        """True if the *common* items between saved and current differ in order."""
        return detect_reorder(self._owned.saved, list(self._owned))

    def pending_change_count(self) -> int:
        """Number of distinct pending-change entries the page would surface."""
        if not self.is_dirty():
            return 0
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        return count_pending_changes(self._owned.saved, list(self._owned), baselines)

    def revert_reorder(self) -> None:
        """Restore the saved order while preserving other dirty changes.

        Same algorithm as :meth:`AutostartPage.revert_reorder`:

        - Items that exist in both saved and current are repositioned
          to their saved-order slots (any in-flight edits to those
          items are kept — only the position is reverted).
        - Newly-added items (no baseline) keep their values and slot
          in at the end.
        - Items the user removed stay removed.

        Pushes a single undo entry so Ctrl+Z restores the pre-revert
        order in one step.
        """
        by_saved_line: dict[str, tuple[EnvVar, EnvVar | None]] = {}
        new_pairs: list[tuple[EnvVar, EnvVar | None]] = []

        for idx in range(len(self._owned)):
            item = self._owned[idx]
            baseline = self._owned.get_baseline(idx)
            if baseline is None:
                new_pairs.append((item, baseline))
            else:
                by_saved_line[baseline.to_line()] = (item, baseline)

        rebuilt_items: list[EnvVar] = []
        rebuilt_baselines: list[EnvVar | None] = []
        for saved in self._owned.saved:
            pair = by_saved_line.get(saved.to_line())
            if pair is None:
                continue
            item, baseline = pair
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)
        for item, baseline in new_pairs:
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)

        with self._undo_track():
            self._owned.restore(rebuilt_items, rebuilt_baselines)
        self._notify_dirty()
        self._rebuild_list()

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

    def get_env_lines(self) -> list[str]:
        """Serialize the current entries for ``config.write_all``.

        Order is preserved as-is — users may rely on, e.g., setting
        ``XDG_RUNTIME_DIR`` before referencing it from a later
        variable. Cursor-managed lines are emitted by the Cursor page
        and concatenated upstream; this method returns only the
        non-reserved entries.
        """
        return serialize(list(self._owned))

    @staticmethod
    def has_managed_section(sections: dict[str, list[str]]) -> bool:
        """True if the saved config has any non-reserved env entries.

        The Cursor page already triggers env emission for its four
        reserved names, so we only need to check whether any *other*
        env name lives in the file. If it does, this page must emit
        on save (even if currently clean) to preserve it.
        """
        for raw in sections.get(config.KEYWORD_ENV, []):
            entry = parse_env_lines([raw])
            if entry and entry[0].name not in RESERVED_NAMES:
                return True
        return False


__all__ = ["EnvVarsPage"]
