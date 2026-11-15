"""About dialog — standard GNOME ``Adw.AboutDialog``.

Presents version, license, credits, and links to the project's homepage
and issue tracker. Attached to the ``win.show-about`` action, which is
registered in ``HyprModWindow._build_ui`` and exposed through the primary
menu on every page header.
"""

from gi.repository import Adw, Gtk
from hyprland_schema import HYPRLAND_VERSION

from hyprmod.constants import APPLICATION_ID
from hyprmod.core.bug_report import REPO_URL, build_bug_report_url, hyprmod_version
from hyprmod.i18n import _

APPLICATION_NAME = "HyprMod"
DEVELOPER_NAME = "Ivo Šmerek"
COPYRIGHT = "© 2026 Ivo Šmerek"
COMMENTS = _("A native GTK4/libadwaita settings app for Hyprland")

# Adw.AboutDialog renders entries in the ``developers`` list as clickable
# links when they match the "Name URL" format. Keep the handle in the URL
# so users can reach the author's GitHub profile from the Credits tab.
DEVELOPERS = ["Ivo Šmerek https://github.com/BlueManCZ"]


def build_about_dialog(running_hyprland_version: str | None = None) -> Adw.AboutDialog:
    """Construct the About dialog for the application.

    *running_hyprland_version* is the version string reported by the live
    compositor (e.g. ``"0.54.3"``). When provided, it's shown in the main
    description; when omitted (Hyprland offline), the bundled schema
    version is shown instead so users still have a version reference.
    """
    version = hyprmod_version()

    # hyprland_schema keys versions by the GitHub tag (``vX.Y.Z``) while the
    # live compositor reports without the prefix. Strip so both numbers
    # display consistently.
    schema_version = HYPRLAND_VERSION.removeprefix("v")
    running = running_hyprland_version.removeprefix("v") if running_hyprland_version else None

    if running:
        hyprland_line = f"Hyprland {running}"
    else:
        hyprland_line = _("Hyprland schema (bundled): {schema_version}").format(
            schema_version=schema_version
        )

    # debug_info goes into the Troubleshooting section — give bug reporters
    # both numbers unambiguously, regardless of detection state.
    running_line = (
        _("Hyprland (running): {version}").format(version=running)
        if running
        else _("Hyprland (running): not detected")
    )
    schema_line = _("Hyprland schema (bundled): {schema_version}").format(
        schema_version=schema_version
    )
    debug_info = f"HyprMod {version}\n{running_line}\n{schema_line}"

    # Route the standard "Report a problem" link through the same prefilled
    # URL the Help menu uses, so reports filed from either entry point carry
    # the version/config-language block (no second-class blank-issue path).
    issue_url = build_bug_report_url(running_hyprland_version=running_hyprland_version)

    return Adw.AboutDialog(
        application_name=APPLICATION_NAME,
        application_icon=APPLICATION_ID,
        version=version,
        developer_name=DEVELOPER_NAME,
        developers=DEVELOPERS,
        copyright=COPYRIGHT,
        license_type=Gtk.License.GPL_3_0,
        comments=f"{_(COMMENTS)}\n\n{hyprland_line}",
        debug_info=debug_info,
        website=REPO_URL,
        issue_url=issue_url,
    )
