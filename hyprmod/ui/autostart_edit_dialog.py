"""Dialog for adding or editing a single autostart entry.

Lives outside ``hyprmod/pages/autostart.py`` so the page module isn't a
catch-all; matches the existing pattern of putting reusable dialogs
under ``hyprmod/ui/`` (see ``app_picker.py``).
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.core import config
from hyprmod.core.autostart import ExecData
from hyprmod.core.desktop_apps import DesktopApp
from hyprmod.ui.app_picker import AppPickerDialog
from hyprmod.ui.dialog import SingletonDialogMixin


class AutostartEditDialog(SingletonDialogMixin, Adw.Dialog):
    """Dialog for adding or editing a single autostart entry.

    Two inputs:

    1. **Command** — free-text, with a "Pick app" suffix that opens the
       installed-apps picker. Picking auto-fills the command field; the
       user can still edit before applying (e.g. to add args).
    2. **Re-run on every config reload** — switch row, off by default.
       When off, the entry is saved as ``exec-once`` (the common case);
       when on, as ``exec``. Hidden inside the dialog as a single
       advanced toggle rather than presented as an equal-weight choice.

    Open via :meth:`SingletonDialogMixin.present_singleton` rather than
    constructing directly — that path collapses rapid double-clicks
    on the trigger button into a single dialog.
    """

    def __init__(
        self,
        *,
        entry: ExecData | None = None,
        initial_advanced: bool = False,
        on_apply: Callable[[ExecData], None] | None = None,
    ):
        super().__init__()
        self._is_new = entry is None
        self._on_apply_callback = on_apply

        self.set_title("Add Autostart Entry" if self._is_new else "Edit Autostart Entry")
        self.set_content_width(520)
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

        # Command group: entry row with a "pick app" suffix.
        cmd_group = Adw.PreferencesGroup(title="Command")
        cmd_group.set_description(
            "Pick an installed app or type any shell command. "
            "Hyprland passes this to /bin/sh -c, so quoting and "
            "metacharacters work as in shell."
        )
        self._cmd_entry = Adw.EntryRow(title="Command line")
        self._cmd_entry.set_text(entry.command if entry else "")
        self._cmd_entry.connect("changed", self._on_changed)
        # Apply on Enter.
        self._cmd_entry.connect("entry-activated", lambda _e: self._on_apply())

        pick_btn = Gtk.Button.new_from_icon_name("system-search-symbolic")
        pick_btn.set_valign(Gtk.Align.CENTER)
        pick_btn.add_css_class("flat")
        pick_btn.set_tooltip_text("Pick from installed apps")
        pick_btn.connect("clicked", lambda _b: self._on_pick_app())
        self._cmd_entry.add_suffix(pick_btn)

        cmd_group.add(self._cmd_entry)
        content.append(cmd_group)

        # Behaviour group: a single switch instead of an exec/exec-once
        # combo. Default off (= exec-once) matches the overwhelmingly
        # common case; flipping it on is the "advanced" path.
        when_group = Adw.PreferencesGroup(title="Behaviour")
        self._reload_switch = Adw.SwitchRow(title="Re-run on every config reload")
        self._reload_switch.set_subtitle(
            "Off: run once at Hyprland startup (recommended).\n"
            "On: re-run every time the config is reloaded — useful for "
            "cleanup commands like pkill -SIGUSR1."
        )
        starting_advanced = entry.keyword == config.KEYWORD_EXEC if entry else initial_advanced
        self._reload_switch.set_active(starting_advanced)
        when_group.add(self._reload_switch)
        content.append(when_group)

        toolbar.set_content(content)
        self.set_child(toolbar)

        # Focus the command field so users can start typing right away
        # (or hit the picker button, which is one Tab away).
        self._cmd_entry.grab_focus()
        self._refresh_apply_sensitive()

    # ── App picker integration ──

    def _on_pick_app(self) -> None:
        def on_pick(app: DesktopApp) -> None:
            self._cmd_entry.set_text(app.command)
            # Move focus back to the command field so the user can edit
            # the auto-filled command (e.g. to add args) without an
            # extra click.
            self._cmd_entry.grab_focus()

        AppPickerDialog.present_singleton(self, on_pick=on_pick)

    # ── Apply gating ──

    def _selected_keyword(self) -> str:
        return config.KEYWORD_EXEC if self._reload_switch.get_active() else config.KEYWORD_EXEC_ONCE

    def _on_changed(self, *_args: object) -> None:
        self._refresh_apply_sensitive()

    def _refresh_apply_sensitive(self) -> None:
        self._apply_btn.set_sensitive(bool(self._cmd_entry.get_text().strip()))

    def _on_apply(self, *_args: object) -> None:
        command = self._cmd_entry.get_text().strip()
        if not command:
            return
        new_entry = ExecData(keyword=self._selected_keyword(), command=command)
        if self._on_apply_callback is not None:
            self._on_apply_callback(new_entry)
        self.close()


__all__ = ["AutostartEditDialog"]
