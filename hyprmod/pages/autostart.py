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

from gi.repository import Adw, GLib, Gtk

from hyprmod.core import config
from hyprmod.core.autostart import (
    EXEC_KEYWORDS,
    KEYWORD_LABELS,
    ExecData,
    parse_exec_lines,
    serialize,
)
from hyprmod.core.desktop_apps import DesktopApp, list_apps, match_command
from hyprmod.core.ownership import SavedList
from hyprmod.core.undo import SavedListSnapshot
from hyprmod.pages.section import DragDropReorderMixin
from hyprmod.ui import clear_children, make_inline_hint, make_page_layout
from hyprmod.ui.app_picker import AppPickerDialog
from hyprmod.ui.autostart_edit_dialog import AutostartEditDialog
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# AutostartPage
# ---------------------------------------------------------------------------


class AutostartPage(DragDropReorderMixin[ExecData]):
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
        self._init_drag_state()
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
        return SavedListSnapshot(
            page_attr="_autostart_page",
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
            self._content_box.append(
                make_inline_hint(
                    "Reorder entries by dragging them within their group, "
                    "or with Alt+↑ / Alt+↓ on a focused row."
                )
            )

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
                # before grab_focus runs (see ``_grab_focus_once`` in
                # the base class for the SOURCE_REMOVE rationale).
                GLib.idle_add(self._grab_focus_once, target)

    def _build_empty_state(self) -> EmptyState:
        """Empty-state page with two action buttons.

        Surfaces both paths upfront so users don't have to discover the
        picker hidden inside the edit dialog: "Pick from Installed Apps"
        opens the app picker directly, "Custom Command…" opens the edit
        dialog.
        """
        return EmptyState(
            title="No Autostart Entries",
            description="Add programs that should launch automatically when Hyprland starts.",
            icon_name="media-playback-start-symbolic",
            primary_action=("Pick from Installed Apps", self._on_quick_pick),
            secondary_action=("Custom Command…", self._on_add),
        )

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

    # ── Reorder (mixin provides drag-and-drop + Alt+arrow keyboard) ──

    def _is_valid_move(self, src_idx: int, dst_idx: int) -> bool:
        """Restrict reorder to within a single keyword group.

        Turning an ``exec-once`` into an ``exec`` (or vice versa) by
        reordering would silently change the entry's behaviour. Users
        who need to flip the trigger edit the entry instead.
        """
        if not super()._is_valid_move(src_idx, dst_idx):
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
