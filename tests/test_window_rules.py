"""Tests for v3 window-rule parsing, serialization, and change tracking.

Hyprland 0.53+ uses a different syntax than v2:

- Format: ``windowrule = match:KEY VALUE, EFFECT VALUE``
- Boolean effects need ``on``: ``float on`` (not just ``float``)
- Match keys carry a ``match:`` prefix and a space (not ``key:value``)
- Effect names are snake_case: ``no_blur``, ``stay_focused``, etc.
- Bool matchers renamed: ``floating`` → ``float``, ``pinned`` → ``pin``

The v2 → v3 line rewrite (and the ``hyprland-config<0.4.4`` corruption
recovery) live in ``hyprland_config._migrate`` and are unit-tested
there. This file's tests cover the v3 round-trip path plus an
*integration* test that hyprmod's read pipeline routes legacy v2
input through ``hyprland_config.migrate()`` before the parser sees it.
"""

import dataclasses
import re
from pathlib import Path

import pytest
from hyprland_socket import Window

from hyprmod.core.window_rules import (
    ACTION_PRESETS_BY_ID,
    CUSTOM_PRESET,
    HYPRMOD_APP_ID,
    KEYWORD_WRITE,
    RAW_KEY,
    RETROACTIVE_EFFECTS,
    V3_BOOL_EFFECTS,
    ExternalWindowRule,
    Matcher,
    WindowRule,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
    existing_window_dispatchers,
    existing_window_revert_dispatchers,
    iter_item_changes,
    load_external_window_rules,
    lookup_matcher_kind,
    lookup_preset,
    matches_hyprmod,
    matches_window,
    parse_window_rule_line,
    parse_window_rule_lines,
    serialize,
    summarize_action,
    summarize_matchers,
    summarize_rule,
)

# ---------------------------------------------------------------------------
# v3 single-line parser
# ---------------------------------------------------------------------------


class TestParseV3:
    def test_basic_float(self):
        rule = parse_window_rule_line("windowrule = match:class ^(firefox)$, float on")
        assert rule is not None
        assert rule.effect_name == "float"
        assert rule.effect_args == "on"
        assert rule.matchers == [Matcher(key="class", value="^(firefox)$")]

    def test_match_first_or_last_both_work(self):
        a = parse_window_rule_line("windowrule = match:class ^(firefox)$, float on")
        b = parse_window_rule_line("windowrule = float on, match:class ^(firefox)$")
        assert a is not None and b is not None
        # Same logical content; order in output is normalised to
        # matchers-first by ``to_line``.
        assert a.matchers == b.matchers
        assert (a.effect_name, a.effect_args) == (b.effect_name, b.effect_args)

    def test_multi_arg_effect(self):
        rule = parse_window_rule_line("windowrule = match:class ^(steam)$, size 1920 1080")
        assert rule is not None
        assert rule.effect_name == "size"
        assert rule.effect_args == "1920 1080"

    def test_opacity_three_args(self):
        rule = parse_window_rule_line("windowrule = match:class ^(kitty)$, opacity 1.0 0.5 0.8")
        assert rule is not None
        assert rule.effect_args == "1.0 0.5 0.8"

    def test_multi_matcher(self):
        rule = parse_window_rule_line(
            "windowrule = match:class ^(kitty)$, match:title ^(scratch)$, no_blur on"
        )
        assert rule is not None
        assert len(rule.matchers) == 2
        assert rule.matchers[0].key == "class"
        assert rule.matchers[1].key == "title"

    def test_expression_with_parens_not_split_on_inner_comma(self):
        # ``move`` can take expressions with commas inside parens.
        # Top-level split must respect paren depth.
        rule = parse_window_rule_line(
            "windowrule = match:class ^(kitty)$, move (cursor_x-(window_w*0.5)) 100"
        )
        assert rule is not None
        assert rule.effect_name == "move"
        assert rule.effect_args == "(cursor_x-(window_w*0.5)) 100"

    def test_no_effect_returns_none(self):
        # A rule with only matchers and no effect is meaningless and
        # rejected by Hyprland; we return None so the page silently
        # drops it instead of carrying a half-broken row.
        assert parse_window_rule_line("windowrule = match:class ^(foo)$") is None

    def test_unknown_keyword_returns_none(self):
        assert parse_window_rule_line("bind = SUPER, T, exec, kitty") is None
        assert parse_window_rule_line("layerrule = blur on, namespace:waybar") is None

    def test_missing_equals_returns_none(self):
        assert parse_window_rule_line("windowrule match:class ^foo$, float on") is None


# ---------------------------------------------------------------------------
# v2 → v3 read pipeline (integration)
# ---------------------------------------------------------------------------
#
# The v2 → v3 line rewrite and the hyprland-config<0.4.4 corruption
# recovery are unit-tested in hyprland_config's own test suite. The
# tests below verify the *integration*: hyprmod's read paths invoke
# ``hyprland_config.migrate()`` before the parser sees the lines, so
# legacy ``windowrulev2`` input ends up as v3 ``WindowRule`` instances
# with the correct content.


class TestReadPathMigratesV2:
    def test_read_all_sections_rewrites_v2_to_v3(self, tmp_path, monkeypatch):
        from hyprmod.core import config as core_config

        conf = tmp_path / "hyprland-gui.conf"
        conf.write_text(
            "windowrulev2 = float, class:^(firefox)$\n"
            "windowrulev2 = noblur, initialClass:^(kitty)$\n"
        )
        monkeypatch.setattr(core_config, "_DEFAULT_GUI_CONF", conf)

        _, sections = core_config.read_all_sections()
        # All entries land under the v3 keyword after migration; the
        # v2 keyword should be empty (or absent) in the collected map.
        assert not sections.get("windowrulev2")
        v3_lines = sections.get("windowrule", [])
        assert any("match:class ^(firefox)$" in ln and "float on" in ln for ln in v3_lines)
        assert any("match:initial_class ^(kitty)$" in ln and "no_blur on" in ln for ln in v3_lines)

    def test_read_all_sections_recovers_corrupted_v3_in_v2_packaging(self, tmp_path, monkeypatch):
        # The hyprland-config<0.4.4 corruption pattern: a v3 body
        # (``match:`` token present) wrongly wrapped as ``windowrulev2``
        # with a stray ``title:`` glued onto the effect. Migration
        # detects the v3 marker and strips the bogus prefix.
        from hyprmod.core import config as core_config

        conf = tmp_path / "hyprland-gui.conf"
        conf.write_text(r"windowrulev2 = match:class ^(foo)$, title:float on" + "\n")
        monkeypatch.setattr(core_config, "_DEFAULT_GUI_CONF", conf)

        _, sections = core_config.read_all_sections()
        v3_lines = sections.get("windowrule", [])
        assert v3_lines, "expected migrated v3 line"
        assert "title:float" not in v3_lines[0]
        assert "match:class ^(foo)$" in v3_lines[0]
        assert "float on" in v3_lines[0]

    def test_load_external_window_rules_migrates_v2(self, tmp_path):
        # The external-rules loader runs migration too, so v2 lines
        # in a sourced file surface as v3 WindowRule instances in the
        # read-only display.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("windowrulev2 = noblur, class:^(legacy)$\n")
        managed.write_text("")
        external = load_external_window_rules(root, managed)
        assert len(external) == 1
        rule = external[0].rule
        # Effect renamed: ``noblur`` → ``no_blur``; matcher key
        # rewritten with ``match:`` prefix; bool effect gained ``on``.
        assert rule.effect_name == "no_blur"
        assert rule.effect_args == "on"
        assert any(m.key == "class" and m.value == "^(legacy)$" for m in rule.matchers)


# ---------------------------------------------------------------------------
# v3 round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_basic_v3_round_trip(self):
        # Already-v3 lines round-trip byte-for-byte (with matcher-first
        # normalisation).
        original = "windowrule = match:class ^(firefox)$, float on"
        rule = parse_window_rule_line(original)
        assert rule is not None
        assert rule.to_line() == original

    def test_serialize_list(self):
        rules = [
            WindowRule(
                matchers=[Matcher(key="class", value="^(firefox)$")],
                effect_name="float",
                effect_args="on",
            ),
            WindowRule(
                matchers=[Matcher(key="class", value="^(pavucontrol)$")],
                effect_name="pin",
                effect_args="on",
            ),
        ]
        assert serialize(rules) == [
            "windowrule = match:class ^(firefox)$, float on",
            "windowrule = match:class ^(pavucontrol)$, pin on",
        ]

    def test_bool_effect_auto_adds_on(self):
        # If a WindowRule is built with empty effect_args for a bool
        # effect, ``effect_full`` (and therefore ``to_line``) auto-
        # appends ``on`` so we never write a syntax error.
        rule = WindowRule(
            matchers=[Matcher(key="class", value="^(kitty)$")],
            effect_name="float",
            effect_args="",
        )
        assert rule.to_line().endswith(", float on")

    def test_non_bool_effect_no_auto_on(self):
        # Non-bool effects (size, opacity, …) keep their explicit args
        # and don't gain a spurious ``on``.
        rule = WindowRule(
            matchers=[Matcher(key="class", value="^(kitty)$")],
            effect_name="opacity",
            effect_args="0.8 0.95",
        )
        assert rule.to_line().endswith(", opacity 0.8 0.95")

    def test_multi_effect_v3_splits_into_separate_rules(self):
        # ``windowrule = match:class kitty, opacity 0.8, no_blur on``
        # has two effects on one line — Hyprland-valid, but we model
        # one effect per rule. The list parser splits these so the
        # round-trip preserves both effects.
        out = parse_window_rule_lines(
            ["windowrule = match:class ^(kitty)$, opacity 0.8 0.8, no_blur on"]
        )
        assert len(out) == 2
        assert out[0].effect_name == "opacity"
        assert out[1].effect_name == "no_blur"
        # Same matchers on both.
        assert out[0].matchers == out[1].matchers


# ---------------------------------------------------------------------------
# Action / matcher catalog lookups
# ---------------------------------------------------------------------------


class TestActionLookup:
    def test_known_effect_resolves(self):
        preset = lookup_preset("float")
        assert preset.id == "float"
        assert preset.label == "Float window"

    def test_unknown_effect_falls_to_custom(self):
        preset = lookup_preset("plugin:foo:bar")
        assert preset is CUSTOM_PRESET

    def test_action_preset_format_drops_trailing_empties(self):
        preset = ACTION_PRESETS_BY_ID["opacity"]
        assert preset.format(["0.8", ""]) == "0.8"

    def test_action_preset_format_two_args(self):
        preset = ACTION_PRESETS_BY_ID["size"]
        assert preset.format(["1920", "1080"]) == "1920 1080"

    def test_action_preset_parse_args_pads(self):
        preset = ACTION_PRESETS_BY_ID["opacity"]
        args = preset.parse_args("0.8")
        assert args == ["0.8", ""]


class TestMatcherKindLookup:
    def test_known_matcher(self):
        kind = lookup_matcher_kind("class")
        assert kind.key == "class"
        assert kind.value_kind == "regex"

    def test_bool_matcher(self):
        # ``float`` (the v3 matcher key, not the v3 effect) is bool.
        kind = lookup_matcher_kind("float")
        assert kind.value_kind == "bool"

    def test_unknown_matcher_falls_to_custom(self):
        kind = lookup_matcher_kind("plugin:custommatch")
        assert kind.key == "__custom__"

    def test_raw_key_falls_to_custom(self):
        kind = lookup_matcher_kind(RAW_KEY)
        assert kind.key == "__custom__"


# ---------------------------------------------------------------------------
# Boolean effect catalog
# ---------------------------------------------------------------------------


class TestBoolEffectCatalog:
    def test_common_bool_effects_listed(self):
        # Spot-check that the bool set covers the key effects so the
        # auto-``on`` path doesn't miss any of them.
        for name in ["float", "tile", "pin", "no_blur", "no_shadow", "stay_focused"]:
            assert name in V3_BOOL_EFFECTS

    def test_non_bool_effects_excluded(self):
        for name in ["opacity", "size", "move", "workspace", "monitor", "rounding"]:
            assert name not in V3_BOOL_EFFECTS


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


class TestSummaries:
    def test_summarize_class_matcher(self):
        s = summarize_matchers([Matcher(key="class", value="^(firefox)$")])
        assert "class" in s.lower()
        assert "firefox" in s

    def test_summarize_negative_prefix(self):
        # v3 negation lives in the value as ``negative:foo``. Summary
        # strips it for readability.
        s = summarize_matchers([Matcher(key="class", value="negative:^(firefox)$")])
        assert "not" in s
        # The ``negative:`` prefix is hidden in the summary.
        assert "negative:" not in s

    def test_summarize_prefers_class_over_title(self):
        s = summarize_matchers(
            [
                Matcher(key="title", value="^(.*foo)$"),
                Matcher(key="class", value="^(bar)$"),
            ]
        )
        assert "bar" in s
        assert "title" not in s.lower()

    def test_summarize_bool_matcher_true(self):
        s = summarize_matchers([Matcher(key="float", value="true")])
        # "Floating" — without "not" prefix.
        assert "not" not in s.lower()
        assert "floating" in s.lower()

    def test_summarize_bool_matcher_false(self):
        s = summarize_matchers([Matcher(key="float", value="false")])
        assert "not" in s

    def test_summarize_action_unary(self):
        rule = WindowRule(
            matchers=[Matcher(key="class", value="^(firefox)$")],
            effect_name="float",
            effect_args="on",
        )
        # Boolean presets don't surface ``on`` in the summary —
        # "Float window" reads cleaner than "Float window: on".
        assert summarize_action(rule) == "Float window"

    def test_summarize_action_with_args(self):
        rule = WindowRule(
            matchers=[Matcher(key="class", value="^(kitty)$")],
            effect_name="opacity",
            effect_args="0.8 0.95",
        )
        s = summarize_action(rule)
        assert "0.8" in s

    def test_summarize_action_custom(self):
        rule = WindowRule(
            matchers=[Matcher(key="class", value="^(foo)$")],
            effect_name="plugin:foo:bar",
            effect_args="baz",
        )
        s = summarize_action(rule)
        assert "plugin:foo:bar" in s

    def test_summarize_rule_returns_pair(self):
        rule = WindowRule(
            matchers=[Matcher(key="class", value="^(firefox)$")],
            effect_name="float",
            effect_args="on",
        )
        title, subtitle = summarize_rule(rule)
        assert title == "Float window"
        assert "firefox" in subtitle


# ---------------------------------------------------------------------------
# Drag-target index translation
# ---------------------------------------------------------------------------


class TestDropTargetIdx:
    def test_drop_above_when_src_before_hover(self):
        assert drop_target_idx(0, 2, before=True) == 1

    def test_drop_below_when_src_before_hover(self):
        assert drop_target_idx(0, 2, before=False) == 2

    def test_drop_above_when_src_after_hover(self):
        assert drop_target_idx(3, 1, before=True) == 1

    def test_drop_below_when_src_after_hover(self):
        assert drop_target_idx(3, 1, before=False) == 2


# ---------------------------------------------------------------------------
# Reorder + change tracking
# ---------------------------------------------------------------------------


def _rule(effect: str, cls: str = "x", args: str = "on") -> WindowRule:
    return WindowRule(
        matchers=[Matcher(key="class", value=cls)],
        effect_name=effect,
        effect_args=args,
    )


class TestDetectReorder:
    def test_no_reorder_on_identical_lists(self):
        a = [_rule("float"), _rule("pin"), _rule("center")]
        b = [_rule("float"), _rule("pin"), _rule("center")]
        assert detect_reorder(a, b) is False

    def test_detects_swap(self):
        a = [_rule("float"), _rule("pin")]
        b = [_rule("pin"), _rule("float")]
        assert detect_reorder(a, b) is True

    def test_ignores_pure_add(self):
        a = [_rule("float")]
        b = [_rule("float"), _rule("pin")]
        assert detect_reorder(a, b) is False

    def test_single_common_item_is_not_reorder(self):
        a = [_rule("float")]
        b = [_rule("pin"), _rule("float"), _rule("center")]
        assert detect_reorder(a, b) is False


class TestIterItemChanges:
    def test_added(self):
        saved = [_rule("float")]
        current = [_rule("float"), _rule("pin")]
        baselines: list[WindowRule | None] = [saved[0], None]
        kinds = [k for k, *_ in iter_item_changes(saved, current, baselines)]
        assert kinds == ["added"]

    def test_modified(self):
        saved = [_rule("float", "x")]
        current = [_rule("pin", "x")]
        baselines: list[WindowRule | None] = [saved[0]]
        changes = list(iter_item_changes(saved, current, baselines))
        assert len(changes) == 1
        assert changes[0][0] == "modified"

    def test_removed(self):
        saved = [_rule("float"), _rule("pin")]
        current = [saved[0]]
        baselines: list[WindowRule | None] = [saved[0]]
        kinds = [k for k, *_ in iter_item_changes(saved, current, baselines)]
        assert kinds == ["removed"]

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            list(iter_item_changes([], [_rule("float")], []))


class TestCountPendingChanges:
    def test_clean_state_returns_zero(self):
        saved = [_rule("float")]
        current = list(saved)
        baselines: list[WindowRule | None] = list(saved)
        assert count_pending_changes(saved, current, baselines) == 0

    def test_add_modify_remove_summed(self):
        saved = [_rule("float", "a"), _rule("pin", "b")]
        current = [_rule("center", "a"), _rule("no_blur", "c")]
        baselines: list[WindowRule | None] = [saved[0], None]
        # 1 modified + 1 added + 1 removed = 3
        assert count_pending_changes(saved, current, baselines) == 3

    def test_reorder_adds_one(self):
        saved = [_rule("float", "a"), _rule("pin", "b")]
        current = [saved[1], saved[0]]
        baselines: list[WindowRule | None] = [saved[1], saved[0]]
        # 0 per-item changes + 1 reorder roll-up.
        assert count_pending_changes(saved, current, baselines) == 1


# ---------------------------------------------------------------------------
# Smoke test: KEYWORD_WRITE is the v3 keyword
# ---------------------------------------------------------------------------


def test_keyword_write_is_v3():
    # Hyprland 0.53+ rejects ``windowrulev2``. Anything we write must
    # use the v3 keyword.
    assert KEYWORD_WRITE == "windowrule"


# ---------------------------------------------------------------------------
# Self-targeting detection (matches_hyprmod)
# ---------------------------------------------------------------------------


def _rule_with_matchers(*matchers: Matcher) -> WindowRule:
    """Build a WindowRule with given matchers and a placeholder effect.

    The effect doesn't matter for ``matches_hyprmod`` checks (it only
    looks at matchers); ``float on`` is the simplest valid choice.
    """
    return WindowRule(matchers=list(matchers), effect_name="float", effect_args="on")


class TestMatchesHyprmod:
    def test_exact_class_anchored_matches(self):
        rule = _rule_with_matchers(Matcher("class", f"^({re.escape(HYPRMOD_APP_ID)})$"))
        assert matches_hyprmod(rule)

    def test_unrelated_class_does_not_match(self):
        rule = _rule_with_matchers(Matcher("class", "^(firefox)$"))
        assert not matches_hyprmod(rule)

    def test_wildcard_class_matches(self):
        # ``.*`` is the canonical "everything" pattern; it must trigger
        # the warning because Hyprland would apply the rule to HyprMod.
        rule = _rule_with_matchers(Matcher("class", ".*"))
        assert matches_hyprmod(rule)

    def test_substring_class_matches(self):
        # Hyprland's regex matcher uses search semantics, not full match
        # — a partial pattern inside the app id should still trigger.
        rule = _rule_with_matchers(Matcher("class", "hyprmod"))
        assert matches_hyprmod(rule)

    def test_initial_class_matches(self):
        rule = _rule_with_matchers(Matcher("initial_class", f"^({re.escape(HYPRMOD_APP_ID)})$"))
        assert matches_hyprmod(rule)

    def test_negative_prefix_inverts_match(self):
        # ``negative:`` flips the matcher's truth value; a regex that
        # would have matched HyprMod must now exclude it.
        rule = _rule_with_matchers(Matcher("class", f"negative:{re.escape(HYPRMOD_APP_ID)}"))
        assert not matches_hyprmod(rule)

    def test_negative_prefix_inverts_non_match(self):
        # And vice versa: a ``negative:firefox`` matcher matches
        # everything except Firefox, including HyprMod.
        rule = _rule_with_matchers(Matcher("class", "negative:firefox"))
        assert matches_hyprmod(rule)

    def test_xwayland_true_does_not_match_native(self):
        # HyprMod is Wayland-native, so a rule scoped to XWayland
        # windows is guaranteed not to touch us.
        rule = _rule_with_matchers(
            Matcher("class", ".*"),
            Matcher("xwayland", "true"),
        )
        assert not matches_hyprmod(rule)

    def test_xwayland_false_alongside_class_matches(self):
        # The opposite scope: Wayland-native windows matching .*
        # → still matches HyprMod.
        rule = _rule_with_matchers(
            Matcher("class", ".*"),
            Matcher("xwayland", "false"),
        )
        assert matches_hyprmod(rule)

    def test_fullscreen_true_does_not_match(self):
        # HyprMod isn't typically fullscreen; rules scoped to
        # fullscreen windows shouldn't trigger the warning.
        rule = _rule_with_matchers(
            Matcher("class", ".*"),
            Matcher("fullscreen", "true"),
        )
        assert not matches_hyprmod(rule)

    def test_title_match_with_known_title(self):
        rule = _rule_with_matchers(Matcher("title", "HyprMod"))
        assert matches_hyprmod(rule, hyprmod_title="HyprMod")
        assert not matches_hyprmod(rule, hyprmod_title="Firefox")

    def test_title_match_without_title_is_conservative(self):
        # When the live title isn't known, title matchers fall back to
        # "could match" so the user sees a warning rather than a
        # silent self-disturbance.
        rule = _rule_with_matchers(Matcher("title", "^Some Specific Title$"))
        assert matches_hyprmod(rule)

    def test_all_matchers_must_match(self):
        # Hyprland AND-combines matchers — a single non-matching
        # matcher in the rule means HyprMod is not affected.
        rule = _rule_with_matchers(
            Matcher("class", ".*"),
            Matcher("class", "^(firefox)$"),
        )
        assert not matches_hyprmod(rule)

    def test_no_matchers_does_not_match(self):
        # A zero-matcher rule is invalid; treat as no-match so we
        # never warn for it.
        rule = _rule_with_matchers()
        assert not matches_hyprmod(rule)

    def test_empty_value_does_not_match(self):
        # A blank matcher value would never match in Hyprland; don't
        # trigger the warning for a half-typed rule.
        rule = _rule_with_matchers(Matcher("class", ""))
        assert not matches_hyprmod(rule)

    def test_invalid_regex_does_not_match(self):
        # Hyprland would reject the rule entirely; we err on benign.
        rule = _rule_with_matchers(Matcher("class", "[unclosed"))
        assert not matches_hyprmod(rule)

    def test_unknown_matcher_is_conservative(self):
        # Plugin matchers / unrecognised keys → assume could match
        # so the warning fires.
        rule = _rule_with_matchers(Matcher("plugin:foo", "bar"))
        assert matches_hyprmod(rule)

    def test_workspace_matcher_is_conservative(self):
        rule = _rule_with_matchers(
            Matcher("class", ".*"),
            Matcher("workspace", "1"),
        )
        # Workspace-scoped: we can't easily tell which workspace
        # HyprMod is on, so we keep the warning on.
        assert matches_hyprmod(rule)


# ---------------------------------------------------------------------------
# Retroactive matching: matches_window against a Window snapshot
# ---------------------------------------------------------------------------


# Realistic test default — every other field falls back to ``Window``'s
# dataclass defaults.
_DEFAULT_WINDOW = Window(
    address="0xdeadbeef",
    mapped=True,
    class_name="firefox",
    title="Mozilla Firefox",
    initial_class="firefox",
    initial_title="Loading…",
    floating=False,
    pinned=False,
    fullscreen=0,
    xwayland=False,
    workspace_id=1,
    workspace_name="1",
    tags=(),
    xdg_tag="",
    grouped=(),
)


def _window(**overrides) -> Window:
    """Build a Window from :data:`_DEFAULT_WINDOW` with per-test overrides.

    ``dataclasses.replace`` keeps the type-checker happy where a plain
    ``Window(**defaults)`` would lose the per-field types after a
    ``dict.update``-style merge.
    """
    return dataclasses.replace(_DEFAULT_WINDOW, **overrides)


class TestMatchesWindow:
    def test_class_regex_matches(self):
        rule = _rule_with_matchers(Matcher("class", "^(firefox)$"))
        assert matches_window(rule, _window(class_name="firefox"))
        assert not matches_window(rule, _window(class_name="kitty"))

    def test_initial_class_distinct_from_class(self):
        rule = _rule_with_matchers(Matcher("initial_class", "^(loader)$"))
        assert matches_window(rule, _window(initial_class="loader", class_name="app"))
        assert not matches_window(rule, _window(initial_class="app"))

    def test_title_regex(self):
        rule = _rule_with_matchers(Matcher("title", "Picture-in-Picture"))
        assert matches_window(rule, _window(title="Firefox Picture-in-Picture"))
        assert not matches_window(rule, _window(title="Mozilla Firefox"))

    def test_negation_inverts(self):
        rule = _rule_with_matchers(Matcher("class", "negative:firefox"))
        assert not matches_window(rule, _window(class_name="firefox"))
        assert matches_window(rule, _window(class_name="kitty"))

    def test_bool_floating_matches_state(self):
        rule = _rule_with_matchers(Matcher("float", "true"))
        assert matches_window(rule, _window(floating=True))
        assert not matches_window(rule, _window(floating=False))

    def test_bool_xwayland(self):
        rule = _rule_with_matchers(Matcher("xwayland", "true"))
        assert matches_window(rule, _window(xwayland=True))
        assert not matches_window(rule, _window(xwayland=False))

    def test_bool_fullscreen_treats_nonzero_as_true(self):
        # Hyprland uses an integer fullscreen state; any non-zero
        # value means the window is fullscreen-of-some-kind.
        rule = _rule_with_matchers(Matcher("fullscreen", "true"))
        assert matches_window(rule, _window(fullscreen=2))
        assert matches_window(rule, _window(fullscreen=1))
        assert not matches_window(rule, _window(fullscreen=0))

    def test_workspace_by_id(self):
        rule = _rule_with_matchers(Matcher("workspace", "3"))
        assert matches_window(rule, _window(workspace_id=3, workspace_name="3"))
        assert not matches_window(rule, _window(workspace_id=1, workspace_name="1"))

    def test_workspace_by_name_prefix(self):
        rule = _rule_with_matchers(Matcher("workspace", "name:work"))
        assert matches_window(rule, _window(workspace_id=99, workspace_name="work"))
        assert not matches_window(rule, _window(workspace_id=99, workspace_name="play"))

    def test_tag_matcher(self):
        rule = _rule_with_matchers(Matcher("tag", "scratch"))
        assert matches_window(rule, _window(tags=("scratch", "term")))
        assert not matches_window(rule, _window(tags=("term",)))

    def test_and_combines(self):
        rule = _rule_with_matchers(
            Matcher("class", "^(firefox)$"),
            Matcher("title", "Picture-in-Picture"),
        )
        assert matches_window(
            rule, _window(class_name="firefox", title="Firefox Picture-in-Picture")
        )
        # Class matches but title doesn't → no match.
        assert not matches_window(rule, _window(class_name="firefox", title="Bookmarks"))

    def test_no_matchers_does_not_match(self):
        rule = WindowRule(matchers=[], effect_name="float", effect_args="on")
        assert not matches_window(rule, _window())

    def test_unknown_matcher_does_not_match(self):
        # Conservative direction is reversed vs. matches_hyprmod:
        # here we'd rather skip a window we can't evaluate than
        # mutate it incorrectly.
        rule = _rule_with_matchers(Matcher("plugin:foo", "bar"))
        assert not matches_window(rule, _window())


# ---------------------------------------------------------------------------
# Retroactive dispatch mapping: existing_window_dispatchers
# ---------------------------------------------------------------------------


def _addr(window: Window) -> str:
    return f"address:{window.address}"


class TestExistingWindowDispatchers:
    def test_float_on_tiled_window_toggles(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "float", "on")
        win = _window(floating=False)
        assert existing_window_dispatchers(rule, win) == [("togglefloating", _addr(win))]

    def test_float_on_already_floating_is_noop(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "float", "on")
        assert existing_window_dispatchers(rule, _window(floating=True)) == []

    def test_tile_on_floating_window_toggles(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "tile", "on")
        win = _window(floating=True)
        assert existing_window_dispatchers(rule, win) == [("togglefloating", _addr(win))]

    def test_tile_on_tiled_is_noop(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "tile", "on")
        assert existing_window_dispatchers(rule, _window(floating=False)) == []

    def test_pin_only_when_unpinned(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "pin", "on")
        win = _window(pinned=False)
        assert existing_window_dispatchers(rule, win) == [("pin", _addr(win))]
        assert existing_window_dispatchers(rule, _window(pinned=True)) == []

    def test_workspace_uses_silent_variant(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "workspace", "2")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("movetoworkspacesilent", f"2,{_addr(win)}")
        ]

    def test_workspace_strips_extra_modifiers(self):
        # ``workspace 2 silent`` in a rule still works; the dispatcher
        # arg drops anything past the first whitespace token.
        rule = WindowRule([Matcher("class", "^(x)$")], "workspace", "2 silent")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("movetoworkspacesilent", f"2,{_addr(win)}")
        ]

    def test_size_emits_resize_pixel(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "size", "1280 720")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("resizewindowpixel", f"exact 1280 720,{_addr(win)}")
        ]

    def test_size_with_one_arg_skips(self):
        # Malformed size args are tolerated — Hyprland would reject
        # the rule too, but we don't want to send a broken dispatch.
        rule = WindowRule([Matcher("class", "^(x)$")], "size", "1280")
        assert existing_window_dispatchers(rule, _window()) == []

    def test_move_emits_move_pixel(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "move", "100 200")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("movewindowpixel", f"exact 100 200,{_addr(win)}")
        ]

    def test_fullscreen_skips_when_already_fullscreen(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "fullscreen", "on")
        assert existing_window_dispatchers(rule, _window(fullscreen=2)) == []

    def test_maximize_skips_when_already_maximized(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "maximize", "on")
        assert existing_window_dispatchers(rule, _window(fullscreen=1)) == []

    def test_opacity_single_value_sets_active_and_inactive(self):
        # ``opacity 0.5`` applies the same value to active+inactive,
        # mirroring Hyprland's at-spawn behaviour. Each ``setprop``
        # takes a single float, so multi-arg rules fan out.
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5")
        win = _window()
        addr = _addr(win)
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 0.5"),
            ("setprop", f"{addr} opacity_inactive 0.5"),
        ]

    def test_opacity_two_values(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5 0.8")
        win = _window()
        addr = _addr(win)
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 0.5"),
            ("setprop", f"{addr} opacity_inactive 0.8"),
        ]

    def test_opacity_three_values_includes_fullscreen(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5 0.8 1.0")
        win = _window()
        addr = _addr(win)
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 0.5"),
            ("setprop", f"{addr} opacity_inactive 0.8"),
            ("setprop", f"{addr} opacity_fullscreen 1.0"),
        ]

    def test_opacity_drops_override_keyword(self):
        # ``opacity 0.5 override`` is valid Hyprland but the setprop
        # interface separates the override flag into its own props.
        # We surface the alpha values only and skip the flag for now.
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5 override")
        win = _window()
        addr = _addr(win)
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 0.5"),
            ("setprop", f"{addr} opacity_inactive 0.5"),
        ]

    def test_no_blur_passthrough(self):
        # In Hyprland 0.54+ the setprop name *is* the v3 effect name,
        # so the value passes through unchanged (parsePropTrivial
        # accepts ``on`` as a truthy string).
        rule = WindowRule([Matcher("class", "^(x)$")], "no_blur", "on")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{_addr(win)} no_blur on"),
        ]

    def test_no_shadow_passthrough(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "no_shadow", "on")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{_addr(win)} no_shadow on"),
        ]

    def test_rounding_passthrough(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "rounding", "8")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{_addr(win)} rounding 8"),
        ]

    def test_border_color_emits_active_and_inactive(self):
        # ``border_color`` rule sets both gradients in Hyprland; we
        # mirror that with two setprops.
        rule = WindowRule([Matcher("class", "^(x)$")], "border_color", "rgb(ff0000)")
        win = _window()
        addr = _addr(win)
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{addr} active_border_color rgb(ff0000)"),
            ("setprop", f"{addr} inactive_border_color rgb(ff0000)"),
        ]

    def test_xray_passthrough(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "xray", "on")
        win = _window()
        assert existing_window_dispatchers(rule, win) == [
            ("setprop", f"{_addr(win)} xray on"),
        ]

    def test_setprop_does_not_emit_lock(self):
        # Hyprland 0.54.3's setprop ignores trailing args (``CVarList``
        # has lastArgNo=3) and uses ``PRIORITY_SET_PROP`` for
        # persistence. Adding a ``lock`` keyword would be silently
        # discarded — we drop it to keep the IPC string honest.
        rule = WindowRule([Matcher("class", "^(x)$")], "no_blur", "on")
        for _, arg in existing_window_dispatchers(rule, _window()):
            assert " lock" not in arg, f"unexpected lock in {arg!r}"

    def test_unhandled_effect_returns_empty(self):
        # Effects we deliberately don't translate (no per-window
        # mutation, or expression-parsed args we'd have to mirror)
        # silently drop through. The keyword push has already
        # registered them for new windows.
        for effect in (
            "center",
            "no_initial_focus",
            "suppress_event",
            "min_size",
            "max_size",
            "tag",
        ):
            rule = WindowRule([Matcher("class", "^(x)$")], effect, "on")
            assert existing_window_dispatchers(rule, _window()) == [], (
                f"{effect} unexpectedly emitted a dispatcher"
            )

    def test_retroactive_effects_set_matches_dispatcher_implementation(self):
        # The fast-path predicate in the page (``effect_name in
        # RETROACTIVE_EFFECTS``) must agree with the actual mapping
        # function — otherwise we'd either skip apply for a listed
        # effect or pay for a no-op ``get_windows`` round-trip on
        # an unlisted one.
        per_effect_args = {
            "workspace": "1",
            "monitor": "DP-1",
            "size": "100 100",
            "move": "0 0",
            "rounding": "8",
            "rounding_power": "2",
            "border_size": "2",
            "border_color": "rgb(ff0000)",
            "opacity": "0.5",
            "animation": "popin",
            "scroll_mouse": "1.5",
            "scroll_touchpad": "1.5",
            "idle_inhibit": "focus",
        }
        # Make tests state-independent: pick a window whose state
        # doesn't trigger an idempotent skip for the effect under test.
        per_effect_state = {
            "float": dict(floating=False),
            "tile": dict(floating=True),
            "pin": dict(pinned=False),
            "fullscreen": dict(fullscreen=0),
            "maximize": dict(fullscreen=0),
        }
        for effect in RETROACTIVE_EFFECTS:
            args = per_effect_args.get(effect, "on")
            rule = WindowRule([Matcher("class", "^(x)$")], effect, args)
            state = per_effect_state.get(effect, {})
            assert existing_window_dispatchers(rule, _window(**state)), (
                f"{effect} listed in RETROACTIVE_EFFECTS but emitted no dispatcher"
            )


# ---------------------------------------------------------------------------
# Retroactive revert: existing_window_revert_dispatchers
# ---------------------------------------------------------------------------


class TestExistingWindowRevertDispatchers:
    def test_opacity_single_arg_reverts_to_default(self):
        # Hyprland 0.54.3's ``setprop opacity`` rejects ``unset``
        # (calls ``std::stof`` directly), so the revert path falls
        # back to the compositor default of ``1.0``. The count
        # mirrors the apply path: 1-arg rule emits 2 setprops, so
        # revert emits 2 — *not* 3 — to avoid locking
        # ``opacity_fullscreen`` to 1.0 when the rule never set it.
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5")
        win = _window()
        addr = _addr(win)
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 1.0"),
            ("setprop", f"{addr} opacity_inactive 1.0"),
        ]

    def test_opacity_two_args_reverts_two_props(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5 0.8")
        win = _window()
        addr = _addr(win)
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 1.0"),
            ("setprop", f"{addr} opacity_inactive 1.0"),
        ]

    def test_opacity_three_args_reverts_three_props(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5 0.8 1.0")
        win = _window()
        addr = _addr(win)
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 1.0"),
            ("setprop", f"{addr} opacity_inactive 1.0"),
            ("setprop", f"{addr} opacity_fullscreen 1.0"),
        ]

    def test_opacity_override_keyword_dropped_in_revert_too(self):
        # ``opacity 0.5 override`` apply emits 2 setprops; revert
        # mirrors that count, ignoring the keyword.
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5 override")
        win = _window()
        addr = _addr(win)
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{addr} opacity 1.0"),
            ("setprop", f"{addr} opacity_inactive 1.0"),
        ]

    def test_no_blur_reverts_via_unset(self):
        # Bool effects flow through ``parsePropTrivial`` which DOES
        # accept ``unset`` — that's the cleanest revert (clears the
        # SET_PROP override entirely, lets the rule resolver retake).
        rule = WindowRule([Matcher("class", "^(x)$")], "no_blur", "on")
        win = _window()
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{_addr(win)} no_blur unset"),
        ]

    def test_rounding_reverts_via_unset(self):
        rule = WindowRule([Matcher("class", "^(x)$")], "rounding", "8")
        win = _window()
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{_addr(win)} rounding unset"),
        ]

    def test_border_color_reverts_via_unset(self):
        # ``setprop active_border_color unset`` in 0.54.3 ends up
        # storing an empty gradient at SET_PROP — equivalent to "no
        # override" for our purposes.
        rule = WindowRule([Matcher("class", "^(x)$")], "border_color", "rgb(ff0000)")
        win = _window()
        addr = _addr(win)
        assert existing_window_revert_dispatchers(rule, win) == [
            ("setprop", f"{addr} active_border_color unset"),
            ("setprop", f"{addr} inactive_border_color unset"),
        ]

    def test_static_effects_have_no_clean_revert(self):
        # ``float``/``size``/``workspace``/etc. mutate the window's
        # actual layout state; safely undoing them needs per-window
        # history we don't track. The user's escape hatch is
        # save+reload.
        for effect, args in [
            ("float", "on"),
            ("tile", "on"),
            ("pin", "on"),
            ("fullscreen", "on"),
            ("maximize", "on"),
            ("workspace", "2"),
            ("monitor", "DP-1"),
            ("size", "100 100"),
            ("move", "0 0"),
        ]:
            rule = WindowRule([Matcher("class", "^(x)$")], effect, args)
            assert existing_window_revert_dispatchers(rule, _window()) == [], (
                f"{effect} unexpectedly emitted a revert dispatcher"
            )

    def test_revert_props_overlap_apply_props(self):
        # Symmetry check: any dynamic effect that emits
        # ``setprop NAME VALUE`` on apply must touch the same NAME on
        # revert (whether via ``unset`` or a default-value fallback).
        # A leak in the property name would leave the original
        # override active after removal.
        per_effect_args = {
            "opacity": "0.5",
            "border_color": "rgb(ff0000)",
        }
        static_effects = {
            "float",
            "tile",
            "pin",
            "fullscreen",
            "maximize",
            "workspace",
            "monitor",
            "size",
            "move",
        }
        for effect in RETROACTIVE_EFFECTS:
            if effect in static_effects:
                continue
            args = per_effect_args.get(effect, "on")
            rule = WindowRule([Matcher("class", "^(x)$")], effect, args)
            applied = {
                arg.split()[1] for _disp, arg in existing_window_dispatchers(rule, _window())
            }
            reverted = {
                arg.split()[1] for _disp, arg in existing_window_revert_dispatchers(rule, _window())
            }
            assert applied <= reverted, (
                f"{effect}: apply props {applied} not covered by revert props {reverted}"
            )


# ---------------------------------------------------------------------------
# External rules: load_external_window_rules
# ---------------------------------------------------------------------------


def _make_config_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a synthetic root + managed config layout under *tmp_path*.

    Returns ``(root, managed)``. The root sources a sibling file plus
    the managed file, and the sourced sibling sources a third file —
    enough depth to verify recursive walking.
    """
    root = tmp_path / "hyprland.conf"
    sourced = tmp_path / "rules.conf"
    nested = tmp_path / "more-rules.conf"
    managed = tmp_path / "hyprland-gui.conf"

    root.write_text(
        f"windowrule = match:class ^(firefox)$, opacity 0.8\n"
        f"windowrulev2 = float, class:^(legacy)$\n"
        f"source = {sourced}\n"
        f"source = {managed}\n"
    )
    sourced.write_text(f"windowrule = match:class ^(steam)$, no_blur on\nsource = {nested}\n")
    nested.write_text("windowrule = match:class ^(deeply-nested)$, opacity 0.7\n")
    managed.write_text("windowrule = match:class ^(hyprmod-managed)$, opacity 0.5\n")
    return root, managed


class TestLoadExternalWindowRules:
    def test_walks_sourced_files_recursively(self, tmp_path):
        root, managed = _make_config_tree(tmp_path)
        external = load_external_window_rules(root, managed)
        # Three external rules: root (×2), sourced (×1), nested (×1).
        # The managed file's rule is NOT in the result.
        assert len(external) == 4
        classes = sorted(m.value for ext in external for m in ext.rule.matchers if m.key == "class")
        assert classes == ["^(deeply-nested)$", "^(firefox)$", "^(legacy)$", "^(steam)$"]

    def test_excludes_managed_file(self, tmp_path):
        root, managed = _make_config_tree(tmp_path)
        external = load_external_window_rules(root, managed)
        # No external rule should come from the managed file.
        assert all(ext.source_path != managed for ext in external)
        # And the rule we know lives in the managed file shouldn't surface.
        assert not any(
            m.value == "^(hyprmod-managed)$" for ext in external for m in ext.rule.matchers
        )

    def test_v2_rules_are_migrated_on_load(self, tmp_path):
        # Legacy ``windowrulev2 = float, class:^(legacy)$`` should be
        # parsed into the v3 model so the UI doesn't have to know
        # about v2.
        root, managed = _make_config_tree(tmp_path)
        external = load_external_window_rules(root, managed)
        legacy = next(
            ext for ext in external if any(m.value == "^(legacy)$" for m in ext.rule.matchers)
        )
        assert legacy.rule.effect_name == "float"
        assert legacy.rule.effect_args == "on"  # v2's bare ``float`` gains ``on``

    def test_provenance_tracks_actual_source_file(self, tmp_path):
        root, managed = _make_config_tree(tmp_path)
        external = load_external_window_rules(root, managed)
        provenance = {ext.source_path.name: ext for ext in external}
        # Each rule reports the file it actually lives in, not the
        # entry point — that's what the UI surfaces in the row tooltip.
        firefox = next(
            ext for ext in external if any(m.value == "^(firefox)$" for m in ext.rule.matchers)
        )
        assert firefox.source_path.name == "hyprland.conf"
        assert "more-rules.conf" in provenance
        assert provenance["more-rules.conf"].lineno == 1

    def test_missing_root_returns_empty(self, tmp_path):
        # No hyprland.conf? No external rules — the page just won't
        # show the read-only group.
        managed = tmp_path / "hyprland-gui.conf"
        managed.write_text("windowrule = match:class ^(x)$, opacity 1.0\n")
        external = load_external_window_rules(tmp_path / "missing.conf", managed)
        assert external == []

    def test_empty_root_returns_empty(self, tmp_path):
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("# just a comment\n")
        managed.write_text("")
        assert load_external_window_rules(root, managed) == []

    def test_unparseable_rules_are_silently_skipped(self, tmp_path):
        # ``windowrule = match:class ^(foo)$`` (matchers but no
        # effect) returns ``None`` from ``parse_window_rule_line``;
        # the loader should drop it instead of choking. Hyprland
        # itself rejects the line at config-parse time, so surfacing
        # it would be misleading anyway.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(
            "windowrule = match:class ^(firefox)$, opacity 0.8\n"
            "windowrule = match:class ^(no-effect)$\n"
        )
        managed.write_text("")
        external = load_external_window_rules(root, managed)
        assert len(external) == 1
        assert any(m.value == "^(firefox)$" for m in external[0].rule.matchers)

    def test_external_rule_dataclass_is_immutable(self):
        # Frozen dataclass — defensively checked because the page
        # caches the list and would break if rules mutated under it.
        rule = WindowRule([Matcher("class", "^(x)$")], "opacity", "0.5")
        ext = ExternalWindowRule(rule=rule, source_path=Path("/x.conf"), lineno=1)
        with pytest.raises((AttributeError, TypeError)):
            ext.lineno = 2  # type: ignore[misc]
