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

from gi.repository import Adw, Gdk, GLib, Gtk
from hyprland_socket import HyprlandError

from hyprmod.core import config
from hyprmod.core.layer_rules import (
    KEYWORD_WRITE,
    LAYER_RULE_KEYWORDS,
    ExternalLayerRule,
    LayerRule,
    count_pending_changes,
    detect_reorder,
    load_external_layer_rules,
    parse_layer_rule_lines,
    serialize,
    summarize_rule,
)
from hyprmod.core.ownership import SavedList
from hyprmod.core.setup import HYPRLAND_CONF
from hyprmod.core.undo import LayerRulesUndoEntry
from hyprmod.pages.section import SectionPage
from hyprmod.ui import clear_children, display_path, make_page_layout, try_with_toast
from hyprmod.ui.layer_rule_dialog import LayerRuleEditDialog
from hyprmod.ui.row_actions import RowActions


class LayerRulesPage(SectionPage):
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
        return LayerRulesUndoEntry(
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
                # Defer to idle so the row is mapped before grab_focus.
                # The callback returns SOURCE_REMOVE to one-shot it —
                # ``grab_focus`` returns True, which an idle handler
                # would otherwise read as "fire me again".
                GLib.idle_add(self._grab_focus_once, target)

    @staticmethod
    def _grab_focus_once(widget: Gtk.Widget) -> bool:
        widget.grab_focus()
        return GLib.SOURCE_REMOVE

    def _build_empty_state(self) -> Adw.StatusPage:
        """Empty-state page with a single "Add Rule" button.

        Unlike the window-rules empty state, there's no "Pick from open
        window" path — ``hyprland-socket`` doesn't expose a layers
        query, and even if it did, the user-recognisable identity for a
        layer surface is its namespace (``waybar``, ``rofi``), not a
        running window the user can point at.
        """
        empty = Adw.StatusPage(
            title="No Layer Rules",
            description=(
                "Tweak how shell surfaces (waybar, notifications, rofi, wallpapers) "
                "are decorated — backdrop blur, dim-around, animations, render order."
            ),
            icon_name="overlapping-windows-symbolic",
        )

        button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
        )

        add_btn = Gtk.Button(label="Add Rule…")
        add_btn.add_css_class("suggested-action")
        add_btn.add_css_class("pill")
        add_btn.connect("clicked", lambda _b: self._on_add())
        button_box.append(add_btn)

        empty.set_child(button_box)
        return empty

    def _build_order_hint(self) -> Gtk.Widget:
        """Inline note: explains how rule order interacts with ``unset``."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(4)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.5)
        icon.set_valign(Gtk.Align.START)
        box.append(icon)

        label = Gtk.Label(
            label=(
                "Rules accumulate per surface. ‘unset’ clears every prior rule for the "
                "matched namespace — place it first when you want to start fresh. "
                "Reorder with Alt+↑ / Alt+↓ on a focused row."
            ),
        )
        label.set_wrap(True)
        label.set_xalign(0)
        # Without ``hexpand=True`` the label settles at its preferred
        # (narrow) width, so longer copy wraps early and looks like a
        # column instead of a paragraph.
        label.set_hexpand(True)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
        return box

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
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(4)

        icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        icon.set_opacity(0.5)
        icon.set_valign(Gtk.Align.START)
        box.append(icon)

        label = Gtk.Label(
            label=(
                "Rules below come from your hyprland.conf or its sourced files. "
                "Edit those files directly to change them — hyprmod doesn't "
                "manage rules outside its own file."
            ),
        )
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_hexpand(True)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
        return box

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

    def _deleted_baselines(self) -> list[LayerRule]:
        """Return saved rules that are no longer in the owned list."""
        current = {r.to_line() for r in self._owned}
        return [b for b in self._owned.saved if b.to_line() not in current]

    # ── Reorder (Alt+arrow keyboard shortcut) ──

    def _attach_keyboard_reorder(self, row: Adw.ActionRow, idx: int) -> None:
        """Bind Alt+Up / Alt+Down on *row* to move it within the list."""
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_row_key_pressed, idx)
        row.add_controller(controller)

    def _on_row_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        idx: int,
    ) -> bool:
        # Require Alt only — Shift/Ctrl/Super combos are reserved for
        # future shortcuts (e.g. Alt+Shift+Up = move-to-top).
        wanted = Gdk.ModifierType.ALT_MASK
        relevant = (
            Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if state & relevant != wanted:
            return False

        if keyval == Gdk.KEY_Up:
            delta = -1
        elif keyval == Gdk.KEY_Down:
            delta = 1
        else:
            return False
        return self._move_relative(idx, delta)

    def _move_relative(self, idx: int, delta: int) -> bool:
        """Move the rule at *idx* by *delta* slots."""
        target = idx + delta
        n = len(self._owned)
        if target < 0 or target >= n or idx == target:
            return False
        with self._undo_track():
            self._owned.move(idx, target)
        self._notify_dirty()
        self._rebuild_list(focus_idx=target)
        return True

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

    # ── Reorder helpers (queried by pages/pending.py) ──

    def is_reordered(self) -> bool:
        """True if the *common* items between saved and current differ in order."""
        return detect_reorder(self._owned.saved, list(self._owned))

    def pending_change_count(self) -> int:
        """Number of distinct pending-change entries the page would surface."""
        if not self.is_dirty():
            return 0
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        return count_pending_changes(self._owned.saved, list(self._owned), baselines)

    def revert_reorder(self) -> None:
        """Restore the saved order while preserving other dirty changes.

        Same algorithm as :meth:`WindowRulesPage.revert_reorder`:

        - Items present in both saved and current are repositioned to
          their saved-order slots; in-flight value edits are kept.
        - Newly-added items (no baseline) keep their values and slot
          in at the end.
        - Items the user removed stay removed.

        Pushes a single undo entry so Ctrl+Z restores the pre-revert
        order in one step.
        """
        by_saved_line: dict[str, tuple[LayerRule, LayerRule | None]] = {}
        new_pairs: list[tuple[LayerRule, LayerRule | None]] = []

        for idx in range(len(self._owned)):
            item = self._owned[idx]
            baseline = self._owned.get_baseline(idx)
            if baseline is None:
                new_pairs.append((item, baseline))
            else:
                by_saved_line[baseline.to_line()] = (item, baseline)

        rebuilt_items: list[LayerRule] = []
        rebuilt_baselines: list[LayerRule | None] = []
        for saved in self._owned.saved:
            pair = by_saved_line.get(saved.to_line())
            if pair is None:
                continue
            item, baseline = pair
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)
        for item, baseline in new_pairs:
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)

        with self._undo_track():
            self._owned.restore(rebuilt_items, rebuilt_baselines)
        self._notify_dirty()
        self._rebuild_list()

    # ── SectionPage protocol ──

    def is_dirty(self) -> bool:
        return self._owned.is_dirty()

    def mark_saved(self) -> None:
        self._owned.mark_saved()
        self._rebuild_list()

    def discard(self) -> None:
        self._owned.discard_all()
        self._rebuild_list()

    def reload_from_saved(self, saved_sections: dict[str, list[str]]) -> None:
        """Re-load baseline from the given saved sections (after profile switch)."""
        self._load(saved_sections)
        self._rebuild_list()

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
