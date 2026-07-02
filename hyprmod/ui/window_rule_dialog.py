"""Add/edit dialog for a single window rule.

Two halves:

1. **Match windows where…** — a list of matcher rows, each with a
   key dropdown (class, title, xwayland, …) and a value entry. Plus
   a "Pick from open window" button that auto-fills class regex
   from a currently-running window.
2. **Apply these actions** — a dynamic list of action blocks, each
   with a dropdown of common Hyprland v3 effects (float, opacity,
   size, no_blur, …) and effect-specific argument fields. A "Custom
   action…" entry covers anything we haven't catalogued, including
   plugin actions. Blocks can be added and removed freely; a rule
   carries one effect per block.

A live preview at the bottom shows the exact ``windowrule = …`` line
that will be written to the config — so users can see what the visual
editor will produce, and power users can verify it before applying.

The dialog is opened via :meth:`SingletonDialogMixin.present_singleton`,
not constructed directly.
"""

import re
from collections.abc import Callable

from gi.repository import Adw, Gtk
from hyprland_config import render_rule_hyprlang, render_rule_lua
from hyprland_socket import Window

from hyprmod.core import config
from hyprmod.core.window_rules import (
    ACTION_PRESETS,
    CUSTOM_MATCHER_KIND,
    CUSTOM_PRESET,
    MATCHER_KINDS,
    MATCHER_KINDS_BY_KEY,
    RAW_KEY,
    ActionField,
    ActionPreset,
    Effect,
    Matcher,
    MatcherKind,
    WindowRule,
    lookup_matcher_kind,
    lookup_preset,
)
from hyprmod.ui import build_preview_group
from hyprmod.ui.dialog import SingletonDialogMixin
from hyprmod.ui.window_picker import WindowPickerDialog


def _escape_regex(value: str) -> str:
    """Wrap a plain string into an exact-match RE2 regex.

    Used by the "Pick from open window" path: a window's class is
    typically a fixed identifier (``firefox``, ``org.kde.dolphin``),
    so anchoring it as ``^(escaped)$`` matches that one app and
    nothing else. Users can loosen the regex afterwards if they want
    to match a family of classes.
    """
    return f"^({re.escape(value)})$"


def _preview_for(rule: WindowRule) -> str:
    """Render *rule* in the active mode's syntax for the dialog preview.

    Builds the structured :class:`hyprland_config.Rule` node and hands
    it to the right language-specific renderer — Lua mode picks
    :func:`render_rule_lua` (one ``hl.window_rule({…})`` call),
    Hyprlang mode picks :func:`render_rule_hyprlang` (block when
    name/disabled, single-line otherwise). Both routes match what
    would actually hit disk so the preview is byte-faithful.
    """
    node = rule.to_rule_node()
    if config.is_lua_mode():
        return render_rule_lua(node)
    return render_rule_hyprlang(node).rstrip("\n")


class WindowRuleEditDialog(SingletonDialogMixin, Adw.Dialog):
    """Add/edit dialog for a single window rule."""

    def __init__(
        self,
        *,
        rule: WindowRule | None = None,
        on_apply: Callable[[WindowRule], None] | None = None,
    ):
        super().__init__()
        self._is_new = rule is None
        self._on_apply_callback = on_apply
        self._picked_window: Window | None = None

        # Matcher rows are tracked imperatively so Add/Remove and the
        # preview rebuild can find each row's current values. Each row
        # carries a kind + key/value widgets exposed via the
        # ``_MatcherRow`` helper class.
        self._matcher_rows: list[_MatcherRow] = []
        self._matchers_listbox: Gtk.ListBox

        # Action picker state. We maintain a list of _ActionBlock helper
        # instances, each corresponding to one effect in the rule.
        self._action_blocks: list["_ActionBlock"] = []
        self._actions_box: Gtk.Box
        self._presets: tuple[ActionPreset, ...] = (*ACTION_PRESETS, CUSTOM_PRESET)

        # Live-preview label updated on every form change.
        self._preview_label: Gtk.Label

        # Pass-through state for the rule's optional name and enabled
        # flag — set via the Name section UI; preserved unchanged when
        # editing an anonymous rule.
        self._rule_name: str = ""
        self._rule_enabled: bool = True

        self.set_title("New Window Rule" if self._is_new else "Edit Window Rule")
        self.set_content_width(560)
        self.set_content_height(640)

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._apply_btn = Gtk.Button(label="Apply")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        self._apply_btn.set_sensitive(False)
        header.pack_end(self._apply_btn)
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(720)
        clamp.set_tightening_threshold(560)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        content.append(self._build_name_section())
        content.append(self._build_match_section())
        content.append(self._build_apply_section())
        content.append(self._build_preview_section())

        clamp.set_child(content)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)

        # Hydrate from the rule being edited (or seed with a default
        # matcher row + the first action for new rules).
        if rule is not None:
            self._load_from_rule(rule)
        else:
            self._add_matcher_row(MATCHER_KINDS[0])
            self._add_action_block(ACTION_PRESETS[0])

        self._refresh()

    # ── Section builders ──────────────────────────────────────────────

    def _build_name_section(self) -> Gtk.Widget:
        """Optional ``Name`` row plus disabled toggle for block-form rules.

        A name promotes the rule from anonymous to named — Hyprland's
        Lua API and ``hyprctl`` can then reference it for dynamic
        enable/disable. Leaving the name blank keeps the rule
        anonymous and emits the compact single-line syntax.
        """
        group = Adw.PreferencesGroup(title="Name (optional)")
        group.set_description(
            "Naming a rule lets you enable / disable it at runtime via "
            "Hyprland's Lua API or hyprctl. Anonymous rules are written "
            "as the compact one-line form."
        )

        self._name_entry = Adw.EntryRow(title="Name")
        self._name_entry.set_text(self._rule_name)
        self._name_entry.connect("changed", self._on_name_changed)
        group.add(self._name_entry)

        self._enabled_row = Adw.SwitchRow(
            title="Enabled",
            subtitle="Uncheck to keep the rule defined but inactive on next reload.",
        )
        self._enabled_row.set_active(self._rule_enabled)
        self._enabled_row.connect("notify::active", self._on_enabled_changed)
        group.add(self._enabled_row)

        return group

    def _build_match_section(self) -> Gtk.Widget:
        """The 'Match windows where…' group with matcher rows + add buttons."""
        group = Adw.PreferencesGroup(title="Match windows where…")
        group.set_description(
            "Add one or more conditions. Hyprland matches windows where ALL conditions apply."
        )

        # Header-suffix buttons: pick-from-window (the high-leverage
        # shortcut) and add-condition (the manual fallback).
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        pick_btn = Gtk.Button.new_from_icon_name("system-search-symbolic")
        pick_btn.set_valign(Gtk.Align.CENTER)
        pick_btn.add_css_class("flat")
        pick_btn.set_tooltip_text("Pick from an open window")
        pick_btn.connect("clicked", lambda _b: self._on_pick_window())
        button_box.append(pick_btn)

        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add a condition")
        add_btn.connect("clicked", lambda _b: self._on_add_matcher())
        button_box.append(add_btn)

        group.set_header_suffix(button_box)

        self._matchers_listbox = Gtk.ListBox()
        self._matchers_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._matchers_listbox.add_css_class("boxed-list")
        group.add(self._matchers_listbox)

        return group

    def _build_apply_section(self) -> Gtk.Widget:
        """The 'Apply this action' section with action blocks + add button."""
        group = Adw.PreferencesGroup(title="Apply these actions")
        group.set_description("Pick what Hyprland should do when a matching window opens.")

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add an action")
        add_btn.connect("clicked", lambda _b: self._on_add_action())
        button_box.append(add_btn)

        group.set_header_suffix(button_box)

        self._actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.append(group)
        outer.append(self._actions_box)
        return outer

    def _build_preview_section(self) -> Gtk.Widget:
        """The bottom preview: shows the actual config line that will be written."""
        group, self._preview_label = build_preview_group()
        return group

    # ── Hydration / loading from an existing rule ─────────────────────

    def _load_from_rule(self, rule: WindowRule) -> None:
        """Populate widgets from an existing ``WindowRule`` for editing."""
        # Block handler signals while seeding so the changed callbacks
        # don't fire on hydration (they'd treat the load as a user edit
        # and trigger a redundant preview refresh).
        self._rule_name = rule.name
        self._rule_enabled = rule.enabled

        if hasattr(self, "_name_entry"):
            self._name_entry.handler_block_by_func(self._on_name_changed)
            self._name_entry.set_text(rule.name)
            self._name_entry.handler_unblock_by_func(self._on_name_changed)
        if hasattr(self, "_enabled_row"):
            self._enabled_row.handler_block_by_func(self._on_enabled_changed)
            self._enabled_row.set_active(rule.enabled)
            self._enabled_row.handler_unblock_by_func(self._on_enabled_changed)
        # Matchers first — the dropdown field rebuild reads them when
        # computing the preview, so seeding them ahead of the effect
        # avoids a flash of "no matchers" during dialog open.
        if not rule.matchers:
            # An invalid existing rule (no matchers) shouldn't open with
            # an empty matcher list — the apply gate would lock the user
            # out. Seed a class row so they can fix it.
            self._add_matcher_row(MATCHER_KINDS[0])
        for m in rule.matchers:
            kind = lookup_matcher_kind(m.key)
            # When a matcher's key is parseable but not in our catalog
            # (e.g. ``match:xdg_tag value`` or some plugin-specific
            # key), ``lookup_matcher_kind`` falls through to Custom.
            # Surface the *full* ``match:KEY VALUE`` token in the
            # value field so save+reload round-trips byte-for-byte —
            # otherwise the ``match:KEY`` prefix would be silently
            # dropped on save.
            if kind is CUSTOM_MATCHER_KIND and m.key != RAW_KEY:
                value = f"match:{m.key} {m.value}"
            else:
                value = m.value
            self._add_matcher_row(kind, value=value, original_key=m.key)

        # Effects: load each into its own action block.
        if not rule.effects:
            self._add_action_block(ACTION_PRESETS[0])
        for effect in rule.effects:
            preset = lookup_preset(effect.name)
            if preset is CUSTOM_PRESET:
                self._add_action_block(preset, args_str=effect.full)
            else:
                self._add_action_block(preset, args_str=effect.args)

    # ── Matcher row management ────────────────────────────────────────

    def _add_matcher_row(
        self,
        kind: MatcherKind,
        *,
        value: str = "",
        original_key: str = "",
    ) -> None:
        """Append a new matcher row to the list."""
        row = _MatcherRow(
            initial_kind=kind,
            initial_value=value,
            original_key=original_key or kind.key,
            on_remove=self._on_remove_matcher,
            on_changed=self._refresh,
            on_kind_changed=self._on_matcher_kind_changed,
        )
        self._matcher_rows.append(row)
        self._matchers_listbox.append(row.widget)

        if self._picked_window and not value:
            self._autofill_row(row, self._picked_window)

    def _on_add_matcher(self) -> None:
        # Default new rows to "Class" — overwhelmingly the most common
        # matcher people reach for.
        self._add_matcher_row(MATCHER_KINDS[0])
        self._refresh()

    def _on_matcher_kind_changed(self, row: "_MatcherRow", old_key: str, old_value: str) -> None:
        window = self._picked_window
        if window is None:
            return
        # Refill for the new kind only when there is nothing of the
        # user's to lose: the previous value was empty or still the
        # untouched autofill, or nothing carried into the new widget.
        untouched = not old_value or old_value == self._autofill_value_for(old_key, window)
        if untouched or not row.read_matcher().value:
            self._autofill_row(row, window)

    def _on_remove_matcher(self, row: "_MatcherRow") -> None:
        # Always keep at least one matcher row — Hyprland rejects
        # bare ``windowrulev2 = float`` and the apply button would lock,
        # so we replace the last row with a fresh blank rather than
        # going to zero.
        if len(self._matcher_rows) <= 1:
            self._reset_last_matcher_row()
            self._refresh()
            return
        self._matcher_rows.remove(row)
        self._matchers_listbox.remove(row.widget)
        self._refresh()

    def _reset_last_matcher_row(self) -> None:
        """Reset the (only) remaining matcher row to a blank Class row."""
        if not self._matcher_rows:
            self._add_matcher_row(MATCHER_KINDS[0])
            return
        last = self._matcher_rows[0]
        last.set_kind(MATCHER_KINDS[0])
        last.set_value("")

    # ── Action block management ───────────────────────────────────────

    def _add_action_block(self, preset: ActionPreset, *, args_str: str = "") -> None:
        block = _ActionBlock(
            presets=self._presets,
            initial_preset=preset,
            initial_args=args_str,
            on_remove=self._on_remove_action,
            on_changed=self._refresh,
        )
        self._action_blocks.append(block)
        self._actions_box.append(block.widget)

    def _on_add_action(self) -> None:
        self._add_action_block(ACTION_PRESETS[0])
        self._refresh()

    def _on_remove_action(self, block: "_ActionBlock") -> None:
        if len(self._action_blocks) <= 1:
            self._reset_last_action_block()
            self._refresh()
            return
        self._action_blocks.remove(block)
        self._actions_box.remove(block.widget)
        self._refresh()

    def _reset_last_action_block(self) -> None:
        if not self._action_blocks:
            self._add_action_block(ACTION_PRESETS[0])
            return
        last = self._action_blocks[0]
        last.set_preset(ACTION_PRESETS[0], "")

    # ── Pick-from-window ──────────────────────────────────────────────

    def _on_pick_window(self) -> None:
        def on_pick(window: Window) -> None:
            self._picked_window = window
            self._apply_picked_window(window)

        WindowPickerDialog.present_singleton(self, on_pick=on_pick)

    def _autofill_row(self, row: "_MatcherRow", window: Window) -> None:
        """Autofill a single row from the picked window."""
        value = self._autofill_value_for(row.read_matcher().key, window)
        if value is not None:
            row.set_value(value)

    def _autofill_value_for(self, key: str, window: Window) -> str | None:
        """Value the picked window suggests for a matcher *key*, or None."""
        if key == "class" and window.class_name:
            return _escape_regex(window.class_name)
        elif key == "title" and window.title:
            return _escape_regex(window.title)
        elif key == "initial_class" and window.initial_class:
            return _escape_regex(window.initial_class)
        elif key == "initial_title" and window.initial_title:
            return _escape_regex(window.initial_title)
        elif key == "xwayland":
            return "true" if window.xwayland else "false"
        elif key == "float":
            return "true" if window.floating else "false"
        elif key == "fullscreen":
            return "true" if window.fullscreen else "false"
        elif key == "pin":
            return "true" if window.pinned else "false"
        elif key == "workspace" and window.workspace_id > 0:
            # -1 is the model's "unset" sentinel; named workspaces have negative ids.
            return str(window.workspace_id)
        return None

    def _apply_picked_window(self, window: Window) -> None:
        """Replace the current matcher rows with class+title from the picked window.

        Picking is treated as a "start over" gesture — anything the
        user typed before is replaced. This is less surprising than
        appending: the most common picker flow is "I want to make a
        rule for THIS window," not "add THIS as a clause to an
        existing rule."
        """
        for row in list(self._matcher_rows):
            self._matchers_listbox.remove(row.widget)
        self._matcher_rows.clear()

        if window.class_name:
            self._add_matcher_row(
                MATCHER_KINDS_BY_KEY["class"],
                value=_escape_regex(window.class_name),
            )
        # Title is usually too volatile to be useful as an exact match
        # (browser tab changes change the whole title), so we only
        # add it when class is empty — better to give the user one
        # solid hook and let them add more if they want.
        elif window.title:
            self._add_matcher_row(
                MATCHER_KINDS_BY_KEY["title"],
                value=_escape_regex(window.title),
            )
        else:
            self._add_matcher_row(MATCHER_KINDS[0])

        self._refresh()

    # ── Refresh: preview + apply gating ───────────────────────────────

    def _on_name_changed(self, *_args: object) -> None:
        self._rule_name = self._name_entry.get_text().strip()
        self._refresh()

    def _on_enabled_changed(self, *_args: object) -> None:
        self._rule_enabled = self._enabled_row.get_active()
        self._refresh()

    def _refresh(self) -> None:
        rule = self._build_rule()
        if rule is None:
            self._preview_label.set_text("(rule incomplete)")
        else:
            self._preview_label.set_text(_preview_for(rule))
        # Apply gates on a non-empty effect name AND at least one
        # non-empty matcher. This rejects both halves of an incomplete
        # rule, both of which Hyprland would reject at runtime.
        ok = (
            rule is not None
            and bool(rule.effect_name)
            and any(m.value.strip() for m in rule.matchers)
        )
        self._apply_btn.set_sensitive(ok)

    def _build_rule(self) -> WindowRule | None:
        """Snapshot the current dialog state into a ``WindowRule``."""
        effects: list[Effect] = []
        for block in self._action_blocks:
            effect = block.read_effect()
            if not effect.name:
                continue
            effects.append(effect)

        if not effects:
            return None

        matchers: list[Matcher] = []
        for row in self._matcher_rows:
            matcher = row.read_matcher()
            # Drop fully-blank rows from serialization but keep the
            # widget around (the user may still be typing).
            if not matcher.value.strip():
                continue
            matchers.append(matcher)

        return WindowRule(
            matchers=matchers,
            effects=effects,
            name=self._rule_name,
            enabled=self._rule_enabled,
        )

    # ── Apply ─────────────────────────────────────────────────────────

    def _on_apply(self, *_args: object) -> None:
        rule = self._build_rule()
        if rule is None or not rule.effect_name or not rule.matchers:
            # The apply gate should make this unreachable, but be
            # defensive — a stale signal could fire after a rebuild.
            return
        if self._on_apply_callback is not None:
            self._on_apply_callback(rule)
        self.close()


# ---------------------------------------------------------------------------
# Helper widget: a single matcher row
# ---------------------------------------------------------------------------


class _MatcherRow:
    """Single matcher row: dropdown of keys + value entry + remove button.

    Encapsulates the kind/key/value tri-state because the value widget
    type changes when the kind changes (regex/text → ``Gtk.Entry``,
    bool → ``Gtk.Switch``). Each row owns its widgets and exposes a
    :meth:`read_matcher` that returns the current ``Matcher`` snapshot.
    """

    # Build a dropdown model that is the catalog plus a Custom entry
    # at the end. Same shape pattern as the action dropdown so future
    # plugin matchers can land in Custom without UI churn.
    _KINDS_WITH_CUSTOM: tuple[MatcherKind, ...] = (*MATCHER_KINDS, CUSTOM_MATCHER_KIND)

    def __init__(
        self,
        *,
        initial_kind: MatcherKind,
        initial_value: str,
        original_key: str,
        on_remove: Callable[["_MatcherRow"], None],
        on_changed: Callable[[], None],
        on_kind_changed: Callable[["_MatcherRow", str, str], None],
    ):
        self._on_remove = on_remove
        self._on_changed = on_changed
        self._on_kind_changed_callback = on_kind_changed
        # ``_original_key`` only matters for matchers we couldn't
        # parse: when the user is editing a token like
        # ``plugin:foo:bar:baz`` (which is RAW because the parser
        # didn't strip the leading key), we want to preserve the raw
        # text on save — the dropdown stays on "Custom" and the value
        # field carries the full token.
        self._original_key = original_key
        self._kind: MatcherKind = initial_kind
        self._value_widget: Gtk.Widget

        self._row = Adw.ActionRow()
        self._row.set_title("")  # title space used by the kind dropdown
        self._row.add_css_class("matcher-row")

        # Kind dropdown — narrow column on the left so the value field
        # gets the room. Using ``Gtk.DropDown`` directly (not ComboRow)
        # because we want it inline with the other suffixes, not as the
        # row's primary content.
        labels = Gtk.StringList.new([k.label for k in _MatcherRow._KINDS_WITH_CUSTOM])
        self._kind_dropdown = Gtk.DropDown(model=labels)
        self._kind_dropdown.set_valign(Gtk.Align.CENTER)
        self._kind_dropdown.set_size_request(180, -1)
        try:
            initial_idx = _MatcherRow._KINDS_WITH_CUSTOM.index(initial_kind)
        except ValueError:
            initial_idx = len(_MatcherRow._KINDS_WITH_CUSTOM) - 1
        self._kind_dropdown.set_selected(initial_idx)
        self._kind_dropdown.connect("notify::selected", self._on_kind_changed)
        self._row.add_prefix(self._kind_dropdown)

        # Value widget — built fresh on every kind change because the
        # widget *type* depends on the kind (entry vs. switch).
        self._value_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._value_box.set_hexpand(True)
        self._value_box.set_valign(Gtk.Align.CENTER)
        self._value_widget = self._build_value_widget(initial_kind, initial_value)
        self._value_box.append(self._value_widget)
        self._row.add_suffix(self._value_box)

        # Remove button — small flat icon, last position so the user's
        # eye lands on the value field first.
        remove_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text("Remove this condition")
        remove_btn.connect("clicked", lambda _b: self._on_remove(self))
        self._row.add_suffix(remove_btn)

        # v3 Hyprland encodes regex negation as a ``negative:`` prefix
        # on the value (e.g. ``match:class negative:firefox``), not as
        # a separate flag on the matcher. Users can type that prefix
        # manually; surfacing a checkbox is a future polish item.

    @property
    def widget(self) -> Gtk.Widget:
        return self._row

    # ── Public mutators (used by the parent dialog's reset path) ──

    def set_kind(self, kind: MatcherKind) -> None:
        try:
            idx = _MatcherRow._KINDS_WITH_CUSTOM.index(kind)
        except ValueError:
            idx = len(_MatcherRow._KINDS_WITH_CUSTOM) - 1
        self._kind_dropdown.handler_block_by_func(self._on_kind_changed)
        self._kind_dropdown.set_selected(idx)
        self._kind_dropdown.handler_unblock_by_func(self._on_kind_changed)
        self._swap_value_widget(kind, "")

    def set_value(self, value: str) -> None:
        if isinstance(self._value_widget, Gtk.Entry):
            self._value_widget.set_text(value)
        elif isinstance(self._value_widget, Gtk.Switch):
            # v3 boolean matchers use ``true``/``false`` (also accepts
            # ``yes``/``no``/``1``/``0``); we canonicalise to ``true``.
            self._value_widget.set_active(value.strip().lower() in {"1", "true", "yes", "on"})

    # ── Reading current state ──

    def read_matcher(self) -> Matcher:
        """Return a ``Matcher`` snapshot of the current widget state."""
        if self._kind is CUSTOM_MATCHER_KIND:
            text = (
                self._value_widget.get_text() if isinstance(self._value_widget, Gtk.Entry) else ""
            )
            # Custom holds opaque text — round-trip as a RAW token so
            # whatever the user typed (``match:foo bar``, plugin
            # tokens, …) survives serialization byte-for-byte.
            return Matcher(key=RAW_KEY, value=text)

        if self._kind.value_kind == "bool":
            value = (
                "true"
                if (isinstance(self._value_widget, Gtk.Switch) and self._value_widget.get_active())
                else "false"
            )
            return Matcher(key=self._kind.key, value=value)

        text = self._value_widget.get_text() if isinstance(self._value_widget, Gtk.Entry) else ""
        return Matcher(key=self._kind.key, value=text)

    # ── Internal: kind change rebuilds the value widget ──

    def _on_kind_changed(self, *_args: object) -> None:
        idx = self._kind_dropdown.get_selected()
        if idx < 0 or idx >= len(_MatcherRow._KINDS_WITH_CUSTOM):
            return
        new_kind = _MatcherRow._KINDS_WITH_CUSTOM[idx]
        if new_kind is self._kind:
            return
        # Carry the existing text across kind changes — switching
        # between class/title both keep the regex value, which is
        # what the user usually wants. Bool values never carry into
        # a text kind: a literal ``false`` in a regex entry is noise.
        old_key = self._kind.key
        old_value = ""
        carry = ""
        if isinstance(self._value_widget, Gtk.Entry):
            old_value = self._value_widget.get_text()
            carry = old_value
        elif isinstance(self._value_widget, Gtk.Switch):
            old_value = "true" if self._value_widget.get_active() else "false"
            if new_kind.value_kind == "bool":
                carry = old_value
        self._swap_value_widget(new_kind, carry)
        self._on_kind_changed_callback(self, old_key, old_value)
        self._on_changed()

    def _swap_value_widget(self, kind: MatcherKind, initial_value: str) -> None:
        # Drop the old widget and replace with one matching the new kind.
        self._value_box.remove(self._value_widget)
        self._value_widget = self._build_value_widget(kind, initial_value)
        self._value_box.append(self._value_widget)
        self._kind = kind

    def _build_value_widget(self, kind: MatcherKind, initial_value: str) -> Gtk.Widget:
        if kind.value_kind == "bool":
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            # v3 accepts ``true``/``false``/``yes``/``no``/``1``/``0``;
            # we canonicalise to ``true``/``false`` on output.
            switch.set_active(initial_value.strip().lower() in {"1", "true", "yes", "on"})
            switch.connect("notify::active", lambda *_: self._on_changed())
            return switch

        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_valign(Gtk.Align.CENTER)
        if kind.placeholder:
            entry.set_placeholder_text(kind.placeholder)
        entry.set_text(initial_value)
        entry.connect("changed", lambda *_: self._on_changed())
        return entry


# ---------------------------------------------------------------------------
# Helper widget: a single action block
# ---------------------------------------------------------------------------


class _ActionBlock:
    """Helper representing a single Action in the UI.

    Contains the preset ComboRow, its description as a subtitle, and its argument fields,
    wrapped in a single Adw.PreferencesGroup for a cohesive container.
    """

    def __init__(
        self,
        *,
        presets: tuple[ActionPreset, ...],
        initial_preset: ActionPreset,
        initial_args: str,
        on_remove: Callable[["_ActionBlock"], None],
        on_changed: Callable[[], None],
    ):
        self._presets = presets
        self._on_remove = on_remove
        self._on_changed = on_changed
        self._preset = initial_preset

        self._group = Adw.PreferencesGroup()
        self._group.add_css_class("action-block")

        # Action selector
        self._action_dropdown = Adw.ComboRow(title="Action")
        labels = Gtk.StringList.new([p.label for p in self._presets])
        self._action_dropdown.set_model(labels)
        self._action_dropdown.set_subtitle_lines(2)

        # Remove button as a suffix on the ComboRow
        remove_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text("Remove this action")
        remove_btn.connect("clicked", lambda _b: self._on_remove(self))
        self._action_dropdown.add_suffix(remove_btn)

        idx = self._presets.index(initial_preset) if initial_preset in self._presets else 0
        self._action_dropdown.set_selected(idx)
        self._action_dropdown.connect("notify::selected", self._on_action_changed)
        self._group.add(self._action_dropdown)

        self._action_field_widgets: list[Gtk.Widget] = []
        self._render_action_fields(initial_preset, args_str=initial_args)

    @property
    def widget(self) -> Gtk.Widget:
        return self._group

    def set_preset(self, preset: ActionPreset, args_str: str = "") -> None:
        idx = self._presets.index(preset) if preset in self._presets else 0
        self._action_dropdown.handler_block_by_func(self._on_action_changed)
        self._action_dropdown.set_selected(idx)
        self._action_dropdown.handler_unblock_by_func(self._on_action_changed)
        self._render_action_fields(preset, args_str=args_str)

    def _on_action_changed(self, *_args: object) -> None:
        idx = self._action_dropdown.get_selected()
        if idx < 0 or idx >= len(self._presets):
            return
        preset = self._presets[idx]
        if preset is self._preset:
            return
        self._render_action_fields(preset)
        self._on_changed()

    def _render_action_fields(self, preset: ActionPreset, *, args_str: str = "") -> None:
        for widget in self._action_field_widgets:
            self._group.remove(widget)
        self._action_field_widgets = []

        self._preset = preset
        self._action_dropdown.set_subtitle(preset.description)

        if not preset.fields:
            return

        if preset is CUSTOM_PRESET:
            initial_values = [args_str] if args_str else [""]
        else:
            parsed = preset.parse_args(args_str) if args_str else None
            initial_values = parsed if parsed is not None else [f.default for f in preset.fields]

        for field, initial in zip(preset.fields, initial_values, strict=False):
            widget = self._build_action_field_widget(field, initial)
            self._action_field_widgets.append(widget)
            self._group.add(widget)

    def _build_action_field_widget(self, field: ActionField, initial: str) -> Gtk.Widget:
        if field.kind == "number":
            row = Adw.SpinRow.new_with_range(field.min_value, field.max_value, field.step)
            row.set_title(field.label)
            if field.hint:
                row.set_subtitle(field.hint)
            row.set_digits(field.digits)
            try:
                row.set_value(float(initial) if initial else float(field.default or "0"))
            except ValueError:
                row.set_value(float(field.default or "0"))
            row.connect("notify::value", lambda *_: self._on_changed())
            return row

        row = Adw.EntryRow(title=field.label)
        if field.hint:
            row.set_tooltip_text(field.hint)
        row.set_text(initial)
        row.connect("changed", lambda *_: self._on_changed())
        return row

    def read_effect(self) -> Effect:
        if self._preset is CUSTOM_PRESET:
            values = self._read_action_fields()
            full = values[0].strip() if values else ""
            effect_name, _, effect_args = full.partition(" ")
            return Effect(name=effect_name.strip(), args=effect_args.strip())

        effect_name = self._preset.id
        effect_args = self._preset.format(self._read_action_fields())
        return Effect(name=effect_name, args=effect_args)

    def _read_action_fields(self) -> list[str]:
        result: list[str] = []
        for widget in self._action_field_widgets:
            if isinstance(widget, Adw.SpinRow):
                value = widget.get_value()
                digits = widget.get_digits()
                if digits == 0:
                    result.append(str(int(value)))
                else:
                    result.append(f"{value:.{digits}f}")
            elif isinstance(widget, Adw.EntryRow):
                result.append(widget.get_text())
            else:
                result.append("")
        return result


__all__ = ["WindowRuleEditDialog"]
