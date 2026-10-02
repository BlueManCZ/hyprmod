from typing import Callable, cast, final

from gi.repository import Gio, GObject, Gtk
from hyprland_monitors.monitors import MonitorState


@final
class Preset(GObject.Object):
    __gtype_name__: str = "Preset"

    def __init__(self, name: str, description: str):
        super().__init__()
        self._name: str = name
        self._description: str = description

    @GObject.Property(type=str)
    def preset_name(self) -> str:
        return self._name

    @GObject.Property(type=str)
    def preset_description(self) -> str:
        return self._description


@final
class MonitorSwitch(Gtk.Box):
    def __init__(
        self,
        monitors: list[MonitorState],
        on_preset_selected: Callable[[str, list[MonitorState]], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._monitors: list[MonitorState] = monitors
        self._on_preset_selected = on_preset_selected
        self._model = Gio.ListStore(item_type=Preset)
        self._build_presets()

        drop_down = Gtk.DropDown(model=self._model)
        _factory = Gtk.SignalListItemFactory()
        _ = _factory.connect("setup", self._setup_item)
        _ = _factory.connect("bind", self._bind_item)
        drop_down.set_factory(_factory)

        label = Gtk.Label(label="Projection Presets", xalign=0)
        label.add_css_class("heading")
        self.append(label)
        self.append(drop_down)

    @staticmethod
    def _setup_item(factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        """Create the widget for a preset item."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_label = Gtk.Label(xalign=0)
        name_label.add_css_class("body")
        desc_label = Gtk.Label(xalign=0)
        desc_label.add_css_class("caption")
        desc_label.add_css_class("dim-label")
        box.append(name_label)
        box.append(desc_label)
        item.set_child(box)

    @staticmethod
    def _bind_item(factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        """Bind preset data to the dropdown item."""
        box = cast(Gtk.Box, item.get_child())
        if not box:
            raise RuntimeError("ListItem has no child widget.")

        name_label: Gtk.Label = cast(Gtk.Label, box.get_first_child())
        desc_label: Gtk.Label = cast(Gtk.Label, box.get_last_child())
        preset = cast(Preset, item.get_item())

        _name = cast(str, preset.preset_name)
        _descritpion = cast(str, preset.preset_description)

        name_label.set_text(_name.replace("_", " ").title())
        desc_label.set_text(_descritpion)

    def _build_presets(self):
        if len(self._monitors) <= 1:
            self._model.append(Preset("Single Monitor", "Only one monitor is connected."))

        for monitor in self._monitors:
            preset_name = f"{monitor.make} {monitor.model}"
            preset_description = f"Display only on {monitor.name}"
            self._model.append(Preset(preset_name, preset_description))

        self._model.append(Preset("Mirror", "Mirror the display across all monitors."))
        self._model.append(Preset("Extend", "Extend the display across all monitors."))
