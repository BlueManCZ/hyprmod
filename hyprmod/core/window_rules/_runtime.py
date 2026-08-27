"""Matcher evaluation + per-window dispatch wrappers for window rules.

Two responsibilities:

1. **Matching.** Given a :class:`WindowRule`, decide whether it would
   apply to (a) HyprMod's own window (gates the self-target confirm
   dialog) or (b) any specific live window (drives the retroactive
   dispatch when the user clicks Apply). This is hyprmod's domain
   because it depends on hyprmod's :class:`WindowRule` data shape.
2. **Dispatch.** Thin wrappers around
   :func:`hyprland_state.dispatchers_for_effect` /
   :func:`revert_dispatchers_for_effect` that adapt the :class:`WindowRule`
   data shape to the library's ``(name, args, window)`` interface.
   The compositor-version-specific knowledge (which effect maps to
   which dispatcher, when ``setprop unset`` works) lives in
   ``hyprland-state``.

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

from hyprland_config import V3_BOOL_MATCHERS
from hyprland_state import (
    RETROACTIVE_EFFECTS,
    SETPROP_PASSTHROUGH_EFFECTS,
    STATIC_RETROACTIVE_EFFECTS,
    dispatchers_for_effect,
    revert_dispatchers_for_effect,
)

from hyprmod.core.window_rules._model import (
    HYPRMOD_APP_ID,
    Matcher,
    WindowRule,
)

if TYPE_CHECKING:
    from hyprland_socket import Window


__all__ = [
    "RETROACTIVE_EFFECTS",
    "SETPROP_PASSTHROUGH_EFFECTS",
    "STATIC_RETROACTIVE_EFFECTS",
    "existing_window_dispatchers",
    "existing_window_revert_dispatchers",
    "matches_hyprmod",
    "matches_window",
]


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
# Per-window dispatch (wrappers around hyprland-state)
# ---------------------------------------------------------------------------


def existing_window_dispatchers(
    rule: WindowRule, window: "Window", *, compositor_reapplies_dynamic: bool = False
) -> list[tuple[str, str]]:
    """Dispatchers that retroactively apply *rule*'s effects to *window*.

    Adapts the page-level :class:`WindowRule` data shape to the
    library's effect-string-based interface. See
    :func:`hyprland_state.dispatchers_for_effect` for the full
    per-effect behaviour, *compositor_reapplies_dynamic* included, and
    the rationale behind the dispatcher choices. Multi-effect rules
    (block-form / named bundles) emit dispatchers in declaration order
    so the visual result matches the user's authored sequence.
    """
    result: list[tuple[str, str]] = []
    for effect in rule.effects:
        result.extend(
            dispatchers_for_effect(
                effect.name,
                effect.args,
                window,
                compositor_reapplies_dynamic=compositor_reapplies_dynamic,
            )
        )
    return result


def existing_window_revert_dispatchers(
    rule: WindowRule, window: "Window", *, compositor_reapplies_dynamic: bool = False
) -> list[tuple[str, str]]:
    """Dispatchers that revert *rule*'s runtime effects on *window*.

    Adapter around :func:`hyprland_state.revert_dispatchers_for_effect`;
    symmetric to :func:`existing_window_dispatchers`, iterating each
    effect in the rule so multi-effect bundles fully revert.
    """
    result: list[tuple[str, str]] = []
    for effect in rule.effects:
        result.extend(
            revert_dispatchers_for_effect(
                effect.name,
                effect.args,
                window,
                compositor_reapplies_dynamic=compositor_reapplies_dynamic,
            )
        )
    return result
