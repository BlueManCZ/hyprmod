"""Read/write hyprland-gui.conf — hyprmod's managed config file."""

import logging
from pathlib import Path

from hyprland_config import (
    Assignment,
    Keyword,
    atomic_write,
    check_deprecated,
    is_bind_keyword,
    migrate,
    parse_string,
)
from hyprland_config import load as load_document

log = logging.getLogger(__name__)

HYPRMOD_DIR = Path.home() / ".config" / "hypr" / "hyprmod"
_DEFAULT_GUI_CONF = Path.home() / ".config" / "hypr" / "hyprland-gui.conf"
_gui_conf_override: Path | None = None


def gui_conf() -> Path:
    """Return the active config file path (user-configured or default)."""
    return _gui_conf_override or _DEFAULT_GUI_CONF


def set_gui_conf(path: Path | None) -> None:
    """Set a custom config file path, or ``None`` to revert to the default."""
    global _gui_conf_override  # noqa: PLW0603
    _gui_conf_override = path


# Hyprland special-section keyword names. Unlike regular ``key = value``
# options, these can appear multiple times in a config (e.g. one ``monitor =``
# per display) and must be tracked as ordered lists rather than scalar values.
KEYWORD_MONITOR = "monitor"
KEYWORD_ANIMATION = "animation"
KEYWORD_BEZIER = "bezier"
KEYWORD_UNBIND = "unbind"
KEYWORD_ENV = "env"

_NON_BIND_SPECIAL = frozenset(
    (KEYWORD_MONITOR, KEYWORD_ANIMATION, KEYWORD_BEZIER, KEYWORD_UNBIND, KEYWORD_ENV)
)


class _BindKeysSentinel:
    """Sentinel type for ``collect_section()`` to match all bind-variant keywords."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "BIND_KEYS"


BIND_KEYS = _BindKeysSentinel()


def _is_special_keyword(name: str) -> bool:
    """Check if a keyword name is a special key (bind variants, monitor, animation, etc.)."""
    return name in _NON_BIND_SPECIAL or is_bind_keyword(name)


def parse_conf(path: Path) -> dict[str, str]:
    """Parse all key=value pairs from a .conf file (comments and blanks skipped)."""
    if not path.exists():
        return {}
    doc = load_document(path, follow_sources=False)
    return {line.full_key: line.value for line in doc.lines if isinstance(line, Assignment)}


def read_all_sections(
    path: Path | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Single-pass parse of a config file.

    Returns (options, sections) where:
    - options: key -> value for regular option lines
    - sections: section_key -> [raw lines] for special keys (bind, monitor, etc.)
    """
    path = path or gui_conf()
    if not path.exists():
        return {}, {}
    doc = load_document(path, follow_sources=False)
    options: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    for line in doc.lines:
        if isinstance(line, Assignment):
            options[line.full_key] = line.value
        elif isinstance(line, Keyword) and _is_special_keyword(line.key):
            raw = line.raw.strip()
            sections.setdefault(line.key, []).append(raw)
    return options, sections


def collect_section(
    sections: dict[str, list[str]],
    *keys: str | _BindKeysSentinel,
) -> list[str]:
    """Extract lines from a pre-parsed sections dict.

    Each *key* can be a single string or the sentinel ``BIND_KEYS``
    which matches all bind-variant keywords present in *sections*.
    """
    result = []
    for key in keys:
        if isinstance(key, _BindKeysSentinel):
            for k in sections:
                if is_bind_keyword(k):
                    result.extend(sections[k])
        else:
            result.extend(sections.get(key, []))
    return result


def remove_key(key: str) -> None:
    """Remove a single key from the config file, preserving everything else."""
    path = gui_conf()
    if not path.exists():
        return
    doc = load_document(path, follow_sources=False)
    doc.remove(key)
    doc.save()


def remove_animation(name: str) -> None:
    """Remove a single animation line from the config file by animation name."""
    path = gui_conf()
    if not path.exists():
        return
    doc = load_document(path, follow_sources=False)
    doc.remove_where(KEYWORD_ANIMATION, lambda v: v.split(",")[0].strip() == name)
    doc.save()


def _append_section(lines: list[str], header: str, section_lines: list[str]) -> None:
    """Append a labeled section of config lines, normalizing line endings."""
    lines.append(f"\n# {header}\n")
    for line in section_lines:
        lines.append(line if line.endswith("\n") else line + "\n")


def _auto_migrate_content(content: str) -> str:
    """Normalize outgoing config against known Hyprland deprecations.

    Parses the serialized config, logs any deprecated syntax found for
    debuggability, and applies known automatic migrations in place.  The
    file is fully owned by hyprmod, so renamed keys can be rewritten
    silently — this keeps our file current without touching the user's
    own ``hyprland.conf``.

    Falls back to the original content if parsing or migration raises.
    """
    try:
        doc = parse_string(content, name="hyprland-gui.conf", lenient=True)
    except Exception:  # noqa: BLE001 — migration must never block a save
        log.exception("failed to parse outgoing config for migration; writing as-is")
        return content

    for d in check_deprecated(doc):
        log.info("deprecated syntax in outgoing config: %s", d)

    try:
        result = migrate(doc)
    except Exception:  # noqa: BLE001 — migration must never block a save
        log.exception("migration raised; writing un-migrated content")
        return content

    if result.applied:
        log.info(
            "auto-migrated %d rule(s) on save: %s",
            len(result.applied),
            "; ".join(result.applied),
        )
        return doc.serialize()
    return content


def write_all(
    values: dict[str, str],
    bind_lines: list[str] | None = None,
    monitor_lines: list[str] | None = None,
    animation_lines: list[str] | None = None,
    bezier_lines: list[str] | None = None,
    env_lines: list[str] | None = None,
) -> None:
    """Write all values and special lines to the config file."""
    lines: list[str] = ["# Generated by HyprMod\n\n"]

    if env_lines:
        _append_section(lines, "Environment", env_lines)
        lines.append("\n")

    for k, v in sorted(values.items()):
        lines.append(f"{k} = {v}\n")

    if bezier_lines:
        _append_section(lines, "Bezier curves", bezier_lines)
    if animation_lines:
        _append_section(lines, "Animations", animation_lines)
    if monitor_lines:
        _append_section(lines, "Monitors", monitor_lines)
    if bind_lines:
        _append_section(lines, "Keybinds", bind_lines)

    content = _auto_migrate_content("".join(lines))
    atomic_write(gui_conf(), content)
