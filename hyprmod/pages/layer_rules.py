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
from pathlib import Path

from gi.repository import Adw, GLib, Gtk
from hyprland_socket import HyprlandError

from hyprmod.core import config
from hyprmod.core.layer_rules import (
    KEYWORD_WRITE,
    LAYER_RULE_KEYWORDS,
    ExternalLayerRule,
    LayerRule,
    load_external_layer_rules,
    parse_layer_rule_lines,
    serialize,
    summarize_rule,
)
from hyprmod.core.ownership import SavedList
from hyprmod.core.setup import HYPRLAND_CONF
from hyprmod.core.undo import SavedListSnapshot
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import (
    clear_children,
    display_path,
    make_inline_hint,
    make_page_layout,
    try_with_toast,
)
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.layer_rule_dialog import LayerRuleEditDialog
from hyprmod.ui.row_actions import RowActions


class LayerRulesPage(SavedListSectionPage[LayerRule]):
    """List editor for ``layerrule`` entries."""

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
        # Layer rules from sourced config files outside our managed file —
        # surfaced read-only so users see the full picture. Rebuilt on
        # every load (including profile switches), since their content
        # can change without our involvement.
        self._external: list[ExternalLayerRule] = []
        # Maps each owned-list index to the row widget representing it.
        # Same shape as window_rules / autostart — pre-sized to ``None``
        # and filled in as ``_make_row`` runs. Used by the keyboard
        # reorder path to refocus the moved row post-rebuild.
        self._rows_by_idx: list[Adw.ActionRow | None] = []
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            _, saved_sections = config.read_all_sections()
        raw_lines = config.collect_section(saved_sections, *LAYER_RULE_KEYWORDS)
        items = parse_layer_rule_lines(raw_lines)
        self._owned = SavedList(items, key=lambda r: r.to_line())
        self._external = load_external_layer_rules(HYPRLAND_CONF, config.gui_conf())

    # ── Undo / Redo ──

    def _capture_undo(self):
        return self._owned.snapshot()

    def _undo_key(self) -> list[str]:
        return [r.to_line() for r in self._owned]

    def _build_undo_entry(self, old, new):
        old_items, old_baselines = old
        new_items, new_baselines = new
        return SavedListSnapshot(
            page_attr="_layer_rules_page",
            old_items=old_items,
            new_items=new_items,
            old_baselines=old_baselines,
            new_baselines=new_baselines,
        )

    def restore_snapshot(
        self,
        items: list[LayerRule],
        baselines: list[LayerRule | None],
    ) -> None:
        """Restore state from an undo/redo snapshot.

        Unlike the window-rules version, no per-surface runtime sync is
        needed: Hyprland reads dynamic layer rules every frame, so the
        running compositor reflects whatever's in its in-memory
        windowrule list at the next frame regardless of how it got there.
        Save+reload still produces the canonical state (and is what
        *removes* a rule from the running compositor — there's no
        ``unlayerrule`` IPC).
        """
        self._owned.restore(items, baselines)
        self._rebuild_list()
        self._notify_dirty()

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

    def _rebuild_list(self, focus_idx: int = -1) -> None:
        clear_children(self._content_box)
        self._rows_by_idx = [None] * len(self._owned)

        # Reorder hint shown only when there are at least two entries.
        if len(self._owned) >= 2:
            self._content_box.append(self._build_order_hint())

        if len(self._owned) > 0:
            self._content_box.append(self._build_group())

        # Surface deleted rules so the user can restore them before save.
        deleted = self._deleted_baselines()
        if deleted:
            self._content_box.append(self._build_deleted_group(deleted))

        # External rules from the user's hyprland.conf or sourced files —
        # read-only display with source-path provenance. Always at the
        # bottom: it's reference info, not the primary content.
        if self._external:
            for widget in self._build_external_section():
                self._content_box.append(widget)

        if len(self._owned) == 0 and not deleted and not self._external:
            self._content_box.append(self._build_empty_state())

        if 0 <= focus_idx < len(self._rows_by_idx):
            target = self._rows_by_idx[focus_idx]
            if target is not None:
                # Defer to idle so the row is mapped before grab_focus
                # (see ``_grab_focus_once`` in the base class for the
                # SOURCE_REMOVE rationale).
                GLib.idle_add(self._grab_focus_once, target)

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
            icon_name="overlapping-windows-symbolic",
            primary_action=("Add Rule…", self._on_add),
        )

    def _build_order_hint(self) -> Gtk.Widget:
        """Inline note: explains how rule order interacts with ``unset``."""
        return make_inline_hint(
            "Rules accumulate per surface. ‘unset’ clears every prior rule for the "
            "matched namespace — place it first when you want to start fresh. "
            "Reorder with Alt+↑ / Alt+↓ on a focused row."
        )

    def _build_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Layer Rules")
        n = len(self._owned)
        group.set_description(f"{n} rule{'' if n == 1 else 's'}")

        # Header-suffix add button mirrors the page-header one for users
        # who scrolled past the page header.
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add another rule")
        add_btn.connect("clicked", lambda _b: self._on_add())
        group.set_header_suffix(add_btn)

        for idx in range(len(self._owned)):
            group.add(self._make_row(idx, self._owned[idx]))
        return group

    def _build_deleted_group(self, deleted: list[LayerRule]) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Removed (pending save)")
        group.set_description(
            f"{len(deleted)} rule{'' if len(deleted) == 1 else 's'} will be removed on save"
        )
        for item in deleted:
            title, subtitle = summarize_rule(item)
            row = Adw.ActionRow(
                title=html_escape(title),
                subtitle=html_escape(subtitle),
            )
            row.set_title_lines(1)
            row.set_subtitle_lines(2)
            row.add_css_class("option-default")
            row.set_opacity(0.65)

            restore_btn = Gtk.Button(icon_name="edit-undo-symbolic")
            restore_btn.set_valign(Gtk.Align.CENTER)
            restore_btn.add_css_class("flat")
            restore_btn.set_tooltip_text("Restore this rule")
            restore_btn.connect("clicked", lambda _b, e=item: self._on_restore_deleted(e))
            row.add_suffix(restore_btn)

            group.add(row)
        return group

    def _build_external_section(self) -> list[Gtk.Widget]:
        """Build the read-only external-rules display.

        Returns an inline hint + one PreferencesGroup per source file —
        same grouping pattern as the window-rules page so users see one
        path-as-title per file instead of repeating it on every row.
        """
        widgets: list[Gtk.Widget] = [self._build_external_hint()]

        # ``find_all`` returns rules in source-traversal order, so a
        # plain (insertion-ordered) dict gives us the right grouping
        # for free.
        by_file: dict[Path, list[ExternalLayerRule]] = {}
        for ext in self._external:
            by_file.setdefault(ext.source_path, []).append(ext)

        for source_path, rules in by_file.items():
            widgets.append(self._build_external_file_group(source_path, rules))
        return widgets

    def _build_external_hint(self) -> Gtk.Widget:
        """Inline note explaining that the rules below are read-only."""
        return make_inline_hint(
            "Rules below come from your hyprland.conf or its sourced files. "
            "Edit those files directly to change them — hyprmod doesn't "
            "manage rules outside its own file.",
            icon_name="changes-prevent-symbolic",
        )

    def _build_external_file_group(
        self, source_path: Path, rules: list[ExternalLayerRule]
    ) -> Adw.PreferencesGroup:
        """A PreferencesGroup containing every external rule from one file."""
        group = Adw.PreferencesGroup(title=display_path(source_path))
        n = len(rules)
        group.set_description(f"{n} rule{'' if n == 1 else 's'}")
        for ext in rules:
            group.add(self._make_external_row(ext))
        return group

    def _make_external_row(self, ext: ExternalLayerRule) -> Adw.ActionRow:
        title, namespace_summary = summarize_rule(ext.rule)
        # Subtitle = namespace summary + line number. Path is in the
        # group title above; middle dot mirrors the window-rules page.
        subtitle = f"{namespace_summary}  ·  line {ext.lineno}"

        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.add_css_class("option-default")
        row.set_opacity(0.65)
        row.set_tooltip_text(f"{ext.source_path}:{ext.lineno}")

        prefix = Gtk.Image.new_from_icon_name("overlapping-windows-symbolic")
        prefix.set_opacity(0.4)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        # Lock icon as the only suffix — same pattern the window-rules
        # page uses for read-only entries. No "override" action because
        # Hyprland has no ``unlayerrule`` IPC.
        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock_icon.set_opacity(0.4)
        lock_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(lock_icon)

        return row

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

        prefix = Gtk.Image.new_from_icon_name("overlapping-windows-symbolic")
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

        Returns ``True`` if the keyword push succeeded (a toast has
        already been shown on failure).
        """
        return try_with_toast(
            self._window.show_toast,
            "Layer rule failed",
            lambda: self._window.hypr.keyword(KEYWORD_WRITE, rule.body()),
            catch=HyprlandError,
        )

    # ── Commit helpers (mutate SavedList + repaint) ──

    def _commit_appended(self, rule: LayerRule) -> None:
        """Add *rule* to the owned list as a new entry."""
        with self._undo_track():
            self._owned.append_new(rule)
        self._notify_dirty()
        self._rebuild_list()

    def _commit_replaced(self, idx: int, rule: LayerRule) -> None:
        """Replace the owned entry at *idx* with *rule*."""
        with self._undo_track():
            self._owned[idx] = rule
        self._notify_dirty()
        self._rebuild_list()

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

    def _on_delete_at(self, idx: int) -> None:
        # Hyprland has no ``unlayerrule`` IPC, so the rule itself stays
        # in the runtime list until the next reload — surfaces mapped
        # *after* this delete still see it. Save+reload is the escape
        # hatch.
        if idx < 0 or idx >= len(self._owned):
            return
        with self._undo_track():
            self._owned.pop_at(idx)
        self._notify_dirty()
        self._rebuild_list()

    def _discard_at(self, idx: int) -> None:
        """Revert a single rule to its saved value (or remove if unsaved)."""
        baseline = self._owned.get_baseline(idx)
        if baseline is None:
            self._on_delete_at(idx)
            return
        with self._undo_track():
            self._owned.discard_at(idx)
        # Re-push the baseline so the running compositor reflects the
        # restored value for any *new* surfaces. (Existing surfaces
        # picked up the dirty version while it was active; their state
        # reverts automatically on next frame for dynamic rules.)
        self._apply_rule_live(baseline)
        self._notify_dirty()
        self._rebuild_list()

    def _on_restore_deleted(self, item: LayerRule) -> None:
        """Restore a previously-deleted rule to its saved position.

        Routes through :meth:`SavedList.restore_deleted` so the row
        comes back with its saved baseline at the slot consistent with
        the saved order — a pure delete-then-restore round trip leaves
        the page non-dirty. The rule is also re-pushed to the running
        compositor so dynamic effects (blur, dim, ignorealpha) take
        effect again on the next frame.
        """
        with self._undo_track():
            self._owned.restore_deleted(item)
        self._notify_dirty()
        self._rebuild_list()
        self._apply_rule_live(item)

    # ── Save plumbing ──

    def get_layer_rule_lines(self) -> list[str]:
        """Serialize the current rules for ``config.write_all``.

        Order is preserved — placement matters for ``unset`` and for
        ``order N`` predictability, so the order users see in the UI
        is exactly what's written.
        """
        return serialize(list(self._owned))

    @staticmethod
    def has_managed_section(sections: dict[str, list[str]]) -> bool:
        """True if the saved config already contains any layer-rule lines."""
        return any(sections.get(kw) for kw in LAYER_RULE_KEYWORDS)


__all__ = ["LayerRulesPage"]
