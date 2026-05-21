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

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hyprland_config import (
    LAYER_BOOL_EFFECTS,
    LAYERRULE_V3_VERSION,
    Rule,
    parse_version,
    render_rule_hyprlang,
    split_top_level,
)

from hyprmod.core import config
from hyprmod.core.external import load_external_rule_entries


def _predates_v3_layerrule(version: str) -> bool:
    """True when *version* predates Hyprland's v3 layerrule grammar (0.54).

    Below 0.54 the compositor uses the effect-first form
    (``layerrule = blur, ^(waybar)$``) with run-together effect names
    (``noanim``, ``blurpopups``, …); pushing ``match:namespace`` is
    rejected.
    """
    return parse_version(version) < LAYERRULE_V3_VERSION


# Sequence of accepted keywords on read. Single-element tuple kept as a
# tuple (not a bare string) so the external loader and any hypothetical
# future versioned alias can extend without API churn at the call sites.
LAYER_RULE_KEYWORDS: tuple[str, ...] = (config.KEYWORD_LAYERRULE,)


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
}

# Legacy names without a v3 equivalent. Dropped by ``_migrate_legacy_effect``
# rather than emitted as invalid config; users editing such a rule see it
# disappear from the UI, which matches what Hyprland would do on reload.
_DROPPED_LEGACY_EFFECTS: frozenset[str] = frozenset({"unset", "noshadow"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LayerEffect:
    """A single ``EFFECT [ARGS]`` clause inside a layerrule.

    Block-form rules (``layerrule { match:namespace waybar; blur =
    on; ignore_alpha = 0.5 }``) become one :class:`LayerRule` with
    several :class:`LayerEffect` entries; single-line rules collapse
    to a one-element list.
    """

    name: str
    args: str = ""

    @property
    def full(self) -> str:
        """Serialize as ``name [args]`` with auto-``on`` for bool effects.

        Hyprland 0.54.3 rejects bare bool effects with "invalid field
        X: missing a value", so we always emit a value.
        """
        args = self.args.strip()
        if not args and self.name in LAYER_BOOL_EFFECTS:
            args = "on"
        return f"{self.name} {args}" if args else self.name


@dataclass(slots=True)
class LayerRule:
    """A v2 layer rule: namespace match plus one or more effects.

    *namespace* is the regex matched against the layer surface's
    namespace (``waybar``, ``^(rofi|wofi)$``, ``notifications``).
    Stored verbatim so byte-for-byte round-trips survive even unusual
    escape sequences.

    Hyprland's special-category form (``layerrule { name = X;
    match:namespace = N; e1 = a; e2 = b }``) bundles multiple effects
    under a single name; single-line rules carry exactly one effect
    and no name. The serializer picks block vs. single-line at
    :meth:`to_line` time based on whether *name*, *enabled*, or a
    multi-effect list demand the block form.
    """

    namespace: str
    effects: list["LayerEffect"]
    # Empty when the rule is anonymous. Naming enables hyprctl/Lua
    # dynamic enable/disable.
    name: str = ""
    # False when the rule is defined-but-inactive (``enable = 0``).
    enabled: bool = True

    # -- Single-effect compatibility shims ---------------------------------
    # Predates the multi-effect refactor; reads the first effect so
    # legacy single-effect call sites keep working unchanged.

    @property
    def rule_name(self) -> str:
        return self.effects[0].name if self.effects else ""

    @property
    def rule_args(self) -> str:
        return self.effects[0].args if self.effects else ""

    @property
    def effect_full(self) -> str:
        return self.effects[0].full if self.effects else ""

    def body(self) -> str:
        """Serialize as the v2 single-line value half of the rule.

        Returns ``match:namespace REGEX, EFFECT [VALUE], …`` — the
        match clause first by convention. Live-apply via
        ``hypr.keyword("layerrule", body)`` wants exactly this; the
        keyword prefix is supplied separately. :attr:`name` and the
        disabled flag are intentionally omitted because Hyprland's
        single-line handler rejects them. Use :meth:`to_line` for the
        on-disk form that switches to block when those fields demand it.
        """
        parts = [f"match:namespace {self.namespace}"]
        parts.extend(e.full for e in self.effects)
        return ", ".join(parts)

    def to_rule_node(self) -> Rule:
        """Build the equivalent library :class:`hyprland_config.Rule` node.

        Mirrors :meth:`hyprmod.core.window_rules.WindowRule.to_rule_node`:
        feeds the language-specific serializers
        (:func:`hyprland_config.render_rule_hyprlang`,
        :func:`hyprland_config.render_rule_lua`) without going through a
        stringly-typed intermediate.
        """
        return Rule(
            raw="",
            kind=config.KEYWORD_LAYERRULE,
            name=self.name,
            enabled=self.enabled,
            matchers=[("namespace", self.namespace)],
            effects=[(e.name, e.args) for e in self.effects],
        )

    def to_line(self, version: str | None = None) -> str:
        """Serialize as on-disk form for *version*.

        Hyprland 0.54+ accepts the v3 grammar: anonymous rules emit as
        compact single-line, named/disabled ones as a ``layerrule { … }``
        block (the single-line handler rejects ``name``/``enable``).
        Below 0.54 the grammar is effect-first (``layerrule = blur,
        ^(waybar)$``) with the older run-together effect names; that
        path delegates to :func:`hyprland_config.render_rule_hyprlang`
        so the grammar lives in one place. ``None`` (the default, used
        for identity keys) always emits v3.
        """
        if version is not None and _predates_v3_layerrule(version):
            return render_rule_hyprlang(self.to_rule_node(), version).rstrip("\n")
        needs_block = bool(self.name) or not self.enabled
        if needs_block:
            return self._to_block()
        return f"{config.KEYWORD_LAYERRULE} = {self.body()}"

    def _to_block(self) -> str:
        """Serialize as a multi-line ``layerrule { … }`` block."""
        lines = [f"{config.KEYWORD_LAYERRULE} {{"]
        if self.name:
            lines.append(f"    name = {self.name}")
        if not self.enabled:
            lines.append("    enable = 0")
        lines.append(f"    match:namespace = {self.namespace}")
        for e in self.effects:
            args = e.args.strip()
            if not args and e.name in LAYER_BOOL_EFFECTS:
                args = "on"
            lines.append(f"    {e.name} = {args}" if args else f"    {e.name} =")
        lines.append("}")
        return "\n".join(lines)


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
    no v3 equivalent (``unset``, ``noshadow``) which the parser drops.
    """
    if name in _LEGACY_EFFECT_RENAMES:
        new_name, new_args = _LEGACY_EFFECT_RENAMES[name]
        return new_name, new_args if new_args is not None else args
    if name in _DROPPED_LEGACY_EFFECTS:
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
    if head.strip() != config.KEYWORD_LAYERRULE:
        return None
    body = tail.strip()
    if not body:
        return None
    rules = _parse_body_with_split(body)
    return rules[0] if rules else None


def _parse_body_with_split(body: str) -> list[LayerRule]:
    """Parse a layerrule body, returning LayerRule entries for the line.

    Handles the two text shapes Hyprland's single-line handler accepts:

    - **v2 single-line** (``match:namespace …, effect …``): split into
      N single-effect rules so an anonymous multi-effect line preserves
      its on-disk shape after a round-trip.
    - **Legacy v1** (``effect, namespace``): single-effect rule with
      effect names migrated to v2 form (``noanim`` → ``no_anim``, etc.).

    Block-form / named layerrules are normalised upstream by
    :func:`hyprland_config.migrate` into :class:`hyprland_config.Rule`
    nodes and never reach this parser — :func:`from_rule_node` is the
    structured-input entry point for those.
    """
    tokens = split_top_level(body)
    if not tokens:
        return []

    namespace: str | None = None
    effects: list[tuple[str, str]] = []
    legacy_namespace_candidates: list[str] = []

    for tok in tokens:
        stripped = tok.strip()
        match_pair = _parse_match_token(stripped)
        if match_pair is not None:
            mkey, mvalue = match_pair
            # Layer rules only honour ``match:namespace`` at runtime;
            # we still accept other prop keys to round-trip future
            # additions, but for our data model only the namespace
            # matters. First-wins on duplicate namespace tokens.
            if mkey == "namespace" and namespace is None:
                namespace = mvalue
            continue

        # Effect token: first space-separated word is the name, rest is args.
        ename, _, eargs = stripped.partition(" ")
        ename = ename.strip()
        eargs = eargs.strip()
        if not ename:
            continue

        # Legacy form recognition: in pre-0.54 layerrule syntax, the
        # bare namespace appeared as a token without `match:` prefix
        # and without a space-separated value (e.g. ``blur, waybar``
        # or ``blur, ^(waybar)$``). If a token has no space (no value),
        # it's a candidate legacy namespace.
        if not eargs:
            looks_like_effect = (
                ename in _LEGACY_EFFECT_RENAMES
                or ename in LAYER_ACTION_PRESETS_BY_ID
                or ename in _DROPPED_LEGACY_EFFECTS
            )
            if not looks_like_effect:
                legacy_namespace_candidates.append(ename)
                continue

        migrated = _migrate_legacy_effect(ename, eargs)
        if migrated is None:
            continue  # legacy effect with no v3 equivalent — drop
        effects.append(migrated)

    # Legacy form fallback: if we didn't find a v2 ``match:namespace``
    # token but did see a bare namespace candidate, use the *last*
    # such candidate (Hyprland's legacy parser took the rightmost
    # comma-separated token as the namespace).
    if namespace is None and legacy_namespace_candidates:
        namespace = legacy_namespace_candidates[-1]

    if namespace is None or not effects:
        return []

    return [
        LayerRule(namespace=namespace, effects=[LayerEffect(name=n, args=a)]) for n, a in effects
    ]


def parse_layer_rule_lines(lines: list[str]) -> list[LayerRule]:
    """Parse multiple raw rule lines, dropping anything unparseable.

    Multi-effect v3 lines split into N one-effect rules sharing the
    same namespace; the round-trip preserves every effect without
    collapsing them into one rule.
    """
    result: list[LayerRule] = []
    for raw in lines:
        head, sep, tail = raw.partition("=")
        if not sep or head.strip() != config.KEYWORD_LAYERRULE:
            continue
        body = tail.strip()
        if not body:
            continue
        result.extend(_parse_body_with_split(body))
    return result


def serialize(items: list[LayerRule], version: str | None = None) -> list[str]:
    """Serialize a list of :class:`LayerRule` to Hyprlang config lines.

    *version* selects the grammar: below 0.54 each rule renders in the
    legacy effect-first form so the saved config reloads cleanly on the
    compositor that's actually running. ``None`` emits v3.
    """
    return [item.to_line(version) for item in items]


def from_rule_node(node: "Rule") -> LayerRule | None:
    """Build a :class:`LayerRule` from a library :class:`Rule`.

    Returns ``None`` for non-layerrule nodes (lets callers iterate a
    mixed Rule list with a one-liner filter-and-convert).
    """
    if node.kind != config.KEYWORD_LAYERRULE:
        return None
    namespace = ""
    for k, v in node.matchers:
        if k == "namespace":
            namespace = v
            break
    if not namespace or not node.effects:
        return None
    return LayerRule(
        namespace=namespace,
        effects=[LayerEffect(name=n, args=a) for n, a in node.effects],
        name=node.name,
        enabled=node.enabled,
    )


def from_rule_nodes(nodes: list["Rule"]) -> list[LayerRule]:
    """Convert a Document's :class:`Rule` list into UI :class:`LayerRule`s.

    Mirrors :func:`hyprmod.core.window_rules.from_rule_nodes`: named
    rules stay bundled, anonymous multi-effect rules split per effect
    so the page UI's "one row per effect" model holds.
    """
    out: list[LayerRule] = []
    for node in nodes:
        lr = from_rule_node(node)
        if lr is None:
            continue
        if lr.name or len(lr.effects) <= 1:
            out.append(lr)
            continue
        for effect in lr.effects:
            out.append(
                LayerRule(
                    namespace=lr.namespace,
                    effects=[effect],
                    enabled=lr.enabled,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Summaries (for row titles and pending-changes copy)
# ---------------------------------------------------------------------------


def _summarize_one_effect(effect: LayerEffect) -> str:
    """Friendly label for one effect (e.g. ``Animation style: slide``)."""
    preset = LAYER_ACTION_PRESETS_BY_ID.get(effect.name)
    if preset is None:
        full = effect.full
        return full or "(no rule)"
    args = effect.args.strip()
    # Bool effects auto-fill ``on`` on serialization but read cleaner
    # in the title without the redundant value.
    if not args or args.lower() == "on":
        return preset.label
    return f"{preset.label}: {args}"


def summarize_action(rule: LayerRule) -> str:
    """Friendly label for a rule's effects, multi-effect joined with ``+``."""
    if not rule.effects:
        return "(no rule)"
    if len(rule.effects) == 1:
        return _summarize_one_effect(rule.effects[0])
    return " + ".join(_summarize_one_effect(e) for e in rule.effects)


def summarize_namespace(rule: LayerRule) -> str:
    """Plain-English summary of the namespace clause."""
    return f"namespace: {rule.namespace}"


def summarize_rule(rule: LayerRule) -> tuple[str, str]:
    """Two-line ``(title, subtitle)`` summary for an ``Adw.ActionRow``."""
    subtitle = summarize_namespace(rule)
    if rule.name:
        subtitle = f"{subtitle} · {rule.name}"
    if not rule.enabled:
        subtitle = f"{subtitle} (disabled)"
    return summarize_action(rule), subtitle


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

    v2 layerrules (``layerrule = match:namespace …, effect …``) and
    block-form rules surface as structured :class:`Rule` nodes from
    ``hyprland_config.migrate()``. Legacy v1 rules
    (``layerrule = effect, namespace`` — no ``match:`` prefix) fall
    through the normaliser and stay as ``Keyword`` lines; we still
    surface them by running them through hyprmod's lenient v1 parser
    so users with hand-rolled old configs see their rules in the UI.
    """
    from hyprmod.core.external import load_external_keyword_entries

    external: list[ExternalLayerRule] = []
    for entry in load_external_rule_entries(root_path, managed_path, (config.KEYWORD_LAYERRULE,)):
        lr = from_rule_node(entry.rule)
        if lr is None:
            continue
        external.append(
            ExternalLayerRule(
                rule=lr,
                source_path=entry.source_path,
                lineno=entry.lineno,
            )
        )

    # Legacy v1 lines stayed as Keyword nodes — parse them via hyprmod's
    # lenient fallback so users with pre-0.54 configs still see them.
    for entry in load_external_keyword_entries(
        root_path, managed_path, LAYER_RULE_KEYWORDS, migrate_doc=True
    ):
        line = f"{entry.key} = {entry.value}"
        for rule in parse_layer_rule_lines([line]):
            external.append(
                ExternalLayerRule(
                    rule=rule,
                    source_path=entry.source_path,
                    lineno=entry.lineno,
                )
            )
    return external


__all__ = [
    "CUSTOM_PRESET",
    "LAYER_ACTION_PRESETS",
    "LAYER_ACTION_PRESETS_BY_ID",
    "LAYER_RULE_KEYWORDS",
    "ExternalLayerRule",
    "LayerActionField",
    "LayerActionPreset",
    "LayerEffect",
    "LayerRule",
    "from_rule_node",
    "from_rule_nodes",
    "load_external_layer_rules",
    "lookup_preset",
    "parse_layer_rule_line",
    "parse_layer_rule_lines",
    "serialize",
    "summarize_action",
    "summarize_namespace",
    "summarize_rule",
]
