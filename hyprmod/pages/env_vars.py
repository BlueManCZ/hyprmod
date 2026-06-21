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

from gi.repository import Adw, Gtk

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
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import make_inline_hint, make_page_layout
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.env_var_edit_dialog import EnvVarEditDialog
from hyprmod.ui.icons import ENV_VARS_ICON
from hyprmod.ui.row_actions import RowActions

# ---------------------------------------------------------------------------
# EnvVarsPage
# ---------------------------------------------------------------------------


class EnvVarsPage(SavedListSectionPage[EnvVar]):
    """List editor for ``env = NAME,value`` config entries."""

    _unit_singular = "variable"
    _unit_plural = "variables"
    _page_attr = "_env_vars_page"
    _pending_category = "Env Variables"
    _pending_navigate_to = "env_vars"
    _pending_icon = ENV_VARS_ICON
    _group_title = "Variables"
    _group_add_tooltip = "Add another variable"

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
        # Snapshot of which external names are shadowed by an owned
        # entry; refreshed at the top of ``_rebuild_list`` so the
        # base-class external-row renderer can read a stable value
        # without recomputing per row.
        self._overridden_external: set[str] = set()
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            saved_sections = self._window.saved_sections
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
        self._external = load_external_env_vars(config.user_entry_path(), config.managed_path())

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

    def _pre_rebuild(self) -> None:
        # Snapshot once per rebuild so the external-row renderer can look
        # up "is this entry shadowed by an owned line?" without threading
        # the set through method signatures.
        self._overridden_external = overridden_external_names(self._external, list(self._owned))

    def _build_order_hint(self) -> Gtk.Widget:
        return make_inline_hint(
            "Reorder entries by dragging them, "
            "or with Alt+↑ / Alt+↓ on a focused row. "
            "Order matters when one variable references another (e.g. ‘PATH’)."
        )

    def _build_empty_state(self) -> EmptyState:
        return EmptyState(
            title="No Environment Variables",
            description=(
                "Export variables to processes Hyprland spawns — toolkit "
                "hints (QT_QPA_PLATFORM), theme overrides, scaling settings, "
                "and so on."
            ),
            icon_name=ENV_VARS_ICON,
            primary_action=("Add Variable…", self._on_add),
        )

    def _deleted_row_summary(self, item: EnvVar) -> tuple[str, str]:
        return item.name, item.value or "(empty)"

    # ── Pending-changes summarizers ──

    def _summarize_item(self, item: EnvVar) -> tuple[str, str]:
        return item.name, item.value or "(empty)"

    def _summarize_modified(self, baseline: EnvVar, item: EnvVar) -> tuple[str, str]:
        if baseline.name != item.name:
            # Renames (delete-old + add-new) shouldn't reach here — they
            # appear as one "added" and one "removed" — but if the page
            # ever supports in-place rename, surface both halves of the diff.
            return item.name, f"{baseline.name} → {item.name}"
        return item.name, f"{baseline.value or '(empty)'} → {item.value or '(empty)'}"

    def _make_row(self, idx: int, item: EnvVar) -> Adw.ActionRow:
        row = Adw.ActionRow(
            title=html_escape(item.name),
            # The value is the interesting part — show it as the subtitle
            # in monospace so users can scan long values (e.g. paths).
            subtitle=html_escape(item.value or "(empty)"),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)

        prefix = Gtk.Image.new_from_icon_name(ENV_VARS_ICON)
        prefix.set_opacity(0.6)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        # Whole-row drag-and-drop reorder, mirroring the autostart page.
        # ``Gtk.DragSource`` only claims the press if motion crosses its
        # threshold, so a plain click still activates the row (edit dialog).
        # Keyboard parallel: Alt+Up / Alt+Down on the focused row.
        self._reorder.attach(row, idx)
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
    #
    # External-section layout (hint + per-file groups) uses the base
    # ``SavedListSectionPage`` template; ``_make_external_row`` reads
    # the cached ``_overridden_external`` set so it can muted-badge
    # already-overridden entries instead of offering a redundant
    # override button.

    def _build_external_hint(self) -> Gtk.Widget:
        return make_inline_hint(
            "Variables below come from your hyprland.conf or its "
            "sourced files. Click the edit button to override them — "
            "your managed entry will take precedence on the next "
            "Hyprland session."
        )

    def _make_external_row(self, ext: ExternalEnvVar) -> Adw.ActionRow:
        """One locked row representing an external env var."""
        is_overridden = ext.var.name in self._overridden_external
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

        prefix = Gtk.Image.new_from_icon_name(ENV_VARS_ICON)
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
        EnvVarEditDialog.present_singleton(
            self._window,
            entry=ext.var,
            is_override=True,
            on_apply=self._commit_appended,
        )

    # ── Add / Edit / Remove ──

    def _on_add(self) -> None:
        EnvVarEditDialog.present_singleton(self._window, on_apply=self._commit_appended)

    def _on_edit_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._owned):
            return
        current = self._owned[idx]

        def on_apply(new_item: EnvVar) -> None:
            if new_item != current:
                self._commit_replaced(idx, new_item)

        EnvVarEditDialog.present_singleton(
            self._window,
            entry=current,
            on_apply=on_apply,
        )

    # ``_on_delete_at`` / ``_discard_at`` / ``_on_restore_deleted`` use
    # the base ``SavedListSectionPage`` defaults — env vars don't have
    # any live-apply side effects, so no override is needed.

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
