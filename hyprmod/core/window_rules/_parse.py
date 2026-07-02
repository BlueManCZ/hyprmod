"""Hyprland window-rule structured-node adapter and Hyprlang serializer.

After ``hyprland_config.migrate()`` runs, every windowrule on disk —
single-line ``windowrule = match:class …, float on`` or block-form
``windowrule { name = X; … }`` — appears in the parsed :class:`Document`
as a structured :class:`hyprland_config.Rule` node. This module bridges
that node type and hyprmod's UI-facing :class:`WindowRule` dataclass:

- :func:`from_rule_node` adapts one ``Rule`` into a hyprmod
  :class:`WindowRule` (matchers → :class:`Matcher`, effects → :class:`Effect`).
- :func:`from_rule_nodes` filters / converts a Document's full Rule list.
- :func:`serialize` produces the Hyprlang text per rule for the write
  path; the language-specific serializer in ``hyprland-config`` picks
  block vs. single-line based on the rule's contents.
- :func:`parse_window_rule_line` / :func:`parse_window_rule_lines` are
  thin compatibility shims that route a single Hyprlang text line
  through ``parse_string`` + ``migrate`` and return the resulting UI
  :class:`WindowRule`(s). New code should consume Rule nodes from the
  Document directly via the adapters above.
"""

from hyprland_config import Rule
from hyprland_config import migrate as _migrate
from hyprland_config import parse_string as _parse_string

from hyprmod.core import config
from hyprmod.core.window_rules._model import (
    Effect,
    Matcher,
    WindowRule,
)


def from_rule_node(node: Rule) -> WindowRule | None:
    """Build a hyprmod :class:`WindowRule` from a library :class:`Rule`.

    Returns ``None`` if the node isn't a windowrule (callers iterating a
    mixed Rule list use this for the filter-and-convert one-liner).
    """
    if node.kind != config.KEYWORD_WINDOWRULE:
        return None
    return WindowRule(
        matchers=[Matcher(key=k, value=v) for k, v in node.matchers],
        effects=[Effect(name=n, args=a) for n, a in node.effects],
        name=node.name,
        enabled=node.enabled,
    )


def from_rule_nodes(nodes: list[Rule]) -> list[WindowRule]:
    """Convert a Document's :class:`Rule` list into UI :class:`WindowRule`s."""
    out: list[WindowRule] = []
    for node in nodes:
        wr = from_rule_node(node)
        if wr is not None:
            out.append(wr)
    return out


def serialize(items: list[WindowRule], version: str | None = None) -> list[str]:
    """Serialize a list of :class:`WindowRule` to Hyprlang config lines.

    *version* is the running Hyprland version; below 0.53 each rule
    renders in the effect-first ``windowrulev2`` grammar (one line per
    effect; see :meth:`WindowRule.to_line`). ``None`` (the default)
    emits v3.
    """
    return [item.to_line(version) for item in items]


def parse_window_rule_line(line: str) -> WindowRule | None:
    """Parse one Hyprlang ``windowrule = …`` line via the canonical pipeline.

    Routes through ``hyprland_config.parse_string`` + ``migrate`` so
    block-form input (``windowrule { name = X; … }``) and single-line
    input both land as :class:`Rule` nodes that :func:`from_rule_node`
    can adapt. Returns ``None`` for unrelated keywords, syntactically
    broken lines, or rules with no effects.

    Multi-effect / named blocks return one :class:`WindowRule` with all
    effects bundled.
    """
    rules = parse_window_rule_lines([line])
    return rules[0] if rules else None


def parse_window_rule_lines(lines: list[str]) -> list[WindowRule]:
    """Parse multiple Hyprlang rule lines via the canonical pipeline.

    Same routing as :func:`parse_window_rule_line` but processes a list.
    Lines that aren't windowrules (or that fail to produce a Rule) are
    silently dropped.
    """
    doc = _parse_string("\n".join(lines) + "\n", lenient=True)
    _migrate(doc)
    return from_rule_nodes([ln for ln in doc.lines if isinstance(ln, Rule)])
