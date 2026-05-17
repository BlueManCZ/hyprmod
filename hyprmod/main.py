"""HyprMod application entry point."""

import signal
import sys
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from hyprmod.constants import APPLICATION_ID
from hyprmod.core.setup import needs_setup, run_setup
from hyprmod.install import ensure_registered_silently, install_user_files, uninstall_user_files
from hyprmod.ui import try_with_toast
from hyprmod.ui.onboarding_dialog import OnboardingDialog
from hyprmod.window import HyprModWindow


class HyprModApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        icon_dir = str(Path(__file__).resolve().parent / "data" / "icons")
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            paths = theme.get_search_path() or []
            theme.set_search_path([icon_dir, *paths])
        # Rescue users who did `pipx install hyprmod` without running the
        # install script: drop a .desktop + icon into $XDG_DATA_HOME so
        # the app shows up in the launcher next time. No-op if any entry
        # is already visible on XDG_DATA_DIRS (distro install, prior run).
        ensure_registered_silently()

    def do_activate(self):
        win = self.props.active_window
        if not isinstance(win, HyprModWindow):
            win = HyprModWindow(application=self)

        if needs_setup():
            window = win  # locally typed for the closure below

            def _on_setup() -> None:
                try_with_toast(window.show_toast, "Setup failed", run_setup)

            OnboardingDialog(on_setup=_on_setup).present(win)

        win.present()


def main():
    if "--install" in sys.argv[1:]:
        install_user_files()
        return 0
    if "--uninstall" in sys.argv[1:]:
        uninstall_user_files()
        return 0

    app = HyprModApp()

    # Route SIGINT/SIGTERM through the GLib main loop so Ctrl-C from the
    # terminal (or `kill`) shuts the app down cleanly. Python's default
    # SIGINT handler can't interrupt GLib's C-level loop — the exception
    # only surfaces when control next returns to Python, which typically
    # produces a stray traceback after the UI has already been frozen.
    def _on_signal(*_args) -> bool:
        app.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _on_signal)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _on_signal)

    return app.run(sys.argv)


if __name__ == "__main__":
    main()
