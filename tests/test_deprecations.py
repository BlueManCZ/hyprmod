"""Tests for ``hyprmod.core.deprecations`` — scan + apply pure logic.

These cover the contract used by the deprecation dialog: a scan must
identify which files need rewriting and what would change, and an apply
must produce a backup beside the file before atomically overwriting.
"""

from pathlib import Path

from hyprmod.core import deprecations


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestScan:
    def test_detects_user_config_with_fixable_rule(self, tmp_path):
        """cursor:no_cursor_warps in the user's hyprland.conf surfaces as a fixable file."""
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        user_root = _write(
            tmp_path / "hyprland.conf",
            "cursor {\n    no_cursor_warps = true\n}\n",
        )

        result = deprecations.scan(managed_path=managed, user_root_path=user_root)

        assert result.has_fixable
        assert len(result.files) == 1
        plan = result.files[0]
        assert plan.path == user_root
        assert plan.is_managed is False
        assert "no_cursor_warps" in plan.original
        assert "no_warps" in plan.migrated
        assert "no_cursor_warps" not in plan.migrated

    def test_detects_managed_config_with_fixable_rule(self, tmp_path):
        """A deprecated key in hyprmod's managed file surfaces as is_managed=True."""
        managed = _write(
            tmp_path / "managed.conf",
            "cursor:no_cursor_warps = true\n",
        )
        user_root = tmp_path / "hyprland.conf"  # does not exist

        result = deprecations.scan(managed_path=managed, user_root_path=user_root)

        assert len(result.files) == 1
        assert result.files[0].is_managed is True

    def test_returns_empty_when_no_deprecations(self, tmp_path):
        """A clean config tree produces no FilePlans."""
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        user_root = _write(
            tmp_path / "hyprland.conf",
            "general:gaps_in = 10\n",
        )

        result = deprecations.scan(managed_path=managed, user_root_path=user_root)

        assert result.has_fixable is False
        assert result.files == ()

    def test_skips_missing_files(self, tmp_path):
        """Non-existent entrypoints are silently ignored."""
        result = deprecations.scan(
            managed_path=tmp_path / "missing-managed.conf",
            user_root_path=tmp_path / "missing-user.conf",
        )
        assert result.files == ()
        assert result.unfixable == ()

    def test_dedupes_managed_sourced_from_user_root(self, tmp_path):
        """If the user's main config sources the managed file, it appears once."""
        managed = _write(
            tmp_path / "managed.conf",
            "cursor:no_cursor_warps = true\n",
        )
        user_root = _write(
            tmp_path / "hyprland.conf",
            f"source = {managed}\n",
        )

        result = deprecations.scan(managed_path=managed, user_root_path=user_root)

        # Only the managed file has fixable content; user_root just sources it.
        assert len(result.files) == 1
        assert result.files[0].path == managed
        assert result.files[0].is_managed is True

    def test_flags_symlinks(self, tmp_path):
        """A symlinked target gets is_symlink=True so the UI can warn."""
        real = _write(tmp_path / "dotfiles" / "hyprland.conf", "cursor:no_cursor_warps = true\n")
        link = tmp_path / "hyprland.conf"
        link.symlink_to(real)

        result = deprecations.scan(
            managed_path=tmp_path / "managed.conf",  # missing — only the symlink matters
            user_root_path=link,
        )

        assert len(result.files) == 1
        assert result.files[0].is_symlink is True

    def test_unfixable_listed_separately(self, tmp_path):
        """``general:max_fps`` has no migration → appears in unfixable, not files."""
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        user_root = _write(
            tmp_path / "hyprland.conf",
            "general {\n    max_fps = 144\n}\n",
        )

        result = deprecations.scan(managed_path=managed, user_root_path=user_root)

        assert result.files == ()
        assert len(result.unfixable) >= 1
        assert any("max_fps" in str(rule.key) for rule in result.unfixable)

    def test_skips_lua_entrypoints(self, tmp_path):
        """Lua entrypoints are out of scope for Hyprlang migrations."""
        managed = _write(tmp_path / "managed.lua", "hl.config({})\n")
        user_root = _write(
            tmp_path / "hyprland.conf",
            "cursor:no_cursor_warps = true\n",
        )

        result = deprecations.scan(managed_path=managed, user_root_path=user_root)

        # User root still scanned; managed.lua skipped silently.
        assert len(result.files) == 1
        assert result.files[0].path == user_root

    def test_hyprland_version_filters_newer_deprecations(self, tmp_path):
        """A 0.49 user should not see 0.53 / 0.55 deprecations."""
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        user_root = _write(
            tmp_path / "hyprland.conf",
            "windowrulev2 = float, class:^(firefox)$\ndwindle {\n    pseudotile = 1\n}\n",
        )

        # Without the gate, both rules surface.
        unfiltered = deprecations.scan(managed_path=managed, user_root_path=user_root)
        keys = {r.key for plan in unfiltered.files for r in plan.rules} | {
            r.key for r in unfiltered.unfixable
        }
        assert "windowrulev2" in keys
        assert "dwindle:pseudotile" in keys

        # With hyprland_version=0.49.0, both should be filtered out — leaving
        # nothing for this fixture.
        filtered = deprecations.scan(
            managed_path=managed,
            user_root_path=user_root,
            hyprland_version="0.49.0",
        )
        assert filtered.has_fixable is False
        assert filtered.unfixable == ()

    def test_hyprland_version_keeps_applicable_deprecations(self, tmp_path):
        """Deprecations from versions <= running version stay visible."""
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        # exec_once (0.33) and cursor:no_cursor_warps (0.41) both pre-date 0.49.
        user_root = _write(
            tmp_path / "hyprland.conf",
            "exec_once = waybar\ncursor {\n    no_cursor_warps = true\n}\n",
        )

        result = deprecations.scan(
            managed_path=managed,
            user_root_path=user_root,
            hyprland_version="0.49.0",
        )
        # Both lines have automatic migrations — one FilePlan, two rules.
        assert len(result.files) == 1
        rule_keys = {r.key for r in result.files[0].rules}
        assert "exec_once" in rule_keys
        assert "cursor:no_cursor_warps" in rule_keys

    def test_fingerprint_changes_with_content(self, tmp_path):
        """Adding a new deprecation produces a different fingerprint."""
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        user_root = _write(tmp_path / "hyprland.conf", "cursor:no_cursor_warps = true\n")
        first = deprecations.scan(managed_path=managed, user_root_path=user_root).fingerprint()

        user_root.write_text("cursor:no_cursor_warps = true\ngeneral:max_fps = 144\n")
        second = deprecations.scan(managed_path=managed, user_root_path=user_root).fingerprint()

        assert first != second


class TestApplyToFile:
    def _make_plan(self, tmp_path: Path) -> deprecations.FilePlan:
        managed = _write(tmp_path / "managed.conf", "general:gaps_in = 5\n")
        user_root = _write(tmp_path / "hyprland.conf", "cursor:no_cursor_warps = true\n")
        result = deprecations.scan(managed_path=managed, user_root_path=user_root)
        assert len(result.files) == 1
        return result.files[0]

    def test_writes_migrated_content(self, tmp_path):
        plan = self._make_plan(tmp_path)

        outcome = deprecations.apply_to_file(plan)

        assert outcome.success
        assert "no_warps" in plan.path.read_text()
        assert "no_cursor_warps" not in plan.path.read_text()

    def test_writes_backup_beside_original(self, tmp_path):
        plan = self._make_plan(tmp_path)
        original_text = plan.path.read_text()

        outcome = deprecations.apply_to_file(plan)

        assert outcome.backup_path is not None
        assert outcome.backup_path.exists()
        assert outcome.backup_path.read_text() == original_text
        assert outcome.backup_path.name.startswith("hyprland.conf.hyprmod-bak-")

    def test_backup_skipped_when_disabled(self, tmp_path):
        plan = self._make_plan(tmp_path)

        outcome = deprecations.apply_to_file(plan, backup=False)

        assert outcome.success
        assert outcome.backup_path is None

    def test_idempotent_after_apply(self, tmp_path):
        plan = self._make_plan(tmp_path)

        deprecations.apply_to_file(plan)
        # Re-scan should find nothing — the file is already migrated.
        managed = tmp_path / "managed.conf"
        rescan = deprecations.scan(managed_path=managed, user_root_path=plan.path)
        assert rescan.has_fixable is False

    def test_reports_failure_when_parent_is_not_a_directory(self, tmp_path):
        """Writing under a path whose parent is a regular file surfaces as a failed ApplyResult."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        bad_plan = deprecations.FilePlan(
            path=blocker / "hyprland.conf",
            is_managed=False,
            is_symlink=False,
            original="",
            migrated="cursor:no_warps = true\n",
            rules=(),
        )

        outcome = deprecations.apply_to_file(bad_plan, backup=False)

        assert outcome.success is False
        assert outcome.error
