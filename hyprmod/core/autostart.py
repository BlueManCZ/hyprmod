"""Parsing and serialization helpers for ``exec``/``exec-once`` autostart entries.

Hyprland exposes a handful of ``exec*`` keywords that all share the same
``keyword = command`` shape but differ in *when* the command runs:

============  =========================================================
``exec``      runs every time the config is reloaded
``exec-once`` runs once at Hyprland startup
============  =========================================================

(Other variants like ``execr``, ``exec-shutdown`` exist but are out of
scope for the initial autostart UI; the data model accepts arbitrary
keyword strings so they can be added later without a migration.)

These keywords can appear multiple times in a config and must be tracked
as an *ordered list* — order matters: ``exec-once`` entries are executed
sequentially, and users may rely on, e.g., ``swaybg`` finishing before
``waybar`` starts.
"""

from dataclasses import dataclass

from hyprmod.core import config

# Set of keyword names this module handles. The order here also defines
# the order entries are written back to disk (once-then-recurring), which
# matches how most users mentally group startup vs. reload behaviour.
EXEC_KEYWORDS: tuple[str, ...] = (config.KEYWORD_EXEC_ONCE, config.KEYWORD_EXEC)

# Human-friendly labels for each keyword, used in the UI and pending
# changes summary. Kept here (not in pages/autostart.py) so pending.py
# can render entries even if the page hasn't been built yet.
KEYWORD_LABELS: dict[str, str] = {
    config.KEYWORD_EXEC_ONCE: "Once at startup",
    config.KEYWORD_EXEC: "On every reload",
}


@dataclass(slots=True)
class ExecData:
    """A single autostart entry: a keyword and the command to run.

    ``command`` is preserved verbatim from the source line — Hyprland
    passes it to ``/bin/sh -c``, so quoting and shell metacharacters
    are the user's responsibility (and we don't try to interpret them).
    """

    keyword: str
    command: str

    def to_line(self) -> str:
        """Serialize as a single ``keyword = command`` config line."""
        return f"{self.keyword} = {self.command}"


def parse_exec_line(line: str) -> ExecData | None:
    """Parse a single ``exec``/``exec-once`` line into an ``ExecData``.

    Returns ``None`` if the line doesn't match a known exec keyword or
    is missing the ``=`` separator.  Whitespace around both the keyword
    and the command is stripped; the command itself is preserved
    otherwise (including any embedded quotes).
    """
    head, sep, tail = line.partition("=")
    if not sep:
        return None
    keyword = head.strip()
    if keyword not in EXEC_KEYWORDS:
        return None
    command = tail.strip()
    if not command:
        return None
    return ExecData(keyword=keyword, command=command)


def parse_exec_lines(lines: list[str]) -> list[ExecData]:
    """Parse multiple raw exec lines, dropping anything unparsable.

    Order is preserved.  Lines that don't match a known exec keyword
    are silently skipped — the caller has already filtered ``sections``
    by keyword, so a mismatch here is a sign of corruption rather than
    user error and shouldn't block loading the rest of the page.
    """
    result = []
    for raw in lines:
        parsed = parse_exec_line(raw)
        if parsed is not None:
            result.append(parsed)
    return result


def serialize(items: list[ExecData]) -> list[str]:
    """Serialize a list of ``ExecData`` back to config lines.

    Items are emitted in the order they appear in *items* — the page
    is responsible for any reordering (e.g. grouping exec-once before
    exec) before calling this.
    """
    return [item.to_line() for item in items]
