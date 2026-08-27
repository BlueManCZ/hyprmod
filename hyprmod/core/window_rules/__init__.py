"""Window rule data, parsing, runtime dispatch, and external loader.

Public API is re-exported from this ``__init__`` so callers don't need
to know about the internal split. The submodules (all underscore-prefixed)
group cohesive concerns:

- :mod:`._model` — data shapes (:class:`WindowRule`, :class:`Matcher`),
  v3 keyword/effect/matcher constants, the action and matcher catalogs
  the rule-edit dialog renders, and the ``summarize_*`` helpers used
  for row titles.
- :mod:`._parse` — adapters between the library's structured
  :class:`hyprland_config.Rule` nodes and hyprmod's UI-facing
  :class:`WindowRule`, plus the Hyprlang text :func:`serialize`.
  ``hyprland_config.migrate()`` upstream normalises every authored
  shape (single-line ``windowrule = …`` and block-form
  ``windowrule { … }``) into Rule nodes before they reach here.
- :mod:`._runtime` — matcher evaluation against HyprMod's own window
  (gates the self-target confirm) and live windows (drives the
  retroactive dispatch); apply / revert dispatcher mappings.
- :mod:`._external` — read-only loader for windowrule lines from
  outside the managed config (``hyprland.conf`` and its sources).

Generic change-tracking primitives (``iter_item_changes``,
``detect_reorder``, ``drop_target_idx``, ``count_pending_changes``)
live in :mod:`hyprmod.core.change_tracking`. Import them from there
directly.
"""

from hyprmod.core.window_rules._external import (
    ExternalWindowRule,
    load_external_window_rules,
)
from hyprmod.core.window_rules._model import (
    ACTION_PRESETS,
    ACTION_PRESETS_BY_ID,
    CUSTOM_MATCHER_KIND,
    CUSTOM_PRESET,
    HYPRMOD_APP_ID,
    MATCHER_KINDS,
    MATCHER_KINDS_BY_KEY,
    RAW_KEY,
    WINDOW_RULE_KEYWORDS,
    ActionField,
    ActionPreset,
    Effect,
    Matcher,
    MatcherKind,
    WindowRule,
    lookup_matcher_kind,
    lookup_preset,
    summarize_action,
    summarize_matchers,
    summarize_rule,
)
from hyprmod.core.window_rules._parse import (
    from_rule_node,
    from_rule_nodes,
    parse_window_rule_line,
    parse_window_rule_lines,
    serialize,
)
from hyprmod.core.window_rules._runtime import (
    RETROACTIVE_EFFECTS,
    SETPROP_PASSTHROUGH_EFFECTS,
    STATIC_RETROACTIVE_EFFECTS,
    existing_window_dispatchers,
    existing_window_revert_dispatchers,
    matches_hyprmod,
    matches_window,
)

__all__ = [
    # Data shapes & constants.
    "ACTION_PRESETS",
    "ACTION_PRESETS_BY_ID",
    "CUSTOM_MATCHER_KIND",
    "CUSTOM_PRESET",
    "HYPRMOD_APP_ID",
    "MATCHER_KINDS",
    "MATCHER_KINDS_BY_KEY",
    "RAW_KEY",
    "RETROACTIVE_EFFECTS",
    "STATIC_RETROACTIVE_EFFECTS",
    "SETPROP_PASSTHROUGH_EFFECTS",
    "WINDOW_RULE_KEYWORDS",
    "ActionField",
    "ActionPreset",
    "Effect",
    "ExternalWindowRule",
    "Matcher",
    "MatcherKind",
    "WindowRule",
    # Rule-node adapters & serialize.
    "from_rule_node",
    "from_rule_nodes",
    "parse_window_rule_line",
    "parse_window_rule_lines",
    "serialize",
    # Catalog lookups & summaries.
    "lookup_matcher_kind",
    "lookup_preset",
    "summarize_action",
    "summarize_matchers",
    "summarize_rule",
    # Runtime matching & dispatch.
    "existing_window_dispatchers",
    "existing_window_revert_dispatchers",
    "matches_hyprmod",
    "matches_window",
    # External loader.
    "load_external_window_rules",
]
