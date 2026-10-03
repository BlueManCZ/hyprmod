import json
from typing import Callable, cast, final

from gi.repository import Gio, GObject, Gtk
from hyprland_config import atomic_write
from hyprland_monitors.monitors import MonitorState

from hyprmod.core.config import HYPRMOD_DIR

_ACTIVE_PRESET_FILE = HYPRMOD_DIR / "active_projection_preset"
_DEFAULT_PRESET_KEY = "mirror"


def _read_active_preset() -> dict[str, object]:
    """Return the saved active preset, or ``{}`` if missing or unreadable."""
    try:
        data: object = json.loads(_ACTIVE_PRESET_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, object], data) if isinstance(data, dict) else {}


def _write_active_preset(key: str, monitors: list[MonitorState]) -> None:
    """Persist *key* with each monitor's position so the layout can be restored."""
    data: dict[str, object] = {"key": key}
    for mon in monitors:
        data[mon.name] = {"position": [mon.x, mon.y]}
    _ACTIVE_PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_ACTIVE_PRESET_FILE, json.dumps(data, indent=2) + "\n")


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

        _saved = _read_active_preset().get("key")
        _saved_key = _saved if isinstance(_saved, str) else _DEFAULT_PRESET_KEY
        _active_pos = self._position_of(_saved_key)
        if _active_pos == Gtk.INVALID_LIST_POSITION:
            _active_pos = self._position_of(_DEFAULT_PRESET_KEY)
        drop_down.set_selected(_active_pos)
        # Connect after restoring the selection so it isn't written back as a user change.
        _ = drop_down.connect("notify::selected", self._on_selected)

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

        self._model.append(Preset("extend", "Extend", "Extend the display across all monitors."))
        self._model.append(Preset("mirror", "Mirror", "Mirror the display across all monitors."))

        for monitor in self._monitors:
            preset_name = f"{monitor.make} {monitor.model}"
            preset_description = f"Display only on {monitor.name}"
            self._model.append(Preset(f"only:{monitor.name}", preset_name, preset_description))

    def _on_selected(self, drop_down: Gtk.DropDown, _pspec: GObject.ParamSpec) -> None:
        preset = cast(Preset | None, drop_down.get_selected_item())
        if preset is None:
            return
        key = cast(str, preset.preset_key)
        _write_active_preset(key, self._monitors)
        if self._on_preset_selected:
            self._on_preset_selected(key, self._monitors)

    def _position_of(self, preset_key: str) -> int:
        for pos, preset in enumerate(self._model):
            _key: str = cast(str, preset.preset_key)
            if _key == preset_key:
                return pos
        return Gtk.INVALID_LIST_POSITION
