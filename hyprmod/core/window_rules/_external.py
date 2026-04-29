"""Read-only loader for window rules from outside hyprmod's managed file.

The Window Rules page surfaces these so users see the full picture of
what's affecting their windows — but read-only. Hyprland has no
``unwindowrule`` IPC, so we can't offer the override action the Binds
page uses; the source path + line number are preserved so the UI can
point users at the file they need to edit by hand.
"""

from dataclasses import dataclass
from pathlib import Path

import hyprland_config

from hyprmod.core.window_rules._model import WINDOW_RULE_KEYWORDS, WindowRule
from hyprmod.core.window_rules._parse import parse_window_rule_line


@dataclass(frozen=True, slots=True)
class ExternalWindowRule:
    """A windowrule from a config file outside hyprmod's managed file."""

    rule: WindowRule
    source_path: Path
    lineno: int


def load_external_window_rules(
    root_path: Path,
    managed_path: Path,
) -> list[ExternalWindowRule]:
    """Walk *root_path* and its sourced files for windowrule entries
    that don't live in *managed_path*.

    *root_path* is typically ``~/.config/hypr/hyprland.conf`` (the
    file Hyprland actually loads); *managed_path* is whichever file
    hyprmod owns — the path is user-configurable via the
    ``hyprmod.config-path`` setting, so the loader takes it as a
    parameter rather than assuming a fixed filename. Lines are returned
    in document order — the order Hyprland evaluates them, which
    matters because the last matching rule wins for a given effect.

    Hyprland reads our managed file via ``source = …`` after
    everything in *root_path*, so anything in this list is
    semantically "earlier" than the user's hyprmod-authored rules:
    a competing rule in our managed list silently wins. The UI
    documents this so users debugging a non-applying rule know to
    check what's already been "won" against.

    Failures (root file missing, parse errors, OS errors) return an
    empty list — external rules are advisory display, not load-bearing,
    so failing silently is safer than blocking the page on a flaky
    config.
    """
    if not root_path.exists():
        return []
    try:
        doc = hyprland_config.load(root_path, follow_sources=True, lenient=True)
    except (OSError, hyprland_config.ParseError, hyprland_config.SourceCycleError):
        return []

    # Rewrite v1/v2 windowrule lines to v3 in-memory before walking,
    # so the parser only ever sees the current syntax. This mutates
    # the in-memory Document but never touches disk — the user's
    # source files are unchanged; the migration is just a normalised
    # view for our display.
    hyprland_config.migrate(doc)

    managed_str = str(managed_path)
    external: list[ExternalWindowRule] = []
    for keyword in WINDOW_RULE_KEYWORDS:
        for entry in doc.find_all(keyword):
            if entry.source_name == managed_str:
                continue
            line = f"{entry.key} = {entry.value}"
            rule = parse_window_rule_line(line)
            if rule is None:
                continue
            external.append(
                ExternalWindowRule(
                    rule=rule,
                    source_path=Path(entry.source_name),
                    lineno=entry.lineno,
                )
            )
    return external
