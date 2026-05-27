"""Detection and application of fixable Hyprland config deprecations.

Pure logic — no GTK, no GIO. The UI lives in ``hyprmod.ui.deprecation_*``.

The contract for callers:

1. :func:`scan` walks hyprmod's managed config and the user's main
   ``hyprland.conf`` (with sourced fragments), runs
   :func:`hyprland_config.migrate` in memory, and returns a
   :class:`ScanResult` listing files that would change on disk plus
   deprecations that have no automatic fix.
2. :func:`apply_to_file` writes a timestamped ``.hyprmod-bak-<ts>``
   beside the original and then atomically replaces the file with the
   migrated text.

Silent rewrites are deliberately *not* the contract: callers must
present each plan to the user and apply on confirmation.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from hyprland_config import (
    ConfigDeprecation,
    Document,
    ParseError,
    SourceCycleError,
    atomic_write,
    check_deprecated,
    load_any,
    migrate,
    serialize_any,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilePlan:
    """A single file's worth of migration work."""

    path: Path
    is_managed: bool
    is_symlink: bool
    original: str
    migrated: str
    # All deprecations detected in this file — both the ones migrate() fixed
    # and any leftover unfixable ones in the same file. Useful for showing
    # users the full picture in the dialog.
    rules: tuple[ConfigDeprecation, ...]


@dataclass(frozen=True)
class ScanResult:
    """Outcome of scanning the user's config tree for fixable deprecations."""

    files: tuple[FilePlan, ...] = ()
    # Deprecations in files where migrate() couldn't change anything —
    # surfaced separately so the user knows these need hand-editing.
    unfixable: tuple[ConfigDeprecation, ...] = ()

    @property
    def has_fixable(self) -> bool:
        return bool(self.files)

    def fingerprint(self) -> str:
        """Stable hash identifying this scan result.

        Used by the dismissed-state machinery: a banner the user dismissed
        for fingerprint X should re-appear if the next scan yields a
        different fingerprint (new deprecation showed up).
        """
        parts: list[str] = []
        for plan in sorted(self.files, key=lambda p: str(p.path)):
            keys = ",".join(sorted({r.key or r.message for r in plan.rules}))
            parts.append(f"{plan.path}:{keys}")
        for rule in sorted(self.unfixable, key=lambda r: (r.source_name, r.lineno)):
            parts.append(f"unfixable:{rule.source_name}:{rule.lineno}:{rule.key or rule.message}")
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of writing a single :class:`FilePlan` to disk."""

    path: Path
    success: bool
    error: str = ""
    backup_path: Path | None = None


def scan(
    *,
    managed_path: Path,
    user_root_path: Path,
    hyprland_version: str | None = None,
) -> ScanResult:
    """Look for fixable Hyprland-config deprecations across user-facing files.

    Walks *managed_path* (hyprmod's owned file) and *user_root_path* (the
    user's hyprland.conf entrypoint, with sourced fragments). Returns a
    :class:`ScanResult` listing files whose serialized form would change
    under :func:`hyprland_config.migrate`, plus any leftover deprecations
    that have no automatic fix.

    When *hyprland_version* is set, rules whose ``version_deprecated`` is
    newer than the running version are filtered out (both for the warning
    list and for the in-memory migrate pass) — a user on Hyprland 0.49
    should not see a v0.53 ``windowrulev2`` rename offered to them.
    """
    files: list[FilePlan] = []
    seen: set[Path] = set()
    unfixable: list[ConfigDeprecation] = []

    for root_path in (managed_path, user_root_path):
        plans, leftover = _scan_root(
            root_path,
            managed_path=managed_path,
            seen=seen,
            hyprland_version=hyprland_version,
        )
        files.extend(plans)
        unfixable.extend(leftover)

    return ScanResult(files=tuple(files), unfixable=tuple(unfixable))


def apply_to_file(plan: FilePlan, *, backup: bool = True) -> ApplyResult:
    """Write *plan*.migrated to *plan*.path, with an optional backup file.

    The backup lands in a ``.hyprmod-backups/`` directory next to the
    original so ``source = …/*`` globs don't pick it up. Returns a
    structured :class:`ApplyResult` rather than raising — the caller
    (dialog) wants to report per-file outcomes without unwinding the
    whole apply loop.
    """
    backup_path: Path | None = None
    try:
        if backup and plan.path.exists():
            backup_path = _write_backup(plan.path)
        atomic_write(plan.path, plan.migrated)
    except OSError as exc:
        log.warning("failed to apply migration to %s: %s", plan.path, exc)
        return ApplyResult(path=plan.path, success=False, error=str(exc), backup_path=backup_path)
    return ApplyResult(path=plan.path, success=True, backup_path=backup_path)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _scan_root(
    root_path: Path,
    *,
    managed_path: Path,
    seen: set[Path],
    hyprland_version: str | None = None,
) -> tuple[list[FilePlan], list[ConfigDeprecation]]:
    """Scan one entrypoint and its sourced sub-docs for fixable migrations.

    *seen* is mutated with the resolved paths of every file inspected so
    callers can dedupe across multiple roots (the managed config is often
    sourced from the user's main hyprland.conf — without deduping, the
    same file would surface twice).
    """
    if not root_path.exists():
        return [], []
    if root_path.suffix == ".lua":
        # Lua docs use a different AST; migration rules target Hyprlang.
        return [], []
    # Captured *before* load_any resolves the path — if the user's entrypoint
    # itself is a symlink (typical dotfiles setup), every file we reach via
    # it deserves the same "this is inside your symlink chain" warning.
    root_was_symlink = root_path.is_symlink()
    try:
        doc = load_any(root_path, follow_sources=True, lenient=True)
    except (OSError, ParseError, SourceCycleError) as exc:
        log.debug("skipping %s during deprecation scan: %s", root_path, exc)
        return [], []

    # 1) Snapshot every sub-doc's serialized form *before* migration so we
    # can diff against the post-migration form below.
    originals: dict[Path, str] = {}
    docs_by_path: dict[Path, Document] = {}
    for sub in doc.target_documents(True):
        if sub.path is None or sub.path.suffix == ".lua":
            continue
        resolved = _safe_resolve(sub.path)
        if resolved in seen:
            continue
        try:
            originals[sub.path] = serialize_any(sub, sub.path)
        except Exception as exc:  # noqa: BLE001 — defensive
            log.debug("failed to serialize %s pre-migration: %s", sub.path, exc)
            continue
        docs_by_path[sub.path] = sub

    if not originals:
        return [], []

    # 2) Collect deprecations *before* migration so the per-file rule list
    # captures everything the user might want to see, including rules that
    # migrate() will fix.
    rules_by_source: dict[str, list[ConfigDeprecation]] = {}
    for warning in check_deprecated(doc, hyprland_version=hyprland_version):
        rules_by_source.setdefault(warning.source_name, []).append(warning)

    # 3) Migrate in place across the whole tree, then re-serialize each
    # sub-doc and compare to its original snapshot. ``to_version`` mirrors
    # the version gate on check_deprecated: a migration whose target
    # syntax doesn't exist in the user's Hyprland would corrupt their
    # config if applied.
    migrate(doc, recursive=True, to_version=hyprland_version)

    plans: list[FilePlan] = []
    fixable_paths: set[Path] = set()
    managed_resolved = _safe_resolve(managed_path)
    for path, original in originals.items():
        sub = docs_by_path[path]
        try:
            new_text = serialize_any(sub, path)
        except Exception as exc:  # noqa: BLE001 — defensive
            log.debug("failed to serialize %s post-migration: %s", path, exc)
            continue
        if new_text == original:
            continue
        # ``migrate()`` also runs structural normalisations (windowrule
        # block-form / single-line → :class:`Rule` node) that re-serialize
        # to a canonical token order. That can change a file's text even
        # when nothing in it is actually deprecated — show a plan only
        # when the file carries at least one ``check_deprecated`` warning,
        # so cosmetic reorders don't trigger the migration banner.
        file_rules = rules_by_source.get(str(path), [])
        if not file_rules:
            continue
        fixable_paths.add(path)
        seen.add(_safe_resolve(path))
        plans.append(
            FilePlan(
                path=path,
                is_managed=_safe_resolve(path) == managed_resolved,
                is_symlink=root_was_symlink or path.is_symlink(),
                original=original,
                migrated=new_text,
                rules=tuple(file_rules),
            )
        )

    # 4) "Unfixable" = rules in files we *didn't* rewrite. Files we rewrote
    # may also have leftover unfixable rules, but those already appear in
    # the FilePlan's rules tuple — the user sees them as part of that file's
    # context rather than in a separate footer.
    leftover: list[ConfigDeprecation] = []
    for source_name, rules in rules_by_source.items():
        if Path(source_name) in fixable_paths:
            continue
        leftover.extend(rules)
        # Mark these files as seen so a subsequent root scan doesn't double-count.
        seen.add(_safe_resolve(Path(source_name)))

    return plans, leftover


def _safe_resolve(path: Path) -> Path:
    """Resolve symlinks, falling back to the raw path when the FS errors out."""
    try:
        return path.resolve()
    except OSError:
        return path


def _write_backup(path: Path) -> Path:
    """Copy *path* into a ``.hyprmod-backups/`` sibling directory.

    Using a dot-prefixed directory keeps backups out of ``source = …/*``
    globs that Hyprland expands when loading config fragments.
    """
    backup_dir = path.parent / ".hyprmod-backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f"{path.name}.{int(time.time())}"
    atomic_write(backup, path.read_text())
    return backup


__all__ = [
    "ApplyResult",
    "FilePlan",
    "ScanResult",
    "apply_to_file",
    "scan",
]
