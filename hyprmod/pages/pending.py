"""Pending Changes page — aggregated overview of unsaved edits.

Walks every section page's ``iter_pending_changes()`` plus the
window-level option diff, and renders a unified diff between the
on-disk config and the next-save serialization so users can verify
what's about to land in hyprmod's managed config.
"""

import logging
from html import escape as html_escape
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk
from hyprland_config import ParseError, value_to_conf

from hyprmod.core import config, schema
from hyprmod.core.pending import ChangeKind, PendingChange
from hyprmod.core.state import OptionState
from hyprmod.ui import clear_children, make_page_layout
from hyprmod.ui.diff import ConfigDiffWidget
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import (
    AUTOSTART_ICON,
    BINDS_ICON,
    ENV_VARS_ICON,
    FALLBACK_ICON,
    LAYER_RULES_ICON,
    LAYOUTS_ICON,
    MONITORS_ICON,
    WINDOW_RULES_ICON,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hyprmod.window import HyprModWindow


# Categories shown in the page, in the order they appear.
_CATEGORY_ORDER = (
    "Options",
    "Animations",
    "Keybinds",
    "Monitors",
    "Cursor",
    "Autostart",
    "Env Variables",
    "Window Rules",
    "Layer Rules",
)

# Visual label and CSS class for each kind of change.
_KIND_BADGE = {
    "modified": ("Modified", "pending-badge-modified"),
    "added": ("Added", "pending-badge-added"),
    "removed": ("Removed", "pending-badge-removed"),
}


class PendingChangesPage:
    """Live overview of every unsaved item plus a config diff preview."""

    def __init__(self, window: "HyprModWindow"):
        self._window = window
        self._toolbar: Adw.ToolbarView | None = None
        self._content_box: Gtk.Box | None = None
        self._summary_label: Gtk.Label | None = None
        self._empty_state: EmptyState | None = None
        self._groups_box: Gtk.Box | None = None
        self._diff: ConfigDiffWidget | None = None
        self._diff_group: Adw.PreferencesGroup | None = None
        # Coalesce many quick "dirty" pings (e.g. typing in a spinbutton) into
        # a single rebuild. Without this the page rebuilds at signal speed
        # which is wasteful and can fight scroll position recovery.
        self._refresh_pending = False
        # Map sidebar group_id -> icon name, sourced from the same schema the
        # sidebar uses, so each row's icon mirrors its source page exactly.
        # Also seed the hardcoded sidebar pages (binds/monitors) so that
        # schema groups routed onto those pages (e.g. ``monitor_globals``
        # which has ``parent_page: "monitors"``) still resolve correctly.
        self._group_icons: dict[str, str] = {
            "binds": BINDS_ICON,
            "monitors": MONITORS_ICON,
            "autostart": AUTOSTART_ICON,
            "env_vars": ENV_VARS_ICON,
            "window_rules": WINDOW_RULES_ICON,
            "layer_rules": LAYER_RULES_ICON,
            "layouts": LAYOUTS_ICON,
        }
        for g in schema.get_groups(window._schema):
            icon = g.get("icon")
            if icon:
                self._group_icons[g["id"]] = icon

    # ── Build ──

    def build(self, header: Adw.HeaderBar) -> Adw.ToolbarView:
        toolbar, _, content_box, _ = make_page_layout(header=header)
        self._toolbar = toolbar
        self._content_box = content_box

        # Summary banner-style label at the top
        self._summary_label = Gtk.Label(xalign=0)
        self._summary_label.add_css_class("title-2")
        content_box.append(self._summary_label)

        self._empty_state = EmptyState(
            title="No Pending Changes",
            description=(
                "Edits made on any page will appear here for you to review before saving."
            ),
            icon_name="emblem-ok-symbolic",
        )
        content_box.append(self._empty_state)

        self._groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content_box.append(self._groups_box)

        # Diff section — hidden when there are no changes (saved file already
        # matches what the next save would write).
        diff_group = Adw.PreferencesGroup()
        diff_group.set_title("Config diff preview")
        diff_group.set_description(
            "Comparison between the saved config and what the next save would write."
        )

        diff_card = Gtk.Frame()
        diff_card.add_css_class("config-diff-frame")
        self._diff = ConfigDiffWidget()
        self._diff.set_size_request(-1, 280)
        diff_card.set_child(self._diff)
        diff_group.add(diff_card)
        diff_group.set_visible(False)

        self._diff_group = diff_group
        content_box.append(diff_group)

        self.refresh()
        return toolbar

    # ── Public refresh entry points ──

    def schedule_refresh(self) -> None:
        """Coalesce repeated change pings into a single idle-time rebuild."""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        GLib.idle_add(self._refresh_idle)

    def _refresh_idle(self) -> bool:
        self._refresh_pending = False
        self.refresh()
        return GLib.SOURCE_REMOVE

    def refresh(self) -> None:
        """Rebuild the change list and the diff preview from current state."""
        # All widgets are populated together by build(); guarding on any one
        # of them is enough to skip pre-build refreshes safely.
        groups_box = self._groups_box
        summary_label = self._summary_label
        empty_state = self._empty_state
        if groups_box is None or summary_label is None or empty_state is None:
            return

        changes = self.collect_changes()
        self._render_changes(changes, groups_box, summary_label, empty_state)
        self._render_diff()

    # ── Render: change list ──

    def _render_changes(
        self,
        changes: list[PendingChange],
        groups_box: Gtk.Box,
        summary_label: Gtk.Label,
        empty_state: EmptyState,
    ) -> None:
        clear_children(groups_box)

        # Group by category, preserving discovery order within each.
        by_cat: dict[str, list[PendingChange]] = {}
        for ch in changes:
            by_cat.setdefault(ch.category, []).append(ch)

        total = len(changes)
        if total == 0:
            summary_label.set_visible(False)
            empty_state.set_visible(True)
            groups_box.set_visible(False)
            return

        summary_label.set_visible(True)
        summary_label.set_label(f"{total} unsaved change{'s' if total != 1 else ''}")
        empty_state.set_visible(False)
        groups_box.set_visible(True)

        for cat in _CATEGORY_ORDER:
            cat_changes = by_cat.get(cat)
            if not cat_changes:
                continue
            group = Adw.PreferencesGroup(title=cat)
            group.set_description(
                f"{len(cat_changes)} change" + ("s" if len(cat_changes) != 1 else "")
            )
            for change in cat_changes:
                row = self._make_row(change)
                group.add(row)
            groups_box.append(group)

    def _make_row(self, change: PendingChange) -> Adw.ActionRow:
        row = Adw.ActionRow(
            title=html_escape(change.title),
            subtitle=html_escape(change.subtitle),
        )
        icon = Gtk.Image.new_from_icon_name(change.icon)
        icon.add_css_class("dim-label")
        row.add_prefix(icon)

        # Kind badge ("Modified" / "Added" / "Removed")
        badge_label, badge_class = _KIND_BADGE[change.kind]
        badge = Gtk.Label(label=badge_label)
        badge.add_css_class("pending-badge")
        badge.add_css_class(badge_class)
        badge.set_valign(Gtk.Align.CENTER)
        row.add_suffix(badge)

        # Discard button — primary action for the row
        discard_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        discard_btn.set_tooltip_text("Revert this change")
        discard_btn.set_valign(Gtk.Align.CENTER)
        discard_btn.add_css_class("flat")

        def _on_discard(_btn: Gtk.Button, ch: PendingChange = change) -> None:
            try:
                ch.revert()
            finally:
                self.schedule_refresh()

        discard_btn.connect("clicked", _on_discard)
        row.add_suffix(discard_btn)

        # Optional navigation arrow when we know the source page
        if change.navigate_to:
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow.add_css_class("dim-label")
            row.add_suffix(arrow)
            row.set_activatable(True)
            row.connect("activated", self._on_row_activated, change)
        return row

    def _on_row_activated(self, _row: Adw.ActionRow, change: PendingChange) -> None:
        if not change.navigate_to:
            return
        # Pass the target key so navigate() can flip ViewSwitcher sub-tabs
        # (e.g. Layouts → Dwindle) before we try to focus the option row.
        self._window.navigate(change.navigate_to, option_key=change.target_key)

        # Highlight + focus the source option once the target page has had a
        # chance to render — same pattern the search-result navigation uses.
        target_key = change.target_key
        if not target_key:
            return
        opt_row = self._window.option_rows.get(target_key)
        if opt_row is None:
            return

        def _scroll_and_highlight() -> bool:
            opt_row.row.grab_focus()
            opt_row.flash_highlight()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_scroll_and_highlight)

    # ── Render: diff ──

    def _render_diff(self) -> None:
        if self._diff is None or self._diff_group is None:
            return
        path = config.managed_path()
        old_text = self._read_saved_config_text()
        try:
            new_text = self._compose_resulting_config()
        except ParseError:
            # Log so dev builds surface bugs in build_content / collect; the
            # diff falls through to "no changes" which is harmless visually.
            log.exception("Failed to compose pending-changes diff preview")
            new_text = old_text
        # Hide the whole "Config diff preview" group when there's nothing to
        # show — the standalone empty placeholder inside the diff widget is
        # redundant when the parent group is collapsed.
        if old_text == new_text:
            self._diff_group.set_visible(False)
            return
        self._diff_group.set_visible(True)
        self._diff.set_texts(
            old_text,
            new_text,
            old_label=str(path),
            new_label=f"{path} (next save)",
            title=f"{path.name}",
        )

    def _read_saved_config_text(self) -> str:
        path = config.managed_path()
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def _compose_resulting_config(self) -> str:
        """Build the next-save config content, in whatever format will hit disk.

        Mirrors the production save path (collect + serialize) but never writes.
        """
        win = self._window
        return config.to_managed_text(
            win.app_state.get_all_live_values(),
            win.collect_save_sections(),
            hyprland_version=win.hypr.version,
        )

    # ── Change collection ──

    def collect_changes(self) -> list[PendingChange]:
        """Walk every change-tracking surface and produce a flat list.

        Options are window-level (schema-driven) so they're collected
        here directly; everything else delegates to the section page's
        ``iter_pending_changes()``.
        """
        out: list[PendingChange] = list(self._collect_option_changes())
        for page in self._window.section_pages:
            out.extend(page.iter_pending_changes())
        return out

    # -- Options --

    def _collect_option_changes(self) -> list[PendingChange]:
        result: list[PendingChange] = []
        win = self._window
        options_flat = win.options_flat
        for key, state in win.app_state.options.items():
            if not state.is_dirty:
                continue
            option = options_flat.get(key, {})
            label = option.get("label") or key
            kind, subtitle = self._describe_option_change(state)
            group_id = win.group_for_option(key)
            result.append(
                PendingChange(
                    category="Options",
                    title=label,
                    subtitle=f"{key} · {subtitle}",
                    kind=kind,
                    revert=lambda k=key: win.discard_option(k),
                    navigate_to=group_id,
                    icon=self._group_icon(group_id),
                    target_key=key,
                )
            )
        return result

    def _group_icon(self, group_id: str | None) -> str:
        """Resolve the sidebar icon for a schema group id."""
        if group_id is None:
            return FALLBACK_ICON
        return self._group_icons.get(group_id, FALLBACK_ICON)

    @staticmethod
    def _describe_option_change(state: OptionState) -> tuple[ChangeKind, str]:
        old, new = state.saved_value, state.live_value
        old_str = "" if old is None else value_to_conf(old)
        new_str = "" if new is None else value_to_conf(new)

        # Override added (was unmanaged, now a value is set)
        if not state.saved_managed and state.managed:
            return "added", f"set to {new_str or '—'}"
        # Override removed (was managed, no longer)
        if state.saved_managed and not state.managed:
            return "removed", "removing override"
        # Same managed flag, value changed
        if old_str != new_str:
            return "modified", f"{old_str or '—'} → {new_str or '—'}"
        return "modified", "value updated"
