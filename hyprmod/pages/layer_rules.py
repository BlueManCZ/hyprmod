"""Layer Rules page — manage ``layerrule`` entries.

Hyprland's layer rules let users tag layer-shell surfaces (status bars,
notification daemons, launchers, wallpapers, lock screens) for special
treatment — backdrop blur, dim-around, animation overrides, render
order, transparency tuning. This page is a list editor for those rules,
following the same data-flow shape as :mod:`hyprmod.pages.window_rules`
but considerably simpler:

- One ``SavedList[LayerRule]`` is the source of truth.
- The user adds/edits/removes via :class:`LayerRuleEditDialog`. The
  dialog has a single namespace entry (regex by default, address mode
  available) and a curated dropdown of common rules with a live preview.
- On Apply, the new rule is pushed live to the compositor via
  ``hypr.keyword("layerrule", body)``. Hyprland reads dynamic layer
  rules every frame for blur / dim / xray / ignorealpha, so the keyword
  push reaches existing surfaces without us walking the layer list.
  Static rules (``order``, ``noanim``, ``animation``) apply at next
  surface map; rules are still written to the managed config on global
  save for persistence.
- Rules from outside our managed file (the user's ``hyprland.conf`` or
  any file it sources) are surfaced read-only at the bottom, with the
  source path + line number — same display pattern as window rules.

Differences from :mod:`hyprmod.pages.window_rules`:

- **No retroactive dispatch.** The ``setprop`` apparatus that brings
  existing windows into the new rule's state on apply doesn't exist
  for layer surfaces (Hyprland exposes no per-surface setprop), and
  for the dynamic rules it isn't needed: the rule resolver runs every
  frame and picks up keyword changes automatically.
- **No self-targeting check.** HyprMod is an ``xdg-shell`` toplevel,
  not a layer surface — a layer rule can never match its own window.
- **No ``unlayerrule`` IPC.** Same caveat as window rules: deleting,
  reordering, or discarding a rule doesn't take effect on the running
  compositor until save (which rewrites the config and triggers a
  reload). Adding new rules works live; removal needs the reload.

Reorder is keyboard-only (Alt+↑/↓ on a focused row) for the initial
release. Layer rule order is less critical than window rule order
(no "later wins" for most effects), but it still matters for
``unset`` (place first to clear before re-applying) and for
``order N`` predictability — so the affordance has to exist.
"""

from html import escape as html_escape

from gi.repository import Adw, Gtk
from hyprland_config import render_rule_live
from hyprland_socket import HyprlandError

from hyprmod.core import config
from hyprmod.core.layer_rules import (
    ExternalLayerRule,
    LayerRule,
    from_rule_nodes,
    load_external_layer_rules,
    serialize,
    summarize_rule,
)
from hyprmod.core.ownership import SavedList
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import (
    make_inline_hint,
    make_page_layout,
    try_with_toast,
)
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import LAYER_RULES_ICON
from hyprmod.ui.layer_rule_dialog import LayerRuleEditDialog
from hyprmod.ui.row_actions import RowActions


class LayerRulesPage(SavedListSectionPage[LayerRule]):
    """List editor for ``layerrule`` entries."""

    _unit_singular = "rule"
    _unit_plural = "rules"
    _deleted_subtitle_lines = 2
    _page_attr = "_layer_rules_page"
    _pending_category = "Layer Rules"
    _pending_navigate_to = "layer_rules"
    _pending_icon = LAYER_RULES_ICON
    _group_title = "Layer Rules"
    _group_add_tooltip = "Add another rule"
    _external_prefix_icon = LAYER_RULES_ICON

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
        self._owned: SavedList[LayerRule]
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        # Layer rules come through as structured :class:`Rule` nodes from
        # ``hyprland_config.migrate()``; the adapter converts them to UI
        # :class:`LayerRule` entries (same pattern as windowrule page).
        del saved_sections
        items = from_rule_nodes(self._window.saved_rules)
        self._owned = SavedList(items, key=lambda r: r.to_line())
        self._external = load_external_layer_rules(config.user_entry_path(), config.managed_path())

    # Restore-snapshot default applies: layer rules need no per-surface
    # runtime sync. Hyprland reads dynamic layer rules every frame, so the
    # running compositor reflects the in-memory rule list at the next frame
    # regardless of how it got there. Save+reload still produces the
    # canonical state (and is what *removes* a rule from the running
    # compositor — there's no ``unlayerrule`` IPC).

    # ── Build ──

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add layer rule")
        add_btn.connect("clicked", lambda _b: self._on_add())
        page_header.pack_start(add_btn)

        toolbar_view, _, self._content_box, self._scrolled = make_page_layout(header=page_header)

        self._rebuild_list()
        return toolbar_view

    # ── List rendering ──

    def _build_empty_state(self) -> EmptyState:
        """Empty-state page with a single "Add Rule" button.

        Unlike the window-rules empty state, there's no "Pick from Open
        Window" path — ``hyprland-socket`` doesn't expose a layers query,
        and even if it did, the user-recognisable identity for a layer
        surface is its namespace (``waybar``, ``rofi``), not a running
        window the user can point at.
        """
        return EmptyState(
            title="No Layer Rules",
            description=(
                "Tweak how shell surfaces (waybar, notifications, rofi, wallpapers) "
                "are decorated — backdrop blur, dim-around, animations, render order."
            ),
            icon_name=LAYER_RULES_ICON,
            primary_action=("Add Rule…", self._on_add),
        )

    def _build_order_hint(self) -> Gtk.Widget:
        """Inline note: explains how rule order interacts with ``unset``."""
        return make_inline_hint(
            "Rules accumulate per surface. ‘unset’ clears every prior rule for the "
            "matched namespace — place it first when you want to start fresh. "
            "Reorder with Alt+↑ / Alt+↓ on a focused row."
        )

    # Group + deleted-row + external-section rendering uses the base
    # ``SavedListSectionPage`` template; only the per-row content is
    # page-specific.

    def _deleted_row_summary(self, item: LayerRule) -> tuple[str, str]:
        return summarize_rule(item)

    # ── Pending-changes summarizers ──

    def _summarize_item(self, item: LayerRule) -> tuple[str, str]:
        return summarize_rule(item)

    def _summarize_modified(self, baseline: LayerRule, item: LayerRule) -> tuple[str, str]:
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

    def _make_external_row(self, ext: ExternalLayerRule) -> Adw.ActionRow:
        title, namespace_summary = summarize_rule(ext.rule)
        # No "override" action: Hyprland has no ``unlayerrule`` IPC, same
        # caveat as window rules. Lock-icon-only suffix mirrors that page.
        return self._make_readonly_external_row(
            title=title,
            subtitle=f"{namespace_summary}  ·  line {ext.lineno}",
            source_path=ext.source_path,
            lineno=ext.lineno,
        )

    def _make_row(self, idx: int, item: LayerRule) -> Adw.ActionRow:
        title, subtitle = summarize_rule(item)
        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        # Two subtitle lines so a long namespace regex doesn't get
        # ellipsized into uselessness.
        row.set_subtitle_lines(2)

        prefix = Gtk.Image.new_from_icon_name(LAYER_RULES_ICON)
        prefix.set_opacity(0.6)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

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
            reset_tooltip="Remove this rule",
        )
        row.add_suffix(actions.box)
        actions.update(is_managed=True, is_dirty=is_dirty, is_saved=is_saved)

        row.set_activatable(True)
        row.connect("activated", lambda _r, i=idx: self._on_edit_at(i))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    # ── Live apply (push to running compositor) ──

    def _apply_rule_live(self, rule: LayerRule) -> bool:
        """Push *rule* to the running compositor.

        One-step: ``hypr.keyword("layerrule", body)`` registers the
        rule. Hyprland's rule resolver runs every frame for dynamic
        rules (blur, dim, ignorealpha, xray) and at next surface map
        for static rules (order, animation, noanim) — both pick up
        the new rule without us walking the layer list, unlike the
        window-rules page where existing windows need explicit
        ``setprop`` per-window dispatch.

        Disabled rules (``enabled=False``) skip the push entirely —
        the user's intent is "defined but inactive", and the keyword
        would activate the rule until the next reload. Returns
        ``True`` when the rule was actually pushed.
        """
        if not rule.enabled:
            return False
        # ``render_rule_live`` picks the grammar the running compositor
        # understands: ``layerrule = match:namespace …`` on 0.54+, the
        # effect-first ``layerrule = blur, ^(waybar)$`` below, with one
        # (keyword, value) pair per effect on the older grammar.
        version = self._window.hypr.version

        def _push() -> None:
            for keyword, value in render_rule_live(rule.to_rule_node(), version):
                self._window.hypr.keyword(keyword, value)

        return try_with_toast(
            self._window.show_bug_toast,
            "Layer rule failed",
            _push,
            catch=HyprlandError,
        )

    # ── Add / Edit / Remove ──

    def _on_add(self) -> None:
        def on_apply(new_rule: LayerRule) -> None:
            self._commit_appended(new_rule)
            self._apply_rule_live(new_rule)

        LayerRuleEditDialog.present_singleton(self._window, on_apply=on_apply)

    def _on_edit_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._owned):
            return
        current = self._owned[idx]

        def on_apply(new_rule: LayerRule) -> None:
            if new_rule == current:
                return
            self._commit_replaced(idx, new_rule)
            self._apply_rule_live(new_rule)

        LayerRuleEditDialog.present_singleton(self._window, rule=current, on_apply=on_apply)

    # ``_on_delete_at`` uses the base default — Hyprland has no
    # ``unlayerrule`` IPC, so the rule stays in the runtime list until
    # the next reload (surfaces mapped *after* this delete still see it).
    # Save+reload is the escape hatch.

    def _discard_at(self, idx: int) -> None:
        """Revert a single rule to its saved value, re-pushing it live.

        Re-push so the running compositor reflects the restored value
        for any *new* surfaces. (Existing surfaces picked up the dirty
        version while it was active; their state reverts automatically
        on next frame for dynamic rules.)
        """
        baseline = self._owned.get_baseline(idx)
        super()._discard_at(idx)
        if baseline is not None:
            self._apply_rule_live(baseline)

    def _on_restore_deleted(self, item: LayerRule) -> None:
        """Restore a previously-deleted rule, re-pushing it to the compositor.

        Dynamic effects (blur, dim, ignorealpha) take effect again on
        the next frame after the keyword push.
        """
        super()._on_restore_deleted(item)
        self._apply_rule_live(item)

    # ── Save plumbing ──

    def get_layer_rule_lines(self) -> list[str]:
        """Serialize the current rules for ``config.write_all``.

        Order is preserved — placement matters for ``unset`` and for
        ``order N`` predictability, so the order users see in the UI
        is exactly what's written. The running Hyprland version drives
        the grammar: pre-0.54 emits the legacy ``layerrule = effect,
        namespace`` form so the saved config reloads cleanly on the
        compositor that's actually running.
        """
        return serialize(list(self._owned), self._window.hypr.version)


__all__ = ["LayerRulesPage"]
