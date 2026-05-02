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
from pathlib import Path

from gi.repository import Adw, GLib, Gtk
from hyprland_socket import HyprlandError, get_windows

from hyprmod.core import config
from hyprmod.core.ownership import SavedList
from hyprmod.core.setup import HYPRLAND_CONF
from hyprmod.core.undo import SavedListSnapshot
from hyprmod.core.window_rules import (
    ACTION_PRESETS,
    HYPRMOD_APP_ID,
    KEYWORD_WRITE,
    RETROACTIVE_EFFECTS,
    WINDOW_RULE_KEYWORDS,
    ExternalWindowRule,
    Matcher,
    WindowRule,
    existing_window_dispatchers,
    existing_window_revert_dispatchers,
    load_external_window_rules,
    matches_hyprmod,
    matches_window,
    parse_window_rule_lines,
    serialize,
    summarize_rule,
)
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import (
    clear_children,
    confirm,
    display_path,
    make_inline_hint,
    make_page_layout,
    try_with_toast,
)
from hyprmod.ui.row_actions import RowActions
from hyprmod.ui.window_picker import WindowPickerDialog
from hyprmod.ui.window_rule_dialog import WindowRuleEditDialog


class WindowRulesPage(SavedListSectionPage[WindowRule]):
    """List editor for ``windowrule`` / ``windowrulev2`` entries."""

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
        # Rules from sourced config files outside our managed file —
        # surfaced read-only so users see the full picture. Rebuilt on
        # every load (including profile switches), since their content
        # can change without our involvement.
        self._external: list[ExternalWindowRule] = []
        # Maps each owned-list index to the row widget currently
        # representing it. Same shape as the autostart page — pre-sized
        # to ``None`` and filled in as ``_make_row`` runs, so a
        # freshly-rebuilt list briefly has ``None`` entries before all
        # rows are constructed. Used by the keyboard reorder path
        # (Alt+Up/Down) to refocus the moved row post-rebuild for
        # chained shortcuts.
        self._rows_by_idx: list[Adw.ActionRow | None] = []
        self._load(saved_sections)

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        if saved_sections is None:
            _, saved_sections = config.read_all_sections()
        # Read both ``windowrule`` and ``windowrulev2`` so users with
        # hand-rolled lines in either form see them in the UI. Any v2
        # lines have already been rewritten to v3 in memory by
        # ``hyprland_config.migrate()`` upstream in ``read_all_sections``,
        # so the ``windowrulev2`` bucket is normally empty — collecting
        # it is just defence in depth for callers that pass us a
        # pre-built ``saved_sections`` from an unmigrated source. The
        # write path always emits v3 ``windowrule`` (see KEYWORD_WRITE).
        raw_lines = config.collect_section(saved_sections, *WINDOW_RULE_KEYWORDS)
        items = parse_window_rule_lines(raw_lines)
        self._owned = SavedList(items, key=lambda r: r.to_line())
        # Surface any windowrule lines that live in the user's
        # hyprland.conf or any file it sources (other than our managed
        # file). These are advisory display only — Hyprland has no IPC
        # to remove individual rules, so we can't offer an "override"
        # action like the Binds page does. The escape hatch for the
        # user is to edit the source file directly.
        self._external = load_external_window_rules(HYPRLAND_CONF, config.gui_conf())

    # ── Undo / Redo ──

    def _capture_undo(self):
        return self._owned.snapshot()

    def _undo_key(self) -> list[str]:
        return [r.to_line() for r in self._owned]

    def _build_undo_entry(self, old, new):
        old_items, old_baselines = old
        new_items, new_baselines = new
        return SavedListSnapshot(
            page_attr="_window_rules_page",
            old_items=old_items,
            new_items=new_items,
            old_baselines=old_baselines,
            new_baselines=new_baselines,
        )

    def restore_snapshot(
        self,
        items: list[WindowRule],
        baselines: list[WindowRule | None],
    ) -> None:
        """Restore state from an undo/redo snapshot.

        Also syncs the runtime: rules that disappeared in this hop have
        their per-window setprop overrides reverted, rules that
        appeared get pushed. Without this, undo would silently leave
        a window's opacity / no_blur / etc. wherever the prior dirty
        edit had set it.
        """
        old_items = list(self._owned)
        self._owned.restore(items, baselines)
        self._sync_runtime_diff(old_items, items)
        self._rebuild_list()
        self._notify_dirty()

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

    def _rebuild_list(self, focus_idx: int = -1) -> None:
        clear_children(self._content_box)
        self._rows_by_idx = [None] * len(self._owned)

        # Reorder hint shown only when there are at least two entries.
        # Rule order matters in Hyprland (later rule wins), so calling
        # it out is more than just a discoverability nudge.
        if len(self._owned) >= 2:
            self._content_box.append(self._build_order_hint())

        if len(self._owned) > 0:
            self._content_box.append(self._build_group())

        # Surface deleted rules so the user can restore them before save.
        deleted = self._deleted_baselines()
        if deleted:
            self._content_box.append(self._build_deleted_group(deleted))

        # External rules from the user's hyprland.conf or sourced files
        # — read-only display with source-path provenance. Always at the
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

    def _build_empty_state(self) -> Adw.StatusPage:
        """Empty-state page with two prominent action buttons.

        Two paths surfaced upfront: "Pick from open window" (zero-friction
        for the common case "make a rule for THIS app") and "Add rule"
        (the manual path for rules that target windows that aren't
        currently running).
        """
        empty = Adw.StatusPage(
            title="No Window Rules",
            description=(
                "Make Hyprland treat specific windows differently — pin them, "
                "set opacity, open on a workspace, and more."
            ),
            icon_name="window-rules-symbolic",
        )

        button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
        )

        pick_btn = Gtk.Button(label="Pick from Open Window")
        pick_btn.add_css_class("suggested-action")
        pick_btn.add_css_class("pill")
        pick_btn.connect("clicked", lambda _b: self._on_pick_window())
        button_box.append(pick_btn)

        add_btn = Gtk.Button(label="Add Rule…")
        add_btn.add_css_class("pill")
        add_btn.connect("clicked", lambda _b: self._on_add())
        button_box.append(add_btn)

        empty.set_child(button_box)
        return empty

    def _build_order_hint(self) -> Gtk.Widget:
        """Inline note: explains that rule order matters and how to reorder."""
        return make_inline_hint(
            "Rules are evaluated top to bottom. When two rules match the "
            "same window, the lower one wins. Reorder with Alt+↑ / Alt+↓ "
            "on a focused row."
        )

    def _build_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Window Rules")
        n = len(self._owned)
        group.set_description(f"{n} rule{'' if n == 1 else 's'}")

        # Header-suffix add button mirrors the page-header one for users
        # who scrolled past the page header. Same handler, same dialog.
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add another rule")
        add_btn.connect("clicked", lambda _b: self._on_add())
        group.set_header_suffix(add_btn)

        for idx in range(len(self._owned)):
            group.add(self._make_row(idx, self._owned[idx]))
        return group

    def _build_deleted_group(self, deleted: list[WindowRule]) -> Adw.PreferencesGroup:
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

        Returns an inline hint explaining what the section is, followed
        by one :class:`Adw.PreferencesGroup` *per source file* — the
        file path appears once as the group title instead of being
        repeated on every row. Files are listed in document-walk order
        (the order Hyprland resolves sources), which matches what the
        user sees if they grep their config.
        """
        widgets: list[Gtk.Widget] = [self._build_external_hint()]

        # Group preserving first-seen order. ``find_all`` returns rules
        # in source-traversal order, so a plain dict (insertion-ordered)
        # gives us the right grouping for free.
        by_file: dict[Path, list[ExternalWindowRule]] = {}
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
        self, source_path: Path, rules: list[ExternalWindowRule]
    ) -> Adw.PreferencesGroup:
        """A PreferencesGroup containing every external rule from one file.

        Title = the path (relative to ``$HOME`` when possible),
        description = rule count. The path lives in the title rather
        than on every row so a 50-rule file doesn't fill the screen
        with redundant location text.
        """
        group = Adw.PreferencesGroup(title=display_path(source_path))
        n = len(rules)
        group.set_description(f"{n} rule{'' if n == 1 else 's'}")
        for ext in rules:
            group.add(self._make_external_row(ext))
        return group

    def _make_external_row(self, ext: ExternalWindowRule) -> Adw.ActionRow:
        title, matchers_summary = summarize_rule(ext.rule)
        # Subtitle = matcher summary + line number. The file path is
        # *not* duplicated here — it's the group title above.
        # Middle dot separator matches GNOME convention for inline
        # metadata.
        subtitle = f"{matchers_summary}  ·  line {ext.lineno}"

        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.add_css_class("option-default")
        row.set_opacity(0.65)
        # Tooltip carries the full path in case the user wants to
        # copy it without scrolling up to the group title.
        row.set_tooltip_text(f"{ext.source_path}:{ext.lineno}")

        # Window-rule icon prefix for visual parity with owned rows;
        # dimmer to signal "not yours to edit".
        prefix = Gtk.Image.new_from_icon_name("window-rules-symbolic")
        prefix.set_opacity(0.4)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        # Lock icon as the only suffix — same pattern the Binds page
        # uses for read-only entries, minus the "override" button (no
        # ``unwindowrule`` IPC, no clean override path; see this
        # module's docstring).
        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock_icon.set_opacity(0.4)
        lock_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(lock_icon)

        return row

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
        prefix = Gtk.Image.new_from_icon_name("window-rules-symbolic")
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

    def _apply_rule_live(self, rule: WindowRule) -> bool:
        """Apply *rule* to the running compositor.

        Two-step:

        1. ``hypr.keyword("windowrule", …)`` registers the rule so
           future windows pick it up.
        2. Walk currently-mapped windows and run the equivalent
           per-window dispatcher for each match — Hyprland resolves
           windowrules to per-window state at map time and never
           re-evaluates them against existing windows when a new rule
           arrives via IPC, so this step is what makes opacity / float
           / etc. visible on the windows the user already has open.

        Returns ``True`` if the keyword push succeeded (a toast has
        already been shown on failure).
        """
        ok = try_with_toast(
            self._window.show_toast,
            "Window rule failed",
            lambda: self._window.hypr.keyword(KEYWORD_WRITE, rule.body()),
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
        """Replicate *rule*'s effect on each already-mapped match.

        Bails immediately for effects we have no per-window mapping
        for, so we don't pay for an IPC ``get_windows`` round-trip
        on every (e.g.) ``stay_focused`` tweak.
        """
        if rule.effect_name not in RETROACTIVE_EFFECTS:
            return
        applied, error = self._foreach_matching_window(rule, existing_window_dispatchers)
        if error is not None:
            self._window.show_toast(
                f"Couldn't apply to existing windows — {error}",
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
        if rule.effect_name not in RETROACTIVE_EFFECTS:
            return
        _applied, error = self._foreach_matching_window(rule, existing_window_revert_dispatchers)
        if error is not None:
            self._window.show_toast(
                f"Couldn't revert on existing windows — {error}",
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

    # ── Commit helpers (mutate SavedList + repaint) ──

    def _commit_appended(self, rule: WindowRule) -> None:
        """Add *rule* to the owned list as a new entry."""
        with self._undo_track():
            self._owned.append_new(rule)
        self._notify_dirty()
        self._rebuild_list()

    def _commit_replaced(self, idx: int, rule: WindowRule) -> None:
        """Replace the owned entry at *idx* with *rule*."""
        with self._undo_track():
            self._owned[idx] = rule
        self._notify_dirty()
        self._rebuild_list()

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
            # always change. ``effect_args=""`` triggers the auto-``on``
            # for booleans on serialization.
            stub = WindowRule(
                matchers=matchers,
                effect_name=ACTION_PRESETS[0].id,
                effect_args="",
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
            self._maybe_apply_rule_live(new_rule)

        WindowRuleEditDialog.present_singleton(self._window, rule=current, on_apply=on_apply)

    def _on_delete_at(self, idx: int) -> None:
        # Hyprland has no "remove a single windowrule" IPC, so the rule
        # itself stays in the runtime list until the next reload —
        # *new* windows still see it. For *existing* windows we clear
        # the per-window setprop overrides so the visible state snaps
        # back to whatever Hyprland's rule resolver computed at map
        # time. Static effects (float, size, …) have no clean undo
        # and stay as-is until save+reload.
        if idx < 0 or idx >= len(self._owned):
            return
        removed = self._owned[idx]
        with self._undo_track():
            self._owned.pop_at(idx)
        self._revert_to_existing(removed)
        self._notify_dirty()
        self._rebuild_list()

    def _discard_at(self, idx: int) -> None:
        """Revert a single rule to its saved value (or remove if unsaved).

        Same caveat as :meth:`_on_delete_at` for the runtime rule
        list (no per-rule removal IPC), but per-window setprop
        overrides do get reset: we revert the dirty version's effect,
        then re-push the baseline so existing windows snap back to
        the saved state.
        """
        baseline = self._owned.get_baseline(idx)
        if baseline is None:
            self._on_delete_at(idx)
            return
        current = self._owned[idx]
        with self._undo_track():
            self._owned.discard_at(idx)
        self._sync_runtime_diff([current], [baseline])
        self._notify_dirty()
        self._rebuild_list()

    def _on_restore_deleted(self, item: WindowRule) -> None:
        """Restore a previously-deleted rule to its saved position.

        Routes through :meth:`SavedList.restore_deleted` so the row
        comes back with its saved baseline at the slot consistent with
        the saved order — a pure delete-then-restore round trip leaves
        the page non-dirty. The rule is also re-pushed to the running
        compositor through the same self-targeting gate as Add.
        """
        with self._undo_track():
            self._owned.restore_deleted(item)
        self._notify_dirty()
        self._rebuild_list()
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
        order users see in the UI is exactly what's written.
        """
        return serialize(list(self._owned))

    @staticmethod
    def has_managed_section(sections: dict[str, list[str]]) -> bool:
        """True if the saved config already contains any window-rule lines."""
        return any(sections.get(kw) for kw in WINDOW_RULE_KEYWORDS)


__all__ = ["WindowRulesPage"]
