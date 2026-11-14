"""Controller for the Hyprlang → Lua migration flow.

Owns the banner widget, the menu/action wiring, the dialog open path, and
the post-success cleanup (mark-dismissed, repoint stored config path,
restart notice). Keeps :class:`hyprmod.window.HyprModWindow` ignorant of
the migration internals — the window holds an instance and calls a few
small entry points.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib
from hyprland_config import ConversionResult

from hyprmod.core import config
from hyprmod.ui import ShowToast
from hyprmod.ui.lua_migration_banner import LuaMigrationBanner
from hyprmod.ui.lua_migration_dialog import LuaMigrationDialog

if TYPE_CHECKING:
    from hyprmod.window import HyprModWindow


# Persisted "user clicked Don't show again" flag — schema in
# ``hyprmod/data/io.github.bluemancz.hyprmod.gschema.xml``.
_DISMISSED_KEY = "lua-migration-banner-dismissed"

# Window-scoped GIO action name. Referenced as ``win.<ACTION_NAME>`` by
# menu items in :mod:`hyprmod.window`; shared as a constant so a rename
# breaks both sites at once instead of silently disabling the menu item.
ACTION_NAME = "migrate-to-lua"


def _migration_actionable(version: str | None) -> bool:
    """Whether a Lua migration can actually produce a useful result now.

    Shared by the banner-visibility and action-enabled predicates: both
    require a Hyprland new enough to support Lua (``0.55+``) and either a
    user not already on Lua or one whose ``hyprland.conf`` is stranded
    beside a generated ``hyprland.lua``. The dismissed-banner flag is
    intentionally not folded in here — dismissing the banner is "stop
    pestering me" and must leave the menu action reachable.
    """
    if not config.supports_lua_migration(version):
        return False
    return not config.is_lua_mode() or config.legacy_config_stranded()


class LuaMigrationController:
    """Owns the banner, the action, and the migration completion flow."""

    def __init__(
        self,
        window: "HyprModWindow",
        settings: Gio.Settings | None,
        *,
        show_toast: ShowToast,
        get_hyprland_version: Callable[[], str | None],
    ):
        self._window = window
        self._settings = settings
        self._show_toast = show_toast
        self._get_version = get_hyprland_version
        # Populated by :meth:`install_action`; before that, refresh() is
        # a banner-only update.
        self._action: Gio.SimpleAction | None = None

        self._banner = LuaMigrationBanner(
            on_migrate=self.start_migration,
            on_dismiss=self._mark_done,
            stranded=config.legacy_config_stranded(),
        )
        self._banner.set_reveal_child(self._should_offer())

    # ── Public surface ────────────────────────────────────────────────

    @property
    def banner(self) -> LuaMigrationBanner:
        """The banner widget — caller is responsible for inserting it."""
        return self._banner

    def install_action(self, action_map: Gio.ActionMap) -> None:
        """Register the ``migrate-to-lua`` action so menus can reach it."""
        action = Gio.SimpleAction.new(ACTION_NAME, None)
        action.connect("activate", lambda *_: self.start_migration())
        action.set_enabled(self._action_applicable())
        action_map.add_action(action)
        self._action = action

    def start_migration(self) -> None:
        """Open the migration wizard (singleton)."""
        LuaMigrationDialog.present_singleton(self._window, on_done=self._on_dialog_done)

    def refresh(self) -> None:
        """Re-evaluate banner visibility + action availability (e.g. after IPC reconnect)."""
        self._banner.set_reveal_child(self._should_offer())
        if self._action is not None:
            self._action.set_enabled(self._action_applicable())

    # ── Internals ─────────────────────────────────────────────────────

    def _should_offer(self) -> bool:
        """True on 0.55+ Hyprlang setups the user hasn't dismissed.

        A stranded config is offered regardless: the dismissal was an answer
        to "Lua is recommended", from before Hyprland stopped reading the
        user's config.
        """
        if not _migration_actionable(self._get_version()):
            return False
        if config.legacy_config_stranded():
            return True
        return not (self._settings and self._settings.get_boolean(_DISMISSED_KEY))

    def _action_applicable(self) -> bool:
        """True when the menu action should be enabled (ignores dismissal)."""
        return _migration_actionable(self._get_version())

    def _mark_done(self) -> None:
        if self._settings is not None:
            self._settings.set_boolean(_DISMISSED_KEY, True)
        self.refresh()

    def _on_dialog_done(self, result: ConversionResult) -> None:
        # Surface success or per-path errors via a toast so the wizard
        # itself stays focused on the preview-and-confirm flow.
        if result.errors:
            shown = result.errors[:3]
            more = len(result.errors) - len(shown)
            message = "Migration finished with errors: " + "; ".join(shown)
            if more > 0:
                message += f" (+{more} more)"
            self._show_toast(message, copy=True)
        elif result.skipped:
            self._show_toast(
                f"Skipped {len(result.skipped)} existing .lua "
                "(toggle 'Overwrite' to write them too)."
            )
        else:
            self._show_toast(f"Wrote {len(result.written)} Lua file(s).")

        # A clean run produced ``.lua`` files. A compositor on the ``.conf``
        # parser keeps it until the next login, since the config language is
        # locked at startup, so say so. One already on Lua was running its
        # generated config and can load the recovered one right away.
        if not result.errors and result.written:
            self._mark_done()
            self._repoint_stored_path(result.written)
            if self._window.hypr.is_live_lua_mode():
                self._show_recovered_notice()
            else:
                self._show_restart_notice()

    def _repoint_stored_path(self, written: list[Path]) -> None:
        """Swap a saved .conf ``config-path`` to its newly-written .lua sibling.

        The in-memory override stays on .conf for the rest of this session
        (live-apply still hits the running Hyprlang compositor — the
        restart notice warns about that). Only the persisted GSetting
        moves to .lua, so the next hyprmod launch — after the user logs
        out and back into a Lua-mode Hyprland — writes to the file
        Hyprland will actually load.
        """
        if self._settings is None:
            return
        stored = self._settings.get_string("config-path")
        new_value = config.lua_replacement_for_stored_path(stored, written)
        if new_value is not None:
            self._settings.set_string("config-path", new_value)

    def _show_restart_notice(self) -> None:
        """Tell the user a logout/login is needed for the new Lua config to take over."""
        dialog = Adw.AlertDialog(
            heading="Lua migration complete",
            body=(
                "Hyprland is still running with your existing hyprland.conf — "
                "the config language is fixed at compositor startup, so the new "
                "hyprland.lua won't take over until you log out and log back in.\n\n"
                "Until then, live-preview changes in HyprMod still go through "
                "the .conf path and may behave inconsistently with the new "
                ".lua files on disk."
            ),
        )
        dialog.add_response("ok", "Got it")
        dialog.set_default_response("ok")
        dialog.present(self._window)

    def _show_recovered_notice(self) -> None:
        """Confirm the recovery, then rebuild the window on the migrated config."""
        dialog = Adw.AlertDialog(
            heading="Configuration recovered",
            body=(
                "hyprland.lua now holds your migrated configuration. HyprMod "
                "reloads Hyprland and reopens to pick it up."
            ),
        )
        dialog.add_response("ok", "Reload")
        dialog.set_default_response("ok")
        # Deferred so the dialog finishes closing before its window goes.
        dialog.connect("response", lambda *_: GLib.idle_add(self._rebuild_window))
        dialog.present(self._window)

    def _rebuild_window(self) -> None:
        """Replace the window with one built from the migrated config.

        Every page read the generated config at startup. A new window
        reloads the compositor, reads the config tree again and offers
        onboarding when the migrated config does not load HyprMod's file.
        """
        app = self._window.get_application()
        if app is None:
            return
        # Held so the application does not quit between the two windows.
        app.hold()
        self._window.destroy()
        app.activate()
        app.release()


__all__ = ["ACTION_NAME", "LuaMigrationController"]
