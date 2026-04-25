"""Text-entry option row."""

from gi.repository import Adw

from hyprmod.ui.options.base import OptionRow


class EntryOptionRow(OptionRow):
    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        row = Adw.EntryRow(
            title=option.get("label", option["key"]),
        )
        row.set_text(str(value) if value is not None else option.get("default", ""))
        row.set_tooltip_text(option.get("description", ""))
        row.set_show_apply_button(True)
        super().__init__(row, option, on_change, on_reset, on_discard)

        def on_apply(row_):
            self._emit_change(row_.get_text())

        self._change_handler_id = row.connect("apply", on_apply)

    def _set_widget_value(self, value):
        self.row.set_text(str(value) if value is not None else "")  # type: ignore[attr-defined]
