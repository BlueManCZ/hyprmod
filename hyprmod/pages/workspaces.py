"""Workspaces page — manage ``workspace`` rules.

Hyprland's ``workspace`` keyword binds a workspace selector (numeric ID,
named, range, per-monitor, or special) to a monitor and overrides
per-rule appearance, lifecycle, and layout properties. This page is a
list editor for those rules, following the same data-flow shape as
:mod:`hyprmod.pages.layer_rules`:

- One ``SavedList[WorkspaceRule]`` is the source of truth.
- The user adds/edits/removes via :class:`WorkspaceRuleEditDialog`. The
  dialog exposes every property Hyprland's ``hl.workspace_rule`` accepts
  — monitor binding, default flag, persistence, gap overrides, border
  size, decoration toggles, default name, and ``on-created-empty``
  hook.
- On Apply, the new rule is pushed live to the compositor via
  ``hypr.keyword("workspace", body)``. The library auto-routes the call
  through ``hl.workspace_rule({...})`` in Lua-mode Hyprland 0.55+.
  Hyprland only consults rule properties at workspace creation, so a
  follow-up pass dispatches ``moveworkspacetomonitor`` / ``renameworkspace``
  on any already-open workspace the rule matches — that's what makes
  monitor binding and ``defaultName`` visible right away instead of
  "next time this workspace opens."
- Rules from outside our managed file (the user's ``hyprland.conf`` or
  any file it sources, plus Lua ``dofile`` chains) are surfaced
  read-only at the bottom with the source path + line number.

Hyprland has no ``unworkspace`` IPC — same caveat as window/layer rules.
Deleting or reordering a rule doesn't take effect on the running
compositor until save + reload (which rewrites the config and triggers
a Hyprland reload). Adding new rules works live; removal needs the
reload. The discard/restore paths re-push the affected rule for the
same reason: any pending dirty state visible in the running compositor
needs an explicit "re-apply baseline" to revert.
"""

from html import escape as html_escape

from gi.repository import Adw, Gtk
from hyprland_socket import HyprlandError, get_workspaces

from hyprmod.core import config
from hyprmod.core.ownership import SavedList
from hyprmod.core.workspaces import (
    WORKSPACE_RULE_KEYWORDS,
    ExternalWorkspaceRule,
    WorkspaceRule,
    load_external_workspace_rules,
    matches_workspace,
    parse_workspace_rule_lines,
    serialize,
    summarize_rule,
)
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import (
    make_inline_hint,
    make_page_layout,
    try_with_toast,
)
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import WORKSPACES_ICON
from hyprmod.ui.row_actions import RowActions
from hyprmod.ui.workspace_rule_dialog import WorkspaceRuleEditDialog


class WorkspacesPage(SavedListSectionPage[WorkspaceRule]):
    """List editor for ``workspace`` rule entries."""

    _unit_singular = "rule"
    _unit_plural = "rules"
    _deleted_subtitle_lines = 2
    _page_attr = "_workspaces_page"
    _pending_category = "Workspaces"
    _pending_navigate_to = "workspaces"
    _pending_icon = WORKSPACES_ICON
    _group_title = "Workspace Rules"
    _group_add_tooltip = "Add workspace rule"
    _external_prefix_icon = WORKSPACES_ICON

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
        self._owned: SavedList[WorkspaceRule]
        self._external: list[ExternalWorkspaceRule] = []
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            saved_sections = self._window.saved_sections
        raw_lines = config.collect_section(saved_sections, *WORKSPACE_RULE_KEYWORDS)
        items = parse_workspace_rule_lines(raw_lines)
        self._owned = SavedList(items, key=lambda r: r.to_line())
        self._external = load_external_workspace_rules(
            config.user_entry_path(), config.managed_path()
        )

    # ── Build ──

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add workspace rule")
        add_btn.connect("clicked", lambda _b: self._on_add())
        page_header.pack_start(add_btn)

        toolbar_view, _, self._content_box, self._scrolled = make_page_layout(header=page_header)

        self._rebuild_list()
        return toolbar_view

    # ── List rendering ──

    def _build_empty_state(self) -> EmptyState:
        """Empty-state page with an "Add Rule" button.

        The description is concrete because workspace rules are less
        self-evident than monitor or animation settings — a first-time
        user benefits from seeing the canonical use case (pin workspace
        to monitor) before they open the dialog.
        """
        return EmptyState(
            title="No Workspace Rules",
            description=(
                "Pin workspaces to specific monitors, configure persistence, "
                "override gaps and borders, or run a command when a workspace "
                "first opens."
            ),
            icon_name=WORKSPACES_ICON,
            primary_action=("Add Rule…", self._on_add),
        )

    def _build_order_hint(self) -> Gtk.Widget:
        """Inline note: Hyprland resolves duplicate rules last-wins.

        Workspace rules are looked up per workspace key; if two rules
        target the same workspace, the last one in the file takes
        precedence. The hint surfaces this so users aren't surprised by
        rules they thought were active being overridden later.
        """
        return make_inline_hint(
            "When multiple rules target the same workspace, Hyprland uses the "
            "last one in the file. Reorder with Alt+↑ / Alt+↓ on a focused row."
        )

    # ── Pending-changes summarisers ──

    def _summarize_item(self, item: WorkspaceRule) -> tuple[str, str]:
        return summarize_rule(item)

    def _summarize_modified(self, baseline: WorkspaceRule, item: WorkspaceRule) -> tuple[str, str]:
        old_title, old_subtitle = summarize_rule(baseline)
        new_title, new_subtitle = summarize_rule(item)
        if old_title != new_title:
            return new_title, f"{old_title} → {new_title}"
        if old_subtitle != new_subtitle:
            return new_title, f"{old_subtitle} → {new_subtitle}"
        return new_title, new_subtitle

    def _deleted_row_summary(self, item: WorkspaceRule) -> tuple[str, str]:
        return summarize_rule(item)

    # ── External-rule rendering ──

    def _build_external_hint(self) -> Gtk.Widget:
        return make_inline_hint(
            "Rules below come from your hyprland.conf or its sourced files. "
            "Edit those files directly to change them — hyprmod doesn't "
            "manage rules outside its own file.",
            icon_name="changes-prevent-symbolic",
        )

    def _make_external_row(self, ext: ExternalWorkspaceRule) -> Adw.ActionRow:
        title, subtitle = summarize_rule(ext.rule)
        return self._make_readonly_external_row(
            title=title,
            subtitle=f"{subtitle}  ·  line {ext.lineno}",
            source_path=ext.source_path,
            lineno=ext.lineno,
        )

    # ── Managed row ──

    def _make_row(self, idx: int, item: WorkspaceRule) -> Adw.ActionRow:
        title, subtitle = summarize_rule(item)
        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(2)

        prefix = Gtk.Image.new_from_icon_name(WORKSPACES_ICON)
        prefix.set_opacity(0.6)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        self._reorder.attach_keyboard(row, idx)
        if idx < len(self._rows_by_idx):
            self._rows_by_idx[idx] = row

        is_dirty = self._owned.is_item_dirty(idx)
        is_saved = self._owned.get_baseline(idx) is not None

        actions = RowActions(
            row,
            on_discard=lambda i=idx: self._discard_at(i),
            on_reset=lambda i=idx: self._on_delete_at(i),
            reset_icon="user-trash-symbolic",
            reset_tooltip="Remove this rule",
        )
        row.add_suffix(actions.box)
        actions.update(is_managed=True, is_dirty=is_dirty, is_saved=is_saved)

        row.set_activatable(True)
        row.connect("activated", lambda _r, i=idx: self._on_edit_at(i))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    # ── Live apply (push to running compositor) ──

    def _apply_rule_live(self, rule: WorkspaceRule) -> bool:
        """Push *rule* to the running compositor and project it onto live workspaces.

        Two steps:

        1. ``hypr.keyword("workspace", body)`` registers the rule (in
           Lua-mode Hyprland this is auto-translated to
           ``hl.workspace_rule`` via ``hyprland-state``). Hyprland's
           workspace-rule lookup is per-workspace last-wins, so
           re-pushing the same selector with new properties cleanly
           replaces the previous rule's effect.
        2. For any *already-open* workspace the rule matches, dispatch
           the equivalent live mutator — ``moveworkspacetomonitor`` for
           a monitor binding, ``renameworkspace`` for ``defaultName``.
           Hyprland evaluates rule properties at workspace creation, so
           without this pass the user would have to close + reopen the
           workspace before the change became visible.

        ``persistent``, gap, and decoration overrides apply on the next
        render without help — Hyprland reads them on each frame. The
        ``on-created-empty`` hook is by definition first-map-only and
        can't be triggered retroactively, so we don't try.

        Returns ``True`` if the keyword push succeeded (a toast has
        already been shown on failure).
        """
        ok = try_with_toast(
            self._window.show_bug_toast,
            "Workspace rule failed",
            lambda: self._window.hypr.keyword(config.KEYWORD_WORKSPACE, rule.body()),
            catch=HyprlandError,
        )
        if not ok:
            return False
        self._apply_to_existing(rule)
        return True

    def _apply_to_existing(self, rule: WorkspaceRule) -> None:
        """Replicate *rule*'s effect on each already-open matching workspace.

        Best-effort: bails early when the rule carries nothing we can
        project (monitor binding or rename), so we don't pay for an IPC
        round-trip on a pure decoration tweak. Dispatcher errors are
        swallowed — the rule is already registered, and the next
        workspace creation will pick it up regardless.
        """
        if rule.monitor is None and rule.default_name is None:
            return
        try:
            workspaces = get_workspaces()
        except HyprlandError:
            return
        for ws in workspaces:
            if not matches_workspace(rule, ws.id, ws.name):
                continue
            if rule.monitor is not None and ws.monitor != rule.monitor:
                try:
                    self._window.hypr.dispatch("moveworkspacetomonitor", f"{ws.id} {rule.monitor}")
                except HyprlandError:
                    pass
            if rule.default_name is not None and ws.name != rule.default_name:
                try:
                    self._window.hypr.dispatch("renameworkspace", f"{ws.id} {rule.default_name}")
                except HyprlandError:
                    pass

    # ── Add / Edit / Discard / Restore ──

    def _monitor_choices(self) -> list[tuple[str, str]]:
        """Live monitors as ``(connector, label)`` pairs for the dialog.

        Label is ``"DP-1 — Dell AW3423DWF"`` when make/model is available
        and ``"DP-1"`` when it isn't. The connector half is what we save
        into the rule, so the on-disk form stays portable across monitor
        layout changes that swap make/model strings.
        """
        monitors = self._window.hypr.monitors.get_all() or []
        result: list[tuple[str, str]] = []
        for m in sorted(monitors, key=lambda mon: mon.name):
            friendly = f"{(m.make or '').strip()} {(m.model or '').strip()}".strip()
            label = f"{m.name} — {friendly}" if friendly else m.name
            result.append((m.name, label))
        return result

    def _on_add(self) -> None:
        def on_apply(new_rule: WorkspaceRule) -> None:
            self._commit_appended(new_rule)
            self._apply_rule_live(new_rule)

        WorkspaceRuleEditDialog.present_singleton(
            self._window,
            monitor_choices=self._monitor_choices(),
            on_apply=on_apply,
        )

    def _on_edit_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._owned):
            return
        current = self._owned[idx]

        def on_apply(new_rule: WorkspaceRule) -> None:
            if new_rule == current:
                return
            self._commit_replaced(idx, new_rule)
            self._apply_rule_live(new_rule)

        WorkspaceRuleEditDialog.present_singleton(
            self._window,
            rule=current,
            monitor_choices=self._monitor_choices(),
            on_apply=on_apply,
        )

    def _discard_at(self, idx: int) -> None:
        """Revert a single rule to its saved value, re-pushing it live.

        Re-push so the running compositor reflects the restored value
        for any new windows matched by this workspace rule. Hyprland
        evaluates workspace rules at workspace creation, so existing
        workspaces matching this rule won't see the change until they
        next reopen — Save + Reload is the escape hatch.
        """
        baseline = self._owned.get_baseline(idx)
        super()._discard_at(idx)
        if baseline is not None:
            self._apply_rule_live(baseline)

    def _on_restore_deleted(self, item: WorkspaceRule) -> None:
        """Restore a previously-deleted rule, re-pushing it to the compositor."""
        super()._on_restore_deleted(item)
        self._apply_rule_live(item)

    # ── Save plumbing ──

    def get_workspace_lines(self) -> list[str]:
        """Serialize the current rules for ``config.write_all``.

        Order is preserved — last-wins semantics mean order is
        semantically meaningful, and the user sees their reorderings
        reflected on disk.
        """
        return serialize(list(self._owned))

    @staticmethod
    def has_managed_section(sections: dict[str, list[str]]) -> bool:
        """True if the saved config already contains any workspace rule lines."""
        return any(sections.get(kw) for kw in WORKSPACE_RULE_KEYWORDS)


__all__ = ["WorkspacesPage"]
