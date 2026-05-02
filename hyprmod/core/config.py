"""Read/write hyprmod's managed config file (path is user-configurable via ``set_gui_conf``)."""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(slots=True)
class ConfigSections:
    """Per-section line buffers for a single :func:`write_all` invocation.

    Each field is the serialised output of one feature page's contribution
    to the managed config. ``None`` means "page didn't run" (e.g. that
    page hasn't been built yet); an empty list means "page ran but had
    nothing to emit". Both shapes are written identically — the
    distinction matters to callers, not the writer.
    """

    binds: list[str] | None = None
    monitors: list[str] | None = None
    animations: list[str] | None = None
    beziers: list[str] | None = None
    env: list[str] | None = None
    exec_: list[str] | None = None
    window_rules: list[str] | None = None
    layer_rules: list[str] | None = None

    @classmethod
    def empty(cls) -> "ConfigSections":
        """Convenience for callers that build the sections one field at a time."""
        return cls()


HYPRMOD_DIR = Path.home() / ".config" / "hypr" / "hyprmod"
_DEFAULT_GUI_CONF = Path.home() / ".config" / "hypr" / "hyprland-gui.conf"


class _GuiConfPath:
    """Holder for the active managed-config path.

    Instance state replaces what used to be a module-level mutable global,
    so ``set_gui_conf`` no longer needs ``global`` and tests get clean
    isolation through :func:`gui_conf_override`.
    """

    __slots__ = ("override",)

    def __init__(self) -> None:
        self.override: Path | None = None


_PATH = _GuiConfPath()


def default_gui_conf() -> Path:
    """Return the default config file path (ignoring any override)."""
    return _DEFAULT_GUI_CONF


def gui_conf() -> Path:
    """Return the active config file path (user-configured or default)."""
    return _PATH.override or _DEFAULT_GUI_CONF


def set_gui_conf(path: Path | None) -> None:
    """Set a custom config file path, or ``None`` to revert to the default."""
    _PATH.override = path


@contextmanager
def gui_conf_override(path: Path | None):
    """Temporarily override ``gui_conf()`` for the duration of a ``with`` block."""
    previous = _PATH.override
    _PATH.override = path
    try:
        yield path
    finally:
        _PATH.override = previous


# Hyprland special-section keyword names. Unlike regular ``key = value``
# options, these can appear multiple times in a config (e.g. one ``monitor =``
# per display) and must be tracked as ordered lists rather than scalar values.
KEYWORD_MONITOR = "monitor"
KEYWORD_ANIMATION = "animation"
KEYWORD_BEZIER = "bezier"
KEYWORD_UNBIND = "unbind"
KEYWORD_ENV = "env"
KEYWORD_EXEC = "exec"
KEYWORD_EXEC_ONCE = "exec-once"
# ``windowrule`` is the Hyprland 0.53+ canonical name (v3 syntax:
# ``windowrule = match:KEY VALUE, EFFECT VALUE``). ``windowrulev2`` is the
# 0.48–0.52 spelling for the now-superseded v2 syntax. We accept both on
# read — ``hyprland_config.migrate()`` (>=0.4.4) normalises any v2 lines
# to v3 in memory before they reach our parser — but the Window Rules
# page only ever *writes* v3 ``windowrule`` lines. The auto-migration in
# :func:`_auto_migrate_content` on save is a belt-and-braces guarantee
# that nothing v2-shaped escapes to disk.
KEYWORD_WINDOWRULE = "windowrule"
KEYWORD_WINDOWRULEV2 = "windowrulev2"
# ``layerrule`` controls how layer-shell surfaces (waybar, notifications,
# rofi, wallpapers) are decorated. Single keyword — there's no v1/v2
# rename history the way windowrule has.
KEYWORD_LAYERRULE = "layerrule"

_NON_BIND_SPECIAL = frozenset(
    (
        KEYWORD_MONITOR,
        KEYWORD_ANIMATION,
        KEYWORD_BEZIER,
        KEYWORD_UNBIND,
        KEYWORD_ENV,
        KEYWORD_EXEC,
        KEYWORD_EXEC_ONCE,
        KEYWORD_WINDOWRULE,
        KEYWORD_WINDOWRULEV2,
        KEYWORD_LAYERRULE,
    )
)


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

    The Document is run through ``hyprland_config.migrate`` before
    line collection so deprecated syntax (e.g. ``exec_once``,
    ``windowrulev2``, the legacy ``decoration:blur_*`` flat keys) is
    rewritten to its current form transparently. Hyprmod's internal
    code only ever sees the migrated shape, which is also what we
    write back on save.
    """
    path = path or gui_conf()
    if not path.exists():
        return {}, {}
    doc = load_document(path, follow_sources=False)
    migrate(doc)
    options: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    for line in doc.lines:
        if isinstance(line, Assignment):
            options[line.full_key] = line.value
        elif isinstance(line, Keyword) and _is_special_keyword(line.key):
            raw = line.raw.strip()
            sections.setdefault(line.key, []).append(raw)
    return options, sections


def collect_section(sections: dict[str, list[str]], *keys: str) -> list[str]:
    """Extract lines from a pre-parsed sections dict for the given keys."""
    result = []
    for key in keys:
        result.extend(sections.get(key, []))
    return result


def collect_bind_section(sections: dict[str, list[str]]) -> list[str]:
    """Extract lines for every bind-variant keyword present in *sections*."""
    result = []
    for key, lines in sections.items():
        if is_bind_keyword(key):
            result.extend(lines)
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
        # Use the live filename so parser error messages reference the
        # actual file the user has configured, not the default name.
        doc = parse_string(content, name=gui_conf().name, lenient=True)
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


def build_content(values: dict[str, str], sections: ConfigSections) -> str:
    """Build the config file content without writing it.

    Used by ``write_all()`` to serialize the next save, and by the
    Pending Changes page to render a diff of the upcoming write.
    """
    lines: list[str] = ["# Generated by HyprMod\n\n"]

    if sections.env:
        _append_section(lines, "Environment", sections.env)
        lines.append("\n")

    for k, v in sorted(values.items()):
        lines.append(f"{k} = {v}\n")

    if sections.beziers:
        _append_section(lines, "Bezier curves", sections.beziers)
    if sections.animations:
        _append_section(lines, "Animations", sections.animations)
    if sections.monitors:
        _append_section(lines, "Monitors", sections.monitors)
    if sections.binds:
        _append_section(lines, "Keybinds", sections.binds)
    # Window rules sit before autostart so any rule overrides (e.g.
    # ``opacity 0.9 0.7, class:^(kitty)$``) are in effect before
    # exec'd processes spawn matching windows on reload.
    if sections.window_rules:
        _append_section(lines, "Window rules", sections.window_rules)
    # Layer rules immediately follow window rules — both classes of rule
    # are evaluated when their target surface is mapped, and grouping
    # them keeps the "decoration overrides" stretch of the file in one
    # readable block.
    if sections.layer_rules:
        _append_section(lines, "Layer rules", sections.layer_rules)
    # Autostart goes last: ``exec`` re-runs on every reload, so any
    # config later in the file that affects the exec'd process (env
    # vars, monitor layout, …) is already in effect by the time the
    # commands run.
    if sections.exec_:
        _append_section(lines, "Autostart", sections.exec_)

    return _auto_migrate_content("".join(lines))


def write_all(values: dict[str, str], sections: ConfigSections) -> None:
    """Write all values and special lines to the config file."""
    atomic_write(gui_conf(), build_content(values, sections))
