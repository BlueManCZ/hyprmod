"""First-run welcome dialog — explains HyprMod and writes the include line.

Shown by :class:`hyprmod.main.HyprModApp` when :func:`hyprmod.core.setup.needs_setup`
returns ``True``. The user can either confirm (we append our include
statement to their top-level Hyprland entrypoint) or defer it.

The dialog adapts its preview line to the active mode: in Lua mode it
shows the ``require("…")`` / ``dofile("…")`` snippet that will be
appended to ``hyprland.lua``; otherwise it shows the ``source = …``
line for ``hyprland.conf``.
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from hyprmod.core import config, setup
from hyprmod.core.config import display_path
from hyprmod.i18n import _


class OnboardingDialog(Adw.AlertDialog):
    """First-run welcome + opt-in to inject the include line.

    *on_setup* fires when the user clicks "Get Started"; the caller is
    responsible for invoking :func:`hyprmod.core.setup.run_setup` (or
    wrapping it for error reporting) so the dialog itself stays free
    of side effects.
    """

    def __init__(self, *, on_setup: Callable[[], None]) -> None:
        super().__init__(heading=_("Welcome to HyprMod"))
        self._on_setup = on_setup

        entry = config.user_entry_path()
        entry_name = entry.name
        lua_mode = config.is_lua_target(entry)
        target = config.managed_path()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        intro = Gtk.Label(
            label=(
                _(
                    "HyprMod gives you a visual interface for all Hyprland settings "
                    "with live preview. Every change is applied instantly to your "
                    "running compositor."
                )
            ),
        )
        intro.set_wrap(True)
        intro.set_xalign(0)
        box.append(intro)

        box.append(
            self._feature_row(
                "view-refresh-symbolic",
                _("Live Preview"),
                _(
                    "Changes apply instantly via hyprctl: see the effect on "
                    "your desktop in real time"
                ),
            )
        )
        box.append(
            self._feature_row(
                "security-high-symbolic",
                _("Safe Config"),
                _("Your {entry_name} is never modified. HyprMod manages its own file").format(
                    entry_name=entry_name
                ),
            )
        )
        box.append(
            self._feature_row(
                "input-keyboard-symbolic",
                _("Keyboard Shortcuts"),
                _("Ctrl+S to save, Ctrl+F to search, Ctrl+Z to undo"),
            )
        )

        setup_text = Gtk.Label(
            label=_("To get started, HyprMod needs to add one line to your {entry_name}:").format(
                entry_name=entry_name
            ),
        )
        setup_text.set_wrap(True)
        setup_text.set_xalign(0)
        setup_text.add_css_class("dim-label")
        box.append(setup_text)

        code_line = (
            setup.render_lua_include(target, for_display=True)
            if lua_mode
            else f"source = {display_path(target)}"
        )
        box.append(self._code_block(code_line))

        self.set_extra_child(box)

        self.add_response("cancel", _("Not Now"))
        self.add_response("setup", _("Get Started"))
        self.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("setup")
        self.set_close_response("cancel")

        self.connect("response", self._on_response)

    @staticmethod
    def _feature_row(icon_name: str, title: str, description: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(24)
        img.set_valign(Gtk.Align.START)
        row.append(img)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_label = Gtk.Label(label=title)
        title_label.set_xalign(0)
        title_label.add_css_class("heading")
        text_box.append(title_label)

        desc_label = Gtk.Label(label=description)
        desc_label.set_xalign(0)
        desc_label.set_wrap(True)
        desc_label.add_css_class("dim-label")
        text_box.append(desc_label)

        row.append(text_box)
        return row

    @staticmethod
    def _code_block(line: str) -> Gtk.Widget:
        """One-line, selectable, monospace snippet for the include statement."""
        container = Gtk.Box()
        container.add_css_class("code-block")

        label = Gtk.Label(label=line)
        label.set_xalign(0)
        label.set_hexpand(True)
        label.set_selectable(True)
        label.set_margin_top(10)
        label.set_margin_bottom(10)
        label.set_margin_start(14)
        label.set_margin_end(14)
        label.add_css_class("monospace")
        label.add_css_class("code-block-text")
        container.append(label)
        return container

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response == "setup":
            self._on_setup()


__all__ = ["OnboardingDialog"]
