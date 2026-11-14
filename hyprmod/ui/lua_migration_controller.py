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

from gi.repository import Adw, Gio
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
    require a Hyprland new enough to support Lua (``0.55+``) and a user
    not already on Lua. The dismissed-banner flag is intentionally not
    folded in here — dismissing the banner is "stop pestering me" and
    must leave the menu action reachable.
    """
    return config.supports_lua_migration(version) and not config.is_lua_mode()


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
        """True on 0.55+ Hyprlang setups the user hasn't dismissed."""
        if not _migration_actionable(self._get_version()):
            return False
        if self._settings and self._settings.get_boolean(_DISMISSED_KEY):
            return False
        return True

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

        # A clean run produced ``.lua`` files but Hyprland is still running
        # with the ``.conf`` parser — config language is locked at compositor
        # startup. Surface that explicitly so the user isn't surprised when
        # live-apply keeps using the old config until they next log in.
        if not result.errors and result.written:
            self._mark_done()
            self._repoint_stored_path(result.written)
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


__all__ = ["ACTION_NAME", "LuaMigrationController"]
