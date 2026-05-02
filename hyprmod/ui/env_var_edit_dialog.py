"""Dialog for adding or editing a single ``env = NAME,value`` entry.

Lives outside ``hyprmod/pages/env_vars.py`` so the page module isn't a
catch-all; matches the existing pattern of putting reusable dialogs
under ``hyprmod/ui/`` (see ``autostart_edit_dialog.py``,
``layer_rule_dialog.py``).

Two inputs:

1. **Name** — POSIX env-var identifier. Letters, digits, underscores;
   must not start with a digit. Validation surfaces inline on every
   keystroke; Apply is gated on a valid name.
2. **Value** — free text. Commas, equals signs, spaces, and quotes
   are all preserved verbatim — Hyprland only splits on the *first*
   comma after the keyword's ``=``.

Names in :data:`hyprmod.core.env_vars.RESERVED_NAMES` (currently the
cursor theme/size variables) are blocked here with an inline error,
since editing them in two places at once would let the Cursor page
silently overwrite the user's value on save.

A live preview at the bottom shows the exact ``env = NAME,value``
line that will be written.
"""

import re
from collections.abc import Callable

from gi.repository import Adw, Gtk, Pango

from hyprmod.core.env_vars import RESERVED_NAMES, EnvVar
from hyprmod.ui.dialog import SingletonDialogMixin

# POSIX environment variable name: leading letter or underscore, then any
# number of letters, digits, or underscores. The shell standard, also what
# Hyprland accepts on read. Matched against the trimmed user input.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvVarEditDialog(SingletonDialogMixin, Adw.Dialog):
    """Add/edit/override dialog for a single ``env`` entry.

    Three call shapes:

    - ``EnvVarEditDialog(on_apply=…)`` — Add a new managed entry.
    - ``EnvVarEditDialog(entry=existing, on_apply=…)`` — Edit an
      existing managed entry. ``_original_name`` is set so the
      reserved-name guard allows keeping the name unchanged.
    - ``EnvVarEditDialog(entry=external_var, is_override=True,
      on_apply=…)`` — Override an external entry by spawning a new
      managed entry pre-filled from the external one. Title reads
      "Override" and ``_original_name`` is left ``None`` so the
      reserved-name guard treats this as a fresh add (which it is —
      a new owned line, not an edit of an existing one).
    """

    def __init__(
        self,
        *,
        entry: EnvVar | None = None,
        is_override: bool = False,
        on_apply: Callable[[EnvVar], None] | None = None,
    ):
        super().__init__()
        self._is_new = entry is None or is_override
        # ``_original_name`` only carries the reserved-name carve-out for
        # in-place edits of existing managed rows. Override flow creates
        # a brand-new managed row, so it should be subject to the full
        # reserved-name check (cursor vars are filtered out upstream
        # anyway, but the invariant should hold by construction).
        self._original_name = entry.name if entry and not is_override else None
        self._on_apply_callback = on_apply

        if is_override:
            title = "Override Environment Variable"
        elif self._is_new:
            title = "Add Environment Variable"
        else:
            title = "Edit Environment Variable"
        self.set_title(title)
        self.set_content_width(540)
        self.set_follows_content_size(True)

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

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        # Variable group
        var_group = Adw.PreferencesGroup(title="Variable")
        var_group.set_description(
            "Hyprland exports this variable to processes it spawns "
            "(autostart commands, dispatchers, terminal launches). "
            "Changes take effect on the next Hyprland session."
        )

        self._name_entry = Adw.EntryRow(title="Name")
        self._name_entry.set_text(entry.name if entry else "")
        self._name_entry.connect("changed", self._on_changed)
        var_group.add(self._name_entry)

        self._value_entry = Adw.EntryRow(title="Value")
        self._value_entry.set_text(entry.value if entry else "")
        self._value_entry.connect("changed", self._on_changed)
        # Apply on Enter from the value field (the more common
        # "I'm done typing" terminator than tabbing back to the
        # header button).
        self._value_entry.connect("entry-activated", lambda _e: self._on_apply())
        var_group.add(self._value_entry)

        content.append(var_group)

        # Inline validation message: shown only when the name is non-empty
        # and invalid. Sits below the form so it's visually associated with
        # the entry rows without taking layout space when the form is OK.
        self._error_label = Gtk.Label()
        self._error_label.set_xalign(0)
        self._error_label.set_wrap(True)
        self._error_label.add_css_class("error")
        self._error_label.add_css_class("caption")
        self._error_label.set_visible(False)
        content.append(self._error_label)

        # Preview group (mirrors layer-rule dialog).
        preview_group = Adw.PreferencesGroup(title="Preview")
        preview_group.set_description(
            "This is the exact line that will be written to your HyprMod config."
        )

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

        preview_group.add(frame)
        content.append(preview_group)

        toolbar.set_content(content)
        self.set_child(toolbar)

        # Focus the empty field on add (name), or the value on edit so the
        # user lands on the most-likely-to-edit input.
        if self._is_new:
            self._name_entry.grab_focus()
        else:
            self._value_entry.grab_focus()
        self._refresh()

    # ── Validation / refresh ──────────────────────────────────────────

    def _validate(self) -> str | None:
        """Return an error message if the form is invalid, else ``None``."""
        name = self._name_entry.get_text().strip()
        if not name:
            # Empty is "in progress, not yet apply-able" — surface as a
            # disabled Apply button without a screaming error.
            return None
        if not _NAME_RE.match(name):
            return (
                "Name must start with a letter or underscore and contain "
                "only letters, digits, and underscores."
            )
        # Reserved names are owned by the Cursor page; editing them here
        # would silently get overwritten on save (the cursor page rebuilds
        # them from its own widgets). Block at the dialog instead of
        # surfacing a confusing post-save no-op.
        #
        # Allow the user to keep editing an existing entry whose name was
        # *already* reserved when they opened the dialog — that's the
        # only path to clean up stale data after a future name addition
        # to RESERVED_NAMES, and the value is the user's anyway. New
        # entries can't pick a reserved name, period.
        if name in RESERVED_NAMES and name != self._original_name:
            return f"‘{name}’ is managed by the Cursor page. Edit it there instead."
        return None

    def _on_changed(self, *_args: object) -> None:
        self._refresh()

    def _refresh(self) -> None:
        rule = self._build_entry()

        if rule is not None:
            self._preview_label.set_text(rule.to_line())
        else:
            self._preview_label.set_text("(entry incomplete)")

        error = self._validate()
        if error is not None:
            self._error_label.set_label(error)
            self._error_label.set_visible(True)
        else:
            self._error_label.set_visible(False)

        # Apply gates on: name non-empty, validation passes, and value
        # non-empty (Hyprland 0.54 rejects ``env = NAME,`` with no value).
        ok = rule is not None and rule.name != "" and rule.value != "" and error is None
        self._apply_btn.set_sensitive(ok)

    def _build_entry(self) -> EnvVar | None:
        """Snapshot the current dialog state into an :class:`EnvVar`."""
        name = self._name_entry.get_text().strip()
        # Value preserves leading/trailing spaces? No — Hyprland's parser
        # strips them on read, so we strip on write to keep round-trips
        # idempotent. If a user really needs leading whitespace they're
        # already off the supported path.
        value = self._value_entry.get_text().strip()
        if not name:
            return None
        return EnvVar(name=name, value=value)

    # ── Apply ─────────────────────────────────────────────────────────

    def _on_apply(self, *_args: object) -> None:
        if not self._apply_btn.get_sensitive():
            return
        entry = self._build_entry()
        if entry is None:
            return
        if self._on_apply_callback is not None:
            self._on_apply_callback(entry)
        self.close()


__all__ = ["EnvVarEditDialog"]
