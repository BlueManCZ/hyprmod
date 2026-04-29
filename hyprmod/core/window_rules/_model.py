"""Data shapes and catalogs for v3 window rules.

Holds the parsed-rule classes (:class:`Matcher`, :class:`WindowRule`),
the immutable v3 keyword/effect/matcher constants, and the curated
presentation catalogs (:data:`ACTION_PRESETS`, :data:`MATCHER_KINDS`)
used by the rule-edit dialog.

This module deliberately has no parsing or runtime-dispatch logic —
those live in :mod:`._parse` and :mod:`._runtime` respectively. Keeping
the data types in their own module lets the catalogs (which are
hundreds of lines of static config) coexist with the dataclasses
without dragging the parser or the IPC layer along.
"""

from dataclasses import dataclass
from typing import Literal

from hyprmod.core import config

# HyprMod's own application id — the value Hyprland reports as ``class``
# for our window. Used by :func:`matches_hyprmod` to gate live-apply
# behind a confirmation dialog when a user-authored rule would target
# the editor itself (e.g. floating or fading the running editor mid-edit).
# Must stay in sync with ``application_id`` in ``hyprmod.main`` and the
# ``StartupWMClass`` in our .desktop file.
HYPRMOD_APP_ID: str = "io.github.bluemancz.hyprmod"

# Both keywords accepted on read. Output is always v3 ``windowrule``.
# Legacy ``windowrulev2`` lines auto-migrate (see :mod:`._migrate`).
WINDOW_RULE_KEYWORDS: tuple[str, ...] = (
    config.KEYWORD_WINDOWRULE,
    config.KEYWORD_WINDOWRULEV2,
)
KEYWORD_WRITE: str = config.KEYWORD_WINDOWRULE

# Sentinel matcher key for opaque tokens — anything in the matcher slot
# that doesn't fit the v3 ``match:KEY VALUE`` shape (usually a custom
# token someone pasted in) is round-tripped under this key with the
# raw text in ``value``.
RAW_KEY: str = "_raw"


# ---------------------------------------------------------------------------
# v3 constants
# ---------------------------------------------------------------------------


# v3 effects whose only argument is a boolean. We always emit ``on`` for
# these — Hyprland 0.53+ treats a bare ``float`` (no value) as a config
# error. The set is the union of the "static effects" that take ``[on]``
# and the "dynamic effects" that take ``[on]``, per the v0.54 wiki.
V3_BOOL_EFFECTS: frozenset[str] = frozenset(
    {
        # Static
        "float", "tile", "fullscreen", "maximize", "center", "pseudo",
        "no_initial_focus", "pin",
        # Dynamic
        "persistent_size", "no_max_size", "stay_focused",
        "allows_input", "dim_around", "decorate", "focus_on_activate",
        "keep_aspect_ratio", "nearest_neighbor",
        "no_anim", "no_blur", "no_dim", "no_focus", "no_follow_mouse",
        "no_shadow", "no_shortcuts_inhibit", "no_screen_share", "no_vrr",
        "opaque", "force_rgbx", "sync_fullscreen", "immediate", "xray",
        "render_unfocused",
    }
)  # fmt: skip


# v3 matcher keys whose value is a boolean (``true``/``false``).
V3_BOOL_MATCHERS: frozenset[str] = frozenset(
    {"xwayland", "float", "fullscreen", "pin", "focus", "group", "modal"}
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Matcher:
    """A single ``match:KEY VALUE`` clause.

    Negation in v3 is encoded by prefixing the *value* with
    ``negative:`` (e.g. ``match:class negative:firefox``). We keep that
    in the value field rather than introducing a separate negated flag
    so byte-for-byte round-trips work even for unusual prefixes the
    parser doesn't introspect.
    """

    key: str
    value: str

    def to_str(self) -> str:
        """Serialize as ``match:KEY VALUE`` (or raw text for RAW_KEY)."""
        if self.key == RAW_KEY:
            return self.value
        return f"match:{self.key} {self.value}"


@dataclass(slots=True)
class WindowRule:
    """A v3 window rule: a list of matchers + a single effect.

    The wiki technically allows multiple effects per rule
    (``windowrule = match:class kitty, opacity 0.8, no_blur on``).
    We model one effect per rule for UX simplicity — multi-effect
    rules read by the parser get split into N rules sharing the same
    matchers (and on save, two rules with identical matchers are
    semantically equivalent to one rule with two effects).
    """

    matchers: list[Matcher]
    # Full effect name (e.g. ``float``, ``opacity``, ``no_blur``).
    effect_name: str
    # Args after the effect name. Empty string for unary effects when
    # building from scratch — the writer auto-fills ``on`` if the
    # name is in :data:`V3_BOOL_EFFECTS`.
    effect_args: str = ""

    @property
    def effect_full(self) -> str:
        """Return ``effect_name`` plus args, with auto-``on`` for booleans."""
        args = self.effect_args.strip()
        if not args and self.effect_name in V3_BOOL_EFFECTS:
            # Hyprland 0.53+ rejects bare boolean effects; default to ``on``.
            args = "on"
        if args:
            return f"{self.effect_name} {args}"
        return self.effect_name

    def body(self) -> str:
        """Serialize the value half of the rule line.

        Returns ``match:..., effect ...`` — i.e. everything that would
        come after ``windowrule = ``. Live-apply via ``hypr.keyword``
        wants exactly this; the keyword prefix is supplied separately.

        Matchers come before the effect by convention — Hyprland accepts
        either order, but match-first reads more naturally as "for these
        windows, do this."
        """
        parts = [m.to_str() for m in self.matchers]
        parts.append(self.effect_full)
        return ", ".join(parts)

    def to_line(self) -> str:
        """Serialize as the full ``windowrule = match:..., effect ...`` line."""
        return f"{KEYWORD_WRITE} = {self.body()}"


# ---------------------------------------------------------------------------
# Action catalog (for the UI's structured editor)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActionField:
    """A single argument field for an :class:`ActionPreset`."""

    label: str
    placeholder: str = ""
    hint: str = ""
    kind: Literal["text", "number"] = "text"
    digits: int = 2
    min_value: float = 0.0
    max_value: float = 9999.0
    step: float = 1.0
    default: str = ""


@dataclass(frozen=True, slots=True)
class ActionPreset:
    """A pre-canned v3 effect with a friendly label and zero-or-more args.

    ``id`` doubles as the v3 effect name and the dropdown machine
    value. The dialog asks the preset to ``format(values)`` when the
    user clicks Apply; ``parse_args(args)`` runs in reverse when
    re-opening an existing rule.

    Boolean presets (``id`` in :data:`V3_BOOL_EFFECTS`) have no fields
    — they always emit ``<id> on``. The serializer adds the ``on``;
    the preset just has ``fields=()`` to signal "no UI args."
    """

    id: str
    label: str
    description: str
    fields: tuple[ActionField, ...] = ()

    def format(self, values: list[str]) -> str:
        """Build the effect args string from user-supplied field values."""
        cleaned = [v.strip() for v in values]
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        return " ".join(cleaned)

    def parse_args(self, args_str: str) -> list[str] | None:
        """Try to extract field values from the args portion of an effect.

        Always succeeds (returns the split args, padded to
        ``len(fields)``) — there's no preset-mismatch concept now
        that an effect's ``id`` is the leading token of the line.
        """
        args = args_str.strip().split() if args_str.strip() else []
        while len(args) < len(self.fields):
            args.append("")
        return args


# Curated, ordered set of "common" v3 effects. Boolean-only effects
# show up with ``fields=()`` — picking them auto-emits ``<id> on``.
ACTION_PRESETS: tuple[ActionPreset, ...] = (
    ActionPreset(
        id="float",
        label="Float window",
        description="Open the window detached from the tiling layout.",
    ),
    ActionPreset(
        id="tile",
        label="Tile window",
        description="Force the window into the tiling layout.",
    ),
    ActionPreset(
        id="pin",
        label="Pin to all workspaces",
        description="Window stays visible across workspace switches (floating only).",
    ),
    ActionPreset(
        id="center",
        label="Center on monitor",
        description="Center the window on its monitor (floating only).",
    ),
    ActionPreset(
        id="fullscreen",
        label="Fullscreen",
        description="Open the window fullscreen.",
    ),
    ActionPreset(
        id="maximize",
        label="Maximize",
        description="Open the window maximized.",
    ),
    ActionPreset(
        id="workspace",
        label="Open on workspace",
        description="Send the window to a specific workspace on spawn.",
        fields=(
            ActionField(
                label="Workspace",
                placeholder="1",
                hint=(
                    "Workspace id (e.g. 1) or name (e.g. name:work). "
                    "Append ' silent' to open without focusing."
                ),
            ),
        ),
    ),
    ActionPreset(
        id="monitor",
        label="Open on monitor",
        description="Send the window to a specific monitor on spawn.",
        fields=(
            ActionField(
                label="Monitor",
                placeholder="DP-1",
                hint="Monitor name (e.g. DP-1) or numeric index.",
            ),
        ),
    ),
    ActionPreset(
        id="size",
        label="Set size",
        description="Set the window's initial size (floating only).",
        fields=(
            ActionField(
                label="Width",
                placeholder="1280",
                kind="number",
                digits=0,
                max_value=16384,
                step=10,
                default="1280",
            ),
            ActionField(
                label="Height",
                placeholder="720",
                kind="number",
                digits=0,
                max_value=16384,
                step=10,
                default="720",
            ),
        ),
    ),
    ActionPreset(
        id="move",
        label="Set position",
        description="Set the window's initial position. Two space-separated expressions.",
        fields=(
            ActionField(
                label="X",
                placeholder="100",
                kind="number",
                digits=0,
                min_value=-16384,
                max_value=16384,
                step=10,
                default="100",
            ),
            ActionField(
                label="Y",
                placeholder="100",
                kind="number",
                digits=0,
                min_value=-16384,
                max_value=16384,
                step=10,
                default="100",
            ),
        ),
    ),
    ActionPreset(
        id="opacity",
        label="Set opacity",
        description=(
            "Override active and inactive opacity. Append ' override' to "
            "set absolutely (else multiplied)."
        ),
        fields=(
            ActionField(
                label="Active",
                kind="number",
                digits=2,
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                default="1.00",
            ),
            ActionField(
                label="Inactive",
                hint="Leave blank to use the active value for both.",
                kind="number",
                digits=2,
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                default="1.00",
            ),
        ),
    ),
    ActionPreset(
        id="rounding",
        label="Set corner rounding",
        description="Override the corner rounding (in pixels) for this window.",
        fields=(
            ActionField(
                label="Pixels",
                kind="number",
                digits=0,
                max_value=200,
                step=1,
                default="8",
            ),
        ),
    ),
    ActionPreset(
        id="opaque",
        label="Force opaque",
        description="Disable transparency for this window.",
    ),
    ActionPreset(
        id="no_blur",
        label="No blur",
        description="Disable background blur behind this window.",
    ),
    ActionPreset(
        id="no_shadow",
        label="No shadow",
        description="Disable the drop shadow for this window.",
    ),
    ActionPreset(
        id="no_anim",
        label="No animations",
        description="Disable open/close animations for this window.",
    ),
    ActionPreset(
        id="no_initial_focus",
        label="No initial focus",
        description="Don't focus the window when it spawns.",
    ),
    ActionPreset(
        id="no_focus",
        label="Never focusable",
        description="Hyprland will never focus this window (e.g. legacy XWayland helpers).",
    ),
    ActionPreset(
        id="stay_focused",
        label="Stay focused",
        description="Window keeps focus even when others would steal it.",
    ),
    ActionPreset(
        id="idle_inhibit",
        label="Inhibit idle",
        description="Prevent idle/screensaver while this window is around.",
        fields=(
            ActionField(
                label="Mode",
                placeholder="focus",
                hint="One of: none, always, focus, fullscreen.",
                default="focus",
            ),
        ),
    ),
    ActionPreset(
        id="suppress_event",
        label="Suppress event",
        description="Tell Hyprland to ignore a class of events from this window.",
        fields=(
            ActionField(
                label="Event",
                placeholder="activatefocus",
                hint=(
                    "Space-separated: activate, activatefocus, "
                    "fullscreen, maximize, fullscreenoutput."
                ),
            ),
        ),
    ),
)

ACTION_PRESETS_BY_ID: dict[str, ActionPreset] = {p.id: p for p in ACTION_PRESETS}

# Fall-through preset for plugin actions or anything not catalogued.
# The single field holds the full effect string verbatim, including the
# effect name and any args (e.g. ``plugin:foo:bar arg1``).
CUSTOM_PRESET: ActionPreset = ActionPreset(
    id="__custom__",
    label="Custom action…",
    description="Type any Hyprland action verbatim, including plugin actions.",
    fields=(
        ActionField(
            label="Action",
            placeholder="plugin:foo:bar arg1 arg2",
            hint="The full action string as it would appear before the comma.",
        ),
    ),
)


def lookup_preset(effect_name: str) -> ActionPreset:
    """Return the :class:`ActionPreset` matching *effect_name*, or Custom."""
    return ACTION_PRESETS_BY_ID.get(effect_name, CUSTOM_PRESET)


# ---------------------------------------------------------------------------
# Matcher catalog (for the UI's matcher-key dropdown)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatcherKind:
    """A v3 matcher key, with UI hints and value type."""

    key: str
    label: str
    description: str
    value_kind: Literal["regex", "bool", "text"] = "regex"
    placeholder: str = ""


MATCHER_KINDS: tuple[MatcherKind, ...] = (
    MatcherKind(
        key="class",
        label="Window class",
        description="Match by app class (e.g. firefox, kitty). Regex.",
        placeholder="^(firefox)$",
    ),
    MatcherKind(
        key="title",
        label="Window title",
        description="Match by window title text. Regex.",
        placeholder="^(.*Mozilla Firefox)$",
    ),
    MatcherKind(
        key="initial_class",
        label="Initial class",
        description="Class at spawn time, before the app sets a different one. Regex.",
        placeholder="^(firefox)$",
    ),
    MatcherKind(
        key="initial_title",
        label="Initial title",
        description="Title at spawn time, before the app updates it. Regex.",
        placeholder="^(Loading…)$",
    ),
    MatcherKind(
        key="xwayland",
        label="XWayland window",
        description="Window is running under XWayland.",
        value_kind="bool",
    ),
    MatcherKind(
        key="float",
        label="Floating",
        description="Window is currently floating. Re-evaluates dynamically.",
        value_kind="bool",
    ),
    MatcherKind(
        key="fullscreen",
        label="Fullscreen",
        description="Window is currently fullscreen. Re-evaluates dynamically.",
        value_kind="bool",
    ),
    MatcherKind(
        key="pin",
        label="Pinned",
        description="Window is pinned across workspaces. Re-evaluates dynamically.",
        value_kind="bool",
    ),
    MatcherKind(
        key="focus",
        label="Focused",
        description="Window currently has keyboard focus. Re-evaluates dynamically.",
        value_kind="bool",
    ),
    MatcherKind(
        key="modal",
        label="Modal",
        description="Window is a modal dialog (e.g. 'Are you sure?').",
        value_kind="bool",
    ),
    MatcherKind(
        key="workspace",
        label="On workspace",
        description="Window is on a specific workspace.",
        value_kind="text",
        placeholder="1",
    ),
    MatcherKind(
        key="tag",
        label="Tag",
        description="Window has a specific tag.",
        value_kind="text",
        placeholder="my-tag",
    ),
)

MATCHER_KINDS_BY_KEY: dict[str, MatcherKind] = {m.key: m for m in MATCHER_KINDS}

CUSTOM_MATCHER_KIND: MatcherKind = MatcherKind(
    key="__custom__",
    label="Custom matcher…",
    description="Any other Hyprland matcher key, including future additions.",
    value_kind="text",
    placeholder="match:key value",
)


def lookup_matcher_kind(key: str) -> MatcherKind:
    """Return the :class:`MatcherKind` for *key*, or Custom if unknown.

    ``RAW_KEY`` always falls through to Custom so unparseable tokens
    are editable as opaque text rather than raising in the UI.
    """
    if key == RAW_KEY:
        return CUSTOM_MATCHER_KIND
    return MATCHER_KINDS_BY_KEY.get(key, CUSTOM_MATCHER_KIND)


# ---------------------------------------------------------------------------
# Summaries (for row titles and pending-changes copy)
# ---------------------------------------------------------------------------


def summarize_matchers(matchers: list[Matcher]) -> str:
    """Plain-English summary of what windows the matchers target."""
    if not matchers:
        return "all windows"

    # Identity matchers users mentally key off: class > initial_class
    # > title > initial_title.
    priority = ("class", "initial_class", "title", "initial_title")
    chosen: Matcher | None = None
    for k in priority:
        for m in matchers:
            if m.key == k:
                chosen = m
                break
        if chosen is not None:
            break
    if chosen is None:
        chosen = matchers[0]

    if chosen.key == RAW_KEY:
        return chosen.value or "all windows"

    kind = lookup_matcher_kind(chosen.key)
    label = kind.label.lower()

    # Detect ``negative:`` regex prefix so the summary reads as
    # "not class: foo" instead of "class: negative:foo".
    value = chosen.value
    negated = False
    if kind.value_kind == "regex" and value.startswith("negative:"):
        negated = True
        value = value[len("negative:") :]

    if kind.value_kind == "bool":
        truthy = value.strip().lower() in {"1", "true", "yes", "on"}
        if truthy:
            return label
        return f"not {label}"

    return f"{'not ' if negated else ''}{label}: {value}"


def summarize_action(rule: WindowRule) -> str:
    """Friendly label for a rule's effect (e.g. ``Set opacity: 0.8 0.95``)."""
    preset = ACTION_PRESETS_BY_ID.get(rule.effect_name)
    if preset is None:
        full = rule.effect_full
        return full or "(no action)"
    args = rule.effect_args.strip()
    # Boolean presets don't surface their auto-``on`` in the title —
    # "Float window" reads cleaner than "Float window: on".
    if not args or args.lower() == "on":
        return preset.label
    return f"{preset.label}: {args}"


def summarize_rule(rule: WindowRule) -> tuple[str, str]:
    """Two-line ``(title, subtitle)`` summary for an ``Adw.ActionRow``."""
    return summarize_action(rule), summarize_matchers(rule.matchers)
