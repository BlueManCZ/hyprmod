"""Parsing and serialization helpers for ``env = NAME,value`` entries.

Hyprland's ``env`` keyword exports environment variables to processes
spawned by the compositor (``exec``/``exec-once`` children, dispatcher
``exec`` calls, anything launched from a bind). Lines look like::

    env = XCURSOR_THEME,Bibata-Modern-Ice
    env = GDK_BACKEND,wayland,x11
    env = QT_QPA_PLATFORMTHEME,qt5ct

The first comma separates *name* from *value*; further commas inside
the value are preserved verbatim (the second example above is one
``GDK_BACKEND`` value, not three). Hyprland reads ``env`` lines once
at compositor startup — they **cannot be retroactively applied** to
already-spawned processes via ``hyprctl keyword``, so this page (like
autostart) lands edits in ``hyprland-gui.conf`` and takes effect on
the next Hyprland session, not live.

The ``XCURSOR_THEME`` / ``XCURSOR_SIZE`` / ``HYPRCURSOR_THEME`` /
``HYPRCURSOR_SIZE`` names are owned by the Cursor page (see
:mod:`hyprmod.pages.cursor`) — that page reads/writes them as part of
its theme + size editor. The Env Variables page deliberately *skips*
those names on read (so the user doesn't see them duplicated in two
places) and the save path concatenates env lines from both pages,
with the Cursor page's lines first by convention.
"""

from dataclasses import dataclass
from pathlib import Path

import hyprland_config

from hyprmod.core import config

# Names whose env lines are owned by ``hyprmod.pages.cursor.CursorPage``.
# The Env Variables page skips these on read so the cursor theme/size is
# only editable in one place. Order is irrelevant — this is a membership
# check, not a sequence.
RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "XCURSOR_THEME",
        "XCURSOR_SIZE",
        "HYPRCURSOR_THEME",
        "HYPRCURSOR_SIZE",
    }
)


def is_reserved(name: str) -> bool:
    """True if *name* is owned by another page (currently the Cursor page).

    The check is case-sensitive — POSIX environment variable names are
    case-sensitive, and Hyprland forwards them verbatim.
    """
    return name in RESERVED_NAMES


@dataclass(slots=True)
class EnvVar:
    """A single ``env = NAME,value`` entry.

    *value* is preserved verbatim including any commas — Hyprland only
    splits on the *first* comma, so ``GDK_BACKEND,wayland,x11`` is a
    single entry whose value is ``wayland,x11``.
    """

    name: str
    value: str

    def to_line(self) -> str:
        """Serialize as a single ``env = NAME,value`` config line."""
        return f"{config.KEYWORD_ENV} = {self.name},{self.value}"


def parse_env_line(line: str) -> EnvVar | None:
    """Parse a single ``env = NAME,value`` line into an :class:`EnvVar`.

    Returns ``None`` when the line is missing the ``env`` keyword, the
    ``=`` separator, the ``,`` between name and value, or the name
    itself. Whitespace around the keyword, name, and value is stripped.

    The value preserves embedded commas — only the first comma after
    the keyword's ``=`` is treated as the separator (Hyprland uses the
    same rule). Use :func:`parse_env_lines` for a tolerant batch
    parser that drops unparseable lines.
    """
    head, sep, tail = line.partition("=")
    if not sep:
        return None
    if head.strip() != config.KEYWORD_ENV:
        return None
    body = tail.strip()
    if not body:
        return None
    name, comma, value = body.partition(",")
    if not comma:
        # Hyprland 0.54 rejects ``env = NAME`` with no value, but lenient
        # parsers in the wild sometimes accept it. We don't — emitting
        # such a line would be a runtime error, so we drop it instead.
        return None
    name = name.strip()
    value = value.strip()
    if not name or not value:
        # Empty name (``env = ,value``) is rejected unconditionally.
        # Empty value (``env = NAME,``) is also rejected because Hyprland
        # 0.54 errors out on it; the dialog's apply gate ensures we never
        # emit such a line, so seeing one means the file was edited by
        # hand and is broken.
        return None
    return EnvVar(name=name, value=value)


def parse_env_lines(lines: list[str]) -> list[EnvVar]:
    """Parse multiple raw env lines, dropping anything unparseable.

    Order is preserved. Lines that don't match the ``env`` keyword or
    are syntactically broken are silently skipped — the caller has
    already filtered ``sections`` by keyword, so a mismatch here is a
    sign of corruption rather than user error and shouldn't block
    loading the rest of the page.

    Names in :data:`RESERVED_NAMES` are *not* filtered here — the page
    is responsible for that, since the parser is also used by the
    pending-changes diff which needs to see every env line for an
    accurate save preview.
    """
    result = []
    for raw in lines:
        parsed = parse_env_line(raw)
        if parsed is not None:
            result.append(parsed)
    return result


def serialize(items: list[EnvVar]) -> list[str]:
    """Serialize a list of :class:`EnvVar` back to config lines.

    Items are emitted in the order they appear in *items* — the page
    is responsible for any reordering before calling this.
    """
    return [item.to_line() for item in items]


# ---------------------------------------------------------------------------
# External loader (env vars from outside our managed file)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExternalEnvVar:
    """An ``env = NAME,value`` entry from a config file outside hyprmod's.

    Surfaced as a locked row on the Env Variables page so users can see
    what's already exported from their own ``hyprland.conf`` (or any file
    it sources). Click the override button on the row to add a managed
    entry with the same name — Hyprland reads source files in order and
    "last write wins," so our managed file (sourced from
    ``hyprland.conf`` by HyprMod's first-run setup) wins by virtue of
    being last.

    Mirrors :class:`hyprmod.core.layer_rules.ExternalLayerRule` for
    consistency with the other read-only-external displays.
    """

    var: EnvVar
    source_path: Path
    lineno: int


def load_external_env_vars(
    root_path: Path,
    managed_path: Path,
) -> list[ExternalEnvVar]:
    """Walk *root_path* and its sourced files for env entries outside
    *managed_path*.

    Skips :data:`RESERVED_NAMES` so cursor-managed vars don't double-up
    on this page (the Cursor page already surfaces them).

    Errors return an empty list (advisory display only; failing
    silently is safer than blocking the page on a flaky config).
    """
    if not root_path.exists():
        return []
    try:
        doc = hyprland_config.load(root_path, follow_sources=True, lenient=True)
    except (OSError, hyprland_config.ParseError, hyprland_config.SourceCycleError):
        return []

    managed_str = str(managed_path)
    external: list[ExternalEnvVar] = []
    for entry in doc.find_all(config.KEYWORD_ENV):
        if entry.source_name == managed_str:
            continue
        line = f"{entry.key} = {entry.value}"
        parsed = parse_env_line(line)
        if parsed is None:
            continue
        if parsed.name in RESERVED_NAMES:
            # Cursor page owns these names; surfacing them here too
            # would split the UX of one logical setting across two
            # pages.
            continue
        external.append(
            ExternalEnvVar(
                var=parsed,
                source_path=Path(entry.source_name),
                lineno=entry.lineno,
            )
        )
    return external


def overridden_external_names(
    external: list[ExternalEnvVar],
    owned: list[EnvVar],
) -> set[str]:
    """Return the set of external-var names that an owned var overrides.

    "Overrides" here means same name — Hyprland evaluates env lines in
    source order with last-write-wins semantics, so an owned line and
    an external line sharing a name yield the owned value. The page
    uses this to render overridden externals with a muted "Overridden"
    badge and to suppress the override button on already-overridden
    rows.
    """
    owned_names = {e.name for e in owned}
    return {ext.var.name for ext in external if ext.var.name in owned_names}


__all__ = [
    "RESERVED_NAMES",
    "EnvVar",
    "ExternalEnvVar",
    "is_reserved",
    "load_external_env_vars",
    "overridden_external_names",
    "parse_env_line",
    "parse_env_lines",
    "serialize",
]
