"""Application settings page — config path, auto-save, and app preferences."""

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from hyprmod.core import config
from hyprmod.core.setup import migrate_config_path
from hyprmod.ui import confirm, make_page_layout


class SettingsPage:
    """Settings page for application preferences."""

    def __init__(self, window):
        self._window = window

    def build(self, header: Adw.HeaderBar) -> Adw.ToolbarView:
        toolbar, _page_box, content_box, _scrolled = make_page_layout(header=header)

        # ── Config section ──
        config_group = Adw.PreferencesGroup(
            title="Configuration",
            description="Manage where HyprMod reads and writes Hyprland settings.",
        )

        default_str = str(config.default_gui_conf())
        self._config_path_row = Adw.EntryRow(title="Config file path")
        self._config_path_row.set_text(self._window.config_path)
        self._config_path_row.set_show_apply_button(True)
        self._config_path_row.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
        self._config_path_row.set_tooltip_text(f"Default: {default_str}")
        self._config_path_row.connect("apply", self._on_config_path_apply)

        browse_btn = Gtk.Button(icon_name="document-open-symbolic")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text("Browse\u2026")
        browse_btn.connect("clicked", self._on_browse_config)
        self._config_path_row.add_suffix(browse_btn)

        config_group.add(self._config_path_row)
        content_box.append(config_group)

        # ── Behavior section ──
        behavior_group = Adw.PreferencesGroup(
            title="Behavior",
        )

        self._auto_save_row = Adw.SwitchRow(
            title="Auto-save",
            subtitle="Automatically save changes after each modification.",
        )
        self._auto_save_row.set_active(self._window.auto_save)
        self._auto_save_row.connect("notify::active", self._on_auto_save_toggled)

        behavior_group.add(self._auto_save_row)
        content_box.append(behavior_group)

        return toolbar

    def sync_auto_save(self, value: bool):
        """Update the switch to reflect an external auto-save change."""
        if self._auto_save_row.get_active() != value:
            self._auto_save_row.set_active(value)

    def _reset_path_text(self, text: str):
        """Reset the entry text without re-arming the apply button."""
        self._config_path_row.set_show_apply_button(False)
        self._config_path_row.set_text(text)
        self._config_path_row.set_show_apply_button(True)

    # ── Callbacks ──

    def _apply_new_path(self, new_text: str, *, overwrite_confirmed: bool = False):
        """Validate and apply a new config file path."""
        new_path = Path(new_text).expanduser()
        old_path = config.gui_conf()

        if new_path.resolve() == old_path.resolve():
            return

        if new_path.exists() and not overwrite_confirmed:

            def _on_confirm():
                self._do_migrate(old_path, new_path)
                self._reset_path_text(self._window.config_path)

            confirm(
                self._window,
                heading="Overwrite existing file?",
                body=f"{new_path.name} already exists at this location. "
                "It will be replaced with the current config.",
                label="Overwrite",
                on_confirm=_on_confirm,
            )
        else:
            self._do_migrate(old_path, new_path)

    def _do_migrate(self, old_path: Path, new_path: Path):
        """Move config and update internal state."""
        try:
            migrate_config_path(old_path, new_path)
        except OSError as e:
            self._window.show_toast(f"Cannot move config — {e.strerror}", timeout=5)
            return

        self._window.config_path = str(new_path)

    def _on_config_path_apply(self, row):
        text = row.get_text().strip()
        if not text:
            text = str(config.default_gui_conf())
        if text != self._window.config_path:
            self._apply_new_path(text)
        self._reset_path_text(self._window.config_path)

    def _on_browse_config(self, _btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select config file")

        current = Path(self._window.config_path)
        if current.parent.exists():
            dialog.set_initial_folder(Gio.File.new_for_path(str(current.parent)))
        if current.name:
            dialog.set_initial_name(current.name)

        dialog.save(self._window, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            # User cancelled the dialog or chooser failed — nothing to apply.
            return
        if gfile:
            self._apply_new_path(gfile.get_path(), overwrite_confirmed=True)
            self._reset_path_text(self._window.config_path)

    def _on_auto_save_toggled(self, row, _pspec):
        new_val = row.get_active()
        if new_val != self._window.auto_save:
            self._window.auto_save = new_val
            self._window._auto_save_action.set_state(GLib.Variant.new_boolean(new_val))
            if new_val and self._window.has_dirty():
                self._window._on_save()
