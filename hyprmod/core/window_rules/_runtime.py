"""Runtime matching and per-window dispatch for window rules.

Two responsibilities, both required by the live-apply path:

1. **Matching.** Given a :class:`WindowRule`, decide whether it would
   apply to (a) HyprMod's own window (gates the self-target confirm
   dialog) or (b) any specific live window (drives the retroactive
   dispatch when the user clicks Apply).
2. **Dispatch.** For each effect Hyprland's rule resolver computes at
   map time, return the equivalent ``hyprctl dispatch`` calls that
   bring an *already-mapped* window into the rule's target state —
   and the symmetric calls that revert that change when the rule is
   removed or discarded.

Hyprland resolves windowrules to per-window state at map time and
never re-evaluates them when a new rule arrives via IPC, so without
this module's dispatch helpers a freshly-pushed ``windowrule = …,
opacity 0.5`` would only affect *new* windows. The setprop calls we
emit here close that gap.

The matcher-evaluation logic is shared between :func:`matches_hyprmod`
and :func:`matches_window` via :func:`_evaluate_matcher` — the two
public functions differ only in (a) which fields they check against
and (b) what they default to on uncertainty:

- ``matches_hyprmod`` is **conservative on warn-the-user**: when we
  can't tell, return ``True`` so the user sees the confirm dialog.
- ``matches_window`` is **conservative on don't-disturb-the-window**:
  when we can't tell, return ``False`` so we skip mutating it.
"""

import re
from typing import TYPE_CHECKING, Protocol

from hyprmod.core.window_rules._model import (
    HYPRMOD_APP_ID,
    V3_BOOL_MATCHERS,
    Matcher,
    WindowRule,
)

if TYPE_CHECKING:
    from hyprland_socket import Window


# ---------------------------------------------------------------------------
# Matcher evaluation (shared by matches_hyprmod + matches_window)
# ---------------------------------------------------------------------------


class _MatcherTarget(Protocol):
    """Read-only view of the fields a matcher needs to evaluate against.

    Both :func:`matches_hyprmod` and :func:`matches_window` use this
    protocol so they can share :func:`_evaluate_matcher` despite
    targeting different sources (a hardcoded app id + the live window
    title vs. a full :class:`hyprland_socket.Window` snapshot).
    """

    @property
    def class_name(self) -> str: ...
    @property
    def initial_class(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def initial_title(self) -> str: ...
    def bool_state(self, key: str) -> bool | None: ...
    def workspace_match(self, value: str) -> bool: ...
    def tag_match(self, value: str) -> bool: ...


def _evaluate_matcher(
    matcher: Matcher,
    target: _MatcherTarget,
    *,
    on_unknown: bool,
) -> bool:
    """Decide whether *matcher* matches *target*'s current state.

    *on_unknown* is the value returned for matchers we can't introspect
    (custom plugin matchers, unknown keys, malformed regex). The two
    public callers pick different defaults — see the module docstring.
    """
    key = matcher.key
    value = matcher.value.strip()
    if not value:
        return False

    # v3 negation prefix: ``negative:foo`` matches everything *except*
    # what ``foo`` matches.
    negated = False
    if value.startswith("negative:"):
        negated = True
        value = value[len("negative:") :]
        if not value:
            return False

    def regex_against(haystack: str) -> bool:
        try:
            matched = bool(re.search(value, haystack))
        except re.error:
            # Malformed regex — Hyprland would reject the rule too,
            # so it can't disturb the target either way.
            return False
        return not matched if negated else matched

    if key == "class":
        return regex_against(target.class_name)
    if key == "initial_class":
        return regex_against(target.initial_class)
    if key == "title":
        return regex_against(target.title)
    if key == "initial_title":
        return regex_against(target.initial_title)

    if key in V3_BOOL_MATCHERS:
        truthy = value.lower() in {"1", "true", "yes", "on"}
        actual = target.bool_state(key)
        if actual is None:
            return on_unknown
        return (actual == truthy) ^ negated

    if key == "workspace":
        return target.workspace_match(value) ^ negated

    if key == "tag":
        return target.tag_match(value) ^ negated

    # Plugin matchers, RAW_KEY, anything else we don't introspect.
    return on_unknown


# ---------------------------------------------------------------------------
# Self-targeting detection (gates live-apply against the running editor)
# ---------------------------------------------------------------------------


class _HyprmodTarget:
    """Adapter that exposes HyprMod's own identity through :class:`_MatcherTarget`.

    Class-name and initial-class are HyprMod's app id; title comes
    from the live window when available. Boolean matchers HyprMod
    *can't* satisfy (``xwayland``, ``fullscreen``) return ``False``
    so a rule scoped to that flavour doesn't trigger a spurious
    warning; everything else returns ``None`` to mean "skip via the
    on_unknown default".

    ``workspace_match`` / ``tag_match`` exist to satisfy the protocol
    but are never called: :func:`matches_hyprmod` short-circuits
    those matcher keys before reaching :func:`_evaluate_matcher`.
    """

    __slots__ = ("_title",)

    def __init__(self, hyprmod_title: str) -> None:
        self._title = hyprmod_title

    @property
    def class_name(self) -> str:
        return HYPRMOD_APP_ID

    @property
    def initial_class(self) -> str:
        return HYPRMOD_APP_ID

    @property
    def title(self) -> str:
        return self._title

    @property
    def initial_title(self) -> str:
        # No reliable map-time title for ourselves; share the live one.
        return self._title

    def bool_state(self, key: str) -> bool | None:
        # HyprMod runs Wayland-native and isn't typically fullscreen,
        # so rules scoped to ``xwayland=true`` / ``fullscreen=true``
        # are guaranteed not to touch us. Other booleans (float, pin,
        # focus, group, modal) flip with state we don't query at
        # rule-edit time, so let the on_unknown default handle them.
        if key in ("xwayland", "fullscreen"):
            return False
        return None

    def workspace_match(self, value: str) -> bool:  # noqa: ARG002 — protocol shape
        return False  # unreachable — matches_hyprmod skips workspace matchers

    def tag_match(self, value: str) -> bool:  # noqa: ARG002 — protocol shape
        return False  # unreachable — matches_hyprmod skips tag matchers


def matches_hyprmod(rule: WindowRule, hyprmod_title: str = "") -> bool:
    """True if *rule* could plausibly match HyprMod's own window.

    Hyprland AND-combines a rule's matchers, so a rule applies only
    when *every* matcher matches the target window. We mirror that:
    return ``True`` only when every matcher might match HyprMod
    (with "might" being conservative on uncertainty — when we can't
    tell, we err on warning the user).

    *hyprmod_title* is the live title of the editor's own window, used
    for ``title`` / ``initial_title`` matchers. Passing the empty
    string (the default) makes title matchers conservative: they're
    treated as possibly-matching so the user gets a warning rather
    than a silent self-disturbance.
    """
    if not rule.matchers:
        # Hyprland rejects rules with no matchers, but be safe — a
        # zero-matcher rule logically applies to nothing rather than
        # everything.
        return False

    target = _HyprmodTarget(hyprmod_title)
    for matcher in rule.matchers:
        # Workspace and tag matchers we can't evaluate without live
        # state — route them to the conservative ``on_unknown=True``
        # default so the warning fires rather than skipping silently.
        if matcher.key in ("workspace", "tag"):
            continue
        if matcher.key in ("title", "initial_title") and not hyprmod_title:
            # Without a title we can't introspect — be conservative,
            # warn the user.
            continue
        if not _evaluate_matcher(matcher, target, on_unknown=True):
            return False
    return True


# ---------------------------------------------------------------------------
# Live-window matching (drives retroactive dispatch)
# ---------------------------------------------------------------------------


class _WindowTarget:
    """Adapter that exposes a :class:`hyprland_socket.Window` snapshot."""

    __slots__ = ("_window",)

    def __init__(self, window: "Window") -> None:
        self._window = window

    @property
    def class_name(self) -> str:
        return self._window.class_name

    @property
    def initial_class(self) -> str:
        return self._window.initial_class

    @property
    def title(self) -> str:
        return self._window.title

    @property
    def initial_title(self) -> str:
        return self._window.initial_title

    def bool_state(self, key: str) -> bool | None:
        if key == "xwayland":
            return self._window.xwayland
        if key == "float":
            return self._window.floating
        if key == "fullscreen":
            return self._window.fullscreen != 0
        if key == "pin":
            return self._window.pinned
        if key == "group":
            return bool(self._window.grouped)
        # focus and modal aren't on the snapshot.
        return None

    def workspace_match(self, value: str) -> bool:
        if value.startswith("name:"):
            return self._window.workspace_name == value[len("name:") :]
        return str(self._window.workspace_id) == value or self._window.workspace_name == value

    def tag_match(self, value: str) -> bool:
        return value in self._window.tags


def matches_window(rule: WindowRule, window: "Window") -> bool:
    """True if *rule*'s matchers all match *window*'s current state.

    Mirrors Hyprland's AND-combine semantics across the matchers we
    can evaluate from a Window snapshot. A rule with zero matchers
    returns ``False`` — Hyprland would reject it anyway, and we don't
    want a half-built rule to dispatch against every running window.

    Conservative the *opposite* direction from :func:`matches_hyprmod`:
    when we can't tell whether a matcher applies, we return ``False``
    rather than warn — better to skip a window we can't evaluate
    than mutate it incorrectly.
    """
    if not rule.matchers:
        return False
    target = _WindowTarget(window)
    return all(_evaluate_matcher(m, target, on_unknown=False) for m in rule.matchers)


# ---------------------------------------------------------------------------
# Per-window dispatch (apply / revert)
# ---------------------------------------------------------------------------


# Bool/scalar/string effects whose v3 name is *also* a valid setprop in
# Hyprland 0.54+. Args pass through verbatim — Hyprland's
# ``parsePropTrivial`` accepts the same ``on``/``off``/``true``/``false``
# set we emit for bool effects, the same numeric strings for ints/floats,
# and animation-name strings for ``animation``.
#
# Inverted-direction cases (``persistent_size`` ↔ ``nopersistentsize``,
# ``nearest_neighbor`` ↔ ``nonearestneighbor``) are deliberately
# omitted: re-emitting them as ``setprop noprop 0`` is correct only
# when the window was previously locked to ``noprop 1``, which we
# can't reliably determine from a snapshot.
SETPROP_PASSTHROUGH_EFFECTS: frozenset[str] = frozenset(
    {
        # Bools.
        "allows_input", "decorate", "focus_on_activate",
        "keep_aspect_ratio", "nearest_neighbor",
        "no_anim", "no_blur", "no_dim", "no_focus", "no_max_size",
        "no_shadow", "no_shortcuts_inhibit", "no_follow_mouse",
        "no_screen_share", "no_vrr",
        "dim_around", "opaque", "force_rgbx", "sync_fullscreen",
        "immediate", "xray", "render_unfocused", "persistent_size",
        "stay_focused",
        # Strings / scalars.
        "idle_inhibit", "animation", "scroll_mouse", "scroll_touchpad",
        # Ints/floats.
        "border_size", "rounding", "rounding_power",
    }
)  # fmt: skip


# Effect names whose at-spawn / per-frame behaviour we replicate on
# existing windows via :func:`existing_window_dispatchers`. Used as a
# fast-path predicate so effects without a runtime mutation (e.g.
# ``no_initial_focus``, ``center``, ``suppress_event``, plugin
# effects) skip the IPC ``get_windows`` round-trip.
RETROACTIVE_EFFECTS: frozenset[str] = frozenset(
    {
        # Static effects with mutating dispatchers.
        "float", "tile", "pin",
        "fullscreen", "maximize",
        "workspace", "monitor",
        "size", "move",
        # Dynamic effects with multi-value setprop translations.
        "opacity", "border_color",
        # Dynamic effects whose v3 name is the setprop name verbatim.
        *SETPROP_PASSTHROUGH_EFFECTS,
    }
)  # fmt: skip


def _setprop(window: "Window", prop: str, value: str) -> tuple[str, str]:
    """Build a ``setprop`` dispatcher tuple for *window*.

    Hyprland 0.54+ overrides setprop values at ``PRIORITY_SET_PROP``
    internally, so they persist without a ``lock`` flag — and 0.54
    actively ignores the flag (its ``CVarList`` parser stops at the
    third token). The next config reload re-resolves rules from the
    document and clears the override, so save+reload still produces
    the canonical state.
    """
    return ("setprop", f"address:{window.address} {prop} {value}")


def existing_window_dispatchers(rule: WindowRule, window: "Window") -> list[tuple[str, str]]:
    """Dispatchers that retroactively apply *rule*'s effect to *window*.

    Returns ``[(dispatcher, arg), …]`` for any effect we can mirror
    on an already-mapped window:

    - **Static effects** (``float``, ``size``, ``workspace``, …) →
      mutating dispatchers (``togglefloating``, ``resizewindowpixel``,
      ``movetoworkspacesilent``, …). All gated by current state where
      a toggle would otherwise undo itself.
    - **Dynamic effects** (``opacity``, ``no_blur``, ``rounding``,
      ``border_color``, …) → ``setprop``. Hyprland resolves dynamic
      windowrules to per-window state at map time, so a fresh
      ``keyword windowrule = …, opacity 0.5`` doesn't update existing
      windows on its own — we need the explicit ``setprop`` to mutate
      each match. ``setprop`` overrides at ``PRIORITY_SET_PROP`` in
      0.54+, so the value persists without the legacy ``lock`` flag
      (which 0.54's ``CVarList`` parser ignores).

    Returns an empty list for:

    - **Effects with no useful retroactive operation** (``center``,
      ``no_initial_focus``).
    - **Idempotent cases**: e.g. ``float`` on an already-floating
      window — skipping avoids un-floating it via toggle semantics.

    All dispatchers used here support ``address:0x…`` targeting so we
    can mutate one window without disturbing focus.
    """
    addr = f"address:{window.address}"
    name = rule.effect_name
    args = rule.effect_args.strip()

    # ── Static effects: mutating dispatchers ──────────────────────

    if name == "float":
        # ``togglefloating`` is the universally-supported dispatcher;
        # gate by current state so we don't accidentally un-float.
        return [] if window.floating else [("togglefloating", addr)]

    if name == "tile":
        return [("togglefloating", addr)] if window.floating else []

    if name == "pin":
        # ``pin`` is a toggle; only fire when the window isn't already pinned.
        return [] if window.pinned else [("pin", addr)]

    if name == "fullscreen":
        # ``fullscreenstate <internal> <client>``: 2 = fullscreen, -1 = no-op.
        # Skip if already fullscreen (not just maximized).
        if window.fullscreen == 2:
            return []
        return [("fullscreenstate", f"2 -1,{addr}")]

    if name == "maximize":
        if window.fullscreen == 1:
            return []
        return [("fullscreenstate", f"1 -1,{addr}")]

    if name == "workspace":
        # Rule arg looks like ``2``, ``name:work``, or ``2 silent``.
        # Use the silent variant unconditionally — at-spawn semantics
        # are "don't steal focus", which matches the rule's intent.
        first = args.split()[0] if args else ""
        if not first:
            return []
        return [("movetoworkspacesilent", f"{first},{addr}")]

    if name == "monitor":
        if not args:
            return []
        # ``movewindow mon:NAME`` carries the window to a monitor.
        return [("movewindow", f"mon:{args},{addr}")]

    if name == "size":
        parts = args.split()
        if len(parts) < 2:
            return []
        w, h = parts[0], parts[1]
        return [("resizewindowpixel", f"exact {w} {h},{addr}")]

    if name == "move":
        parts = args.split()
        if len(parts) < 2:
            return []
        x, y = parts[0], parts[1]
        return [("movewindowpixel", f"exact {x} {y},{addr}")]

    # ── Dynamic effects: setprop ──────────────────────────────────

    if name == "opacity":
        # Hyprland's ``setprop opacity`` (and the ``_inactive`` /
        # ``_fullscreen`` variants) each take a single float, so a
        # rule with multiple values has to fan out into one setprop
        # per slot. ``override`` keyword is dropped — there's a
        # separate ``opacity_override`` setprop pair for that, which
        # we don't surface today.
        parts = [p for p in args.split() if p.lower() != "override"]
        if not parts:
            return []
        active = parts[0]
        inactive = parts[1] if len(parts) > 1 else parts[0]
        result = [
            _setprop(window, "opacity", active),
            _setprop(window, "opacity_inactive", inactive),
        ]
        if len(parts) > 2:
            result.append(_setprop(window, "opacity_fullscreen", parts[2]))
        return result

    if name == "border_color":
        # The ``border_color`` rule sets both active and inactive
        # gradients to the same value. Hyprland exposes these as two
        # separate setprops; we emit both so the visible state matches
        # the rule's behaviour on a freshly-mapped window.
        if not args:
            return []
        return [
            _setprop(window, "active_border_color", args),
            _setprop(window, "inactive_border_color", args),
        ]

    if name in SETPROP_PASSTHROUGH_EFFECTS and args:
        return [_setprop(window, name, args)]

    # ``center`` only acts on the focused window (no address-target
    # variant). ``no_initial_focus``, ``suppress_event``, ``tag``,
    # ``min_size``/``max_size`` (expression-parsed, awkward to encode
    # without a parser of our own), and any plugin effect drop
    # through silently — the keyword push has registered them for new
    # windows.
    return []


def existing_window_revert_dispatchers(rule: WindowRule, window: "Window") -> list[tuple[str, str]]:
    """Dispatchers that revert *rule*'s runtime effect on *window*.

    Symmetric to :func:`existing_window_dispatchers`. The behaviour
    splits by which Hyprland 0.54.3 setprop branch the property uses:

    - Properties that flow through ``parsePropTrivial`` (every bool
      effect, plus ``rounding``, ``border_size``, ``rounding_power``,
      ``animation``, ``idle_inhibit``, …) accept the literal value
      ``"unset"`` to clear the ``PRIORITY_SET_PROP`` override and let
      the rule resolver's map-time result take over.
    - ``opacity`` / ``opacity_inactive`` / ``opacity_fullscreen``
      *don't* go through ``parsePropTrivial`` — they call
      ``std::stof(VAL)`` directly, which throws on ``"unset"`` and
      surfaces ``"Error parsing prop value: stof"``. As a fallback we
      send ``1.0`` (Hyprland's compositor default), which is correct
      for the common single-rule case. If the user has another saved
      rule of the same effect that should still apply, the page's
      diff-based runtime sync re-pushes it after this revert.
    - Static effects (``float``, ``size``, ``workspace``, …) mutate
      the window's actual layout state, and reverting safely requires
      per-rule per-window history we don't track. They no-op here;
      the escape hatch is save+reload.
    """
    name = rule.effect_name
    addr = f"address:{window.address}"

    if name == "opacity":
        # Mirror the *count* of setprops the apply path emitted —
        # we don't want to lock ``opacity_fullscreen`` to 1.0 if
        # the rule never set it. Apply emits 2 setprops for 1–2 args
        # and 3 for 3 args (the ``override`` keyword is dropped).
        parts = [p for p in rule.effect_args.split() if p.lower() != "override"]
        if not parts:
            return []
        result = [
            ("setprop", f"{addr} opacity 1.0"),
            ("setprop", f"{addr} opacity_inactive 1.0"),
        ]
        if len(parts) > 2:
            result.append(("setprop", f"{addr} opacity_fullscreen 1.0"))
        return result

    if name == "border_color":
        # Hyprland 0.54.3 *does* accept ``unset`` here (the
        # ``configStringToInt`` path silently no-ops on a non-color
        # token, leaving the gradient empty), but the resulting state
        # is "no override at SET_PROP" which is exactly what we want.
        return [
            ("setprop", f"{addr} active_border_color unset"),
            ("setprop", f"{addr} inactive_border_color unset"),
        ]

    if name in SETPROP_PASSTHROUGH_EFFECTS:
        return [("setprop", f"{addr} {name} unset")]

    return []
