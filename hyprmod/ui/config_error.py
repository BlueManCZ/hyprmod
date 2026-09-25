"""Startup screen shown when the Hyprland config can't be read.

Every page is built from the parsed config, so a config the parser
rejects leaves nothing to show. This window takes the main window's
place for that launch, reports what the parser said, and offers a retry
once the file has been fixed.
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.core.bug_report import build_bug_report_url
from hyprmod.ui.empty_state import EmptyState


def build_config_error_window(
    application: Adw.Application,
    *,
    message: str,
    on_retry: Callable[[], None],
) -> Adw.ApplicationWindow:
    """Build the error window for a config that failed to parse.

    *message* is the parser's own text, which already names the file and
    the offending line.
    """
    window = Adw.ApplicationWindow(application=application, title="HyprMod")
    window.set_default_size(700, 500)

    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    view.set_content(
        EmptyState(
            title="Can't Read Your Config",
            description=f"{message}\n\nFix the file, then try again.",
            icon_name="dialog-warning-symbolic",
            primary_action=("Try Again", on_retry),
            secondary_action=("Report a Bug", lambda: _report_bug(window, message)),
        )
    )
    window.set_content(view)
    return window


def _report_bug(window: Gtk.Window, message: str) -> None:
    # No HyprlandState to ask for the running compositor version: it is
    # the object that failed to build.
    url = build_bug_report_url(title=message, body_extra=message)
    Gtk.UriLauncher.new(url).launch(window, None, None)
