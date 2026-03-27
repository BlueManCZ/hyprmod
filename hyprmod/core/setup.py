"""First-run setup — injects the source line into hyprland.conf."""

from pathlib import Path

from hyprland_config import Source, atomic_write
from hyprland_config import load as load_document

from hyprmod.core.config import GUI_CONF

HYPRLAND_CONF = Path.home() / ".config" / "hypr" / "hyprland.conf"
SOURCE_LINE = f"source = {GUI_CONF}"


def _has_source_line(doc) -> bool:
    """Check if the document already sources GUI_CONF."""
    gui_conf_resolved = GUI_CONF.resolve()
    for line in doc.lines:
        if not isinstance(line, Source):
            continue
        if Path(line.path_str).expanduser().resolve() == gui_conf_resolved:
            return True
    return False


def needs_setup() -> bool:
    """Check if the source line needs to be added."""
    if not HYPRLAND_CONF.exists():
        return False
    doc = load_document(HYPRLAND_CONF, follow_sources=False)
    return not _has_source_line(doc)


def run_setup() -> None:
    """Append the source line to hyprland.conf."""
    GUI_CONF.touch(exist_ok=True)
    doc = load_document(HYPRLAND_CONF, follow_sources=False)
    if _has_source_line(doc):
        return
    content = doc.serialize()
    if not content.endswith("\n"):
        content += "\n"
    content += f"\n# HyprMod managed settings\n{SOURCE_LINE}\n"
    atomic_write(HYPRLAND_CONF, content)
