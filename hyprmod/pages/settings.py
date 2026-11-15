"""Application settings page: config path, auto-save, language, and app preferences."""

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from hyprmod.core import config
from hyprmod.core.setup import migrate_config_path
from hyprmod.i18n import _, get_current_language, get_supported_languages, set_language
from hyprmod.ui import confirm, make_page_layout


class SettingsPage:
    """Settings page for application preferences."""

    def __init__(self, window):
        self._window = window

    def build(self, header: Adw.HeaderBar) -> Adw.ToolbarView:
        toolbar, _page_box, content_box, _scrolled = make_page_layout(header=header)

        # ── Config section ──
        config_group = Adw.PreferencesGroup(
            title=_("Configuration"),
            description=_("Manage where HyprMod reads and writes Hyprland settings."),
        )

        default_str = str(config.default_managed_path())
        self._config_path_row = Adw.EntryRow(title=_("Config file path"))
        self._config_path_row.set_text(self._window.config_path)
        self._config_path_row.set_show_apply_button(True)
        self._config_path_row.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
        self._config_path_row.set_tooltip_text(_("Default: {path}").format(path=default_str))
        # Track the handler id so ``_reset_path_text`` can block it while
        # programmatically resetting the entry. Toggling
        # ``set_show_apply_button`` to suppress the apply signal would
        # work but is the boolean-flag anti-pattern.
        self._apply_handler_id = self._config_path_row.connect("apply", self._on_config_path_apply)

        browse_btn = Gtk.Button(icon_name="document-open-symbolic")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text(_("Browse\u2026"))
        browse_btn.connect("clicked", self._on_browse_config)
        self._config_path_row.add_suffix(browse_btn)

        config_group.add(self._config_path_row)
        content_box.append(config_group)

        # ── Behavior section ──
        behavior_group = Adw.PreferencesGroup(
            title=_("Behavior"),
        )

        self._auto_save_row = Adw.SwitchRow(
            title=_("Auto-save"),
            subtitle=_("Automatically save changes after each modification."),
        )
        self._auto_save_row.set_active(self._window.auto_save)
        self._auto_save_row.connect("notify::active", self._on_auto_save_toggled)

        behavior_group.add(self._auto_save_row)
        content_box.append(behavior_group)

        # ── Language section ──
        language_group = Adw.PreferencesGroup(
            title=_("Language"),
        )

        languages = get_supported_languages()
        self._language_row = Adw.ComboRow(
            title=_("Interface language"),
            subtitle=_("Choose the display language for HyprMod."),
        )
        string_list = Gtk.StringList()
        for code, name in languages.items():
            string_list.append(name)
        self._language_row.set_model(string_list)
        # Select the current language index
        current = get_current_language()
        for idx, code in enumerate(languages.keys()):
            if code == current:
                self._language_row.set_selected(idx)
                break
        else:
            self._language_row.set_selected(0)
        self._language_row.connect("notify::selected-item", self._on_language_changed)

        language_group.add(self._language_row)
        content_box.append(language_group)

        return toolbar

    def sync_auto_save(self, value: bool):
        """Update the switch to reflect an external auto-save change."""
        if self._auto_save_row.get_active() != value:
            self._auto_save_row.set_active(value)

    def _reset_path_text(self, text: str):
        """Reset the entry text without re-arming the apply signal."""
        self._config_path_row.handler_block(self._apply_handler_id)
        try:
            self._config_path_row.set_text(text)
        finally:
            self._config_path_row.handler_unblock(self._apply_handler_id)

    # ── Callbacks ──

    def _apply_new_path(self, new_text: str, *, overwrite_confirmed: bool = False):
        """Validate and apply a new config file path."""
        new_path = Path(new_text).expanduser()
        old_path = config.managed_path()

        if new_path.resolve() == old_path.resolve():
            return

        if new_path.exists() and not overwrite_confirmed:

            def _on_confirm():
                self._do_migrate(old_path, new_path)
                self._reset_path_text(self._window.config_path)

            confirm(
                self._window,
                heading=_("Overwrite existing file?"),
                body=_(
                    "{name} already exists at this location. It will be replaced with the "
                    "current config."
                ).format(name=new_path.name),
                label=_("Overwrite"),
                on_confirm=_on_confirm,
            )
        else:
            self._do_migrate(old_path, new_path)

    def _do_migrate(self, old_path: Path, new_path: Path):
        """Move config and update internal state."""
        try:
            migrate_config_path(old_path, new_path)
        except OSError as e:
            self._window.show_toast(
                _("Cannot move config: {error}").format(error=e.strerror), timeout=5, copy=True
            )
            return

        self._window.config_path = str(new_path)

    def _on_config_path_apply(self, row):
        text = row.get_text().strip()
        if not text:
            text = str(config.default_managed_path())
        if text != self._window.config_path:
            self._apply_new_path(text)
        self._reset_path_text(self._window.config_path)

    def _on_browse_config(self, _btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select config file"))

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
            # User cancelled the dialog or chooser failed; nothing to apply.
            return
        if gfile:
            self._apply_new_path(gfile.get_path(), overwrite_confirmed=True)
            self._reset_path_text(self._window.config_path)

    def _on_auto_save_toggled(self, row, _pspec):
        self._window.set_auto_save(row.get_active())

    def _on_language_changed(self, row, _pspec):
        """Apply the selected language and persist it to GSettings."""
        languages = get_supported_languages()
        codes = list(languages.keys())
        selected_idx = row.get_selected()
        if selected_idx is not None and selected_idx < len(codes):
            code = codes[selected_idx]
            set_language(code)
            self._window.set_language(code)
