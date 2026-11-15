"""Top-of-window banner that offers to migrate deprecated Hyprland syntax.

Shown when :func:`hyprmod.core.deprecations.scan` finds fixable
deprecated rules in the user's main config or in hyprmod's managed
file. ``Adw.Banner`` only allows a single action, so this mirrors the
custom Lua-migration banner shape — ``Gtk.Revealer`` + horizontal
``Gtk.Box`` — with a primary "Review…" action and a separate dismiss.
"""

from collections.abc import Callable

from gi.repository import Gtk

from hyprmod.i18n import _


class DeprecationBanner(Gtk.Revealer):
    """Animated banner with Review / Don't-show-again actions.

    *on_review* opens the deprecation dialog; wire this to the same
    handler as the menu item so the two entry points share code paths.
    *on_dismiss* persists the "don't pester me again for this scan"
    fingerprint and hides the banner.
    """

    def __init__(
        self,
        *,
        on_review: Callable[[], None],
        on_dismiss: Callable[[], None],
    ) -> None:
        super().__init__()
        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.set_reveal_child(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("toolbar")
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.add_css_class("accent")
        box.append(icon)

        self._label = Gtk.Label(label="")
        self._label.set_hexpand(True)
        self._label.set_xalign(0)
        self._label.set_wrap(True)
        box.append(self._label)

        review_btn = Gtk.Button(label=_("Review…"))
        review_btn.add_css_class("suggested-action")
        review_btn.connect("clicked", lambda _b: on_review())
        box.append(review_btn)

        dismiss_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        dismiss_btn.set_tooltip_text(_("Don't show again for this scan"))
        dismiss_btn.add_css_class("flat")
        dismiss_btn.connect("clicked", lambda _b: on_dismiss())
        box.append(dismiss_btn)

        self.set_child(box)

    def set_summary(self, file_count: int) -> None:
        """Update the banner label to reflect *file_count* fixable files."""
        if file_count == 1:
            text = _("Deprecated Hyprland syntax detected in 1 file — review the fix?")
        else:
            text = _(
                "Deprecated Hyprland syntax detected in {count} files — review the fixes?"
            ).format(count=file_count)
        self._label.set_text(text)
