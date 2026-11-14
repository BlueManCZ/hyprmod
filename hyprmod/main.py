"""HyprMod application entry point."""

import signal
import sys
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from hyprland_config import ParseError

from hyprmod import cli
from hyprmod.constants import APPLICATION_ID
from hyprmod.core.setup import needs_setup, run_setup
from hyprmod.install import ensure_registered_silently, install_user_files, uninstall_user_files
from hyprmod.ui import has_cairo_support, try_with_toast
from hyprmod.ui.config_error import build_config_error_window
from hyprmod.ui.onboarding_dialog import OnboardingDialog
from hyprmod.window import HyprModWindow

CAIRO_MISSING_BODY = (
    "PyGObject's Cairo bindings are missing, so the monitor layout, the bezier "
    "curve editor, and the animation preview will show up as empty boxes.\n\n"
    "Install them with your package manager: python-cairo on Arch, "
    "python3-gi-cairo on Debian and Ubuntu."
)


class HyprModApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._config_error_window: Adw.ApplicationWindow | None = None

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
            existing = set(self.get_windows())
            try:
                win = HyprModWindow(application=self)
            except (OSError, ParseError) as exc:
                # ``application=self`` registers the window with the app
                # before ``__init__`` reaches the config read, so a failed
                # one is already in the window list. Left there it answers
                # ``active_window`` on the next activation and presents as
                # an empty window over a swallowed traceback.
                for window in set(self.get_windows()) - existing:
                    window.destroy()
                self._show_config_error(str(exc))
                return
        self._dismiss_config_error()

        # A broken install takes precedence over onboarding: stacking two
        # dialogs hides one behind the other, and setup can wait a launch.
        if not has_cairo_support():
            dialog = Adw.AlertDialog(heading="Missing Cairo Bindings", body=CAIRO_MISSING_BODY)
            dialog.add_response("close", "Close")
            dialog.present(win)
        elif needs_setup():
            window = win  # locally typed for the closure below

            def _on_setup() -> None:
                try_with_toast(window.show_bug_toast, "Setup failed", run_setup)

            OnboardingDialog(on_setup=_on_setup).present(win)

        win.present()

    def _show_config_error(self, message: str) -> None:
        """Put the config-error window up in place of the main window.

        Built before the previous one goes so the app never drops to zero
        windows, which would quit it mid-retry.
        """
        error_window = build_config_error_window(self, message=message, on_retry=self.activate)
        self._dismiss_config_error()
        self._config_error_window = error_window
        error_window.present()

    def _dismiss_config_error(self) -> None:
        if self._config_error_window is not None:
            self._config_error_window.destroy()
            self._config_error_window = None


def main():
    args = sys.argv[1:]
    if "--install" in args:
        install_user_files()
        return 0
    if "--uninstall" in args:
        uninstall_user_files()
        return 0
    # Any leading positional token is a CLI command attempt: route it to the
    # parser so typos get a proper error instead of silently launching the GUI
    # (the app takes no positional args). Flags fall through to GTK.
    if args and not args[0].startswith("-"):
        return cli.run(args)

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
