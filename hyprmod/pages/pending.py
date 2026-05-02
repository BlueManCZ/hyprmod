"""Pending Changes page — aggregated overview of unsaved edits.

Walks every change-tracking surface in the app (schema options, animations,
keybinds, monitors, cursor) and surfaces each modified item as a single
row with a discard button. Also renders a unified diff between the
on-disk config and the next-save serialization, so users can verify
what's actually about to land in hyprmod's managed config.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gi.repository import Adw, GLib, Gtk
from hyprland_config import value_to_conf
from hyprland_monitors.monitors import MonitorState, lines_from_monitors
from hyprland_state import ANIM_FLAT, ANIM_LOOKUP, AnimState

from hyprmod.core import config, schema
from hyprmod.core.autostart import KEYWORD_LABELS as AUTOSTART_LABELS
from hyprmod.core.change_tracking import iter_item_changes
from hyprmod.core.layer_rules import summarize_rule as summarize_layer_rule
from hyprmod.core.window_rules import summarize_rule
from hyprmod.pages.animations import ANIM_LABELS
from hyprmod.ui import clear_children, make_page_layout
from hyprmod.ui.diff import ConfigDiffWidget
from hyprmod.ui.icons import (
    AUTOSTART_ICON,
    BINDS_ICON,
    ENV_VARS_ICON,
    FALLBACK_ICON,
    LAYER_RULES_ICON,
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


@dataclass(slots=True)
class PendingChange:
    """A single unsaved item surfaced in the pending changes list."""

    category: str
    title: str
    subtitle: str
    revert: Callable[[], object]
    navigate_to: str | None = None
    icon: str = FALLBACK_ICON
    kind: str = "modified"  # "modified" | "added" | "removed"
    # Schema option key to focus and flash on navigation, when applicable.
    target_key: str | None = None


class PendingChangesPage:
    """Live overview of every unsaved item plus a config diff preview."""

    def __init__(self, window: "HyprModWindow"):
        self._window = window
        self._toolbar: Adw.ToolbarView | None = None
        self._content_box: Gtk.Box | None = None
        self._summary_label: Gtk.Label | None = None
        self._empty_state: Adw.StatusPage | None = None
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

        self._empty_state = Adw.StatusPage(
            title="No pending changes",
            description=(
                "Edits made on any page will appear here so you can review them before saving."
            ),
            icon_name="emblem-ok-symbolic",
        )
        self._empty_state.set_vexpand(True)
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
        empty_state: Adw.StatusPage,
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
        badge_label, badge_class = _KIND_BADGE.get(change.kind, _KIND_BADGE["modified"])
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
        self._window.navigate(change.navigate_to)

        # Highlight + focus the source option once the target page has had a
        # chance to render — same pattern the search-result navigation uses.
        target_key = change.target_key
        if not target_key:
            return
        opt_row = self._window._option_rows.get(target_key)
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
        path = config.gui_conf()
        old_text = self._read_saved_config_text()
        try:
            new_text = self._compose_resulting_config()
        except Exception:  # noqa: BLE001 — never block the UI on diff errors
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
        path = config.gui_conf()
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def _compose_resulting_config(self) -> str:
        """Build the next-save config content by replicating ``window._perform_save``.

        Mirrors the production save path (collect + serialize) but never writes.
        """
        win = self._window
        return config.build_content(
            win.app_state.get_all_live_values(),
            win._collect_save_sections(),
        )

    # ── Change collection ──

    def collect_changes(self) -> list[PendingChange]:
        """Walk every change-tracking surface and produce a flat list."""
        out: list[PendingChange] = []
        out.extend(self._collect_option_changes())
        out.extend(self._collect_animation_changes())
        out.extend(self._collect_bind_changes())
        out.extend(self._collect_monitor_changes())
        out.extend(self._collect_cursor_changes())
        out.extend(self._collect_autostart_changes())
        out.extend(self._collect_env_var_changes())
        out.extend(self._collect_window_rule_changes())
        out.extend(self._collect_layer_rule_changes())
        return out

    # -- Options --

    def _collect_option_changes(self) -> list[PendingChange]:
        result: list[PendingChange] = []
        win = self._window
        options_flat = win._options_flat
        for key, state in win.app_state.options.items():
            if not state.is_dirty:
                continue
            option = options_flat.get(key, {})
            label = option.get("label") or key
            kind, subtitle = self._describe_option_change(state)
            group_id = win._key_to_group.get(key)
            result.append(
                PendingChange(
                    category="Options",
                    title=label,
                    subtitle=f"{key} · {subtitle}",
                    kind=kind,
                    revert=lambda k=key: win._on_option_discard(k),
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
    def _describe_option_change(state: Any) -> tuple[str, str]:
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

    # -- Animations --

    def _collect_animation_changes(self) -> list[PendingChange]:
        page = self._window._animations_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        icon = self._group_icon("animations")
        for name, *_ in ANIM_FLAT:
            if name not in ANIM_LOOKUP:
                continue
            if not page.is_anim_dirty(name):
                continue
            current = page.anims.get_cached(name)
            baseline = page.anims.get_baseline(name)
            kind, subtitle = self._describe_animation_change(name, baseline, current, page)
            label = ANIM_LABELS.get(name, name)
            result.append(
                PendingChange(
                    category="Animations",
                    title=label,
                    subtitle=subtitle,
                    kind=kind,
                    revert=lambda n=name: page.revert_anim(n),
                    navigate_to="animations",
                    icon=icon,
                )
            )
        return result

    def _describe_animation_change(
        self,
        name: str,
        baseline: AnimState | None,
        current: AnimState | None,
        page: Any,
    ) -> tuple[str, str]:
        was_owned = page.is_saved(name)
        is_owned = page.is_owned(name)
        # Pure ownership flips
        if was_owned and not is_owned:
            return "removed", "remove override on save"
        if not was_owned and is_owned and (baseline is None or not baseline.overridden):
            return "added", "new override"

        # Field-level diff between baseline and current
        if baseline is None or current is None:
            return "modified", "updated"

        diffs: list[str] = []
        if baseline.enabled != current.enabled:
            diffs.append(
                f"{'on' if baseline.enabled else 'off'} → {'on' if current.enabled else 'off'}"
            )
        if abs(baseline.speed - current.speed) > 1e-6:
            diffs.append(f"speed {baseline.speed:g} → {current.speed:g}")
        if (baseline.curve or "") != (current.curve or ""):
            diffs.append(f"curve {baseline.curve or '—'} → {current.curve or '—'}")
        if (baseline.style or "") != (current.style or ""):
            diffs.append(f"style {baseline.style or '—'} → {current.style or '—'}")
        return "modified", " · ".join(diffs) if diffs else "updated"

    # -- Binds --

    def _collect_bind_changes(self) -> list[PendingChange]:
        page = self._window._binds_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        owned = page._owned_binds
        current_lines: set = set()

        for idx, bind in enumerate(owned):
            current_lines.add(bind.to_line())
            baseline = owned.get_baseline(idx)
            if baseline is None:
                # New bind — revert == delete
                title = bind.format_shortcut() or "(no shortcut)"
                action = bind.format_action()
                result.append(
                    PendingChange(
                        category="Keybinds",
                        title=title,
                        subtitle=f"new · {action}",
                        kind="added",
                        revert=lambda i=idx: page._discard_bind_at(i),
                        navigate_to="binds",
                        icon=BINDS_ICON,
                    )
                )
                continue
            if owned.is_item_dirty(idx):
                old_shortcut = baseline.format_shortcut() or "(none)"
                new_shortcut = bind.format_shortcut() or "(none)"
                if old_shortcut == new_shortcut:
                    subtitle = f"{baseline.format_action()} → {bind.format_action()}"
                else:
                    subtitle = f"{old_shortcut} → {new_shortcut}"
                result.append(
                    PendingChange(
                        category="Keybinds",
                        title=new_shortcut,
                        subtitle=subtitle,
                        kind="modified",
                        revert=lambda i=idx: page._discard_bind_at(i),
                        navigate_to="binds",
                        icon=BINDS_ICON,
                    )
                )

        # Deleted binds — appear in saved but not in current
        for saved_bind in owned.saved:
            if saved_bind.to_line() not in current_lines:
                shortcut = saved_bind.format_shortcut() or "(none)"
                action = saved_bind.format_action()
                # Re-adding requires the original index/insertion logic; the
                # safest revert is to defer to the page-wide discard, which
                # restores the saved snapshot in full. We use the SavedList
                # restore by toggling the page discard for this single item.
                result.append(
                    PendingChange(
                        category="Keybinds",
                        title=shortcut,
                        subtitle=f"deleted · {action}",
                        kind="removed",
                        revert=lambda b=saved_bind: self._restore_deleted_bind(page, b),
                        navigate_to="binds",
                        icon=BINDS_ICON,
                    )
                )
        return result

    @staticmethod
    def _restore_deleted_bind(page: Any, bind: Any) -> None:
        """Restore a previously-deleted bind to its saved position.

        Routes through :meth:`SavedList.restore_deleted` so the bind
        comes back with its saved baseline at the slot consistent with
        the saved order — a pure delete-then-restore round trip leaves
        the page non-dirty. The bind is also re-pushed to the running
        compositor.
        """
        with page._undo_track():
            page._apply_bind_live(bind)
            page._owned_binds.restore_deleted(bind)
        page._notify_dirty()
        page._rebuild_list()

    # -- Monitors --

    def _collect_monitor_changes(self) -> list[PendingChange]:
        page = self._window._monitors_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        ownership = page._ownership
        current_by_name = {m.name: m for m in page._monitors}
        saved_by_name = {m.name: m for m in page._saved_monitors}

        for name, mon in current_by_name.items():
            is_owned = ownership.is_owned(name)
            was_saved = ownership.is_saved(name)
            baseline = saved_by_name.get(name)

            if is_owned and not was_saved:
                kind = "added"
                subtitle = self._format_monitor_summary(mon)
            elif was_saved and not is_owned:
                kind = "removed"
                subtitle = "remove from managed config"
            elif (
                is_owned
                and baseline is not None
                and lines_from_monitors([mon]) != lines_from_monitors([baseline])
            ):
                kind = "modified"
                subtitle = self._format_monitor_diff(baseline, mon)
            else:
                continue

            result.append(
                PendingChange(
                    category="Monitors",
                    title=self._monitor_label(mon),
                    subtitle=subtitle,
                    kind=kind,
                    revert=lambda m=mon: page._discard_monitor(m),
                    navigate_to="monitors",
                    icon=MONITORS_ICON,
                )
            )
        return result

    @staticmethod
    def _monitor_label(mon: MonitorState) -> str:
        desc = f"{mon.make} {mon.model}".strip()
        return f"{mon.name} — {desc}" if desc else mon.name

    @staticmethod
    def _format_monitor_summary(mon: MonitorState) -> str:
        if mon.disabled:
            return "disabled"
        return (
            f"{mon.width}x{mon.height}@{mon.refresh_rate:.0f}Hz · "
            f"{mon.x},{mon.y} · scale {mon.scale:g}"
        )

    @classmethod
    def _format_monitor_diff(cls, baseline: MonitorState, current: MonitorState) -> str:
        diffs: list[str] = []
        if baseline.disabled != current.disabled:
            diffs.append("disabled" if current.disabled else "re-enabled")
        if (baseline.width, baseline.height) != (current.width, current.height):
            diffs.append(f"{baseline.width}x{baseline.height} → {current.width}x{current.height}")
        if abs(baseline.refresh_rate - current.refresh_rate) > 0.01:
            diffs.append(f"{baseline.refresh_rate:.0f}Hz → {current.refresh_rate:.0f}Hz")
        if (baseline.x, baseline.y) != (current.x, current.y):
            diffs.append(f"pos {baseline.x},{baseline.y} → {current.x},{current.y}")
        if abs(baseline.scale - current.scale) > 1e-6:
            diffs.append(f"scale {baseline.scale:g} → {current.scale:g}")
        if baseline.transform != current.transform:
            diffs.append(f"rotate {baseline.transform} → {current.transform}")
        if (baseline.mirror_of or "") != (current.mirror_of or ""):
            diffs.append(f"mirror {baseline.mirror_of or '—'} → {current.mirror_of or '—'}")
        return " · ".join(diffs) if diffs else "updated"

    # -- Cursor --

    def _collect_cursor_changes(self) -> list[PendingChange]:
        page = self._window._cursor_page
        if page is None or not page.is_dirty():
            return []
        baseline = page._baseline
        current = page._current
        diffs: list[str] = []
        if baseline.theme != current.theme:
            diffs.append(
                f"theme {self._cursor_theme_label(baseline.theme)} → "
                f"{self._cursor_theme_label(current.theme)}"
            )
        if baseline.size != current.size:
            diffs.append(f"size {baseline.size}px → {current.size}px")
        subtitle = " · ".join(diffs) if diffs else "updated"
        return [
            PendingChange(
                category="Cursor",
                title="Cursor theme",
                subtitle=subtitle,
                kind="modified",
                revert=page.discard,
                navigate_to="cursor",
                icon=self._group_icon("cursor"),
            )
        ]

    @staticmethod
    def _cursor_theme_label(theme: str) -> str:
        # Avoid leaking the internal sentinel into the UI.
        return "System default" if theme.startswith("__") else theme

    # -- Autostart --

    def _collect_autostart_changes(self) -> list[PendingChange]:
        page = self._window._autostart_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        owned = page._owned

        # Drive both per-item rows and the badge counter off the same
        # iterator (in ``core.autostart``) so the sidebar count and
        # the pending-list length can't drift apart.
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        for kind, idx, item, baseline in iter_item_changes(owned.saved, list(owned), baselines):
            result.append(self._make_autostart_change(page, kind, idx, item, baseline))

        # Reorder is a single roll-up entry — separate from the per-item
        # add/edit/remove rows above. Discarding it restores the saved
        # order for items present in both lists while preserving any
        # in-flight value edits and any new/removed items, so users
        # don't lose unrelated work.
        if page.is_reordered():
            common_count = len({e.to_line() for e in owned} & {b.to_line() for b in owned.saved})
            result.append(
                PendingChange(
                    category="Autostart",
                    title="Reordered",
                    subtitle=f"{common_count} entries in a different order",
                    kind="modified",
                    revert=page.revert_reorder,
                    navigate_to="autostart",
                    icon=AUTOSTART_ICON,
                )
            )
        return result

    @staticmethod
    def _make_autostart_change(
        page: Any,
        kind: str,
        idx: int,
        item: Any,
        baseline: Any,
    ) -> PendingChange:
        """Build a ``PendingChange`` for one tuple from ``iter_item_changes``."""
        keyword_label = AUTOSTART_LABELS.get(item.keyword, item.keyword)
        if kind == "added":
            return PendingChange(
                category="Autostart",
                title=item.command,
                subtitle=f"new · {keyword_label}",
                kind="added",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="autostart",
                icon=AUTOSTART_ICON,
            )
        if kind == "modified":
            if baseline.command != item.command:
                subtitle = f"{baseline.command} → {item.command}"
            else:
                old_label = AUTOSTART_LABELS.get(baseline.keyword, baseline.keyword)
                subtitle = f"{old_label} → {keyword_label}"
            return PendingChange(
                category="Autostart",
                title=item.command,
                subtitle=subtitle,
                kind="modified",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="autostart",
                icon=AUTOSTART_ICON,
            )
        # removed — ``item`` is the saved value (the entry that disappeared);
        # revert re-adds it as a new row, keeping other in-flight edits intact.
        return PendingChange(
            category="Autostart",
            title=item.command,
            subtitle=f"deleted · {keyword_label}",
            kind="removed",
            revert=lambda e=item: page._on_restore_deleted(e),
            navigate_to="autostart",
            icon=AUTOSTART_ICON,
        )

    # -- Env vars --

    def _collect_env_var_changes(self) -> list[PendingChange]:
        page = self._window._env_vars_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        owned = page._owned

        # Same iterator pattern as autostart so the sidebar badge count
        # and pending-list length stay in lockstep.
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        for kind, idx, item, baseline in iter_item_changes(owned.saved, list(owned), baselines):
            result.append(self._make_env_var_change(page, kind, idx, item, baseline))

        if page.is_reordered():
            common_count = len({e.to_line() for e in owned} & {b.to_line() for b in owned.saved})
            result.append(
                PendingChange(
                    category="Env Variables",
                    title="Reordered",
                    subtitle=f"{common_count} variables in a different order",
                    kind="modified",
                    revert=page.revert_reorder,
                    navigate_to="env_vars",
                    icon=ENV_VARS_ICON,
                )
            )
        return result

    @staticmethod
    def _make_env_var_change(
        page: Any,
        kind: str,
        idx: int,
        item: Any,
        baseline: Any,
    ) -> PendingChange:
        """Build a ``PendingChange`` for one tuple from ``iter_item_changes``."""
        if kind == "added":
            return PendingChange(
                category="Env Variables",
                title=item.name,
                subtitle=f"new · {item.value or '(empty)'}",
                kind="added",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="env_vars",
                icon=ENV_VARS_ICON,
            )
        if kind == "modified":
            if baseline.name != item.name:
                # Renames (delete-old + add-new) shouldn't reach here —
                # they appear as one ``added`` and one ``removed`` —
                # but if the page ever supports in-place rename, surface
                # both halves of the diff.
                subtitle = f"{baseline.name} → {item.name}"
            else:
                old_val = baseline.value or "(empty)"
                new_val = item.value or "(empty)"
                subtitle = f"{old_val} → {new_val}"
            return PendingChange(
                category="Env Variables",
                title=item.name,
                subtitle=subtitle,
                kind="modified",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="env_vars",
                icon=ENV_VARS_ICON,
            )
        # removed — ``item`` is the saved (vanished) entry; revert re-adds.
        return PendingChange(
            category="Env Variables",
            title=item.name,
            subtitle=f"deleted · {item.value or '(empty)'}",
            kind="removed",
            revert=lambda e=item: page._on_restore_deleted(e),
            navigate_to="env_vars",
            icon=ENV_VARS_ICON,
        )

    # -- Window rules --

    def _collect_window_rule_changes(self) -> list[PendingChange]:
        page = self._window._window_rules_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        owned = page._owned

        # Same iterator pattern as autostart so the sidebar badge count
        # and pending-list length stay in lockstep.
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        for kind, idx, item, baseline in iter_item_changes(owned.saved, list(owned), baselines):
            result.append(self._make_window_rule_change(page, kind, idx, item, baseline))

        if page.is_reordered():
            common_count = len({r.to_line() for r in owned} & {b.to_line() for b in owned.saved})
            result.append(
                PendingChange(
                    category="Window Rules",
                    title="Reordered",
                    subtitle=f"{common_count} rules in a different order",
                    kind="modified",
                    revert=page.revert_reorder,
                    navigate_to="window_rules",
                    icon=WINDOW_RULES_ICON,
                )
            )
        return result

    @staticmethod
    def _make_window_rule_change(
        page: Any,
        kind: str,
        idx: int,
        item: Any,
        baseline: Any,
    ) -> PendingChange:
        """Build a ``PendingChange`` for one tuple from ``iter_item_changes``."""
        title, subtitle = summarize_rule(item)
        if kind == "added":
            return PendingChange(
                category="Window Rules",
                title=title,
                subtitle=f"new · {subtitle}",
                kind="added",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="window_rules",
                icon=WINDOW_RULES_ICON,
            )
        if kind == "modified":
            old_title, old_subtitle = summarize_rule(baseline)
            if old_title != title:
                summary = f"{old_title} → {title}"
            else:
                summary = f"{old_subtitle} → {subtitle}"
            return PendingChange(
                category="Window Rules",
                title=title,
                subtitle=summary,
                kind="modified",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="window_rules",
                icon=WINDOW_RULES_ICON,
            )
        # removed — ``item`` is the saved (vanished) rule.
        return PendingChange(
            category="Window Rules",
            title=title,
            subtitle=f"deleted · {subtitle}",
            kind="removed",
            revert=lambda e=item: page._on_restore_deleted(e),
            navigate_to="window_rules",
            icon=WINDOW_RULES_ICON,
        )

    # -- Layer rules --

    def _collect_layer_rule_changes(self) -> list[PendingChange]:
        page = self._window._layer_rules_page
        if page is None or not page.is_dirty():
            return []
        result: list[PendingChange] = []
        owned = page._owned

        # Same iterator pattern as window rules / autostart so the
        # sidebar badge count and pending-list length stay in lockstep.
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        for kind, idx, item, baseline in iter_item_changes(owned.saved, list(owned), baselines):
            result.append(self._make_layer_rule_change(page, kind, idx, item, baseline))

        if page.is_reordered():
            common_count = len({r.to_line() for r in owned} & {b.to_line() for b in owned.saved})
            result.append(
                PendingChange(
                    category="Layer Rules",
                    title="Reordered",
                    subtitle=f"{common_count} rules in a different order",
                    kind="modified",
                    revert=page.revert_reorder,
                    navigate_to="layer_rules",
                    icon=LAYER_RULES_ICON,
                )
            )
        return result

    @staticmethod
    def _make_layer_rule_change(
        page: Any,
        kind: str,
        idx: int,
        item: Any,
        baseline: Any,
    ) -> PendingChange:
        """Build a ``PendingChange`` for one tuple from ``iter_item_changes``."""
        title, subtitle = summarize_layer_rule(item)
        if kind == "added":
            return PendingChange(
                category="Layer Rules",
                title=title,
                subtitle=f"new · {subtitle}",
                kind="added",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="layer_rules",
                icon=LAYER_RULES_ICON,
            )
        if kind == "modified":
            old_title, old_subtitle = summarize_layer_rule(baseline)
            if old_title != title:
                summary = f"{old_title} → {title}"
            else:
                summary = f"{old_subtitle} → {subtitle}"
            return PendingChange(
                category="Layer Rules",
                title=title,
                subtitle=summary,
                kind="modified",
                revert=lambda i=idx: page._discard_at(i),
                navigate_to="layer_rules",
                icon=LAYER_RULES_ICON,
            )
        # removed — ``item`` is the saved (vanished) rule.
        return PendingChange(
            category="Layer Rules",
            title=title,
            subtitle=f"deleted · {subtitle}",
            kind="removed",
            revert=lambda e=item: page._on_restore_deleted(e),
            navigate_to="layer_rules",
            icon=LAYER_RULES_ICON,
        )

    # Bezier curves are written automatically based on used animations,
    # so they're surfaced through the diff preview rather than as standalone
    # entries — adding/removing a curve shows up in the diff text.
