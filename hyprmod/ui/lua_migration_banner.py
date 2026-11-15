"""Top-of-window banner that offers to migrate hyprland.conf to hyprland.lua.

Shown on Hyprland 0.55+ when the user is still on a ``.conf`` entrypoint,
or when their ``hyprland.conf`` is stranded beside a generated ``hyprland.lua``.
``Adw.Banner`` only allows a single action button, so this is a custom
widget matching the ``DirtyBanner`` shape — ``Gtk.Revealer`` + horizontal
``Gtk.Box`` — with both a primary "Migrate" action and a separate
"Don't show again" dismiss that doesn't require opening the migration
dialog first.
"""

from collections.abc import Callable

from gi.repository import Gtk

from hyprmod.core.config import LUA_MIN_VERSION
from hyprmod.i18n import _


def _min_version_label() -> str:
    """Human-readable Hyprland version threshold ("0.55+")."""
    major, minor, _patch = LUA_MIN_VERSION
    return f"{major}.{minor}+"


class LuaMigrationBanner(Gtk.Revealer):
    """Animated banner with Migrate / Don't-show-again actions.

    *on_migrate* fires when the user clicks "Migrate to Lua…" — wire this
    to the same handler as the menu item so the two entry points share
    code paths. *on_dismiss* fires when the user clicks the dismiss
    icon-button and should persist the "never again" preference.

    A *stranded* config is not a recommendation the user can decline:
    their own config is not loaded. That banner says so and has no
    dismiss button. It goes away once the config is recovered, or once
    the user makes the generated one their own.
    """

    def __init__(
        self,
        *,
        on_migrate: Callable[[], None],
        on_dismiss: Callable[[], None],
        stranded: bool,
    ) -> None:
        super().__init__()
        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.set_reveal_child(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # Reuse the .toolbar CSS class so we visually match the existing
        # DirtyBanner and the standard GTK toolbar look.
        box.add_css_class("toolbar")
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.add_css_class("accent")
        box.append(icon)

        if stranded:
            text = _(
                "Hyprland no longer reads hyprland.conf and is running a "
                "generated hyprland.lua instead. Recover your configuration "
                "by migrating it"
            )
        else:
            text = (
                f"{_('Hyprland {ver} recommends Lua configs — ').format(ver=_min_version_label())}"
                f"{_('migrate your hyprland.conf with one click')}"
            )
        label = Gtk.Label(label=text)
        label.set_hexpand(True)
        label.set_xalign(0)
        label.set_wrap(True)
        box.append(label)

        migrate_btn = Gtk.Button(label=_("Migrate to Lua…"))
        migrate_btn.add_css_class("suggested-action")
        migrate_btn.connect("clicked", lambda _b: on_migrate())
        box.append(migrate_btn)

        if not stranded:
            # Icon-only "Don't show again" — close-symbolic reads as "make
            # this go away," and the tooltip clarifies that it's permanent.
            dismiss_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
            dismiss_btn.set_tooltip_text(_("Don't show again"))
            dismiss_btn.add_css_class("flat")
            dismiss_btn.connect("clicked", lambda _b: on_dismiss())
            box.append(dismiss_btn)

        self.set_child(box)
