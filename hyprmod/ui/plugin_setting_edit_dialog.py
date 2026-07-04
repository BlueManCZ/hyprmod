"""Dialog for adding or editing a single plugin setting."""

import re
from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.core.plugins import PluginSetting
from hyprmod.ui import build_preview_group
from hyprmod.ui.dialog import SingletonDialogMixin


class PluginSettingEditDialog(SingletonDialogMixin, Adw.Dialog):
    """Add/edit dialog for a single plugin setting."""

    def __init__(
        self,
        *,
        entry: PluginSetting | None = None,
        on_apply: Callable[[PluginSetting], None] | None = None,
    ):
        super().__init__()
        self._is_new = entry is None
        self._on_apply_callback = on_apply

        if self._is_new:
            title = "Add Plugin Setting"
        else:
            title = "Edit Plugin Setting"

        self.set_title(title)
        self.set_content_width(540)
        self.set_content_height(500)

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
        var_group = Adw.PreferencesGroup(title="Plugin Setting")
        var_group.set_description(
            "Define custom settings for Hyprland plugins. These settings will be written "
            "into a plugin { ... } block."
        )

        self._plugin_entry = Adw.EntryRow(title="Plugin Name")
        self._plugin_entry.set_text(entry.plugin_name if entry else "")
        self._plugin_entry.connect("changed", self._on_changed)
        var_group.add(self._plugin_entry)

        self._key_entry = Adw.EntryRow(title="Setting Key")
        self._key_entry.set_text(entry.key if entry else "")
        self._key_entry.connect("changed", self._on_changed)
        var_group.add(self._key_entry)

        self._value_entry = Adw.EntryRow(title="Setting Value")
        self._value_entry.set_text(entry.value if entry else "")
        self._value_entry.connect("changed", self._on_changed)
        self._value_entry.connect("entry-activated", lambda _e: self._on_apply())
        var_group.add(self._value_entry)

        content.append(var_group)

        self._error_label = Gtk.Label()
        self._error_label.set_xalign(0)
        self._error_label.set_wrap(True)
        self._error_label.add_css_class("error")
        self._error_label.add_css_class("caption")
        self._error_label.set_visible(False)
        content.append(self._error_label)

        preview_group, self._preview_label = build_preview_group()
        content.append(preview_group)

        toolbar.set_content(content)
        self.set_child(toolbar)

        if self._is_new:
            self._plugin_entry.grab_focus()
        else:
            self._value_entry.grab_focus()

        self._update_state()

    def _on_changed(self, _entry) -> None:
        self._update_state()

    def _update_state(self) -> None:
        plugin_name = self._plugin_entry.get_text().strip()
        key = self._key_entry.get_text().strip()
        value = self._value_entry.get_text().strip()

        is_valid = True
        err_msg = ""

        if not plugin_name:
            is_valid = False
            err_msg = "Plugin name cannot be empty."
        elif not key:
            is_valid = False
            err_msg = "Setting key cannot be empty."
        elif not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", plugin_name):
            is_valid = False
            err_msg = "Plugin name must be a valid identifier."
        elif not re.match(r"^[A-Za-z0-9_:-]+$", key):
            is_valid = False
            err_msg = "Setting key must be a valid identifier."

        if not is_valid and (plugin_name or key):
            self._error_label.set_text(err_msg)
            self._error_label.set_visible(True)
        else:
            self._error_label.set_visible(False)

        self._apply_btn.set_sensitive(is_valid)

        if is_valid:
            lines = ["plugin {", f"    {plugin_name} {{", f"        {key} = {value}", "    }", "}"]
            self._preview_label.set_text("\n".join(lines))
        else:
            self._preview_label.set_text("")

    def _on_apply(self, _btn=None) -> None:
        if not self._apply_btn.get_sensitive():
            return

        entry = PluginSetting(
            plugin_name=self._plugin_entry.get_text().strip(),
            key=self._key_entry.get_text().strip(),
            value=self._value_entry.get_text().strip(),
        )

        if self._on_apply_callback:
            self._on_apply_callback(entry)
        self.close()
