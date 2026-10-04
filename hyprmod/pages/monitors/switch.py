from typing import Callable, cast, final

from gi.repository import Gio, GObject, Gtk
from hyprland_monitors.monitors import MonitorState

from hyprmod.pages.monitors.presets import NO_PRESET


@final
class Preset(GObject.Object):
    __gtype_name__: str = "Preset"

    def __init__(self, key: str, name: str, description: str):
        super().__init__()
        self._key: str = key
        self._name: str = name
        self._description: str = description

    @GObject.Property(type=str)
    def preset_key(self) -> str:
        return self._key

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
        active_key: str,
        on_preset_selected: Callable[[str], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._monitors: list[MonitorState] = monitors
        self._on_preset_selected = on_preset_selected
        self._model = Gio.ListStore(item_type=Preset)
        self._build_presets()

        self._drop_down = drop_down = Gtk.DropDown(model=self._model)
        _factory = Gtk.SignalListItemFactory()
        _ = _factory.connect("setup", self._setup_item)
        _ = _factory.connect("bind", self._bind_item)
        drop_down.set_factory(_factory)

        _active_pos = self._position_of(active_key)
        if _active_pos == Gtk.INVALID_LIST_POSITION:
            # e.g. "only:<name>" for a monitor that is no longer connected
            _active_pos = self._position_of(NO_PRESET)
        drop_down.set_selected(_active_pos)
        # Connect after restoring the selection so it isn't reported as a user change.
        self._selected_handler = drop_down.connect("notify::selected", self._on_selected)

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
            self._model.append(Preset("single", "Single Monitor", "Only one monitor is connected."))

        self._model.append(Preset(NO_PRESET, "-", "No preset selected."))
        self._model.append(Preset("extend", "Extend", "Extend the display across all monitors."))
        # self._model.append(Preset("mirror", "Mirror", "Mirror the display across all monitors."))

        for monitor in self._monitors:
            preset_name = f"{monitor.make} {monitor.model}"
            preset_description = f"Display only on {monitor.name}"
            self._model.append(Preset(f"only:{monitor.name}", preset_name, preset_description))

    def _on_selected(self, drop_down: Gtk.DropDown, _pspec: GObject.ParamSpec) -> None:
        preset = cast(Preset | None, drop_down.get_selected_item())
        if preset is None:
            return
        self._on_preset_selected(cast(str, preset.preset_key))

    def set_active(self, key: str) -> None:
        """Select *key* without reporting it as a user choice."""
        pos = self._position_of(key)
        if pos == Gtk.INVALID_LIST_POSITION:
            return
        self._drop_down.handler_block(self._selected_handler)
        try:
            self._drop_down.set_selected(pos)
        finally:
            self._drop_down.handler_unblock(self._selected_handler)

    def _position_of(self, preset_key: str) -> int:
        for pos, preset in enumerate(self._model):
            _key: str = cast(str, preset.preset_key)
            if _key == preset_key:
                return pos
        return Gtk.INVALID_LIST_POSITION
