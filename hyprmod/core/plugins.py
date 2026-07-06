"""Data models and parsing for plugin settings."""

from dataclasses import dataclass
from pathlib import Path

import hyprland_config
from hyprland_config import Assignment


@dataclass(slots=True)
class PluginSetting:
    plugin_name: str
    key: str
    value: str

    def to_line(self) -> str:
        return f"{self.key} = {self.value}"


def parse_plugin_options(options: dict[str, str]) -> list[PluginSetting]:
    """Extract plugin settings from flattened config options."""
    settings = []
    for full_key, value in options.items():
        if full_key.startswith("plugin:"):
            parts = full_key.split(":")
            if len(parts) >= 3:
                plugin_name = parts[1]
                key = ":".join(parts[2:])
                settings.append(PluginSetting(plugin_name, key, value))
    return settings


def serialize(settings: list[PluginSetting]) -> list[str]:
    """Serialize a list of PluginSettings into raw lines for the `plugin { ... }` block."""
    if not settings:
        return []

    # Group by plugin_name
    by_plugin = {}
    for setting in settings:
        by_plugin.setdefault(setting.plugin_name, []).append(setting)

    lines = []
    for plugin_name, plugin_settings in by_plugin.items():
        lines.append("plugin {\n")
        lines.append(f"  {plugin_name} {{\n")
        for setting in plugin_settings:
            lines.append(f"    {setting.to_line()}\n")
        lines.append("  }\n")
        lines.append("}\n")

    return lines


@dataclass(frozen=True, slots=True)
class ExternalPluginSetting:
    setting: PluginSetting
    source_path: Path
    lineno: int


def load_external_plugins(root_path: Path, managed_path: Path) -> list[ExternalPluginSetting]:
    if not root_path.exists():
        return []
    try:
        doc = hyprland_config.load_any(root_path, follow_sources=True, lenient=True)
    except (OSError, hyprland_config.ParseError, hyprland_config.SourceCycleError):
        return []

    try:
        managed_resolved = managed_path.resolve()
    except OSError:
        managed_resolved = managed_path

    external = []

    def _walk(d):
        for it in d.lines:
            yield it
            if type(it).__name__ == "Source" and it.documents:
                yield from _walk(it.documents[0])

    for item in _walk(doc):
        if isinstance(item, Assignment) and item.full_key.startswith("plugin:"):
            entry_path = Path(item.source_name)
            try:
                entry_resolved = entry_path.resolve()
            except OSError:
                entry_resolved = entry_path

            if entry_resolved == managed_resolved:
                continue

            parts = item.full_key.split(":")
            if len(parts) >= 3:
                plugin_name = parts[1]
                key = ":".join(parts[2:])
                setting = PluginSetting(
                    plugin_name=plugin_name,
                    key=key,
                    value=item.value,
                )
                external.append(
                    ExternalPluginSetting(
                        setting=setting,
                        source_path=entry_path,
                        lineno=item.lineno,
                    )
                )

    return external
