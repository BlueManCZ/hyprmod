"""Window Rules page — manage ``windowrule`` / ``windowrulev2`` entries.

Hyprland's window rules let users tag specific windows for special
treatment (float, pin, set opacity, send to a workspace, …). This page
is a list editor for those rules, mirroring the autostart page's data
flow:

- One ``SavedList[WindowRule]`` is the source of truth.
- The user adds/edits/removes via :class:`WindowRuleEditDialog`, which
  takes the friction out of building a rule by offering a window picker
  ("just point at the running app"), a curated dropdown of common
  actions, and a live preview of the exact config line.
- On Apply, the new rule is pushed live to the compositor via
  ``hypr.keyword("windowrule", …)`` so users get the same
  immediate-feedback flow as the keybinds page. Rules are still
  written to hyprmod's managed config on global save for persistence.
- The ``keyword`` push only registers the rule for *future* windows.
  Hyprland resolves windowrules to per-window state at map time —
  for both static and dynamic effects — and never re-evaluates them
  when a new rule arrives via IPC. To make "Apply Live" feel right
  we also walk the running windows, find ones the rule's matchers
  cover, and dispatch the equivalent per-window action: mutating
  dispatchers for static effects (``togglefloating address:0x…``,
  ``movetoworkspacesilent W,address:0x…``, …) and ``setprop`` for
  dynamic effects (``setprop address:0x… opacity 0.5``,
  ``setprop address:0x… no_blur on``, …). Hyprland 0.54+ keeps the
  setprop override at ``PRIORITY_SET_PROP`` until the next config
  reload, so the live preview survives window moves and resizes
  without needing the legacy ``lock`` flag.
- When the new rule's matchers would also match HyprMod's own window
  (e.g. a wildcard ``class`` regex, or a literal class match), we gate
  the live apply behind a confirmation dialog — applying a self-targeted
  ``opacity`` or ``float`` rule while the user is editing it is jarring
  and easy to do by accident. Cancelling the dialog still commits the
  rule to the SavedList; it just doesn't push it to the compositor
  until the next save+reload.

Two limitations follow from Hyprland's IPC surface:

- There's no "remove a single windowrule" command (only a full
  ``hyprctl reload``), so deleting, reordering, or discarding a rule
  doesn't take effect on the running compositor until save (which
  rewrites the config and triggers a reload). The retroactive
  dispatch we do on Apply is also one-way: changing a rule from
  ``float`` to ``tile`` won't un-float windows that the prior rule
  already floated.
- Editing an existing rule appends the new version on top of the old
  one in the compositor's runtime list. New windows see the new rule
  win (later wins), but the stale rule is still there until reload.
  This is harmless for most effects and gets cleaned up on save.

Rules from the user's ``hyprland.conf`` (or any file it sources outside
our managed file) are surfaced read-only in a separate group at the
bottom of the list. The Binds page handles the equivalent case by
offering an "override this bind" action — that works there because
``hyprctl unbind`` cleanly removes the original. Window rules have no
such IPC, so an "override" would have to rely on Hyprland's "later
wins" resolution, which is partial (works for ``opacity`` but not
cleanly for ``no_blur`` / static effects) and pollutes the config
with counter-rules. We surface external rules with a source-file
location and tooltip so the user can edit them by hand instead.

The page deliberately limits reorder to the keyboard (Alt+↑/↓ on a
focused row) for the initial release — rule order matters in Hyprland
("later rule wins"), so reordering must be possible, but the autostart
page's full drag-and-drop path adds substantial widget plumbing that
we only need to copy over when the simpler keyboard form proves
insufficient.
"""

import re
from html import escape as html_escape

from gi.repository import Adw, Gtk
from hyprland_config import render_rule_live
from hyprland_socket import HyprlandError, get_windows

from hyprmod.core import config
from hyprmod.core.ownership import SavedList
from hyprmod.core.window_rules import (
    ACTION_PRESETS,
    HYPRMOD_APP_ID,
    RETROACTIVE_EFFECTS,
    Effect,
    ExternalWindowRule,
    Matcher,
    WindowRule,
    existing_window_dispatchers,
    existing_window_revert_dispatchers,
    from_rule_nodes,
    load_external_window_rules,
    matches_hyprmod,
    matches_window,
    serialize,
    summarize_rule,
)
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import (
    confirm,
    make_inline_hint,
    make_page_layout,
    try_with_toast,
)
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import WINDOW_RULES_ICON
from hyprmod.ui.row_actions import RowActions
from hyprmod.ui.window_picker import WindowPickerDialog
from hyprmod.ui.window_rule_dialog import WindowRuleEditDialog


class WindowRulesPage(SavedListSectionPage[WindowRule]):
    """List editor for ``windowrule`` / ``windowrulev2`` entries."""

    _unit_singular = "rule"
    _unit_plural = "rules"
    _deleted_subtitle_lines = 2
    _page_attr = "_window_rules_page"
    _pending_category = "Window Rules"
    _pending_navigate_to = "window_rules"
    _pending_icon = WINDOW_RULES_ICON
    _group_title = "Window Rules"
    _group_add_tooltip = "Add another rule"
    _external_prefix_icon = WINDOW_RULES_ICON

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
        self._owned: SavedList[WindowRule]
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        # Window rules come through as structured :class:`Rule` nodes
        # from ``hyprland_config.migrate()`` regardless of source shape
        # (single-line, block-form, or Lua table); the page-level
        # adapter converts them to UI :class:`WindowRule` entries.
        # ``saved_sections`` is no longer load-bearing for windowrules
        # — kept on the constructor for the shared base-class contract
        # other pages still use.
        del saved_sections
        items = from_rule_nodes(self._window.saved_rules)
        self._owned = SavedList(items, key=lambda r: r.to_line())
        # Surface any windowrule lines that live in the user's
        # hyprland.conf or any file it sources (other than our managed
        # file). These are advisory display only — Hyprland has no IPC
        # to remove individual rules, so we can't offer an "override"
        # action like the Binds page does. The escape hatch for the
        # user is to edit the source file directly.
        self._external = load_external_window_rules(config.user_entry_path(), config.managed_path())

    # ── Undo / Redo ──

    def restore_snapshot(
        self,
        items: list[WindowRule],
        baselines: list[WindowRule | None],
    ) -> None:
        """Restore state and sync the runtime.

        Rules that disappeared in this hop have their per-window setprop
        overrides reverted, rules that appeared get pushed. Without this,
        undo would silently leave a window's opacity / no_blur / etc.
        wherever the prior dirty edit had set it.
        """
        old_items = list(self._owned)
        super().restore_snapshot(items, baselines)
        self._sync_runtime_diff(old_items, items)

    # ── Build ──

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add window rule")
        add_btn.connect("clicked", lambda _b: self._on_add())
        page_header.pack_start(add_btn)

        toolbar_view, _, self._content_box, self._scrolled = make_page_layout(header=page_header)

        self._rebuild_list()
        return toolbar_view

    # ── List rendering ──

    def _build_empty_state(self) -> EmptyState:
        """Empty-state page with two prominent action buttons.

        Two paths surfaced upfront: "Pick from Open Window" (zero-friction
        for the common case "make a rule for THIS app") and "Add Rule"
        (the manual path for rules that target windows that aren't
        currently running).
        """
        return EmptyState(
            title="No Window Rules",
            description=(
                "Make Hyprland treat specific windows differently — pin them, "
                "set opacity, open on a workspace, and more."
            ),
            icon_name=WINDOW_RULES_ICON,
            primary_action=("Pick from Open Window", self._on_pick_window),
            secondary_action=("Add Rule…", self._on_add),
        )

    def _build_order_hint(self) -> Gtk.Widget:
        """Inline note: explains that rule order matters and how to reorder."""
        return make_inline_hint(
            "Rules are evaluated top to bottom. When two rules match the "
            "same window, the lower one wins. Reorder with Alt+↑ / Alt+↓ "
            "on a focused row."
        )

    # Group + deleted-row + external-section rendering uses the base
    # ``SavedListSectionPage`` template; only the per-row content is
    # page-specific.

    def _deleted_row_summary(self, item: WindowRule) -> tuple[str, str]:
        return summarize_rule(item)

    # ── Pending-changes summarizers ──

    def _summarize_item(self, item: WindowRule) -> tuple[str, str]:
        return summarize_rule(item)

    def _summarize_modified(self, baseline: WindowRule, item: WindowRule) -> tuple[str, str]:
        old_title, old_subtitle = summarize_rule(baseline)
        new_title, new_subtitle = summarize_rule(item)
        if old_title != new_title:
            return new_title, f"{old_title} → {new_title}"
        return new_title, f"{old_subtitle} → {new_subtitle}"

    def _build_external_hint(self) -> Gtk.Widget:
        """Inline note explaining that the rules below are read-only."""
        return make_inline_hint(
            "Rules below come from your hyprland.conf or its sourced files. "
            "Edit those files directly to change them — hyprmod doesn't "
            "manage rules outside its own file.",
            icon_name="changes-prevent-symbolic",
        )

    def _make_external_row(self, ext: ExternalWindowRule) -> Adw.ActionRow:
        title, matchers_summary = summarize_rule(ext.rule)
        # Subtitle = matcher summary + line number. The file path is
        # *not* duplicated here — it's the group title above. Middle dot
        # mirrors the GNOME convention for inline metadata. The lock icon
        # has no "override" companion: Hyprland exposes no
        # ``unwindowrule`` IPC, so a clean override path doesn't exist
        # (see this module's docstring).
        return self._make_readonly_external_row(
            title=title,
            subtitle=f"{matchers_summary}  ·  line {ext.lineno}",
            source_path=ext.source_path,
            lineno=ext.lineno,
        )

    def _make_row(self, idx: int, item: WindowRule) -> Adw.ActionRow:
        title, subtitle = summarize_rule(item)
        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        # Allow two subtitle lines so a rule with a long regex matcher
        # (the common case for ``initialTitle`` matches) doesn't get
        # ellipsized into uselessness — but cap there to keep rows
        # uniform in height.
        row.set_subtitle_lines(2)

        # Visual cue for the most common rule type — a small icon on
        # the left lets users scan the list without reading. Picked
        # generic-enough to fit any action (lock-screen-style icon).
        prefix = Gtk.Image.new_from_icon_name(WINDOW_RULES_ICON)
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

    def _apply_rule_live(self, rule: WindowRule) -> bool:
        """Apply *rule* to the running compositor.

        Two-step for enabled rules:

        1. ``hypr.keyword("windowrule", …)`` registers the rule so
           future windows pick it up.
        2. Walk currently-mapped windows and run the equivalent
           per-window dispatcher for each match — Hyprland resolves
           windowrules to per-window state at map time and never
           re-evaluates them against existing windows when a new rule
           arrives via IPC, so this step is what makes opacity / float
           / etc. visible on the windows the user already has open.

        Disabled rules (``enabled=False``) skip both steps — the
        user's intent is "defined but inactive", and pushing the
        keyword would activate it until the next reload. Returns
        ``True`` when the rule was actually pushed.
        """
        if not rule.enabled:
            return False
        # ``render_rule_live`` picks the grammar the running compositor
        # accepts (``windowrule = match:…`` on 0.53+, ``windowrulev2 =
        # effect, class:…`` below) and yields one (keyword, value) pair
        # per push: multi-effect rules emit multiple pairs on the pre-v3
        # grammar, one on v3.
        version = self._window.hypr.version

        def _push() -> None:
            for keyword, value in render_rule_live(rule.to_rule_node(), version):
                self._window.hypr.keyword(keyword, value)

        ok = try_with_toast(
            self._window.show_bug_toast,
            "Window rule failed",
            _push,
            catch=HyprlandError,
        )
        if not ok:
            return False
        self._apply_to_existing(rule)
        return True

    def _foreach_matching_window(
        self,
        rule: WindowRule,
        get_dispatchers,
    ) -> tuple[int, HyprlandError | None]:
        """Iterate mapped matches and run *get_dispatchers* per window.

        Returns ``(success_count, first_error_or_None)``. Errors don't
        abort the loop — one window failing shouldn't stop us mutating
        the rest — but we capture the first one for the caller's
        toast. If a window's dispatcher set raises mid-way we skip
        the remaining dispatchers for *that* window, since the set is
        usually atomic (e.g. opacity emits ``opacity`` +
        ``opacity_inactive``) and partial application is worse than
        none.
        """
        try:
            windows = get_windows()
        except HyprlandError as e:
            return 0, e

        first_error: HyprlandError | None = None
        applied = 0
        for window in windows:
            if not window.mapped:
                continue
            if not matches_window(rule, window):
                continue
            window_ok = True
            for dispatcher, arg in get_dispatchers(rule, window):
                try:
                    self._window.hypr.dispatch(dispatcher, arg)
                except HyprlandError as e:
                    if first_error is None:
                        first_error = e
                    window_ok = False
                    break
            if window_ok:
                applied += 1
        return applied, first_error

    def _apply_to_existing(self, rule: WindowRule) -> None:
        """Replicate *rule*'s effects on each already-mapped match.

        Bails immediately when no effect in the rule has a per-window
        mapping, so we don't pay for an IPC ``get_windows`` round-trip
        on (e.g.) a ``stay_focused``-only tweak. Multi-effect rules
        run if *any* effect is retroactive.
        """
        if not any(e.name in RETROACTIVE_EFFECTS for e in rule.effects):
            return
        applied, error = self._foreach_matching_window(rule, existing_window_dispatchers)
        if error is not None:
            self._window.show_bug_toast(
                f"Couldn't apply to existing windows — {error}",
                detail=str(error),
                timeout=5,
            )
        elif applied > 0:
            self._window.show_toast(
                f"Applied to {applied} existing window{'s' if applied != 1 else ''}",
                timeout=2,
            )

    def _revert_to_existing(self, rule: WindowRule) -> None:
        """Clear *rule*'s runtime effect on each already-mapped match.

        Mirror of :meth:`_apply_to_existing` for delete / discard /
        undo. Emits ``setprop NAME unset`` per matching window for
        dynamic effects; static effects no-op (see
        :func:`existing_window_revert_dispatchers` for why).

        No success toast — the visible feedback is the window snapping
        back to its prior opacity/blur/etc. Errors are surfaced because
        a silent failure here is the bug we're fixing.
        """
        if not any(e.name in RETROACTIVE_EFFECTS for e in rule.effects):
            return
        _applied, error = self._foreach_matching_window(rule, existing_window_revert_dispatchers)
        if error is not None:
            self._window.show_bug_toast(
                f"Couldn't revert on existing windows — {error}",
                detail=str(error),
                timeout=5,
            )

    def _sync_runtime_diff(self, old_items: list[WindowRule], new_items: list[WindowRule]) -> None:
        """Bring runtime state from *old_items* to *new_items* via per-rule diff.

        Compared by ``to_line()`` (full rule text), not position —
        reordering doesn't affect runtime for the effects we mutate
        (Hyprland evaluates the full rule list at map time). For each
        rule that disappeared, emit revert dispatchers; for each rule
        that appeared, push it. Used by discard, discard-all, and
        undo/redo so the running compositor tracks the SavedList.
        """
        old_lines = {r.to_line() for r in old_items}
        new_lines = {r.to_line() for r in new_items}
        for r in old_items:
            if r.to_line() not in new_lines:
                self._revert_to_existing(r)
        for r in new_items:
            if r.to_line() not in old_lines:
                self._apply_rule_live(r)

    def _maybe_apply_rule_live(self, rule: WindowRule) -> None:
        """Push *rule* to the compositor, gated by self-targeting confirm.

        Fire-and-forget: callers should already have committed the rule
        to the SavedList before invoking this — applying a self-targeting
        ``opacity 0`` (etc.) mid-edit is jarring, so when the rule's
        matchers also match HyprMod we ask before pushing. If the user
        declines (or closes the dialog), the rule still goes out on the
        next save+reload; we just don't disturb the editor right now.
        """
        hyprmod_title = self._window.get_title() or ""
        if not matches_hyprmod(rule, hyprmod_title=hyprmod_title):
            self._apply_rule_live(rule)
            return

        confirm(
            self._window,
            heading="Apply this rule to HyprMod itself?",
            body=(
                f"This rule's matchers also match HyprMod's own window "
                f"(class {HYPRMOD_APP_ID!r}). Applying it live can "
                "disrupt this editor — for example, an ‘opacity’ or "
                "‘float’ action would take effect on the window you "
                "are using right now.\n\n"
                "‘Save Only’ keeps the rule in your config and applies "
                "it after the next save+reload. ‘Apply Live’ pushes it "
                "to the running compositor immediately."
            ),
            label="Apply Live",
            on_confirm=lambda: self._apply_rule_live(rule),
            appearance=Adw.ResponseAppearance.SUGGESTED,
        )

    # ── Add / Edit / Remove ──

    def _on_add(self) -> None:
        def on_apply(new_rule: WindowRule) -> None:
            self._commit_appended(new_rule)
            self._maybe_apply_rule_live(new_rule)

        WindowRuleEditDialog.present_singleton(self._window, on_apply=on_apply)

    def _on_pick_window(self) -> None:
        """Empty-state shortcut: open the picker, then the edit dialog.

        Picking a window pre-fills the edit dialog with that window's
        class regex (or title fallback) — the user just has to choose
        an action. This is the "I want a rule for THIS app" flow.
        """

        def on_pick(window) -> None:
            # ``^(escaped)$`` here mirrors what the dialog's own pick
            # path produces, so a class picked from this empty-state
            # button and one picked from inside the dialog round-trip
            # to the same regex.
            matchers: list[Matcher] = []
            if window.class_name:
                matchers.append(Matcher(key="class", value=f"^({re.escape(window.class_name)})$"))
            elif window.title:
                # Title is volatile, but it's a better starting hook
                # than a blank dialog when class is unknown.
                matchers.append(Matcher(key="title", value=f"^({re.escape(window.title)})$"))
            # Float is a non-destructive default that users almost
            # always change. Empty args triggers the auto-``on`` for
            # booleans on serialization.
            stub = WindowRule(
                matchers=matchers,
                effects=[Effect(name=ACTION_PRESETS[0].id)],
            )

            def on_apply(new_rule: WindowRule) -> None:
                self._commit_appended(new_rule)
                self._maybe_apply_rule_live(new_rule)

            WindowRuleEditDialog.present_singleton(self._window, rule=stub, on_apply=on_apply)

        WindowPickerDialog.present_singleton(self._window, on_pick=on_pick)

    def _on_edit_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._owned):
            return
        current = self._owned[idx]

        def on_apply(new_rule: WindowRule) -> None:
            if new_rule == current:
                return
            self._commit_replaced(idx, new_rule)
            # Toggling Enabled off needs to undo the prior live effect on
            # already-mapped windows — Hyprland doesn't retroactively
            # "un-float" / clear setprop when a rule is disabled at
            # config-edit time. Skip the apply step in that case.
            if current.enabled and not new_rule.enabled:
                self._revert_to_existing(current)
                return
            self._maybe_apply_rule_live(new_rule)

        WindowRuleEditDialog.present_singleton(self._window, rule=current, on_apply=on_apply)

    def _on_delete_at(self, idx: int) -> None:
        """Remove the rule at *idx* and clear per-window setprop overrides.

        Hyprland has no "remove a single windowrule" IPC, so the rule
        itself stays in the runtime list until the next reload — *new*
        windows still see it. For *existing* windows we clear the
        per-window setprop overrides so the visible state snaps back to
        whatever Hyprland's rule resolver computed at map time. Static
        effects (float, size, …) have no clean undo and stay as-is
        until save+reload.
        """
        if idx < 0 or idx >= len(self._owned):
            return
        removed = self._owned[idx]
        super()._on_delete_at(idx)
        self._revert_to_existing(removed)

    def _discard_at(self, idx: int) -> None:
        """Revert the rule at *idx* and snap existing windows back live.

        Per-window setprop overrides get reset: we revert the dirty
        version's effect on existing windows, then re-push the baseline
        so those windows snap to the saved state. The runtime rule list
        itself can't change without a config reload (same caveat as
        :meth:`_on_delete_at`).
        """
        baseline = self._owned.get_baseline(idx)
        if baseline is None:
            self._on_delete_at(idx)
            return
        current = self._owned[idx]
        super()._discard_at(idx)
        self._sync_runtime_diff([current], [baseline])

    def _on_restore_deleted(self, item: WindowRule) -> None:
        """Restore a previously-deleted rule and re-push it to the compositor.

        Re-pushed through the same self-targeting gate as Add — so a
        rule that would also match HyprMod's own window asks before
        applying live.
        """
        super()._on_restore_deleted(item)
        self._maybe_apply_rule_live(item)

    # ── SectionPage protocol (overrides) ──

    def discard(self) -> None:
        # Capture both the dirty list and the saved baselines BEFORE
        # discard_all rewinds; the runtime diff needs both to compute
        # which setprop overrides to clear and which to re-apply.
        old_items = list(self._owned)
        new_items = list(self._owned.saved)
        self._owned.discard_all()
        self._sync_runtime_diff(old_items, new_items)
        self._rebuild_list()

    # ── Save plumbing ──

    def get_window_rule_lines(self) -> list[str]:
        """Serialize the current rules for ``config.write_all``.

        Order is preserved — rule order matters in Hyprland, so the
        order users see in the UI is exactly what's written. The running
        Hyprland version drives the grammar: pre-0.53 emits the legacy
        ``windowrulev2 = effect, class:regex`` form so the saved config
        reloads cleanly on the compositor that's actually running.
        """
        return serialize(list(self._owned), self._window.hypr.version)


__all__ = ["WindowRulesPage"]
