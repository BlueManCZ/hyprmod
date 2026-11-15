"""Add/edit dialog for a single ``workspace`` rule.

Six preference groups, each one of the rule's logical concerns:

1. **Workspace selector** — type combo (numeric / named / range /
   per-monitor / special) plus a single value entry that adapts to the
   selected type. The selector round-trips verbatim into the model's
   :attr:`WorkspaceRule.workspace` string, so unusual shapes (and any
   future Hyprland selector syntax we don't yet have a UI for) survive
   an edit-cycle.
2. **Bind to monitor** — a monitor combo populated from the live
   compositor's monitor list (plus an "any monitor" option) and a
   "Set as default" switch.
3. **Lifecycle** — Persistent switch + ``on-created-empty`` shell
   command entry.
4. **Appearance** — Border / Rounding / Shadow / Decoration overrides
   plus border size.
5. **Gaps** — Inner and outer gap overrides, each with a mode selector
   (single value vs. four per-side values).
6. **Naming** — Default workspace name entry.

Each "override" field uses one of two patterns. The boolean decoration
flags (border, rounding, …) use a tri-state :class:`Adw.ComboRow`
(*Use global* / *On* / *Off*) mapping to ``None`` / ``True`` /
``False``. Fields with a richer value (border size, gaps, command,
name) keep a switchable pattern: an :class:`Adw.SwitchRow` controls
whether the field is part of the rule, and the value widget below it
sets the value. Either way an unset field emits ``None`` into the model
so the resulting config line only carries fields the user has actually
configured.

A live preview at the bottom always shows the exact ``workspace = …``
line (or ``hl.workspace_rule({...})`` snippet in Lua mode) hyprmod will
write — making the connection between the dialog state and the on-disk
result explicit.
"""

import re
from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.core import config
from hyprmod.core.workspaces import GapValue, WorkspaceRule
from hyprmod.i18n import _
from hyprmod.ui import build_preview_group, format_config_preview
from hyprmod.ui.dialog import SingletonDialogMixin

# Workspace selector types — the user picks one in the dialog and the
# value entry adapts (spin button for numeric, free-text for the rest).
_SELECTOR_TYPES: tuple[tuple[str, str, str], ...] = (
    ("numeric", "Numeric ID", "Workspace number — e.g. 1, 2, 42."),
    ("named", "Named", "Named workspace — e.g. work, dev."),
    ("range", "Range", "Range of IDs — e.g. 1-10 (rendered as r[1-10])."),
    (
        "per_monitor",
        "Per-monitor",
        "Per-monitor index — e.g. 1 (rendered as m[1]) or 1-3 (m[1-3]).",
    ),
    ("special", "Special", "Special workspace name — e.g. scratchpad."),
)

# Regex validation for each selector type's raw value entry.
_SELECTOR_PATTERNS: dict[str, re.Pattern[str]] = {
    "numeric": re.compile(r"^\d+$"),
    "named": re.compile(r"^[^,\s][^,]*$"),
    "range": re.compile(r"^\d+-\d+$"),
    "per_monitor": re.compile(r"^\d+(-\d+)?$"),
    "special": re.compile(r"^[^,\s][^,]*$"),
}


def _classify_selector(selector: str) -> tuple[str, str]:
    """Classify a stored ``workspace`` selector into ``(type, value)``.

    Falls back to ``("numeric", "1")`` for empty / unparseable input —
    matches the dialog's empty-state default and prevents the type combo
    from getting stuck on a malformed entry the user can't see.
    """
    if not selector:
        return ("numeric", "1")
    if selector.isdigit():
        return ("numeric", selector)
    if selector.startswith("name:"):
        return ("named", selector[5:])
    if selector.startswith("special:"):
        return ("special", selector[8:])
    if selector.startswith("r[") and selector.endswith("]"):
        return ("range", selector[2:-1])
    if selector.startswith("m[") and selector.endswith("]"):
        return ("per_monitor", selector[2:-1])
    # Unknown shape — surface as Named so the user can still see + edit it.
    return ("named", selector)


def _compose_selector(stype: str, value: str) -> str:
    """Build the canonical Hyprlang selector string for ``(type, value)``."""
    value = value.strip()
    if not value:
        return ""
    if stype == "numeric":
        return value
    if stype == "named":
        return f"name:{value}"
    if stype == "special":
        return f"special:{value}"
    if stype == "range":
        return f"r[{value}]"
    if stype == "per_monitor":
        return f"m[{value}]"
    return value


class WorkspaceRuleEditDialog(SingletonDialogMixin, Adw.Dialog):
    """Add/edit dialog for a single ``workspace = …`` entry."""

    def __init__(
        self,
        *,
        rule: WorkspaceRule | None = None,
        monitor_choices: list[tuple[str, str]] | None = None,
        on_apply: Callable[[WorkspaceRule], None] | None = None,
    ):
        super().__init__()
        self._is_new = rule is None
        self._on_apply_callback = on_apply
        # (connector, label) pairs. Connector is the underlying value we save
        # into the rule; label is the friendly form ("DP-1 — Dell AW3423DWF")
        # we show in the combo.
        self._monitor_choices = monitor_choices or []
        self._monitor_connectors = [c for c, _ in self._monitor_choices]

        self.set_title(_("New Workspace Rule") if self._is_new else _("Edit Workspace Rule"))
        self.set_content_width(600)
        self.set_content_height(680)

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._apply_btn = Gtk.Button(label=_("Apply"))
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        self._apply_btn.set_sensitive(False)
        header.pack_end(self._apply_btn)
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(720)
        clamp.set_tightening_threshold(560)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        content.append(self._build_selector_group())
        content.append(self._build_monitor_group())
        content.append(self._build_lifecycle_group())
        content.append(self._build_appearance_group())
        content.append(self._build_gaps_group())
        content.append(self._build_naming_group())
        content.append(self._build_preview_group())

        clamp.set_child(content)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)

        if rule is not None:
            self._load_from_rule(rule)
        else:
            self._selector_value.set_text("1")
        self._refresh()

    # ── Section builders ──────────────────────────────────────────────────

    def _build_selector_group(self) -> Gtk.Widget:
        """Selector group plus the per-type description label below it.

        Returned as a :class:`Gtk.Box` (not :class:`Adw.PreferencesGroup`)
        because the type-help caption sits *outside* the group's frame —
        ``PreferencesGroup`` only wraps rows, and the caption would look
        like another row if put inside.
        """
        group = Adw.PreferencesGroup(title=_("Workspace"))
        group.set_description(_("Which workspace(s) this rule applies to."))

        self._selector_type_row = Adw.ComboRow(title=_("Type"))
        self._selector_type_row.set_model(
            Gtk.StringList.new([label for _, label, _ in _SELECTOR_TYPES])
        )
        self._selector_type_row.connect("notify::selected", self._on_selector_type_changed)
        group.add(self._selector_type_row)

        self._selector_value = Adw.EntryRow(title=_("Value"))
        self._selector_value.connect("changed", lambda *_: self._refresh())
        group.add(self._selector_value)

        # Description line (updated whenever the type changes).
        self._selector_description = Gtk.Label()
        self._selector_description.set_xalign(0)
        self._selector_description.set_wrap(True)
        self._selector_description.add_css_class("dim-label")
        self._selector_description.add_css_class("caption")
        self._selector_description.set_margin_top(2)
        self._selector_description.set_margin_start(12)
        self._selector_description.set_margin_end(12)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.append(group)
        outer.append(self._selector_description)
        return outer

    def _build_monitor_group(self) -> Adw.PreferencesGroup:
        """Monitor binding + default-for-monitor switch."""
        group = Adw.PreferencesGroup(title=_("Bind to monitor"))
        group.set_description(
            _(
                "Optional. Pin this workspace to a specific output. "
                "Disconnected monitors still apply when they return."
            )
        )

        # First entry is "no monitor binding" — keeps the rule unbound.
        self._monitor_options: list[str | None] = [None] + list(self._monitor_connectors)
        labels = ["Any monitor"] + [label for _, label in self._monitor_choices]
        self._monitor_row = Adw.ComboRow(title=_("Monitor"))
        self._monitor_row.set_model(Gtk.StringList.new(labels))
        self._monitor_row.connect("notify::selected", lambda *_: self._refresh())
        group.add(self._monitor_row)

        self._default_row = Adw.SwitchRow(
            title=_("Default for monitor"),
            subtitle=_("Open this workspace automatically when the monitor connects."),
        )
        self._default_row.connect("notify::active", lambda *_: self._refresh())
        group.add(self._default_row)

        return group

    def _build_lifecycle_group(self) -> Adw.PreferencesGroup:
        """Persistent switch + on-created-empty command entry."""
        group = Adw.PreferencesGroup(title=_("Lifecycle"))
        group.set_description(_("Keep the workspace alive and run a command when it first opens."))

        self._persistent_row = Adw.SwitchRow(
            title=_("Persistent"),
            subtitle=_("Don't auto-destroy the workspace when its last window closes."),
        )
        self._persistent_row.connect("notify::active", lambda *_: self._refresh())
        group.add(self._persistent_row)

        # on-created-empty: we expose this as a "set + value" pair to keep
        # the model contract clean (None = don't emit). The switch acts as
        # the override toggle; the entry holds the command.
        self._on_created_switch = Adw.SwitchRow(
            title=_("On first map"),
            subtitle=_("Run a shell command the first time this workspace is opened."),
        )
        self._on_created_switch.connect("notify::active", self._on_on_created_toggle)
        group.add(self._on_created_switch)

        self._on_created_entry = Adw.EntryRow(title=_("Command"))
        self._on_created_entry.set_tooltip_text(
            _("Shell command — same syntax as ‘exec’ entries. Example: ‘kitty’.")
        )
        self._on_created_entry.set_visible(False)
        self._on_created_entry.connect("changed", lambda *_: self._refresh())
        group.add(self._on_created_entry)

        return group

    def _build_appearance_group(self) -> Adw.PreferencesGroup:
        """Border / Rounding / Shadow / Decoration overrides + border size."""
        group = Adw.PreferencesGroup(title=_("Appearance"))
        group.set_description(
            _(
                "Override window decoration for this workspace. "
                "Settings left on “Use global” inherit your normal config."
            )
        )

        self._border_combo = self._add_bool_combo(
            group, "Border", "Draw window borders on this workspace."
        )
        self._rounding_combo = self._add_bool_combo(
            group, "Rounding", "Round window corners on this workspace."
        )
        self._shadow_combo = self._add_bool_combo(
            group, "Shadow", "Draw window shadows on this workspace."
        )
        self._decorate_combo = self._add_bool_combo(
            group, "Decoration", "Render window decoration on this workspace."
        )

        # Border size — same override pattern but with a SpinRow as the value.
        self._border_size_override = Adw.SwitchRow(
            title=_("Border size override"),
            subtitle=_("Set a custom border width in pixels."),
        )
        self._border_size_override.connect("notify::active", self._on_border_size_toggle)
        group.add(self._border_size_override)

        self._border_size_value = Adw.SpinRow.new_with_range(0, 50, 1)
        self._border_size_value.set_title(_("Border size (px)"))
        self._border_size_value.set_visible(False)
        self._border_size_value.connect("notify::value", lambda *_: self._refresh())
        group.add(self._border_size_value)

        return group

    def _add_bool_combo(
        self,
        group: Adw.PreferencesGroup,
        label: str,
        subtitle: str,
    ) -> Adw.ComboRow:
        """Add a tri-state appearance override (Use global / On / Off).

        The selected index maps to the model: 0 → ``None`` (inherit the
        global setting), 1 → ``True``, 2 → ``False``.
        """
        combo = Adw.ComboRow(title=label, subtitle=subtitle)
        combo.set_model(Gtk.StringList.new(["Use global", "On", "Off"]))
        combo.connect("notify::selected", lambda *_: self._refresh())
        group.add(combo)
        return combo

    def _build_gaps_group(self) -> Adw.PreferencesGroup:
        """Inner / outer gap overrides with scalar vs. per-side modes."""
        group = Adw.PreferencesGroup(title=_("Gaps"))
        group.set_description(
            _(
                "Override inner/outer gaps for this workspace. "
                "Per-side mode mirrors CSS: top, right, bottom, left."
            )
        )

        self._gaps_in_widgets = self._add_gap_override(group, _("Inner gaps"), "in")
        self._gaps_out_widgets = self._add_gap_override(group, _("Outer gaps"), "out")

        return group

    def _add_gap_override(self, group: Adw.PreferencesGroup, label: str, key: str) -> dict:
        """Add a gap override with override switch, mode combo, and value widgets.

        Returns a dict of the widgets so the read/write paths can address
        them by name without juggling positional state.
        """
        override = Adw.SwitchRow(title=_("{label} override").format(label=label))
        override.connect("notify::active", lambda *_, k=key: self._on_gap_override_toggle(k))
        group.add(override)

        # Mode: single value (scalar) vs. four sides (tuple).
        mode_row = Adw.ComboRow(title=_("{label} mode").format(label=label))
        mode_row.set_model(Gtk.StringList.new([_("Single value"), _("Per side")]))
        mode_row.set_visible(False)
        mode_row.connect("notify::selected", lambda *_, k=key: self._on_gap_mode_changed(k))
        group.add(mode_row)

        scalar_value = Adw.SpinRow.new_with_range(0, 200, 1)
        scalar_value.set_title(label)
        scalar_value.set_visible(False)
        scalar_value.connect("notify::value", lambda *_: self._refresh())
        group.add(scalar_value)

        # Per-side: four spin rows. Hidden by default; revealed when the
        # mode combo flips to "Per side".
        per_side_rows: dict[str, Adw.SpinRow] = {}
        for side in ("top", "right", "bottom", "left"):
            row = Adw.SpinRow.new_with_range(0, 200, 1)
            row.set_title(f"{label} · {_(side)}")
            row.set_visible(False)
            row.connect("notify::value", lambda *_: self._refresh())
            group.add(row)
            per_side_rows[side] = row

        return {
            "override": override,
            "mode": mode_row,
            "scalar": scalar_value,
            "per_side": per_side_rows,
        }

    def _build_naming_group(self) -> Adw.PreferencesGroup:
        """Default workspace name override."""
        group = Adw.PreferencesGroup(title=_("Name"))
        group.set_description(
            _(
                "Optional. Override the workspace's default display name "
                "(shown in status bars and pickers)."
            )
        )

        self._default_name_switch = Adw.SwitchRow(
            title=_("Default name override"),
            subtitle=_("Set a custom display name for this workspace."),
        )
        self._default_name_switch.connect("notify::active", self._on_default_name_toggle)
        group.add(self._default_name_switch)

        self._default_name_entry = Adw.EntryRow(title=_("Display name"))
        self._default_name_entry.set_visible(False)
        self._default_name_entry.connect("changed", lambda *_: self._refresh())
        group.add(self._default_name_entry)

        return group

    def _build_preview_group(self) -> Adw.PreferencesGroup:
        """Live preview of the exact config line that will be written."""
        group, self._preview_label = build_preview_group()
        return group

    # ── Toggle handlers (override switch reveals value widget) ────────────

    def _on_selector_type_changed(self, *_args: object) -> None:
        idx = self._selector_type_row.get_selected()
        if 0 <= idx < len(_SELECTOR_TYPES):
            stype, _, descr = _SELECTOR_TYPES[idx]
            self._selector_description.set_text(descr)
            # Numeric type uses the spin button widget — but since EntryRow
            # already accepts free-text and we validate via regex, we keep
            # one widget for all types. Adapt the tooltip instead.
            self._selector_value.set_tooltip_text(descr)
        self._refresh()

    def _on_border_size_toggle(self, *_args: object) -> None:
        self._border_size_value.set_visible(self._border_size_override.get_active())
        self._refresh()

    def _on_on_created_toggle(self, *_args: object) -> None:
        self._on_created_entry.set_visible(self._on_created_switch.get_active())
        self._refresh()

    def _on_default_name_toggle(self, *_args: object) -> None:
        self._default_name_entry.set_visible(self._default_name_switch.get_active())
        self._refresh()

    def _on_gap_override_toggle(self, key: str) -> None:
        widgets = self._gap_widgets(key)
        is_on = widgets["override"].get_active()
        widgets["mode"].set_visible(is_on)
        # Show the value widget appropriate to current mode; if turning
        # the override on for the first time, default to scalar mode.
        if is_on:
            self._refresh_gap_visibility(key)
        else:
            widgets["scalar"].set_visible(False)
            for row in widgets["per_side"].values():
                row.set_visible(False)
        self._refresh()

    def _on_gap_mode_changed(self, key: str) -> None:
        self._refresh_gap_visibility(key)
        self._refresh()

    def _refresh_gap_visibility(self, key: str) -> None:
        widgets = self._gap_widgets(key)
        if not widgets["override"].get_active():
            return
        is_per_side = widgets["mode"].get_selected() == 1
        widgets["scalar"].set_visible(not is_per_side)
        for row in widgets["per_side"].values():
            row.set_visible(is_per_side)

    def _gap_widgets(self, key: str) -> dict:
        return self._gaps_in_widgets if key == "in" else self._gaps_out_widgets

    # ── Hydration ─────────────────────────────────────────────────────────

    def _load_from_rule(self, rule: WorkspaceRule) -> None:
        """Populate every widget from an existing rule (edit mode)."""
        # Selector
        stype, svalue = _classify_selector(rule.workspace)
        type_idx = next(
            (i for i, (s, _, _) in enumerate(_SELECTOR_TYPES) if s == stype),
            0,
        )
        self._selector_type_row.set_selected(type_idx)
        self._selector_value.set_text(svalue)

        # Monitor + default
        if rule.monitor is not None and rule.monitor in self._monitor_connectors:
            self._monitor_row.set_selected(self._monitor_connectors.index(rule.monitor) + 1)
        elif rule.monitor is not None:
            # Monitor that isn't in the live list — append it so the user
            # can see what's saved without losing the binding.
            self._monitor_options.append(rule.monitor)
            model = self._monitor_row.get_model()
            if isinstance(model, Gtk.StringList):
                model.append(_("{monitor} (disconnected)").format(monitor=rule.monitor))
            self._monitor_row.set_selected(len(self._monitor_options) - 1)
        if rule.default:
            self._default_row.set_active(True)

        # Lifecycle
        if rule.persistent:
            self._persistent_row.set_active(True)
        if rule.on_created_empty:
            self._on_created_switch.set_active(True)
            self._on_created_entry.set_text(rule.on_created_empty)

        # Appearance — None stays on "Use global" (index 0); True → 1, False → 2.
        for attr, combo in (
            ("border", self._border_combo),
            ("rounding", self._rounding_combo),
            ("shadow", self._shadow_combo),
            ("decorate", self._decorate_combo),
        ):
            stored = getattr(rule, attr)
            if stored is not None:
                combo.set_selected(1 if stored else 2)
        if rule.border_size is not None:
            self._border_size_override.set_active(True)
            self._border_size_value.set_value(float(rule.border_size))

        # Gaps
        self._hydrate_gap("in", rule.gaps_in)
        self._hydrate_gap("out", rule.gaps_out)

        # Naming
        if rule.default_name:
            self._default_name_switch.set_active(True)
            self._default_name_entry.set_text(rule.default_name)

    def _hydrate_gap(self, key: str, value: GapValue | None) -> None:
        if value is None:
            return
        widgets = self._gap_widgets(key)
        widgets["override"].set_active(True)
        if isinstance(value, tuple):
            widgets["mode"].set_selected(1)
            top, right, bottom, left = value
            widgets["per_side"]["top"].set_value(float(top))
            widgets["per_side"]["right"].set_value(float(right))
            widgets["per_side"]["bottom"].set_value(float(bottom))
            widgets["per_side"]["left"].set_value(float(left))
        else:
            widgets["mode"].set_selected(0)
            widgets["scalar"].set_value(float(value))

    # ── Build current state into a WorkspaceRule ─────────────────────────

    def _build_rule(self) -> WorkspaceRule | None:
        """Snapshot the current dialog state into a :class:`WorkspaceRule`.

        Returns ``None`` when the selector is empty or fails validation —
        callers gate the Apply button on this so the user can't commit a
        rule Hyprland would reject at parse time.
        """
        selector = self._read_selector()
        if selector is None:
            return None

        rule = WorkspaceRule(workspace=selector)

        # Monitor binding
        idx = self._monitor_row.get_selected()
        if 0 < idx < len(self._monitor_options):
            rule.monitor = self._monitor_options[idx]
        if self._default_row.get_active():
            rule.default = True

        # Lifecycle
        if self._persistent_row.get_active():
            rule.persistent = True
        if self._on_created_switch.get_active():
            cmd = self._on_created_entry.get_text().strip()
            if cmd:
                rule.on_created_empty = cmd

        # Appearance — index 0 ("Use global") leaves the field unset; 1 → True, 2 → False.
        for attr, combo in (
            ("border", self._border_combo),
            ("rounding", self._rounding_combo),
            ("shadow", self._shadow_combo),
            ("decorate", self._decorate_combo),
        ):
            selected = combo.get_selected()
            if selected != 0:
                setattr(rule, attr, selected == 1)
        if self._border_size_override.get_active():
            rule.border_size = int(self._border_size_value.get_value())

        # Gaps
        rule.gaps_in = self._read_gap("in")
        rule.gaps_out = self._read_gap("out")

        # Naming
        if self._default_name_switch.get_active():
            name = self._default_name_entry.get_text().strip()
            if name:
                rule.default_name = name

        return rule

    def _read_selector(self) -> str | None:
        """Read the selector value, returning ``None`` when invalid."""
        idx = self._selector_type_row.get_selected()
        if idx < 0 or idx >= len(_SELECTOR_TYPES):
            return None
        stype = _SELECTOR_TYPES[idx][0]
        raw = self._selector_value.get_text().strip()
        if not raw:
            return None
        pattern = _SELECTOR_PATTERNS.get(stype)
        if pattern is not None and not pattern.match(raw):
            return None
        return _compose_selector(stype, raw)

    def _read_gap(self, key: str) -> GapValue | None:
        widgets = self._gap_widgets(key)
        if not widgets["override"].get_active():
            return None
        if widgets["mode"].get_selected() == 1:
            return (
                int(widgets["per_side"]["top"].get_value()),
                int(widgets["per_side"]["right"].get_value()),
                int(widgets["per_side"]["bottom"].get_value()),
                int(widgets["per_side"]["left"].get_value()),
            )
        return int(widgets["scalar"].get_value())

    # ── Refresh preview + apply gating ────────────────────────────────────

    def _refresh(self) -> None:
        rule = self._build_rule()
        if rule is None:
            self._preview_label.set_text(_("(selector required)"))
            self._apply_btn.set_sensitive(False)
            return
        self._preview_label.set_text(format_config_preview(config.KEYWORD_WORKSPACE, rule.body()))
        self._apply_btn.set_sensitive(True)

    # ── Apply ─────────────────────────────────────────────────────────────

    def _on_apply(self, *_args: object) -> None:
        rule = self._build_rule()
        if rule is None:
            return
        if self._on_apply_callback is not None:
            self._on_apply_callback(rule)
        self.close()


__all__ = ["WorkspaceRuleEditDialog"]
