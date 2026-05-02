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

from gi.repository import Adw, GLib, Gtk

from hyprmod.core import config
from hyprmod.core.env_vars import (
    RESERVED_NAMES,
    EnvVar,
    ExternalEnvVar,
    load_external_env_vars,
    overridden_external_names,
    parse_env_lines,
    serialize,
)
from hyprmod.core.ownership import SavedList
from hyprmod.core.setup import HYPRLAND_CONF
from hyprmod.core.undo import SavedListSnapshot
from hyprmod.pages.section import DragDropReorderMixin
from hyprmod.ui import clear_children, display_path, make_inline_hint, make_page_layout
from hyprmod.ui.env_var_edit_dialog import EnvVarEditDialog
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# EnvVarsPage
# ---------------------------------------------------------------------------


class EnvVarsPage(DragDropReorderMixin[EnvVar]):
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
        self._init_drag_state()
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
        return SavedListSnapshot(
            page_attr="_env_vars_page",
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
            self._content_box.append(
                make_inline_hint(
                    "Reorder entries by dragging them, "
                    "or with Alt+↑ / Alt+↓ on a focused row. "
                    "Order matters when one variable references another (e.g. ‘PATH’)."
                )
            )

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
                # before grab_focus runs (see ``_grab_focus_once`` in
                # the base class for the SOURCE_REMOVE rationale).
                GLib.idle_add(self._grab_focus_once, target)

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
        return make_inline_hint(
            "Variables below come from your hyprland.conf or its "
            "sourced files. Click the edit button to override them — "
            "your managed entry will take precedence on the next "
            "Hyprland session."
        )

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
