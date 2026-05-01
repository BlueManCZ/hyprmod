"""Layer rule data, parsing, presets, and external loader.

Hyprland's ``layerrule = match:namespace REGEX, EFFECT VALUE`` keyword
controls how layer-shell surfaces — status bars (waybar), notification
daemons (mako/dunst), launchers (rofi/wofi), wallpapers (swaybg/
hyprpaper), lock screens — are decorated. It's the layer-side cousin of
``windowrule``: same rule-resolver concept, same v3 syntax shape.

**Format (Hyprland 0.54+):**

::

    layerrule = TOKEN1, TOKEN2, TOKEN3, ...

Each token is one of:

- ``match:PROP VALUE`` — currently only ``match:namespace REGEX`` is
  meaningful for layer surfaces (other props from the shared catalog
  are silently ignored at match time).
- ``EFFECT VALUE`` — every effect carries an explicit value. Bool
  effects accept ``on``/``off``/``true``/``false``/``1``/``0``; we
  always emit ``on`` for new rules. Numeric effects take ints/floats;
  ``animation`` takes a style name string.

**Available effects (from ``LayerRuleEffectContainer.cpp``):**

================== ===== =========================================
Effect             Type  Notes
================== ===== =========================================
``no_anim``        bool  Disable open/close animations
``blur``           bool  Backdrop blur
``blur_popups``    bool  Blur popups above this layer
``dim_around``     bool  Dim everything else
``xray``           bool  See-through blur
``no_screen_share`` bool Exclude from screen-share captures
``ignore_alpha``   float 0..1 — skip blur for low-alpha pixels
``order``          int   Sort within a layer (higher = on top)
``above_lock``     int   0..2 — render above the lockscreen
``animation``      str   ``slide`` / ``popin`` / ``fade`` / ``none``
================== ===== =========================================

**Legacy (pre-0.54) names auto-migrated on parse:** ``noanim`` →
``no_anim``, ``blurpopups`` → ``blur_popups``, ``dimaround`` →
``dim_around``, ``ignorealpha`` → ``ignore_alpha``, ``ignorezero`` →
``ignore_alpha 0``. The legacy ``RULE, NAMESPACE`` shape (no
``match:`` prefix) is also accepted on read so users with hand-rolled
old configs see their rules in the UI; we emit the v3 form on save.

The data model stays simple — one matcher (``namespace``) + one effect
per :class:`LayerRule`. Multi-effect lines are split into N rules
sharing a namespace at parse time, and N rules sharing a namespace
serialize back out as N separate lines (which Hyprland evaluates
identically).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import hyprland_config

from hyprmod.core import config

# Sequence of accepted keywords on read. Single-element tuple kept as a
# tuple (not a bare string) so the external loader and any hypothetical
# future versioned alias can extend without API churn at the call sites.
LAYER_RULE_KEYWORDS: tuple[str, ...] = (config.KEYWORD_LAYERRULE,)
KEYWORD_WRITE: str = config.KEYWORD_LAYERRULE


# ---------------------------------------------------------------------------
# Effect classifications
# ---------------------------------------------------------------------------


# Bool-typed effects: emit ``on`` if the user didn't specify a value.
# Hyprland 0.54.3 rejects bare ``blur`` (etc.) with "missing a value",
# so this set drives the same auto-fill ``effect_full`` does for
# windowrule's :data:`V3_BOOL_EFFECTS`.
LAYER_BOOL_EFFECTS: frozenset[str] = frozenset(
    {
        "no_anim",
        "blur",
        "blur_popups",
        "dim_around",
        "xray",
        "no_screen_share",
    }
)


# Map from legacy (pre-0.54) effect names to their v3 spelling.
# Applied transparently in :func:`parse_layer_rule_line` so users with
# hand-rolled old configs see their rules in the UI without manual
# migration. ``ignorezero`` is special: it had no argument in v1 but
# is equivalent to ``ignore_alpha 0`` in v3, so the migration carries
# an args override.
_LEGACY_EFFECT_RENAMES: dict[str, tuple[str, str | None]] = {
    "noanim": ("no_anim", None),
    "blurpopups": ("blur_popups", None),
    "dimaround": ("dim_around", None),
    "ignorealpha": ("ignore_alpha", None),
    "ignorezero": ("ignore_alpha", "0"),
    # ``noshadow`` and ``unset`` aren't in the 0.54 effect list at all —
    # there's nothing to migrate them *to*. We drop them silently
    # (return ``None`` from the parser) rather than emit invalid
    # config; users editing such a rule see it disappear from the UI,
    # which matches what Hyprland would do on reload.
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LayerRule:
    """A single ``layerrule = match:namespace REGEX, EFFECT [VALUE]`` entry.

    *namespace* is the regex matched against the layer surface's
    namespace (``waybar``, ``^(rofi|wofi)$``, ``notifications``).
    Stored verbatim so byte-for-byte round-trips survive even unusual
    escape sequences.

    *rule_name* is the v3 effect name (``blur``, ``ignore_alpha``,
    ``order``, …); *rule_args* is the space-joined argument string
    (``""`` for bool effects when constructed from scratch — the
    serializer auto-fills ``on``).
    """

    namespace: str
    rule_name: str
    rule_args: str = ""

    @property
    def effect_full(self) -> str:
        """Return ``rule_name`` plus args, with auto-``on`` for bool effects.

        Hyprland 0.54.3 rejects bare bool effects with "invalid field
        X: missing a value", so we always emit a value. Mirrors
        ``WindowRule.effect_full`` for v3 windowrule bool effects.
        """
        args = self.rule_args.strip()
        if not args and self.rule_name in LAYER_BOOL_EFFECTS:
            args = "on"
        if args:
            return f"{self.rule_name} {args}"
        return self.rule_name

    def body(self) -> str:
        """Serialize as ``match:namespace REGEX, EFFECT [VALUE]``.

        Match clause first by convention — reads as "for this surface,
        do this." Hyprland accepts either order, but match-first
        mirrors the windowrule serialization for consistency. Returned
        as the value half of the keyword; live-apply via
        ``hypr.keyword("layerrule", body)`` wants exactly this string.
        """
        return f"match:namespace {self.namespace}, {self.effect_full}"

    def to_line(self) -> str:
        """Serialize as the full ``layerrule = …`` config line."""
        return f"{KEYWORD_WRITE} = {self.body()}"


# ---------------------------------------------------------------------------
# Action catalog (curated effects shown in the dialog dropdown)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerActionField:
    """A single argument field for a :class:`LayerActionPreset`."""

    label: str
    placeholder: str = ""
    hint: str = ""
    kind: Literal["text", "number", "bool"] = "text"
    digits: int = 2
    min_value: float = 0.0
    max_value: float = 9999.0
    step: float = 1.0
    default: str = ""


@dataclass(frozen=True, slots=True)
class LayerActionPreset:
    """A pre-canned layer rule with a friendly label and zero-or-more args.

    Mirrors :class:`hyprmod.core.window_rules.ActionPreset` — same
    ``format(values)`` / ``parse_args(args_str)`` interface so the
    dialog plumbing stays uniform. Bool effects (``id`` in
    :data:`LAYER_BOOL_EFFECTS`) have no fields and emit ``<id> on`` on
    serialization; numeric/string effects carry their typed fields.
    """

    id: str
    label: str
    description: str
    fields: tuple[LayerActionField, ...] = ()

    def format(self, values: list[str]) -> str:
        """Build the rule args string from user-supplied field values."""
        cleaned = [v.strip() for v in values]
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        return " ".join(cleaned)

    def parse_args(self, args_str: str) -> list[str] | None:
        """Try to extract field values from the args portion of a rule.

        Always succeeds (returns the split args, padded to
        ``len(fields)``).
        """
        args = args_str.strip().split() if args_str.strip() else []
        while len(args) < len(self.fields):
            args.append("")
        return args


# Curated, ordered set of common layer rules. Bool effects come first
# (the typical "quick toggle" cases) followed by valued effects.
LAYER_ACTION_PRESETS: tuple[LayerActionPreset, ...] = (
    LayerActionPreset(
        id="blur",
        label="Blur background",
        description="Apply backdrop blur behind this layer surface (e.g. waybar, rofi).",
    ),
    LayerActionPreset(
        id="blur_popups",
        label="Blur popups",
        description="Also blur popup surfaces spawned above this layer.",
    ),
    LayerActionPreset(
        id="dim_around",
        label="Dim everything else",
        description=(
            "Dim the background while this surface is mapped. "
            "Typical for app launchers like rofi or wofi."
        ),
    ),
    LayerActionPreset(
        id="no_anim",
        label="No animations",
        description="Disable open/close animations for this surface.",
    ),
    LayerActionPreset(
        id="xray",
        label="Xray (see-through blur)",
        description=(
            "Make blur look through other windows instead of blurring them. "
            "Overrides ‘decoration:blur:xray’ for this surface."
        ),
    ),
    LayerActionPreset(
        id="no_screen_share",
        label="Exclude from screen share",
        description=(
            "Hide this surface from screen-sharing captures. "
            "Useful for notification daemons or password prompts."
        ),
    ),
    LayerActionPreset(
        id="ignore_alpha",
        label="Ignore alpha below threshold",
        description="Treat pixels below this alpha as not present when computing blur.",
        fields=(
            LayerActionField(
                label="Threshold",
                kind="number",
                digits=2,
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                default="0.30",
            ),
        ),
    ),
    LayerActionPreset(
        id="animation",
        label="Animation style",
        description="Override the open/close animation for this surface.",
        fields=(
            LayerActionField(
                label="Style",
                placeholder="slide",
                hint="One of: slide, popin, fade, none. (Style names depend on Hyprland version.)",
            ),
        ),
    ),
    LayerActionPreset(
        id="order",
        label="Render order",
        description=(
            "Sort key within a layer; surfaces with higher values render on top. "
            "Useful when two surfaces share a level."
        ),
        fields=(
            LayerActionField(
                label="Order",
                kind="number",
                digits=0,
                min_value=-100,
                max_value=100,
                step=1,
                default="0",
            ),
        ),
    ),
    LayerActionPreset(
        id="above_lock",
        label="Show above lockscreen",
        description=(
            "Allow this layer to render above the session lock. "
            "0 = below (default), 1 = above input fields, 2 = above everything."
        ),
        fields=(
            LayerActionField(
                label="Level",
                kind="number",
                digits=0,
                min_value=0,
                max_value=2,
                step=1,
                default="1",
                hint="0 = below lock; 1 = above input; 2 = above everything.",
            ),
        ),
    ),
)

LAYER_ACTION_PRESETS_BY_ID: dict[str, LayerActionPreset] = {p.id: p for p in LAYER_ACTION_PRESETS}

# Fall-through preset for plugin or future rule names not catalogued.
# The single field holds the entire rule verbatim (name + args).
CUSTOM_PRESET: LayerActionPreset = LayerActionPreset(
    id="__custom__",
    label="Custom rule…",
    description=(
        "Type any layerrule effect verbatim, including plugin rules "
        "or values introduced in newer Hyprland versions."
    ),
    fields=(
        LayerActionField(
            label="Rule",
            placeholder="plugin:foo bar",
            hint=(
                "The full effect text including any args, exactly as it "
                "would appear inside a layerrule line."
            ),
        ),
    ),
)


def lookup_preset(rule_name: str) -> LayerActionPreset:
    """Return the :class:`LayerActionPreset` for *rule_name*, or Custom."""
    return LAYER_ACTION_PRESETS_BY_ID.get(rule_name, CUSTOM_PRESET)


# ---------------------------------------------------------------------------
# Parser / serializer
# ---------------------------------------------------------------------------


def _split_top_level(s: str) -> list[str]:
    """Split a layerrule body on top-level commas.

    Top-level meaning: not inside parentheses (used by some matcher
    regexes — e.g. ``^(waybar|notifications)$``). Square and curly
    brackets are treated as parens too. Mirrors
    :func:`hyprmod.core.window_rules._parse._split_top_level`.
    """
    result: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            if depth > 0:
                depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            piece = "".join(current).strip()
            if piece:
                result.append(piece)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        result.append(tail)
    return result


def _parse_match_token(token: str) -> tuple[str, str] | None:
    """Parse a ``match:KEY VALUE`` token; return ``(key, value)`` or ``None``.

    Layer rules currently only honour ``match:namespace`` at runtime
    (other props from the shared rule catalog are silently ignored at
    match time), but the parser accepts any ``match:*`` token so we
    can round-trip future props without losing them.
    """
    body = token.strip()
    if not body.startswith("match:"):
        return None
    body = body[len("match:") :]
    if not body:
        return None
    key, sep, value = body.partition(" ")
    if not sep:
        # Hyprland's parser also accepts ``match:KEY=VALUE`` in some
        # block-form contexts; we don't write blocks but read leniently.
        if "=" in body:
            key, _, value = body.partition("=")
        else:
            return None
    return key.strip(), value.strip()


def _migrate_legacy_effect(name: str, args: str) -> tuple[str, str] | None:
    """Apply legacy → v3 effect rename, if applicable.

    Returns ``(new_name, new_args)`` for known renames, the input
    unchanged for already-v3 names, or ``None`` for legacy names with
    no v3 equivalent (``unset``, ``noshadow``) which the parser
    should drop.
    """
    if name in _LEGACY_EFFECT_RENAMES:
        new_name, new_args = _LEGACY_EFFECT_RENAMES[name]
        return new_name, new_args if new_args is not None else args
    if name in {"unset", "noshadow"}:
        # Legacy rules with no v3 home — drop them. ``unset`` is no
        # longer an effect type; ``noshadow`` was removed entirely.
        return None
    return name, args


def parse_layer_rule_line(line: str) -> LayerRule | None:
    """Parse a single ``layerrule = …`` line.

    Returns ``None`` for unrelated keywords or syntactically broken
    input (no ``=``, missing namespace, missing effect, all-effects-
    legacy-and-dropped).

    Accepts both formats:

    - **v3 (0.54+):** ``layerrule = match:namespace REGEX, EFFECT VALUE``
      — comma-separated tokens, one ``match:namespace`` plus at least
      one effect.
    - **Legacy (pre-0.54):** ``layerrule = EFFECT, NAMESPACE`` — single
      effect, bare namespace as the second comma-separated token. Effect
      names are migrated to v3 form (``noanim`` → ``no_anim``, etc.).

    For a multi-effect v3 line (``layerrule = match:namespace ^(waybar)$,
    blur on, ignore_alpha 0.3``) only the *first* surviving effect is
    captured — callers needing one-LayerRule-per-effect should use
    :func:`parse_layer_rule_lines`.
    """
    head, sep, tail = line.partition("=")
    if not sep:
        return None
    if head.strip() != KEYWORD_WRITE:
        return None
    body = tail.strip()
    if not body:
        return None
    rules = _parse_body_with_split(body)
    return rules[0] if rules else None


def _parse_body_with_split(body: str) -> list[LayerRule]:
    """Parse a layerrule body, returning N LayerRule entries — one per effect.

    Handles both v3 (``match:namespace …, effect …``) and legacy
    (``effect, namespace``) shapes. Multi-effect v3 lines split into
    N rules sharing the same namespace so a round-trip preserves all
    effects without merging them into a single rule (which would lose
    information when the user edits one).
    """
    tokens = _split_top_level(body)
    if not tokens:
        return []

    namespace: str | None = None
    effects: list[tuple[str, str]] = []
    legacy_namespace_candidates: list[str] = []

    for tok in tokens:
        match_pair = _parse_match_token(tok)
        if match_pair is not None:
            key, value = match_pair
            # Layer rules only honour ``match:namespace`` at runtime;
            # we still accept other prop keys to round-trip future
            # additions, but for our data model only the namespace
            # matters. First-wins on duplicate namespace tokens.
            if key == "namespace" and namespace is None:
                namespace = value
            continue

        # Effect token: first space-separated word is the name, rest is args.
        name, _, args = tok.partition(" ")
        name = name.strip()
        args = args.strip()
        if not name:
            continue

        # Legacy form recognition: in pre-0.54 layerrule syntax, the
        # bare namespace appeared as a token without `match:` prefix
        # and without a space-separated value (e.g. ``blur, waybar``
        # or ``blur, ^(waybar)$``). If a token has no space (no value),
        # it's a candidate legacy namespace.
        if not args:
            # Could be a nullary legacy effect like ``unset`` or a
            # legacy namespace — disambiguate by checking the legacy
            # rename table. Names found in :data:`_LEGACY_EFFECT_RENAMES`
            # or in our v3 catalog stay as effects (the migration step
            # below assigns ``on`` for bool effects). Anything else is
            # treated as the legacy namespace.
            looks_like_effect = (
                name in _LEGACY_EFFECT_RENAMES
                or name in LAYER_ACTION_PRESETS_BY_ID
                or name in {"unset", "noshadow"}
            )
            if not looks_like_effect:
                legacy_namespace_candidates.append(name)
                continue

        migrated = _migrate_legacy_effect(name, args)
        if migrated is None:
            continue  # legacy effect with no v3 equivalent — drop
        effects.append(migrated)

    # Legacy form fallback: if we didn't find a v3 ``match:namespace``
    # token but did see a bare namespace candidate, use the *last*
    # such candidate (Hyprland's legacy parser took the rightmost
    # comma-separated token as the namespace).
    if namespace is None and legacy_namespace_candidates:
        namespace = legacy_namespace_candidates[-1]

    if namespace is None or not effects:
        return []

    return [LayerRule(namespace=namespace, rule_name=n, rule_args=a) for n, a in effects]


def parse_layer_rule_lines(lines: list[str]) -> list[LayerRule]:
    """Parse multiple raw rule lines, dropping anything unparseable.

    Multi-effect v3 lines split into N one-effect rules sharing the
    same namespace; the round-trip preserves every effect without
    collapsing them into one rule.
    """
    result: list[LayerRule] = []
    for raw in lines:
        head, sep, tail = raw.partition("=")
        if not sep or head.strip() != KEYWORD_WRITE:
            continue
        body = tail.strip()
        if not body:
            continue
        result.extend(_parse_body_with_split(body))
    return result


def serialize(items: list[LayerRule]) -> list[str]:
    """Serialize a list of :class:`LayerRule` back to v3 config lines."""
    return [item.to_line() for item in items]


# ---------------------------------------------------------------------------
# Summaries (for row titles and pending-changes copy)
# ---------------------------------------------------------------------------


def summarize_action(rule: LayerRule) -> str:
    """Friendly label for a rule's effect (e.g. ``Animation style: slide``)."""
    preset = LAYER_ACTION_PRESETS_BY_ID.get(rule.rule_name)
    if preset is None:
        full = rule.effect_full
        return full or "(no rule)"
    args = rule.rule_args.strip()
    # Bool effects auto-fill ``on`` on serialization but read cleaner
    # in the title without the redundant value — "Blur background"
    # beats "Blur background: on".
    if not args or args.lower() == "on":
        return preset.label
    return f"{preset.label}: {args}"


def summarize_namespace(rule: LayerRule) -> str:
    """Plain-English summary of the namespace clause."""
    return f"namespace: {rule.namespace}"


def summarize_rule(rule: LayerRule) -> tuple[str, str]:
    """Two-line ``(title, subtitle)`` summary for an ``Adw.ActionRow``."""
    return summarize_action(rule), summarize_namespace(rule)


# ---------------------------------------------------------------------------
# External loader (read-only display of rules from outside our managed file)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExternalLayerRule:
    """A layerrule from a config file outside hyprmod's managed file."""

    rule: LayerRule
    source_path: Path
    lineno: int


def load_external_layer_rules(
    root_path: Path,
    managed_path: Path,
) -> list[ExternalLayerRule]:
    """Walk *root_path* and its sourced files for layerrule entries
    that don't live in *managed_path*.

    Mirrors :func:`hyprmod.core.window_rules.load_external_window_rules`.
    Errors return an empty list (advisory display only; failing
    silently is safer than blocking the page on a flaky config).

    Both v3 and legacy lines surface as :class:`LayerRule` instances —
    the parser auto-migrates legacy effect names so users see a
    consistent view regardless of which syntax their config uses.
    """
    if not root_path.exists():
        return []
    try:
        doc = hyprland_config.load(root_path, follow_sources=True, lenient=True)
    except (OSError, hyprland_config.ParseError, hyprland_config.SourceCycleError):
        return []

    managed_str = str(managed_path)
    external: list[ExternalLayerRule] = []
    for keyword in LAYER_RULE_KEYWORDS:
        for entry in doc.find_all(keyword):
            if entry.source_name == managed_str:
                continue
            line = f"{entry.key} = {entry.value}"
            # Multi-effect lines split into multiple rules, each carried
            # separately so the UI can show every effect with its source
            # location.
            for rule in parse_layer_rule_lines([line]):
                external.append(
                    ExternalLayerRule(
                        rule=rule,
                        source_path=Path(entry.source_name),
                        lineno=entry.lineno,
                    )
                )
    return external


# ---------------------------------------------------------------------------
# Drag-and-drop helper (mirrors core.autostart / core.window_rules._changes)
# ---------------------------------------------------------------------------


def drop_target_idx(src: int, hover: int, before: bool) -> int:
    """Translate a drag-and-drop hover into a ``SavedList.move`` target.

    See :func:`hyprmod.core.autostart.drop_target_idx` for the
    derivation; same semantics apply here.
    """
    if before:
        return hover - 1 if src < hover else hover
    return hover if src < hover else hover + 1


# ---------------------------------------------------------------------------
# Change tracking (mirrors core.autostart)
# ---------------------------------------------------------------------------


ChangeKind = Literal["added", "modified", "removed"]


def detect_reorder(saved: list[LayerRule], current: list[LayerRule]) -> bool:
    """True if entries common to both lists appear in different relative order."""
    saved_lines = [e.to_line() for e in saved]
    current_lines = [e.to_line() for e in current]
    common = set(saved_lines) & set(current_lines)
    if len(common) < 2:
        return False
    saved_positions = [line for line in saved_lines if line in common]
    current_positions = [line for line in current_lines if line in common]
    return saved_positions != current_positions


def iter_item_changes(
    saved: list[LayerRule],
    current: list[LayerRule],
    current_baselines: list[LayerRule | None],
) -> Iterator[tuple[ChangeKind, int, LayerRule, LayerRule | None]]:
    """Yield per-item add/modify/remove changes.

    Same iterator shape as :func:`hyprmod.core.autostart.iter_item_changes`
    so the sidebar badge counter and pending-list collector share one
    source of truth.
    """
    if len(current) != len(current_baselines):
        raise ValueError(
            "current and current_baselines must be the same length "
            f"(got {len(current)} vs. {len(current_baselines)})"
        )

    surviving_baselines: set[str] = set()
    for idx, (item, baseline) in enumerate(zip(current, current_baselines, strict=True)):
        if baseline is None:
            yield "added", idx, item, None
        else:
            surviving_baselines.add(baseline.to_line())
            if baseline.to_line() != item.to_line():
                yield "modified", idx, item, baseline
    for s in saved:
        if s.to_line() not in surviving_baselines:
            yield "removed", -1, s, None


def count_pending_changes(
    saved: list[LayerRule],
    current: list[LayerRule],
    current_baselines: list[LayerRule | None],
) -> int:
    """Total pending-change entries: per-item changes + reorder roll-up."""
    count = sum(1 for _ in iter_item_changes(saved, current, current_baselines))
    if detect_reorder(saved, current):
        count += 1
    return count


__all__ = [
    "CUSTOM_PRESET",
    "KEYWORD_WRITE",
    "LAYER_ACTION_PRESETS",
    "LAYER_ACTION_PRESETS_BY_ID",
    "LAYER_BOOL_EFFECTS",
    "LAYER_RULE_KEYWORDS",
    "ChangeKind",
    "ExternalLayerRule",
    "LayerActionField",
    "LayerActionPreset",
    "LayerRule",
    "count_pending_changes",
    "detect_reorder",
    "drop_target_idx",
    "iter_item_changes",
    "load_external_layer_rules",
    "lookup_preset",
    "parse_layer_rule_line",
    "parse_layer_rule_lines",
    "serialize",
    "summarize_action",
    "summarize_namespace",
    "summarize_rule",
]
