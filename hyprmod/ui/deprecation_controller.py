"""Controller for the deprecation-migration assistant.

Owns the banner, the menu/action wiring, the dialog open path, and the
post-apply cleanup (toast, cache invalidation, dismissed-state). Keeps
:class:`hyprmod.window.HyprModWindow` ignorant of the scan internals — the
window holds an instance and calls a few small entry points.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Gio
from hyprland_config import default_hyprlang_entrypoint

from hyprmod.core import config, deprecations
from hyprmod.ui import ShowToast
from hyprmod.ui.deprecation_banner import DeprecationBanner
from hyprmod.ui.deprecation_dialog import DeprecationDialog

if TYPE_CHECKING:
    from hyprmod.window import HyprModWindow


# Stored fingerprint of the most-recently-dismissed scan. When the scan
# fingerprint changes (new deprecation appears), the banner re-surfaces.
_DISMISSED_KEY = "deprecations-banner-dismissed-hash"

# Window-scoped GIO action name. Menu items reference ``win.<ACTION_NAME>``.
ACTION_NAME = "review-deprecations"


class DeprecationController:
    """Owns the deprecation banner, action, and apply flow."""

    def __init__(
        self,
        window: "HyprModWindow",
        settings: Gio.Settings | None,
        *,
        show_toast: ShowToast,
        get_hyprland_version: Callable[[], str | None] | None = None,
    ):
        self._window = window
        self._settings = settings
        self._show_toast = show_toast
        # Read live each scan so the controller picks up reconnects to a
        # different Hyprland (rare in practice but cheap to support).
        self._get_hyprland_version = get_hyprland_version
        self._action: Gio.SimpleAction | None = None
        # Cached so dismissal can compare against the live scan without
        # re-running it on every banner-visibility check.
        self._last_scan: deprecations.ScanResult | None = None

        self._banner = DeprecationBanner(
            on_review=self.start_review,
            on_dismiss=self._mark_dismissed,
        )
        self.refresh()

    # ── Public surface ────────────────────────────────────────────────

    @property
    def banner(self) -> DeprecationBanner:
        """The banner widget — caller is responsible for inserting it."""
        return self._banner

    def install_action(self, action_map: Gio.ActionMap) -> None:
        """Register the ``review-deprecations`` action so menus can reach it."""
        action = Gio.SimpleAction.new(ACTION_NAME, None)
        action.connect("activate", lambda *_: self.start_review())
        action.set_enabled(True)
        action_map.add_action(action)
        self._action = action

    def start_review(self) -> None:
        """Open the deprecation dialog (singleton)."""
        DeprecationDialog.present_singleton(
            self._window,
            managed_path=config.managed_path(),
            user_root_path=default_hyprlang_entrypoint(),
            hyprland_version=self._current_version(),
            on_done=self._on_dialog_done,
        )

    def refresh(self) -> None:
        """Re-scan and update banner visibility — call after applying or on demand."""
        self._last_scan = self._scan()
        self._banner.set_summary(len(self._last_scan.files))
        self._banner.set_reveal_child(self._should_offer(self._last_scan))

    # ── Internals ─────────────────────────────────────────────────────

    def _scan(self) -> deprecations.ScanResult:
        return deprecations.scan(
            managed_path=config.managed_path(),
            user_root_path=default_hyprlang_entrypoint(),
            hyprland_version=self._current_version(),
        )

    def _current_version(self) -> str | None:
        if self._get_hyprland_version is None:
            return None
        return self._get_hyprland_version()

    def _should_offer(self, scan: deprecations.ScanResult) -> bool:
        """True when fixable deprecations exist and the user hasn't dismissed this exact set."""
        if not scan.has_fixable:
            return False
        if self._settings is None:
            return True
        return self._settings.get_string(_DISMISSED_KEY) != scan.fingerprint()

    def _mark_dismissed(self) -> None:
        """Pin the current scan fingerprint so the banner stays hidden until it changes."""
        if self._settings is not None and self._last_scan is not None:
            self._settings.set_string(_DISMISSED_KEY, self._last_scan.fingerprint())
        self._banner.set_reveal_child(False)

    def _on_dialog_done(self, results: list[deprecations.ApplyResult]) -> None:
        failures = [r for r in results if not r.success]
        successes = [r for r in results if r.success]

        if failures:
            shown = [r.error for r in failures[:3]]
            more = len(failures) - len(shown)
            message = f"Applied {len(successes)} fix(es) with errors: " + "; ".join(shown)
            if more > 0:
                message += f" (+{more} more)"
            self._show_toast(message)
        elif successes:
            self._show_toast(f"Migrated {len(successes)} file(s).")
        else:
            # User opened the dialog and closed without selecting anything.
            return

        # The managed file may have just been rewritten — drop the cached
        # parse so the next read picks up the change.
        config.invalidate_cache()
        self.refresh()


__all__ = ["ACTION_NAME", "DeprecationController"]
