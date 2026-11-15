"""Workspace rule data, parsing, summarization, and external loader.

Hyprland's ``workspace = SELECTOR, key:value, …`` keyword binds a
workspace (or family of workspaces) to a monitor and overrides per-rule
appearance, lifecycle, and layout settings. This module is the data
layer behind :mod:`hyprmod.pages.workspaces`.

**Selector shapes (all preserved verbatim):**

- Numeric — ``1``, ``2``, ``42``
- Named — ``name:work``
- Range — ``r[1-10]``
- Per-monitor — ``m[1]`` / ``m[1-3]``
- Special — ``special:scratchpad``

**Field catalogue (all 12 supported):**

================== ============ ===========================================
Python attribute    Hyprlang key Notes
================== ============ ===========================================
``monitor``         ``monitor``  String — connector name (``DP-1``)
``default``         ``default``  Bool — make this the monitor's default
``persistent``      ``persistent`` Bool — keep alive when empty
``gaps_in``         ``gapsin``   ``int`` or ``(t, r, b, l)`` — inner gaps
``gaps_out``        ``gapsout``  ``int`` or ``(t, r, b, l)`` — outer gaps
``border_size``     ``bordersize`` int — border width in px
``border``          ``border``   Bool — draw the border for this workspace
``rounding``        ``rounding`` Bool — round window corners
``shadow``          ``shadow``   Bool — draw window shadows
``decorate``        ``decorate`` Bool — render decoration at all
``default_name``    ``defaultName`` String — default workspace name
``on_created_empty`` ``on-created-empty`` String — exec on first map
================== ============ ===========================================

Three of the Hyprlang flags use the positive sense (``border:true``);
the live-IPC translation through ``hyprland-config`` flips them to the
``no_border`` / ``no_rounding`` / ``no_shadow`` form Hyprland's Lua API
expects. Our model deliberately stays on the positive side so the UI
labels ("Border", "Rounding", "Shadow") map straight to attributes.

``extra`` keeps any tokens we don't recognise so plugin-added fields and
future Hyprland additions survive a round-trip through the config file
even before this module is updated to surface them in the UI.
"""

from dataclasses import dataclass, field
from pathlib import Path

from hyprland_config import split_top_level

from hyprmod.core import config
from hyprmod.core.external import load_external_keyword_entries
from hyprmod.i18n import _

WORKSPACE_RULE_KEYWORDS: tuple[str, ...] = (config.KEYWORD_WORKSPACE,)


# Hyprland Lua API rejects 1- and 3-value gap shorthand; we store the
# canonical (top, right, bottom, left) form in the model and let the
# UI handle scalar vs per-side display.
GapValue = int | tuple[int, int, int, int]


# Hyprlang field name → Python attribute. Inverse computed below. Keeps
# the rare camelCase / hyphenated keys mapping localised to one place.
_HYPRLANG_TO_ATTR: dict[str, str] = {
    "monitor": "monitor",
    "default": "default",
    "persistent": "persistent",
    "gapsin": "gaps_in",
    "gapsout": "gaps_out",
    "bordersize": "border_size",
    "border": "border",
    "rounding": "rounding",
    "shadow": "shadow",
    "decorate": "decorate",
    "defaultName": "default_name",
    "on-created-empty": "on_created_empty",
}
_ATTR_TO_HYPRLANG: dict[str, str] = {v: k for k, v in _HYPRLANG_TO_ATTR.items()}

_BOOL_ATTRS = frozenset({"default", "persistent", "border", "rounding", "shadow", "decorate"})
_INT_ATTRS = frozenset({"border_size"})
_GAP_ATTRS = frozenset({"gaps_in", "gaps_out"})
_STR_ATTRS = frozenset({"monitor", "default_name", "on_created_empty"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WorkspaceRule:
    """One ``workspace = …`` entry — selector + per-rule overrides.

    *workspace* is the selector string (e.g. ``"1"``, ``"name:work"``,
    ``"r[1-10]"``, ``"special:scratchpad"``). Stored verbatim so unusual
    forms round-trip without parsing.

    Every field except *workspace* and *extra* is optional: ``None``
    means "not set, don't emit this token." The serialised line only
    carries fields the user has actually configured, matching Hyprland's
    "later rule wins per key" semantics.

    *extra* holds unrecognised ``key:value`` tokens verbatim — plugin
    fields and any future-Hyprland additions survive a round-trip
    without us needing to catalogue them here.
    """

    workspace: str
    monitor: str | None = None
    default: bool | None = None
    persistent: bool | None = None
    gaps_in: GapValue | None = None
    gaps_out: GapValue | None = None
    border_size: int | None = None
    border: bool | None = None
    rounding: bool | None = None
    shadow: bool | None = None
    decorate: bool | None = None
    default_name: str | None = None
    on_created_empty: str | None = None
    extra: list[str] = field(default_factory=list)

    def body(self) -> str:
        """Serialize as the value half of the ``workspace = …`` line.

        Selector first, then fields in the catalogue order (matches the
        natural reading flow — bind, lifecycle, appearance, naming).
        Returned as the string ``hypr.keyword("workspace", body)`` wants.
        """
        parts: list[str] = [self.workspace]
        for attr, hyprlang_key in _ATTR_TO_HYPRLANG.items():
            value = getattr(self, attr)
            if value is None:
                continue
            parts.append(f"{hyprlang_key}:{_format_value(attr, value)}")
        parts.extend(self.extra)
        return ", ".join(parts)

    def to_line(self) -> str:
        """Serialize as the full ``workspace = …`` config line."""
        return f"{config.KEYWORD_WORKSPACE} = {self.body()}"


# ---------------------------------------------------------------------------
# Value formatting + coercion
# ---------------------------------------------------------------------------


def _format_value(attr: str, value: object) -> str:
    """Render an attribute value back into Hyprlang token form."""
    if attr in _BOOL_ATTRS:
        return "true" if value else "false"
    if attr in _GAP_ATTRS:
        return _format_gap(value)
    return str(value)


def _format_gap(value: object) -> str:
    """Render a gap value as scalar ``N`` or four-value ``T R B L`` string."""
    if isinstance(value, tuple):
        return " ".join(str(int(v)) for v in value)
    return str(int(value))  # type: ignore[arg-type]


def _coerce_bool(text: str) -> bool:
    """Best-effort Hyprlang bool — accepts the canonical token set."""
    return text.strip().lower() in {"true", "yes", "on", "1"}


def _coerce_gap(text: str) -> GapValue:
    """Parse a Hyprlang gap value (1/2/3/4 space-separated ints) as a model value.

    Single value stays a scalar ``int``. Multi-value forms expand to
    ``(top, right, bottom, left)`` via CSS shorthand: 2 = ``v h``,
    3 = ``t h b``, 4 = literal. Falls back to ``0`` on unparseable input
    so a corrupt config doesn't crash the page.
    """
    parts = text.strip().split()
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            try:
                nums.append(int(float(p)))
            except ValueError:
                nums.append(0)
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        v, h = nums
        return (v, h, v, h)
    if len(nums) == 3:
        t, h, b = nums
        return (t, h, b, h)
    if len(nums) >= 4:
        return (nums[0], nums[1], nums[2], nums[3])
    return 0


def _coerce_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_workspace_rule_body(body: str) -> WorkspaceRule | None:
    """Parse a workspace rule body (everything after ``workspace = ``).

    Returns ``None`` for empty or unparseable bodies so the page can
    skip malformed lines without surfacing errors mid-edit. The selector
    is taken verbatim from the first comma-separated token; subsequent
    ``key:value`` tokens map to attributes via :data:`_HYPRLANG_TO_ATTR`,
    with unknown tokens preserved in :attr:`WorkspaceRule.extra`.
    """
    tokens = split_top_level(body)
    if not tokens:
        return None
    selector = tokens[0].strip()
    if not selector:
        return None
    rule = WorkspaceRule(workspace=selector)
    for token in tokens[1:]:
        stripped = token.strip()
        if not stripped:
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            rule.extra.append(stripped)
            continue
        key = key.strip()
        value = value.strip()
        attr = _HYPRLANG_TO_ATTR.get(key)
        if attr is None:
            rule.extra.append(stripped)
            continue
        if attr in _BOOL_ATTRS:
            setattr(rule, attr, _coerce_bool(value))
        elif attr in _GAP_ATTRS:
            setattr(rule, attr, _coerce_gap(value))
        elif attr in _INT_ATTRS:
            coerced = _coerce_int(value)
            if coerced is not None:
                setattr(rule, attr, coerced)
        else:
            # String attributes pass through verbatim.
            setattr(rule, attr, value)
    return rule


def parse_workspace_rule_lines(lines: list[str]) -> list[WorkspaceRule]:
    """Parse multiple ``workspace = …`` lines, skipping any that don't parse."""
    result: list[WorkspaceRule] = []
    for raw in lines:
        head, sep, tail = raw.partition("=")
        if not sep or head.strip() != config.KEYWORD_WORKSPACE:
            continue
        rule = parse_workspace_rule_body(tail.strip())
        if rule is not None:
            result.append(rule)
    return result


def serialize(items: list[WorkspaceRule]) -> list[str]:
    """Serialize a list of :class:`WorkspaceRule` back to config lines."""
    return [item.to_line() for item in items]


# ---------------------------------------------------------------------------
# Summaries (for row titles and pending-changes copy)
# ---------------------------------------------------------------------------


_SELECTOR_LABELS = {
    "name": "Named workspace",
    "special": "Special workspace",
}


def summarize_selector(rule: WorkspaceRule) -> str:
    """Title-line summary of a rule's selector.

    Numeric → ``"Workspace N"``; named/special use the prefix label;
    range / per-monitor selectors render verbatim — there's no shorter
    way to convey ``r[1-10]`` or ``m[1-3]`` without losing information.
    """
    selector = rule.workspace
    if selector.isdigit():
        return f"{_('Workspace')} {selector}"
    prefix, sep, rest = selector.partition(":")
    if sep and prefix in _SELECTOR_LABELS:
        return f"{_(_SELECTOR_LABELS[prefix])} “{rest}”"
    return f"{_('Workspace')} {selector}"


def summarize_settings(rule: WorkspaceRule) -> str:
    """Subtitle summarising the rule's overrides, comma-separated.

    Empty rules (only a selector) get the ``"(no overrides)"`` fallback
    so the row still has visible subtitle text — otherwise the row's
    height collapses and the layout looks ragged next to fuller rules.
    """
    parts: list[str] = []
    if rule.monitor is not None:
        parts.append(f"{_('on')} {rule.monitor}")
    if rule.default:
        parts.append(_("default"))
    if rule.persistent:
        parts.append(_("persistent"))
    if rule.gaps_in is not None:
        parts.append(f"{_('gaps in')} {_format_gap(rule.gaps_in)}")
    if rule.gaps_out is not None:
        parts.append(f"{_('gaps out')} {_format_gap(rule.gaps_out)}")
    if rule.border_size is not None:
        parts.append(f"{_('border')} {rule.border_size}px")
    if rule.border is False:
        parts.append(_("no border"))
    if rule.rounding is False:
        parts.append(_("no rounding"))
    if rule.shadow is False:
        parts.append(_("no shadow"))
    if rule.decorate is False:
        parts.append(_("no decoration"))
    if rule.default_name:
        parts.append(f"{_('name')} “{rule.default_name}”")
    if rule.on_created_empty:
        parts.append(f"{_('on first map')}: {rule.on_created_empty}")
    if not parts:
        return _("(no overrides)")
    return " · ".join(parts)


def summarize_rule(rule: WorkspaceRule) -> tuple[str, str]:
    """``(title, subtitle)`` pair for an ``Adw.ActionRow``."""
    return summarize_selector(rule), summarize_settings(rule)


# ---------------------------------------------------------------------------
# Live-apply matching
# ---------------------------------------------------------------------------


def matches_workspace(rule: WorkspaceRule, ws_id: int, ws_name: str) -> bool:
    """Does *rule*'s selector match an already-open workspace?

    Used by the live-apply path to enforce monitor binding and
    ``defaultName`` retroactively on existing workspaces — Hyprland only
    consults rule properties at workspace creation, so a freshly-pushed
    rule won't move/rename a workspace that's already open.

    Per-monitor (``m[N]``) selectors are inherently "the N-th workspace
    on a monitor" — they don't pin to a stable workspace identity, so
    we don't attempt retroactive matching for them. Unknown selector
    shapes return ``False`` for the same reason.
    """
    selector = rule.workspace
    if selector.isdigit():
        return ws_id == int(selector)
    if selector.startswith("name:"):
        return ws_name == selector[5:]
    if selector.startswith("special:"):
        # Hyprland names special workspaces ``special:foo`` in IPC.
        return ws_name == selector
    if selector.startswith("r[") and selector.endswith("]"):
        body = selector[2:-1]
        lo_s, sep, hi_s = body.partition("-")
        if not sep:
            return False
        try:
            return int(lo_s) <= ws_id <= int(hi_s)
        except ValueError:
            return False
    return False


# ---------------------------------------------------------------------------
# External loader (read-only display of rules from outside our managed file)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExternalWorkspaceRule:
    """A workspace rule from a config file outside hyprmod's managed file."""

    rule: WorkspaceRule
    source_path: Path
    lineno: int


def load_external_workspace_rules(
    root_path: Path,
    managed_path: Path,
) -> list[ExternalWorkspaceRule]:
    """Walk *root_path* and sourced files for ``workspace`` lines outside *managed_path*.

    Mirrors :func:`hyprmod.core.layer_rules.load_external_layer_rules` —
    advisory UI data, parse failures degrade to an empty list rather
    than blocking the page.
    """
    entries = load_external_keyword_entries(
        root_path,
        managed_path,
        WORKSPACE_RULE_KEYWORDS,
    )
    external: list[ExternalWorkspaceRule] = []
    for entry in entries:
        rule = parse_workspace_rule_body(entry.value)
        if rule is None:
            continue
        external.append(
            ExternalWorkspaceRule(
                rule=rule,
                source_path=entry.source_path,
                lineno=entry.lineno,
            )
        )
    return external


__all__ = [
    "WORKSPACE_RULE_KEYWORDS",
    "ExternalWorkspaceRule",
    "GapValue",
    "WorkspaceRule",
    "load_external_workspace_rules",
    "matches_workspace",
    "parse_workspace_rule_body",
    "parse_workspace_rule_lines",
    "serialize",
    "summarize_rule",
    "summarize_selector",
    "summarize_settings",
]
