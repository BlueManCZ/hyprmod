"""Add/edit dialog for a single window rule.

Two halves:

1. **Match windows where…** — a list of matcher rows, each with a
   key dropdown (class, title, xwayland, …) and a value entry. Plus
   a "Pick from open window" button that auto-fills class regex
   from a currently-running window.
2. **Apply this action** — a single dropdown of common Hyprland
   v3 effects (float, opacity, size, no_blur, …) with effect-specific
   argument fields. A "Custom action…" entry covers anything we
   haven't catalogued, including plugin actions.

A live preview at the bottom shows the exact ``windowrule = …`` line
that will be written to the config — so users can see what the visual
editor will produce, and power users can verify it before applying.

The dialog is opened via :meth:`SingletonDialogMixin.present_singleton`,
not constructed directly.
"""

import re
from collections.abc import Callable

from gi.repository import Adw, Gtk, Pango
from hyprland_socket import Window

from hyprmod.core.window_rules import (
    ACTION_PRESETS,
    CUSTOM_MATCHER_KIND,
    CUSTOM_PRESET,
    MATCHER_KINDS,
    MATCHER_KINDS_BY_KEY,
    RAW_KEY,
    ActionField,
    ActionPreset,
    Matcher,
    MatcherKind,
    WindowRule,
    lookup_matcher_kind,
    lookup_preset,
)
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

        # Matcher rows are tracked imperatively so Add/Remove and the
        # preview rebuild can find each row's current values. Each row
        # carries a kind + key/value widgets exposed via the
        # ``_MatcherRow`` helper class.
        self._matcher_rows: list[_MatcherRow] = []
        self._matchers_listbox: Gtk.ListBox

        # Action picker state. ``_action_dropdown`` is the
        # ``Adw.ComboRow`` that selects which preset is in play;
        # ``_action_field_box`` holds the per-preset argument widgets,
        # rebuilt every time the dropdown changes selection. The list
        # is parallel to the active preset's ``fields`` tuple.
        self._action_field_widgets: list[Gtk.Widget] = []
        self._action_dropdown: Adw.ComboRow
        self._action_field_box: Gtk.Box
        self._action_description: Gtk.Label
        # ``_PRESETS_WITH_CUSTOM`` is the dropdown's model, keeping
        # ``Custom`` as the last option so users discover the
        # structured presets first.
        self._presets: tuple[ActionPreset, ...] = (*ACTION_PRESETS, CUSTOM_PRESET)

        # Live-preview label updated on every form change.
        self._preview_label: Gtk.Label

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
            self._set_action_preset(ACTION_PRESETS[0])

        self._refresh()

    # ── Section builders ──────────────────────────────────────────────

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
        """The 'Apply this action' group with the action dropdown + arg fields."""
        group = Adw.PreferencesGroup(title="Apply this action")
        group.set_description("Pick what Hyprland should do when a matching window opens.")

        # Action selector — a ComboRow showing every preset's label.
        self._action_dropdown = Adw.ComboRow(title="Action")
        labels = Gtk.StringList.new([p.label for p in self._presets])
        self._action_dropdown.set_model(labels)
        self._action_dropdown.connect("notify::selected", self._on_action_changed)
        group.add(self._action_dropdown)

        # Per-preset description below the dropdown — keeps the user
        # oriented when scanning unfamiliar action names.
        self._action_description = Gtk.Label()
        self._action_description.set_xalign(0)
        self._action_description.set_wrap(True)
        self._action_description.add_css_class("dim-label")
        self._action_description.add_css_class("caption")
        self._action_description.set_margin_start(12)
        self._action_description.set_margin_end(12)
        self._action_description.set_margin_top(2)
        self._action_description.set_margin_bottom(8)

        # Argument fields live in their own group so the preview/preset
        # description sits between the selector and the args without
        # disrupting the action-row visual style.
        self._action_field_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.append(group)
        outer.append(self._action_description)
        outer.append(self._action_field_box)
        return outer

    def _build_preview_section(self) -> Gtk.Widget:
        """The bottom preview: shows the actual config line that will be written."""
        group = Adw.PreferencesGroup(title="Preview")
        group.set_description("This is the exact line that will be written to your HyprMod config.")

        frame = Gtk.Frame()
        frame.add_css_class("view")

        self._preview_label = Gtk.Label()
        self._preview_label.set_xalign(0)
        self._preview_label.set_wrap(True)
        self._preview_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._preview_label.set_selectable(True)
        self._preview_label.set_margin_top(10)
        self._preview_label.set_margin_bottom(10)
        self._preview_label.set_margin_start(12)
        self._preview_label.set_margin_end(12)
        self._preview_label.add_css_class("monospace")
        frame.set_child(self._preview_label)

        group.add(frame)
        return group

    # ── Hydration / loading from an existing rule ─────────────────────

    def _load_from_rule(self, rule: WindowRule) -> None:
        """Populate widgets from an existing ``WindowRule`` for editing."""
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

        # Effect: lookup_preset returns CUSTOM_PRESET for unknown leading
        # tokens, so a plugin action like ``plugin:foo:bar`` becomes a
        # Custom preset with the full effect string in its single field.
        preset = lookup_preset(rule.effect_name)
        if preset is CUSTOM_PRESET:
            # Custom holds the entire effect verbatim (name + args).
            full = rule.effect_full
            self._set_action_preset(preset, args_str=full)
        else:
            self._set_action_preset(preset, args_str=rule.effect_args)

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
        )
        self._matcher_rows.append(row)
        self._matchers_listbox.append(row.widget)

    def _on_add_matcher(self) -> None:
        # Default new rows to "Class" — overwhelmingly the most common
        # matcher people reach for.
        self._add_matcher_row(MATCHER_KINDS[0])
        self._refresh()

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

    # ── Action selection ──────────────────────────────────────────────

    def _on_action_changed(self, *_args: object) -> None:
        idx = self._action_dropdown.get_selected()
        if idx < 0 or idx >= len(self._presets):
            return
        preset = self._presets[idx]
        self._render_action_fields(preset)
        self._refresh()

    def _set_action_preset(self, preset: ActionPreset, args_str: str = "") -> None:
        """Select a preset programmatically and populate its fields.

        ``args_str`` is the *args portion* of the effect for structured
        presets (e.g. ``"0.8 0.95"`` for opacity), or the *full effect*
        verbatim (name + args) for the Custom preset.
        """
        idx = self._presets.index(preset) if preset in self._presets else 0
        # Block the notify::selected handler while we set up the field
        # widgets — otherwise it fires before ``_render_action_fields``
        # gets a chance and we'd build empty widgets, then rebuild.
        self._action_dropdown.handler_block_by_func(self._on_action_changed)
        self._action_dropdown.set_selected(idx)
        self._action_dropdown.handler_unblock_by_func(self._on_action_changed)
        self._render_action_fields(preset, args_str=args_str)

    def _render_action_fields(self, preset: ActionPreset, *, args_str: str = "") -> None:
        """Rebuild the per-preset argument widgets."""
        # Drop everything currently in the field box; widgets aren't
        # reused across presets because their types vary (entry vs.
        # spin) and their semantics differ.
        child = self._action_field_box.get_first_child()
        while child is not None:
            self._action_field_box.remove(child)
            child = self._action_field_box.get_first_child()
        self._action_field_widgets = []

        self._action_description.set_text(preset.description)

        if not preset.fields:
            return

        # For Custom: pre-fill the single free-text field with the
        # full effect string, so users opening a plugin rule see what
        # they had.
        # For structured presets: parse_args returns a per-field list
        # padded to ``len(fields)``, so positional alignment is safe.
        if preset is CUSTOM_PRESET:
            initial_values = [args_str] if args_str else [""]
        else:
            parsed = preset.parse_args(args_str) if args_str else None
            initial_values = parsed if parsed is not None else [f.default for f in preset.fields]

        group = Adw.PreferencesGroup()
        for field, initial in zip(preset.fields, initial_values, strict=False):
            widget = self._build_action_field_widget(field, initial)
            self._action_field_widgets.append(widget)
            group.add(widget)
        self._action_field_box.append(group)

    def _build_action_field_widget(self, field: ActionField, initial: str) -> Gtk.Widget:
        """Create the appropriate Adw row for a single action field."""
        if field.kind == "number":
            row = Adw.SpinRow.new_with_range(field.min_value, field.max_value, field.step)
            row.set_title(field.label)
            if field.hint:
                row.set_subtitle(field.hint)
            row.set_digits(field.digits)
            try:
                row.set_value(float(initial) if initial else float(field.default or "0"))
            except ValueError:
                # If the user's existing rule has a non-numeric token in
                # this slot (e.g. ``size 100% 100%``), fall back to the
                # field default rather than raising. Round-trip fidelity
                # is preserved via the Custom preset path on re-open.
                row.set_value(float(field.default or "0"))
            row.connect("notify::value", self._on_field_changed)
            return row

        # Free-text: an EntryRow with placeholder + optional subtitle.
        row = Adw.EntryRow(title=field.label)
        if field.placeholder:
            # ``EntryRow`` uses the title as a placeholder when empty,
            # so we keep ``title=label`` and stash the placeholder in
            # the subtitle for context. (Adw doesn't expose a separate
            # placeholder API on EntryRow.)
            pass
        if field.hint:
            # ``Adw.EntryRow`` does not expose ``set_subtitle``; we
            # carry the hint via the field's input-purpose tooltip
            # instead so it surfaces on hover without requiring a
            # second row.
            row.set_tooltip_text(field.hint)
        row.set_text(initial)
        row.connect("changed", self._on_field_changed)
        return row

    def _read_action_fields(self) -> list[str]:
        """Snapshot current values of the action fields, in order."""
        result: list[str] = []
        for widget in self._action_field_widgets:
            if isinstance(widget, Adw.SpinRow):
                value = widget.get_value()
                # Format with the field's display digits so an integer
                # field doesn't emit ``1280.0`` and a float field
                # doesn't lose its trailing zero.
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

    # ── Pick-from-window ──────────────────────────────────────────────

    def _on_pick_window(self) -> None:
        def on_pick(window: Window) -> None:
            self._apply_picked_window(window)

        WindowPickerDialog.present_singleton(self, on_pick=on_pick)

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

    def _on_field_changed(self, *_args: object) -> None:
        self._refresh()

    def _refresh(self) -> None:
        rule = self._build_rule()
        self._preview_label.set_text(rule.to_line() if rule is not None else "(rule incomplete)")
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
        idx = self._action_dropdown.get_selected()
        if idx < 0 or idx >= len(self._presets):
            return None
        preset = self._presets[idx]
        if preset is CUSTOM_PRESET:
            # Custom: the single field holds the full effect verbatim
            # (name + args). Split the leading word as effect_name,
            # the rest as effect_args.
            values = self._read_action_fields()
            full = values[0].strip() if values else ""
            effect_name, _, effect_args = full.partition(" ")
            effect_name = effect_name.strip()
            effect_args = effect_args.strip()
        else:
            effect_name = preset.id
            effect_args = preset.format(self._read_action_fields())

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
            effect_name=effect_name,
            effect_args=effect_args,
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
    ):
        self._on_remove = on_remove
        self._on_changed = on_changed
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
        # what the user usually wants.
        carry = ""
        if isinstance(self._value_widget, Gtk.Entry):
            carry = self._value_widget.get_text()
        elif isinstance(self._value_widget, Gtk.Switch):
            carry = "true" if self._value_widget.get_active() else "false"
        self._swap_value_widget(new_kind, carry)
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


__all__ = ["WindowRuleEditDialog"]
