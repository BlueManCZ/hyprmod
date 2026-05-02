"""Add/edit dialog for a single layer rule.

Two halves:

1. **Match this layer surface** — a single namespace regex entry.
   Hyprland 0.54.3 layer rules only support ``match:namespace`` for
   layer surfaces (the shared rule catalog includes other ``match:``
   props like ``class`` and ``title``, but the layer rule resolver
   silently ignores them at match time), so the dialog doesn't surface
   any other matcher options. Surface-by-address targeting was removed
   when the rule format was overhauled in 0.54.
2. **Apply this rule** — a dropdown of common Hyprland layer-rule
   effects (``blur``, ``ignore_alpha``, ``animation``, ``no_anim``, …)
   with effect-specific argument fields. A "Custom rule…" entry
   covers anything we haven't catalogued, including plugin rules and
   future Hyprland additions.

A live preview at the bottom shows the exact ``layerrule = …`` line
that will be written. Users can paste the preview straight into a
config file if they prefer.

The dialog deliberately stays simple — one matcher (namespace), one
effect per rule — to mirror the user's mental model. Multi-effect
rules read from disk are split into one :class:`LayerRule` per effect
upstream (see :func:`hyprmod.core.layer_rules.parse_layer_rule_lines`),
so the dialog can always assume single-effect editing.
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.core.layer_rules import (
    CUSTOM_PRESET,
    LAYER_ACTION_PRESETS,
    LAYER_BOOL_EFFECTS,
    LayerActionField,
    LayerActionPreset,
    LayerRule,
    lookup_preset,
)
from hyprmod.ui import build_preview_group
from hyprmod.ui.dialog import SingletonDialogMixin


class LayerRuleEditDialog(SingletonDialogMixin, Adw.Dialog):
    """Add/edit dialog for a single ``layerrule`` entry."""

    def __init__(
        self,
        *,
        rule: LayerRule | None = None,
        on_apply: Callable[[LayerRule], None] | None = None,
    ):
        super().__init__()
        self._is_new = rule is None
        self._on_apply_callback = on_apply

        # Action picker state. ``_action_dropdown`` selects a preset;
        # ``_action_field_box`` holds the per-preset argument widgets,
        # rebuilt every time the dropdown changes selection.
        self._action_field_widgets: list[Gtk.Widget] = []
        self._action_dropdown: Adw.ComboRow
        self._action_field_box: Gtk.Box
        self._action_description: Gtk.Label
        # Custom is appended to the catalog so users discover the
        # structured presets first and the fall-through last.
        self._presets: tuple[LayerActionPreset, ...] = (*LAYER_ACTION_PRESETS, CUSTOM_PRESET)

        # Single namespace entry — Hyprland 0.54.3 only honours
        # ``match:namespace`` for layer rules, so there's no value in
        # surfacing other matcher options.
        self._namespace_entry: Adw.EntryRow

        # Live preview label, refreshed on every form change.
        self._preview_label: Gtk.Label

        self.set_title("New Layer Rule" if self._is_new else "Edit Layer Rule")
        self.set_content_width(560)
        self.set_content_height(560)

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

        # Hydrate from the rule being edited (or seed defaults for new).
        if rule is not None:
            self._load_from_rule(rule)
        else:
            self._set_action_preset(LAYER_ACTION_PRESETS[0])

        self._refresh()

    # ── Section builders ──────────────────────────────────────────────

    def _build_match_section(self) -> Gtk.Widget:
        """The 'Match this layer surface' group with the namespace entry."""
        group = Adw.PreferencesGroup(title="Match this layer surface")
        group.set_description(
            "Type the layer namespace as a regex. "
            "Common namespaces: waybar, notifications, rofi, mako, dunst, wallpaper."
        )

        self._namespace_entry = Adw.EntryRow(title="Namespace regex")
        self._namespace_entry.set_tooltip_text(
            "Regex matching the layer surface namespace. "
            "Examples: ‘waybar’, ‘^(rofi|wofi)$’, ‘notifications’."
        )
        self._namespace_entry.connect("changed", lambda *_: self._refresh())
        group.add(self._namespace_entry)

        return group

    def _build_apply_section(self) -> Gtk.Widget:
        """The 'Apply this rule' group with the action dropdown + arg fields."""
        group = Adw.PreferencesGroup(title="Apply this rule")
        group.set_description("Pick what Hyprland should do for matching layer surfaces.")

        self._action_dropdown = Adw.ComboRow(title="Rule")
        labels = Gtk.StringList.new([p.label for p in self._presets])
        self._action_dropdown.set_model(labels)
        self._action_dropdown.connect("notify::selected", self._on_action_changed)
        group.add(self._action_dropdown)

        self._action_description = Gtk.Label()
        self._action_description.set_xalign(0)
        self._action_description.set_wrap(True)
        self._action_description.add_css_class("dim-label")
        self._action_description.add_css_class("caption")
        self._action_description.set_margin_start(12)
        self._action_description.set_margin_end(12)
        self._action_description.set_margin_top(2)
        self._action_description.set_margin_bottom(8)

        # Argument fields rebuild on every preset change.
        self._action_field_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.append(group)
        outer.append(self._action_description)
        outer.append(self._action_field_box)
        return outer

    def _build_preview_section(self) -> Gtk.Widget:
        """The bottom preview: shows the actual config line that will be written."""
        group, self._preview_label = build_preview_group()
        return group

    # ── Hydration / loading from an existing rule ─────────────────────

    def _load_from_rule(self, rule: LayerRule) -> None:
        """Populate widgets from an existing :class:`LayerRule` for editing."""
        self._namespace_entry.set_text(rule.namespace)

        # Effect: lookup_preset returns CUSTOM_PRESET for unknown leading
        # tokens, so a plugin rule like ``plugin:foo bar`` becomes Custom
        # with the full effect string in its single field.
        preset = lookup_preset(rule.rule_name)
        if preset is CUSTOM_PRESET:
            full = rule.effect_full
            self._set_action_preset(preset, args_str=full)
        else:
            # Bool effects auto-fill ``on`` on serialization but we don't
            # surface that in the dialog — the bool presets have no
            # fields. Pass empty args so ``parse_args`` doesn't try to
            # populate a non-existent field.
            args_str = "" if rule.rule_name in LAYER_BOOL_EFFECTS else rule.rule_args
            self._set_action_preset(preset, args_str=args_str)

    # ── Action selection ──────────────────────────────────────────────

    def _on_action_changed(self, *_args: object) -> None:
        idx = self._action_dropdown.get_selected()
        if idx < 0 or idx >= len(self._presets):
            return
        preset = self._presets[idx]
        self._render_action_fields(preset)
        self._refresh()

    def _set_action_preset(self, preset: LayerActionPreset, args_str: str = "") -> None:
        """Select a preset programmatically and populate its fields."""
        idx = self._presets.index(preset) if preset in self._presets else 0
        # Block the notify::selected handler while we set up the field
        # widgets — otherwise it fires before ``_render_action_fields``
        # gets a chance and we'd build empty widgets, then rebuild.
        self._action_dropdown.handler_block_by_func(self._on_action_changed)
        self._action_dropdown.set_selected(idx)
        self._action_dropdown.handler_unblock_by_func(self._on_action_changed)
        self._render_action_fields(preset, args_str=args_str)

    def _render_action_fields(self, preset: LayerActionPreset, *, args_str: str = "") -> None:
        """Rebuild the per-preset argument widgets."""
        # Drop everything currently in the field box; widget types vary
        # across presets (entry vs. spin) so reuse isn't safe.
        child = self._action_field_box.get_first_child()
        while child is not None:
            self._action_field_box.remove(child)
            child = self._action_field_box.get_first_child()
        self._action_field_widgets = []

        self._action_description.set_text(preset.description)

        if not preset.fields:
            return

        # For Custom: pre-fill the single free-text field with the full
        # rule string. For structured presets: parse_args returns a
        # per-field list padded to ``len(fields)``.
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

    def _build_action_field_widget(self, field: LayerActionField, initial: str) -> Gtk.Widget:
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
                # Fall back to default if the saved string is non-numeric;
                # round-trip fidelity for unusual values is preserved via
                # the Custom preset path on re-open.
                row.set_value(float(field.default or "0"))
            row.connect("notify::value", self._on_field_changed)
            return row

        if field.kind == "bool":
            row = Adw.SwitchRow(title=field.label)
            if field.hint:
                row.set_subtitle(field.hint)
            row.set_active(initial.strip().lower() in {"1", "true", "yes", "on"})
            row.connect("notify::active", self._on_field_changed)
            return row

        # Free-text: an EntryRow with optional tooltip hint.
        row = Adw.EntryRow(title=field.label)
        if field.hint:
            # Adw.EntryRow doesn't expose ``set_subtitle``; carry the
            # hint via tooltip so it surfaces on hover.
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
                # field doesn't emit ``5.0`` and a float field doesn't
                # lose its trailing zero.
                digits = widget.get_digits()
                if digits == 0:
                    result.append(str(int(value)))
                else:
                    result.append(f"{value:.{digits}f}")
            elif isinstance(widget, Adw.SwitchRow):
                # ``on``/``off`` matches Hyprland's documented bool
                # syntax and round-trips via the parser without any
                # special-case handling.
                result.append("on" if widget.get_active() else "off")
            elif isinstance(widget, Adw.EntryRow):
                result.append(widget.get_text())
            else:
                result.append("")
        return result

    # ── Refresh: preview + apply gating ───────────────────────────────

    def _on_field_changed(self, *_args: object) -> None:
        self._refresh()

    def _refresh(self) -> None:
        rule = self._build_rule()
        self._preview_label.set_text(rule.to_line() if rule is not None else "(rule incomplete)")
        # Apply gates on a non-empty namespace AND a non-empty rule name.
        ok = rule is not None and bool(rule.rule_name) and bool(rule.namespace.strip())
        self._apply_btn.set_sensitive(ok)

    def _build_rule(self) -> LayerRule | None:
        """Snapshot the current dialog state into a :class:`LayerRule`."""
        idx = self._action_dropdown.get_selected()
        if idx < 0 or idx >= len(self._presets):
            return None
        preset = self._presets[idx]
        if preset is CUSTOM_PRESET:
            # Custom: the single field holds the full rule verbatim
            # (name + args). Split the leading word as rule_name,
            # the rest as rule_args.
            values = self._read_action_fields()
            full = values[0].strip() if values else ""
            rule_name, _, rule_args = full.partition(" ")
            rule_name = rule_name.strip()
            rule_args = rule_args.strip()
        else:
            rule_name = preset.id
            rule_args = preset.format(self._read_action_fields())

        namespace = self._namespace_entry.get_text().strip()

        return LayerRule(
            namespace=namespace,
            rule_name=rule_name,
            rule_args=rule_args,
        )

    # ── Apply ─────────────────────────────────────────────────────────

    def _on_apply(self, *_args: object) -> None:
        rule = self._build_rule()
        if rule is None or not rule.rule_name or not rule.namespace.strip():
            # The apply gate should make this unreachable, but be
            # defensive — a stale signal could fire after a rebuild.
            return
        if self._on_apply_callback is not None:
            self._on_apply_callback(rule)
        self.close()


__all__ = ["LayerRuleEditDialog"]
