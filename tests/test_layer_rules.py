"""Tests for layer-rule parsing, serialization, and change tracking.

Hyprland 0.54+ uses ``layerrule = match:namespace REGEX, EFFECT VALUE``
— comma-separated tokens, identical shape to windowrule v3:

- One ``match:namespace REGEX`` matcher (other ``match:*`` props from
  the shared rule catalog are silently ignored at match time).
- One or more ``EFFECT VALUE`` tokens; we model one effect per
  :class:`LayerRule`, splitting multi-effect lines on parse.
- Bool effects (``blur``, ``no_anim``, ``dim_around``, ``blur_popups``,
  ``xray``, ``no_screen_share``) need an explicit value — we always
  emit ``on``.

The legacy pre-0.54 format (``layerrule = EFFECT, NAMESPACE`` — no
``match:`` prefix, single bare-namespace token) is accepted on read
with effect-name migration (``noanim`` → ``no_anim`` etc.) so users
with hand-rolled old configs see their rules in the UI. Save always
emits the v3 form.
"""

import pytest

from hyprmod.core import config
from hyprmod.core.layer_rules import (
    CUSTOM_PRESET,
    KEYWORD_WRITE,
    LAYER_ACTION_PRESETS_BY_ID,
    LAYER_BOOL_EFFECTS,
    LAYER_RULE_KEYWORDS,
    LayerRule,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
    iter_item_changes,
    load_external_layer_rules,
    lookup_preset,
    parse_layer_rule_line,
    parse_layer_rule_lines,
    serialize,
    summarize_action,
    summarize_namespace,
    summarize_rule,
)
from hyprmod.core.ownership import SavedList

# ---------------------------------------------------------------------------
# v3 single-line parser
# ---------------------------------------------------------------------------


class TestParseV3:
    def test_basic_blur(self):
        rule = parse_layer_rule_line("layerrule = match:namespace ^(waybar)$, blur on")
        assert rule == LayerRule(namespace="^(waybar)$", rule_name="blur", rule_args="on")

    def test_match_first_or_last_both_work(self):
        a = parse_layer_rule_line("layerrule = match:namespace ^(waybar)$, blur on")
        b = parse_layer_rule_line("layerrule = blur on, match:namespace ^(waybar)$")
        assert a is not None and b is not None
        assert a.namespace == b.namespace
        assert (a.rule_name, a.rule_args) == (b.rule_name, b.rule_args)

    def test_one_arg_float(self):
        rule = parse_layer_rule_line("layerrule = match:namespace waybar, ignore_alpha 0.30")
        assert rule == LayerRule(namespace="waybar", rule_name="ignore_alpha", rule_args="0.30")

    def test_one_arg_int(self):
        rule = parse_layer_rule_line("layerrule = match:namespace notifications, order 5")
        assert rule == LayerRule(namespace="notifications", rule_name="order", rule_args="5")

    def test_animation_string_arg(self):
        rule = parse_layer_rule_line("layerrule = match:namespace waybar, animation slide")
        assert rule == LayerRule(namespace="waybar", rule_name="animation", rule_args="slide")

    def test_above_lock(self):
        rule = parse_layer_rule_line("layerrule = match:namespace lock-prompt, above_lock 1")
        assert rule == LayerRule(namespace="lock-prompt", rule_name="above_lock", rule_args="1")

    def test_regex_namespace_with_alternation(self):
        rule = parse_layer_rule_line("layerrule = match:namespace ^(rofi|wofi)$, dim_around on")
        assert rule is not None
        assert rule.namespace == "^(rofi|wofi)$"
        assert rule.rule_name == "dim_around"

    def test_strips_whitespace(self):
        rule = parse_layer_rule_line("  layerrule  =  match:namespace waybar  ,  blur  on  ")
        assert rule is not None
        assert rule.namespace == "waybar"
        assert rule.rule_name == "blur"
        assert rule.rule_args == "on"

    def test_custom_plugin_rule_round_trips(self):
        rule = parse_layer_rule_line("layerrule = match:namespace waybar, plugin:foo bar")
        assert rule is not None
        assert rule.rule_name == "plugin:foo"
        assert rule.rule_args == "bar"

    def test_unknown_keyword_returns_none(self):
        assert parse_layer_rule_line("windowrule = float on, ^(foo)$") is None
        assert parse_layer_rule_line("monitor = , preferred, auto, 1") is None

    def test_missing_equals_returns_none(self):
        assert parse_layer_rule_line("layerrule blur, waybar") is None

    def test_empty_body_returns_none(self):
        assert parse_layer_rule_line("layerrule = ") is None
        assert parse_layer_rule_line("layerrule =") is None

    def test_no_namespace_returns_none(self):
        # Only an effect, no matcher — Hyprland would reject this too.
        assert parse_layer_rule_line("layerrule = blur on") is None

    def test_no_effect_returns_none(self):
        # Only a matcher, no effect — meaningless rule.
        assert parse_layer_rule_line("layerrule = match:namespace waybar") is None


# ---------------------------------------------------------------------------
# Legacy (pre-0.54) format compatibility
# ---------------------------------------------------------------------------


class TestLegacyFormatMigration:
    """Parsing accepts the old ``EFFECT, NAMESPACE`` shape (no ``match:``
    prefix) so users with hand-rolled pre-0.54 configs see their rules
    in the UI. Effect names are migrated to v3 form transparently."""

    def test_legacy_blur_promotes_to_v3(self):
        # No comma-separator-with-`match:` — it's the legacy form.
        rule = parse_layer_rule_line("layerrule = blur, waybar")
        assert rule is not None
        assert rule.namespace == "waybar"
        assert rule.rule_name == "blur"
        # Bare bool effect gets ``on`` on next serialize.
        assert rule.to_line() == "layerrule = match:namespace waybar, blur on"

    def test_legacy_noanim_renamed(self):
        rule = parse_layer_rule_line("layerrule = noanim, ^(waybar)$")
        assert rule is not None
        # ``noanim`` was renamed to ``no_anim`` in 0.54.
        assert rule.rule_name == "no_anim"

    def test_legacy_blurpopups_renamed(self):
        rule = parse_layer_rule_line("layerrule = blurpopups, waybar")
        assert rule is not None
        assert rule.rule_name == "blur_popups"

    def test_legacy_dimaround_renamed(self):
        rule = parse_layer_rule_line("layerrule = dimaround, ^(rofi)$")
        assert rule is not None
        assert rule.rule_name == "dim_around"

    def test_legacy_ignorealpha_renamed(self):
        rule = parse_layer_rule_line("layerrule = ignorealpha 0.3, waybar")
        assert rule is not None
        assert rule.rule_name == "ignore_alpha"
        assert rule.rule_args == "0.3"

    def test_legacy_ignorezero_becomes_ignore_alpha_zero(self):
        # ``ignorezero`` had no arg in v1; v3 has no equivalent so we
        # migrate it to ``ignore_alpha 0`` (semantically identical).
        rule = parse_layer_rule_line("layerrule = ignorezero, waybar")
        assert rule is not None
        assert rule.rule_name == "ignore_alpha"
        assert rule.rule_args == "0"

    def test_legacy_unset_dropped(self):
        # ``unset`` is no longer an effect type — drop the line.
        assert parse_layer_rule_line("layerrule = unset, waybar") is None

    def test_legacy_noshadow_dropped(self):
        # ``noshadow`` is no longer a layer rule effect.
        assert parse_layer_rule_line("layerrule = noshadow, waybar") is None


class TestParseLayerRuleLines:
    def test_preserves_order(self):
        lines = [
            "layerrule = match:namespace waybar, blur on",
            "layerrule = match:namespace waybar, ignore_alpha 0.3",
            "layerrule = match:namespace ^(rofi)$, dim_around on",
        ]
        rules = parse_layer_rule_lines(lines)
        assert [r.rule_name for r in rules] == ["blur", "ignore_alpha", "dim_around"]

    def test_drops_unparseable(self):
        lines = [
            "layerrule = match:namespace waybar, blur on",
            "garbage",
            "windowrule = float on, ^(foo)$",
            "layerrule = match:namespace waybar",  # no effect
            "layerrule = match:namespace notifications, order 5",
        ]
        rules = parse_layer_rule_lines(lines)
        assert [r.rule_name for r in rules] == ["blur", "order"]

    def test_multi_effect_v3_splits_into_separate_rules(self):
        # ``layerrule = match:namespace ^(waybar)$, blur on, ignore_alpha 0.3``
        # is one Hyprland-valid line with two effects sharing a matcher.
        # We model one effect per LayerRule, so the parser splits these
        # into separate entries that both round-trip on save.
        rules = parse_layer_rule_lines(
            ["layerrule = match:namespace ^(waybar)$, blur on, ignore_alpha 0.3"]
        )
        assert len(rules) == 2
        assert rules[0].rule_name == "blur"
        assert rules[1].rule_name == "ignore_alpha"
        # Same namespace on both.
        assert rules[0].namespace == rules[1].namespace == "^(waybar)$"

    def test_empty_input(self):
        assert parse_layer_rule_lines([]) == []


# ---------------------------------------------------------------------------
# Serializer / round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_basic_v3_round_trip(self):
        original = "layerrule = match:namespace waybar, blur on"
        rule = parse_layer_rule_line(original)
        assert rule is not None
        assert rule.to_line() == original

    def test_serialize_list(self):
        rules = [
            LayerRule(namespace="waybar", rule_name="blur", rule_args="on"),
            LayerRule(namespace="waybar", rule_name="ignore_alpha", rule_args="0.30"),
            LayerRule(namespace="^(rofi)$", rule_name="dim_around", rule_args="on"),
        ]
        assert serialize(rules) == [
            "layerrule = match:namespace waybar, blur on",
            "layerrule = match:namespace waybar, ignore_alpha 0.30",
            "layerrule = match:namespace ^(rofi)$, dim_around on",
        ]

    def test_bool_effect_auto_adds_on(self):
        # Building a LayerRule with empty rule_args for a bool effect
        # auto-fills ``on`` on serialization — Hyprland 0.54.3 rejects
        # bare bool effects with "missing a value".
        rule = LayerRule(namespace="waybar", rule_name="blur")
        assert rule.to_line().endswith(", blur on")

    def test_non_bool_effect_no_auto_on(self):
        # Numeric/string effects keep their explicit args and don't
        # gain a spurious ``on``.
        rule = LayerRule(namespace="waybar", rule_name="ignore_alpha", rule_args="0.5")
        assert rule.to_line().endswith(", ignore_alpha 0.5")

    def test_unknown_rule_round_trips(self):
        rule = LayerRule(namespace="waybar", rule_name="plugin:foo", rule_args="bar baz")
        line = rule.to_line()
        assert parse_layer_rule_line(line) == rule

    def test_regex_with_parens_round_trips(self):
        # The parens-aware top-level split keeps ``^(rofi|wofi)$`` intact
        # even though it contains characters the parser is split on.
        rule = LayerRule(namespace="^(rofi|wofi)$", rule_name="dim_around")
        line = rule.to_line()
        parsed = parse_layer_rule_line(line)
        assert parsed is not None
        assert parsed.namespace == "^(rofi|wofi)$"


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestKeywords:
    def test_layer_rule_keywords_contains_layerrule(self):
        assert config.KEYWORD_LAYERRULE in LAYER_RULE_KEYWORDS

    def test_keyword_write_is_layerrule(self):
        assert KEYWORD_WRITE == "layerrule"


class TestBoolEffectCatalog:
    """Spot-check that the bool set covers the effects with auto-``on`` —
    these are the ones Hyprland 0.54.3 rejects when emitted bare."""

    def test_common_bool_effects_listed(self):
        for name in [
            "blur",
            "blur_popups",
            "no_anim",
            "dim_around",
            "xray",
            "no_screen_share",
        ]:
            assert name in LAYER_BOOL_EFFECTS

    def test_non_bool_effects_excluded(self):
        # These take typed args, never auto-``on``.
        for name in ["ignore_alpha", "order", "above_lock", "animation"]:
            assert name not in LAYER_BOOL_EFFECTS


# ---------------------------------------------------------------------------
# Action catalog
# ---------------------------------------------------------------------------


class TestActionLookup:
    def test_known_rule_resolves(self):
        preset = lookup_preset("blur")
        assert preset.id == "blur"
        assert preset.label == "Blur background"

    def test_unknown_rule_falls_to_custom(self):
        preset = lookup_preset("plugin:foo:bar")
        assert preset is CUSTOM_PRESET

    def test_preset_format_drops_trailing_empties(self):
        preset = LAYER_ACTION_PRESETS_BY_ID["ignore_alpha"]
        assert preset.format(["0.30", ""]) == "0.30"

    def test_preset_format_one_arg(self):
        preset = LAYER_ACTION_PRESETS_BY_ID["order"]
        assert preset.format(["5"]) == "5"

    def test_bool_preset_has_no_fields(self):
        # Bool effects are auto-``on`` on serialization — the dialog
        # doesn't show a field for them.
        preset = LAYER_ACTION_PRESETS_BY_ID["blur"]
        assert preset.fields == ()

    def test_xray_is_bool_no_fields(self):
        # Even though pre-0.54 had ``xray <0/1>``, 0.54.3+ takes a
        # truthy bool — our preset emits ``xray on`` like the others.
        preset = LAYER_ACTION_PRESETS_BY_ID["xray"]
        assert preset.fields == ()
        assert "xray" in LAYER_BOOL_EFFECTS

    def test_custom_preset_has_one_field(self):
        assert len(CUSTOM_PRESET.fields) == 1


class TestPresetCoverage:
    """Spot-check that the curated catalog covers the rules users ask about."""

    @pytest.mark.parametrize(
        "rule_id",
        [
            "blur",
            "blur_popups",
            "dim_around",
            "no_anim",
            "no_screen_share",
            "xray",
            "ignore_alpha",
            "animation",
            "order",
            "above_lock",
        ],
    )
    def test_curated_rule_in_catalog(self, rule_id):
        assert rule_id in LAYER_ACTION_PRESETS_BY_ID


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


class TestSummaries:
    def test_summarize_namespace_regex(self):
        rule = LayerRule(namespace="waybar", rule_name="blur")
        assert summarize_namespace(rule) == "namespace: waybar"

    def test_summarize_action_bool_no_on_in_label(self):
        # Bool effects auto-``on`` on serialization but read cleaner
        # in the title without the redundant value.
        rule = LayerRule(namespace="waybar", rule_name="blur", rule_args="on")
        assert summarize_action(rule) == "Blur background"

    def test_summarize_action_with_args(self):
        rule = LayerRule(namespace="waybar", rule_name="ignore_alpha", rule_args="0.30")
        s = summarize_action(rule)
        assert "0.30" in s

    def test_summarize_action_custom(self):
        rule = LayerRule(namespace="waybar", rule_name="plugin:foo", rule_args="bar")
        s = summarize_action(rule)
        assert "plugin:foo" in s
        assert "bar" in s

    def test_summarize_rule_returns_pair(self):
        rule = LayerRule(namespace="waybar", rule_name="blur", rule_args="on")
        title, subtitle = summarize_rule(rule)
        assert title == "Blur background"
        assert subtitle == "namespace: waybar"


# ---------------------------------------------------------------------------
# Drag-and-drop helper
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
# Reorder detection
# ---------------------------------------------------------------------------


class TestDetectReorder:
    def _items(self, *rule_names: str) -> list[LayerRule]:
        return [LayerRule(namespace="waybar", rule_name=n, rule_args="on") for n in rule_names]

    def test_no_change(self):
        items = self._items("blur", "no_anim")
        assert not detect_reorder(items, items)

    def test_swap_detected(self):
        saved = self._items("blur", "no_anim")
        current = self._items("no_anim", "blur")
        assert detect_reorder(saved, current)

    def test_pure_addition_not_reorder(self):
        saved = self._items("blur")
        current = self._items("blur", "no_anim")
        assert not detect_reorder(saved, current)

    def test_pure_removal_not_reorder(self):
        saved = self._items("blur", "no_anim")
        current = self._items("blur")
        assert not detect_reorder(saved, current)

    def test_single_common_item_not_reorder(self):
        saved = self._items("blur", "no_anim")
        current = self._items("blur", "dim_around")
        assert not detect_reorder(saved, current)


# ---------------------------------------------------------------------------
# iter_item_changes
# ---------------------------------------------------------------------------


class TestIterItemChanges:
    def test_added(self):
        saved: list[LayerRule] = []
        current = [LayerRule(namespace="waybar", rule_name="blur", rule_args="on")]
        baselines: list[LayerRule | None] = [None]
        out = list(iter_item_changes(saved, current, baselines))
        assert out == [("added", 0, current[0], None)]

    def test_modified(self):
        saved = [LayerRule(namespace="waybar", rule_name="blur", rule_args="on")]
        current = [LayerRule(namespace="waybar", rule_name="blur", rule_args="off")]
        # Annotation widens the element type so pyright doesn't flag the
        # invariant-list mismatch when passing into ``iter_item_changes``.
        baselines: list[LayerRule | None] = [saved[0]]
        out = list(iter_item_changes(saved, current, baselines))
        assert len(out) == 1
        assert out[0][0] == "modified"
        assert out[0][1] == 0

    def test_removed(self):
        saved = [LayerRule(namespace="waybar", rule_name="blur", rule_args="on")]
        current: list[LayerRule] = []
        baselines: list[LayerRule | None] = []
        out = list(iter_item_changes(saved, current, baselines))
        assert len(out) == 1
        assert out[0][0] == "removed"
        assert out[0][1] == -1

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            list(iter_item_changes([], [LayerRule("waybar", "blur", "on")], []))


# ---------------------------------------------------------------------------
# count_pending_changes — sidebar badge ↔ pending-list parity
# ---------------------------------------------------------------------------


class TestCountPendingChanges:
    def _baselines(self, owned: SavedList[LayerRule]) -> list[LayerRule | None]:
        return [owned.get_baseline(i) for i in range(len(owned))]

    def test_clean_list_zero(self):
        owned = SavedList(
            [LayerRule(namespace="waybar", rule_name="blur", rule_args="on")],
            key=lambda r: r.to_line(),
        )
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 0

    def test_one_added_counts_one(self):
        owned: SavedList[LayerRule] = SavedList([], key=lambda r: r.to_line())
        owned.append_new(LayerRule(namespace="waybar", rule_name="blur", rule_args="on"))
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_one_modified_counts_one(self):
        owned = SavedList(
            [LayerRule(namespace="waybar", rule_name="blur", rule_args="on")],
            key=lambda r: r.to_line(),
        )
        owned[0] = LayerRule(namespace="waybar", rule_name="blur", rule_args="off")
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_one_removed_counts_one(self):
        owned = SavedList(
            [LayerRule(namespace="waybar", rule_name="blur", rule_args="on")],
            key=lambda r: r.to_line(),
        )
        owned.pop_at(0)
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_reorder_counts_one_extra(self):
        owned = SavedList(
            [
                LayerRule(namespace="waybar", rule_name="blur", rule_args="on"),
                LayerRule(namespace="waybar", rule_name="no_anim", rule_args="on"),
            ],
            key=lambda r: r.to_line(),
        )
        owned.move(0, 1)
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_added_plus_reorder(self):
        owned = SavedList(
            [
                LayerRule(namespace="waybar", rule_name="blur", rule_args="on"),
                LayerRule(namespace="waybar", rule_name="no_anim", rule_args="on"),
            ],
            key=lambda r: r.to_line(),
        )
        owned.move(0, 1)
        owned.append_new(LayerRule(namespace="rofi", rule_name="dim_around", rule_args="on"))
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 2


# ---------------------------------------------------------------------------
# External loader
# ---------------------------------------------------------------------------


class TestExternalLoader:
    def test_loads_layerrule_from_root_file(self, tmp_path):
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("layerrule = match:namespace waybar, blur on\n")
        managed.write_text("")
        external = load_external_layer_rules(root, managed)
        assert len(external) == 1
        assert external[0].rule == LayerRule(namespace="waybar", rule_name="blur", rule_args="on")
        assert external[0].source_path == root
        assert external[0].lineno >= 1

    def test_excludes_managed_file(self, tmp_path):
        # Anything in the managed file is *not* surfaced as external —
        # the page renders managed rules from SavedList and external
        # from this loader; double-counting would confuse users.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(f"source = {managed}\n")
        managed.write_text("layerrule = match:namespace waybar, blur on\n")
        external = load_external_layer_rules(root, managed)
        assert external == []

    def test_missing_root_returns_empty(self, tmp_path):
        managed = tmp_path / "hyprland-gui.conf"
        managed.write_text("")
        external = load_external_layer_rules(tmp_path / "nonexistent.conf", managed)
        assert external == []

    def test_legacy_layerrule_v1_migrates_on_load(self, tmp_path):
        # Pre-0.54 ``EFFECT, NAMESPACE`` lines surface as v3-form
        # LayerRule instances in the read-only display, with effect
        # names migrated to the new spelling.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("layerrule = noanim, ^(legacy)$\n")
        managed.write_text("")
        external = load_external_layer_rules(root, managed)
        assert len(external) == 1
        # ``noanim`` → ``no_anim`` migration applied.
        assert external[0].rule.rule_name == "no_anim"
        assert external[0].rule.namespace == "^(legacy)$"

    def test_skips_unparseable_lines(self, tmp_path):
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(
            "layerrule = match:namespace waybar\n"  # no effect
            "layerrule = match:namespace waybar, blur on\n"
        )
        managed.write_text("")
        external = load_external_layer_rules(root, managed)
        assert len(external) == 1
        assert external[0].rule.rule_name == "blur"

    def test_multi_effect_line_yields_multiple_external_entries(self, tmp_path):
        # A single multi-effect line surfaces as N ExternalLayerRule
        # entries so each effect gets its own row in the read-only display.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("layerrule = match:namespace ^(waybar)$, blur on, ignore_alpha 0.3\n")
        managed.write_text("")
        external = load_external_layer_rules(root, managed)
        assert len(external) == 2
        assert {e.rule.rule_name for e in external} == {"blur", "ignore_alpha"}


# ---------------------------------------------------------------------------
# Config integration: build_content emits the layer-rule section
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_build_content_emits_layer_rules_section(self):
        from hyprmod.core.config import build_content

        content = build_content(
            values={},
            layer_rule_lines=[
                "layerrule = match:namespace waybar, blur on",
                "layerrule = match:namespace waybar, ignore_alpha 0.30",
            ],
        )
        assert "# Layer rules" in content
        assert "layerrule = match:namespace waybar, blur on" in content
        assert "layerrule = match:namespace waybar, ignore_alpha 0.30" in content

    def test_build_content_layer_rules_after_window_rules(self):
        from hyprmod.core.config import build_content

        content = build_content(
            values={},
            window_rule_lines=["windowrule = match:class ^(kitty)$, float on"],
            layer_rule_lines=["layerrule = match:namespace waybar, blur on"],
        )
        wr_idx = content.index("# Window rules")
        lr_idx = content.index("# Layer rules")
        assert wr_idx < lr_idx
