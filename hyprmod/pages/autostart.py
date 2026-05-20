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

from gi.repository import Adw, Gtk

from hyprmod.core import config
from hyprmod.core.autostart import (
    EXEC_KEYWORDS,
    KEYWORD_LABELS,
    ExecData,
    ExternalExec,
    load_external_exec_entries,
    parse_exec_lines,
    serialize,
)
from hyprmod.core.desktop_apps import DesktopApp, list_apps, match_command
from hyprmod.core.ownership import SavedList
from hyprmod.pages.section import DragDropReorderMixin
from hyprmod.ui import make_inline_hint, make_page_layout
from hyprmod.ui.app_picker import AppPickerDialog
from hyprmod.ui.autostart_edit_dialog import AutostartEditDialog
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import AUTOSTART_ICON
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# AutostartPage
# ---------------------------------------------------------------------------


class AutostartPage(DragDropReorderMixin[ExecData]):
    """List editor for ``exec`` / ``exec-once`` config entries."""

    _page_attr = "_autostart_page"
    _pending_category = "Autostart"
    _pending_navigate_to = "autostart"
    _pending_icon = AUTOSTART_ICON

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
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            saved_sections = self._window.saved_sections
        raw_lines = config.collect_section(saved_sections, *EXEC_KEYWORDS)
        items = parse_exec_lines(raw_lines)
        self._owned = SavedList(items, key=lambda e: e.to_line())
        # External entries — exec/exec-once defined in the user's
        # hyprland.conf or any file it sources, excluding our managed
        # file. Surfaced read-only so users see what already runs at
        # Hyprland startup/reload without us pretending we manage it.
        self._external = load_external_exec_entries(config.user_entry_path(), config.managed_path())

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

    def _build_order_hint(self) -> Gtk.Widget:
        return make_inline_hint(
            "Reorder entries by dragging them within their group, "
            "or with Alt+↑ / Alt+↓ on a focused row."
        )

    def _build_managed_groups(self) -> list[Gtk.Widget]:
        """Group entries by keyword so users can scan startup vs. reload separately."""
        by_keyword: dict[str, list[int]] = {kw: [] for kw in EXEC_KEYWORDS}
        for idx, item in enumerate(self._owned):
            by_keyword.setdefault(item.keyword, []).append(idx)

        widgets: list[Gtk.Widget] = []
        for kw in EXEC_KEYWORDS:
            indices = by_keyword.get(kw, [])
            if not indices:
                continue
            widgets.append(self._build_keyword_group(kw, indices))
        return widgets

    def _build_keyword_group(self, keyword: str, indices: list[int]) -> Adw.PreferencesGroup:
        """One group per ``exec`` / ``exec-once`` keyword.

        Mirrors :meth:`SavedListSectionPage._build_managed_group` but each
        group's "+" button defaults to the matching keyword's advanced
        toggle (``exec`` defaults to advanced, ``exec-once`` does not).
        """
        label = KEYWORD_LABELS.get(keyword, keyword)
        group = Adw.PreferencesGroup(title=label)
        n = len(indices)
        group.set_description(f"{n} {self._unit_label(n)}")

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text(f"Add another entry that runs {label.lower()}")
        add_btn.connect(
            "clicked",
            lambda _b, kw=keyword: self._on_add(default_advanced=kw == config.KEYWORD_EXEC),
        )
        group.set_header_suffix(add_btn)

        for idx in indices:
            group.add(self._make_row(idx, self._owned[idx]))
        return group

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
            icon_name=AUTOSTART_ICON,
            primary_action=("Pick from Installed Apps", self._on_quick_pick),
            secondary_action=("Custom Command…", self._on_add),
        )

    def _deleted_row_summary(self, item: ExecData) -> tuple[str, str]:
        matched = match_command(item.command, self._installed_apps)
        keyword_label = KEYWORD_LABELS.get(item.keyword, item.keyword)
        if matched is not None:
            return matched.name, f"{keyword_label} · {item.command}"
        return item.command, keyword_label

    # ── Pending-changes summarizers ──

    def _summarize_item(self, item: ExecData) -> tuple[str, str]:
        return item.command, KEYWORD_LABELS.get(item.keyword, item.keyword)

    def _summarize_modified(self, baseline: ExecData, item: ExecData) -> tuple[str, str]:
        new_label = KEYWORD_LABELS.get(item.keyword, item.keyword)
        if baseline.command != item.command:
            subtitle = f"{baseline.command} → {item.command}"
        else:
            old_label = KEYWORD_LABELS.get(baseline.keyword, baseline.keyword)
            subtitle = f"{old_label} → {new_label}"
        return item.command, subtitle

    def _make_deleted_row(self, item: ExecData) -> Adw.ActionRow:
        row = super()._make_deleted_row(item)
        matched = match_command(item.command, self._installed_apps)
        if matched is not None and matched.icon_name:
            prefix = Gtk.Image.new_from_icon_name(matched.icon_name)
            prefix.set_pixel_size(32)
            row.add_prefix(prefix)
        return row

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
            prefix = Gtk.Image.new_from_icon_name(AUTOSTART_ICON)
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

    # ── External (read-only display) ──
    #
    # Unlike env vars, there's no override path: Hyprland runs every
    # matching ``exec`` / ``exec-once`` line, so an entry in the user's
    # ``hyprland.conf`` can't be suppressed by adding one to our managed
    # file — the user has to edit the source file directly. The rows
    # below are advisory: they let users see what already runs at
    # startup/reload without surfacing it as something hyprmod manages.

    def _build_external_hint(self) -> Gtk.Widget:
        return make_inline_hint(
            "Entries below come from your hyprland.conf or its sourced "
            "files and already run at Hyprland startup or reload. Edit "
            "those files directly to change them.",
            icon_name="changes-prevent-symbolic",
        )

    def _make_external_row(self, ext: ExternalExec) -> Adw.ActionRow:
        # Mirror the managed-row matching so external entries get the
        # same friendly name + app icon when their command resolves to
        # an installed app. The keyword (once-vs-reload) is the most
        # useful disambiguator in the subtitle since the source path is
        # already in the group header.
        matched = match_command(ext.entry.command, self._installed_apps)
        keyword_label = KEYWORD_LABELS.get(ext.entry.keyword, ext.entry.keyword)
        if matched is not None:
            title = matched.name
            subtitle = f"{keyword_label}  ·  {ext.entry.command}  ·  line {ext.lineno}"
        else:
            title = ext.entry.command
            subtitle = f"{keyword_label}  ·  line {ext.lineno}"

        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.add_css_class("option-default")
        row.set_opacity(0.65)
        row.set_tooltip_text(f"{ext.source_path}:{ext.lineno}")

        if matched is not None and matched.icon_name:
            prefix = Gtk.Image.new_from_icon_name(matched.icon_name)
            prefix.set_pixel_size(32)
            prefix.set_opacity(0.6)
        else:
            prefix = Gtk.Image.new_from_icon_name(AUTOSTART_ICON)
            prefix.set_opacity(0.4)
        row.add_prefix(prefix)

        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock_icon.set_opacity(0.4)
        lock_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(lock_icon)
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

    # ``_on_delete_at`` / ``_discard_at`` / ``_on_restore_deleted`` use
    # the base ``SavedListSectionPage`` defaults — autostart has no
    # live-apply side effects (``exec``/``exec-once`` only fire at
    # compositor reload).

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
