"""Confirm/revert countdown controller for monitor changes."""

from collections.abc import Callable

from gi.repository import Adw, GLib

from hyprmod.ui.timer import Timer

CONFIRM_TIMEOUT = 15  # seconds before auto-revert


class ConfirmController:
    """Manages the confirmation/revert countdown banner.

    After a monitor change, shows a banner with a countdown. If the user
    doesn't confirm within the timeout, the revert callback is invoked.
    """

    def __init__(
        self,
        banner: Adw.Banner,
        *,
        is_dirty: Callable[[], bool],
        on_revert: Callable[[], None],
        on_confirmed: Callable[[], None],
    ):
        self._banner = banner
        self._is_dirty = is_dirty
        self._on_revert = on_revert
        self._on_confirmed = on_confirmed
        self._showing = False
        self._seconds_left = 0
        self._countdown = Timer()
        self._debounce = Timer()

        banner.set_button_label("Keep Changes")
        banner.connect("button-clicked", self._on_keep)

    def maybe_confirm(self):
        if self._is_dirty():
            self._schedule()
        else:
            self.cancel()

    def confirm(self):
        """Accept the current monitor configuration (hides banner, stops countdown)."""
        self._debounce.cancel()
        if self._showing:
            self._on_keep()

    def cancel(self):
        self._debounce.cancel()
        if self._showing:
            self._countdown.cancel()
            self._showing = False
            self._reset_banner()

    def cancel_debounce(self):
        """Cancel only the debounce timer (used during drag)."""
        self._debounce.cancel()

    # -- Internal --

    def _schedule(self):
        if self._showing:
            self._seconds_left = CONFIRM_TIMEOUT
            self._update_title()
            self._set_urgency(None)
            return
        self._debounce.schedule(3000, self._show)

    def _show(self):
        if self._showing or not self._is_dirty():
            return GLib.SOURCE_REMOVE
        self._seconds_left = CONFIRM_TIMEOUT
        self._showing = True
        self._update_title()
        self._banner.set_revealed(True)
        self._countdown.schedule(1000, self._on_tick)
        return GLib.SOURCE_REMOVE

    def _on_tick(self) -> bool:
        self._seconds_left -= 1
        if self._seconds_left <= 0:
            self._showing = False
            self._reset_banner()
            self._on_revert()
            return GLib.SOURCE_REMOVE
        self._update_title()
        if self._seconds_left <= 5:
            self._set_urgency("confirm-urgent")
        elif self._seconds_left <= 10:
            self._set_urgency("confirm-warning")
        else:
            self._set_urgency(None)
        return GLib.SOURCE_CONTINUE

    def _on_keep(self, *_args):
        self._countdown.cancel()
        self._debounce.cancel()
        self._showing = False
        self._reset_banner()
        self._on_confirmed()

    def _update_title(self):
        self._banner.set_title(
            f"Reverting in {self._seconds_left}s unless changed or confirmed"
        )

    def _set_urgency(self, level: str | None):
        self._banner.remove_css_class("confirm-warning")
        self._banner.remove_css_class("confirm-urgent")
        if level:
            self._banner.add_css_class(level)

    def _reset_banner(self):
        self._banner.set_revealed(False)
        self._set_urgency(None)
