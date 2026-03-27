"""HyprMod application entry point."""

import sys
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, Gtk

from hyprmod.core.setup import needs_setup, run_setup
from hyprmod.window import HyprModWindow


class HyprModApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.hyprmod",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        icon_dir = str(Path(__file__).resolve().parent / "data" / "icons")
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            paths = theme.get_search_path() or []
            theme.set_search_path([icon_dir, *paths])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = HyprModWindow(application=self)

        # First-run setup check
        if needs_setup():
            self._show_onboarding(win)

        win.present()

    def _show_onboarding(self, win):
        dialog = Adw.AlertDialog(heading="Welcome to HyprMod")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        intro = Gtk.Label(
            label="HyprMod gives you a visual interface for all Hyprland settings "
            "with live preview. Every change is applied instantly to your "
            "running compositor.",
        )
        intro.set_wrap(True)
        intro.set_xalign(0)
        box.append(intro)

        # Feature highlights
        features = [
            (
                "view-refresh-symbolic",
                "Live Preview",
                "Changes apply instantly via hyprctl "
                "\u2014 see the effect on your desktop in real time",
            ),
            (
                "security-high-symbolic",
                "Safe Config",
                "Your hyprland.conf is never modified. HyprMod manages its own file",
            ),
            (
                "input-keyboard-symbolic",
                "Keyboard Shortcuts",
                "Ctrl+S to save, Ctrl+F to search, Ctrl+Z to undo",
            ),
        ]

        for icon, title, desc in features:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(24)
            img.set_valign(Gtk.Align.START)
            row.append(img)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title_label = Gtk.Label(label=title)
            title_label.set_xalign(0)
            title_label.add_css_class("heading")
            text_box.append(title_label)

            desc_label = Gtk.Label(label=desc)
            desc_label.set_xalign(0)
            desc_label.set_wrap(True)
            desc_label.add_css_class("dim-label")
            text_box.append(desc_label)

            row.append(text_box)
            box.append(row)

        setup_text = Gtk.Label(
            label="To get started, HyprMod needs to add one line to your hyprland.conf:",
        )
        setup_text.set_wrap(True)
        setup_text.set_xalign(0)
        setup_text.add_css_class("dim-label")
        box.append(setup_text)

        code_box = Gtk.Box()
        code_box.add_css_class("code-block")
        code_view = Gtk.TextView()
        code_view.set_editable(False)
        code_view.set_cursor_visible(False)
        code_view.set_monospace(True)
        code_view.set_wrap_mode(Gtk.WrapMode.NONE)
        code_view.set_top_margin(10)
        code_view.set_bottom_margin(10)
        code_view.set_left_margin(14)
        code_view.set_right_margin(14)
        code_view.set_hexpand(True)
        code_view.set_size_request(420, -1)
        code_view.get_buffer().set_text("source = ~/.config/hypr/hyprland-gui.conf")
        code_view.add_css_class("code-block-text")
        code_box.append(code_view)
        box.append(code_box)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Not Now")
        dialog.add_response("setup", "Get Started")
        dialog.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("setup")
        dialog.set_close_response("cancel")

        def on_response(dialog_, response):
            if response == "setup":
                run_setup()

        dialog.connect("response", on_response)
        dialog.present(win)


def main():
    app = HyprModApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
