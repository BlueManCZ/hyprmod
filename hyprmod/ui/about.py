"""About dialog — standard GNOME ``Adw.AboutDialog``.

Presents version, license, credits, and links to the project's homepage
and issue tracker. Attached to the ``win.show-about`` action, which is
registered in ``HyprModWindow._build_ui`` and exposed through the primary
menu on every page header.

The version string is read from installed package metadata so it stays in
lock-step with ``pyproject.toml`` — no hardcoded duplicate to drift out
of date when we cut a release.
"""

from importlib.metadata import PackageNotFoundError, version

from gi.repository import Adw, Gtk

APPLICATION_NAME = "HyprMod"
APPLICATION_ID = "io.github.bluemancz.hyprmod"
DEVELOPER_NAME = "Ivo Šmerek"
COPYRIGHT = "© 2026 Ivo Šmerek"
COMMENTS = "A native GTK4/libadwaita settings app for Hyprland"
WEBSITE = "https://github.com/BlueManCZ/hyprmod"
ISSUE_URL = "https://github.com/BlueManCZ/hyprmod/issues"

# Adw.AboutDialog renders entries in the ``developers`` list as clickable
# links when they match the "Name URL" format. Keep the handle in the URL
# so users can reach the author's GitHub profile from the Credits tab.
DEVELOPERS = ["Ivo Šmerek https://github.com/BlueManCZ"]


def _get_version() -> str:
    """Return the installed package version, or a placeholder if unavailable.

    ``PackageNotFoundError`` can happen during editable installs before
    ``pip install -e .`` has been run, or in isolated test environments
    that import the package without installing it.
    """
    try:
        return version("hyprmod")
    except PackageNotFoundError:
        return "unknown"


def build_about_dialog() -> Adw.AboutDialog:
    """Construct the About dialog for the application."""
    return Adw.AboutDialog(
        application_name=APPLICATION_NAME,
        application_icon=APPLICATION_ID,
        version=_get_version(),
        developer_name=DEVELOPER_NAME,
        developers=DEVELOPERS,
        copyright=COPYRIGHT,
        license_type=Gtk.License.GPL_3_0,
        comments=COMMENTS,
        website=WEBSITE,
        issue_url=ISSUE_URL,
    )
