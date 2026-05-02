"""Window rule data, parsing, runtime dispatch, and external loader.

Public API is re-exported from this ``__init__`` so callers don't need
to know about the internal split. The submodules (all underscore-prefixed)
group cohesive concerns:

- :mod:`._model` — data shapes (:class:`WindowRule`, :class:`Matcher`),
  v3 keyword/effect/matcher constants, the action and matcher catalogs
  the rule-edit dialog renders, and the ``summarize_*`` helpers used
  for row titles.
- :mod:`._parse` — v3 ``windowrule = …`` parser plus ``serialize``.
  Legacy ``windowrulev2`` lines are migrated to v3 upstream by
  ``hyprland_config.migrate()``; the parser itself is v3-only.
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
    KEYWORD_WRITE,
    MATCHER_KINDS,
    MATCHER_KINDS_BY_KEY,
    RAW_KEY,
    V3_BOOL_EFFECTS,
    V3_BOOL_MATCHERS,
    WINDOW_RULE_KEYWORDS,
    ActionField,
    ActionPreset,
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
    parse_window_rule_line,
    parse_window_rule_lines,
    serialize,
)
from hyprmod.core.window_rules._runtime import (
    RETROACTIVE_EFFECTS,
    SETPROP_PASSTHROUGH_EFFECTS,
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
    "KEYWORD_WRITE",
    "MATCHER_KINDS",
    "MATCHER_KINDS_BY_KEY",
    "RAW_KEY",
    "RETROACTIVE_EFFECTS",
    "SETPROP_PASSTHROUGH_EFFECTS",
    "V3_BOOL_EFFECTS",
    "V3_BOOL_MATCHERS",
    "WINDOW_RULE_KEYWORDS",
    "ActionField",
    "ActionPreset",
    "ExternalWindowRule",
    "Matcher",
    "MatcherKind",
    "WindowRule",
    # Parse / serialize.
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
