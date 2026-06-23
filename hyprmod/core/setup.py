"""First-run setup — inject the include line into the user's top-level config."""

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

from hyprland_config import (
    ParseError,
    Source,
    atomic_write,
    load,
    load_any,
    serialize_hyprlang,
)

from hyprmod.core import config

# Match a literal ``dofile("...")`` / ``dofile('...')`` call and the post-
# migration ``require("module.name")`` form hyprland-config 0.9.1+ emits.
# The entrypoint is small and any include line hyprmod cares about is
# either written by us or by the converter, so a regex pass is enough;
# resolution is path-based, not string-based.
_DOFILE_RE = re.compile(r"""dofile\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def needs_setup() -> bool:
    """Return ``True`` when Hyprland's config tree doesn't yet load our file.

    Walks the full source/require/dofile chain, not just the entrypoint: a
    user who moved our include line into a sourced sub-file is already set
    up even though ``hyprland.lua`` doesn't mention us directly. When the
    tree can't be loaded (no ``lua`` interpreter, a config that fails to
    execute) we fall back to inspecting the entrypoint alone.
    """
    entry = config.user_entry_path()
    if not entry.exists():
        return False
    if config.is_lua_target(entry):
        target = config.managed_lua_path()
    else:
        target = config.managed_conf_path()
    try:
        doc = load_any(entry, lenient=True)
    except (ParseError, OSError):
        return _needs_setup_shallow(entry)
    return not _tree_includes(doc, target)


def _tree_includes(doc, target: Path) -> bool:
    """Return ``True`` when *target* is sourced anywhere in the loaded tree.

    Both readers pre-resolve include paths into ``Source.resolved_paths``
    (``.resolve()``-d), so a plain membership check handles symlinked
    dotfiles, ``$VAR`` expansion, and ``source`` paths relative to a
    sub-file's own directory without re-deriving them here.
    """
    resolved = target.resolve()
    return any(
        isinstance(line, Source) and resolved in line.resolved_paths
        for _owner, line in doc.iter_lines(recursive=True)
    )


def _needs_setup_shallow(entry: Path) -> bool:
    """Entrypoint-only check — the fallback when the source tree won't load."""
    if config.is_lua_target(entry):
        return not _has_lua_include(
            entry.read_text(encoding="utf-8"),
            config.managed_lua_path(),
            entry.parent,
        )
    doc = load(entry, follow_sources=False)
    return _find_source_node(doc, config.managed_conf_path()) is None


def run_setup() -> None:
    """Append our include line to the user's top-level config.

    In Lua mode: ensure ``managed_lua_path()`` exists, then append a
    ``require("…")`` / ``dofile("…")`` line to ``hyprland.lua``. In
    Hyprlang mode: ensure ``managed_conf_path()`` exists, then append
    ``source = …`` to ``hyprland.conf``.
    """
    entry = config.user_entry_path()
    if config.is_lua_target(entry):
        target = config.managed_lua_path()
        target.touch(exist_ok=True)
        _append_lua_include(entry, target)
        return

    target = config.managed_conf_path()
    target.touch(exist_ok=True)
    doc = load(entry, follow_sources=False)
    if _find_source_node(doc, target) is not None:
        return
    content = serialize_hyprlang(doc)
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"\n# HyprMod managed settings\nsource = {target}\n"
    atomic_write(entry, content)


def render_lua_include(target: Path, *, for_display: bool = False) -> str:
    """Return the Lua include line that would point ``hyprland.lua`` at *target*.

    Prefers ``require("module.name")`` — the form hyprland-config 0.9.1+
    writes and the only one Hyprland's autoreload tracks — falling back
    to an absolute ``dofile("/path/to/file.lua")`` when *target* isn't
    reachable by module name (outside the config dir, or a literal ``.``
    in any path segment). With ``for_display=True`` the ``dofile`` path
    is collapsed to ``~/…`` for UI previews; on-disk writes always use
    the absolute path so Hyprland's loader resolves it without a shell.
    """
    config_root = config.user_entry_path().parent
    module = _module_name_for(target, config_root)
    if module is not None:
        return f'require("{module}")'
    path_str = config.display_path(target) if for_display else str(target)
    return f'dofile("{path_str}")'


def _append_lua_include(entry: Path, target: Path) -> None:
    """Append the Lua include line to *entry* if it's not already present."""
    existing = entry.read_text(encoding="utf-8") if entry.exists() else ""
    if _has_lua_include(existing, target, entry.parent):
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    existing += f"\n-- HyprMod managed settings\n{render_lua_include(target)}\n"
    atomic_write(entry, existing)


def _has_lua_include(text: str, target: Path, config_root: Path) -> bool:
    """Return ``True`` when *text* sources *target* via ``dofile`` or ``require``.

    Path comparison goes through ``.resolve()`` so symlinked-dotfile
    setups (``~/.config/hypr → ~/dotfiles/hypr``) match either spelling.
    ``require`` arguments resolve against *config_root* the same way
    Hyprland's ``package.path`` does (``<root>/a/b.lua`` for ``a.b``).
    """
    resolved_target = target.resolve()
    for resolved, _match in _iter_include_calls(text, config_root):
        if resolved == resolved_target:
            return True
    return False


def _iter_include_calls(text: str, config_root: Path) -> Iterator[tuple[Path, re.Match[str]]]:
    """Yield ``(resolved_target, match)`` for each uncommented include call.

    Skips lines where ``--`` appears before the include keyword (the Lua
    comment marker, treats both ``-- dofile("…")`` and trailing
    ``code() -- dofile("…")`` as inert).
    """
    for line in text.splitlines():
        comment_at = line.find("--")
        for keyword, pattern, resolver in _INCLUDE_RESOLVERS:
            kw_at = line.find(keyword)
            if kw_at == -1 or (comment_at != -1 and comment_at < kw_at):
                continue
            for match in pattern.finditer(line):
                if comment_at != -1 and comment_at < match.start():
                    continue
                candidate = resolver(match.group(1), config_root)
                if candidate is None:
                    continue
                try:
                    yield candidate.resolve(), match
                except OSError:
                    continue


def _resolve_dofile_arg(arg: str, _config_root: Path) -> Path | None:
    return Path(arg).expanduser() if arg else None


def _resolve_require_arg(module: str, config_root: Path) -> Path | None:
    """Map ``require("a.b")`` to ``<config_root>/a/b.lua``.

    Hyprland's ``package.path`` is set to
    ``<config_root>/?.lua;<config_root>/?/init.lua``; we only emit (and
    only need to recognise) the first form, since the converter writes
    every sub-file as ``<name>.lua`` rather than ``<name>/init.lua``.
    """
    if not module:
        return None
    return config_root.joinpath(*module.split(".")).with_suffix(".lua")


_INCLUDE_RESOLVERS = (
    ("dofile", _DOFILE_RE, _resolve_dofile_arg),
    ("require", _REQUIRE_RE, _resolve_require_arg),
)


def _module_name_for(out_path: Path, config_root: Path) -> str | None:
    """Return the ``require`` module name for *out_path*, or ``None``.

    Mirrors hyprland-config 0.9.1's ``_lua_module_name``: the file must
    live under *config_root* and no path segment may contain a literal
    ``.`` (which ``require`` would read as a package separator and miss
    the file). Falls through to ``None`` in either case so the caller
    keeps an absolute ``dofile``.
    """
    try:
        relative = out_path.relative_to(config_root)
    except ValueError:
        return None
    segments = [*relative.parts[:-1], relative.stem]
    if any("." in segment for segment in segments):
        return None
    return ".".join(segments)


def _find_source_node(doc, target: Path) -> Source | None:
    """Find the Source node in *doc* that resolves to *target*."""
    resolved = target.resolve()
    for line in doc.lines:
        if isinstance(line, Source) and Path(line.path_str).expanduser().resolve() == resolved:
            return line
    return None


def migrate_config_path(old_path: Path, new_path: Path) -> None:
    """Move the managed file and update the include line in the user's entrypoint.

    The user's entrypoint format decides which include statement is
    rewritten — Lua entrypoints get their ``dofile("…")`` / ``require("…")``
    updated, Hyprlang entrypoints get their ``source = …`` updated.
    *old_path* and *new_path* are the literal paths from the caller;
    their suffix should match the active mode so the rewritten include
    points at a file Hyprland can actually load.
    """
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if old_path.exists():
        shutil.move(old_path, new_path)

    entry = config.user_entry_path()
    if not entry.exists():
        return

    if config.is_lua_target(entry):
        _migrate_lua_include(entry, old_path, new_path)
    else:
        _migrate_hyprlang_source(entry, old_path, new_path)


def _migrate_hyprlang_source(entry: Path, old_path: Path, new_path: Path) -> None:
    doc = load(entry, follow_sources=False)
    old_node = _find_source_node(doc, old_path)
    if old_node is not None:
        new_raw = old_node.raw.replace(str(old_path), str(new_path))
        if new_raw == old_node.raw:
            new_raw = f"source = {new_path}\n"
        content = serialize_hyprlang(doc).replace(old_node.raw, new_raw, 1)
        atomic_write(entry, content)
    elif _find_source_node(doc, new_path) is None:
        content = serialize_hyprlang(doc)
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"\n# HyprMod managed settings\nsource = {new_path}\n"
        atomic_write(entry, content)


def _migrate_lua_include(entry: Path, old_path: Path, new_path: Path) -> None:
    existing = entry.read_text(encoding="utf-8")
    resolved_old = old_path.resolve()
    new_include = render_lua_include(new_path)
    updated = existing
    changed = False
    for resolved, match in _iter_include_calls(existing, entry.parent):
        if resolved == resolved_old:
            updated = updated.replace(match.group(0), new_include, 1)
            changed = True
    if changed:
        atomic_write(entry, updated)
        return
    if _has_lua_include(existing, new_path, entry.parent):
        return
    if updated and not updated.endswith("\n"):
        updated += "\n"
    updated += f"\n-- HyprMod managed settings\n{new_include}\n"
    atomic_write(entry, updated)
