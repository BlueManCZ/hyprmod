#!/usr/bin/env bash
# nix_flake_maintenance.sh — Update nix/hyprmod.nix and flake.lock for a new release.
#
# Usage:
#   ./scripts/nix_flake_maintenance.sh [--dry-run] <new_version>
#
# Options:
#   --dry-run   Show what would change — fetches real hashes and prints a
#               coloured diff of nix/hyprmod.nix — without writing any files
#               or running nix build / nix flake update.
#
# Requirements: git, nix (2.0+), python3 (3.11+ for tomllib), jq
# Must be run from the repository root.

set -eo pipefail

# ─── colour helpers ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

sep()     { printf '%s\n' "────────────────────────────────────────────────────────────"; }
info()    { printf "\n  ${CYAN}[%s]${RESET} %s\n" "${1}" "${2}"; }
ok()      { printf "      ${GREEN}✓${RESET}  %s\n" "${1}"; }
skip()    { printf "      ${DIM}·  %s${RESET}\n" "${1}"; }
warn()    { printf "      ${YELLOW}!${RESET}  %s\n" "${1}"; }
die()     { printf "\n  ${RED}✗${RESET}  %s\n\n" "${1}" >&2; exit 1; }

# ─── argument parsing ────────────────────────────────────────────────────────────
DRY_RUN=false
NEW_VERSION=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --*)       die "Unknown option: ${arg}" ;;
        *)         NEW_VERSION="$arg" ;;
    esac
done

if [[ -z "$NEW_VERSION" ]]; then
    printf "\n  Usage: %s [--dry-run] <new_version>\n" "$0"
    printf "  Example: %s 0.5.0\n" "$0"
    printf "           %s --dry-run 0.5.0\n\n" "$0"
    exit 1
fi

# Basic version format check
[[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "Version must be in X.Y.Z format (got: ${NEW_VERSION})"

NEW_TAG="v${NEW_VERSION}"

# ─── banner ──────────────────────────────────────────────────────────────────────
echo ""
sep
if $DRY_RUN; then
    printf "  ${BOLD}HyprMod Nix maintenance  →  ${NEW_TAG}${RESET}  ${YELLOW}[dry run — no files will be written]${RESET}\n"
else
    printf "  ${BOLD}HyprMod Nix maintenance  →  ${NEW_TAG}${RESET}\n"
fi
sep

# ─── phase 0: prerequisites ──────────────────────────────────────────────────────
info "0/4" "Checking prerequisites"

[[ -f "nix/hyprmod.nix" ]] \
    || die "nix/hyprmod.nix not found — run this script from the repository root."

for tool in git nix python3 jq; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool not found on PATH: ${tool}"
done
ok "git, nix, python3, jq found"

# Detect Nix version to select the hash-fetching method.
# nix store prefetch-file --json is available from Nix 2.19+; older installs
# fall back to nix-prefetch-url + nix hash to-sri.
NIX_VERSION_STR=$(nix --version | grep -oP '\d+\.\d+\.\d+' | head -1)
NIX_MAJOR=$(echo "$NIX_VERSION_STR" | cut -d. -f1)
NIX_MINOR=$(echo "$NIX_VERSION_STR" | cut -d. -f2)
USE_NEW_PREFETCH=false
if [[ "$NIX_MAJOR" -gt 2 ]] || { [[ "$NIX_MAJOR" -eq 2 ]] && [[ "$NIX_MINOR" -ge 19 ]]; }; then
    USE_NEW_PREFETCH=true
fi
ok "Nix ${NIX_VERSION_STR} (hash method: $($USE_NEW_PREFETCH && echo 'nix store prefetch-file --json' || echo 'nix-prefetch-url fallback'))"

# Fetch tags silently so a freshly pushed tag is visible, then confirm it exists.
git fetch --tags --quiet 2>/dev/null || true
git rev-parse "${NEW_TAG}" >/dev/null 2>&1 \
    || die "Tag ${NEW_TAG} not found in git. Make sure the release is tagged upstream and run git fetch."
ok "Tag ${NEW_TAG} found"

# ─── phase 1: version comparison ────────────────────────────────────────────────
info "1/4" "Comparing dep versions"

# Write the pyproject.toml at the new tag to a temp file to avoid the
# pipe-vs-heredoc stdin conflict ("cmd | python3 - <<'EOF'" causes the heredoc
# to win and the pipe to be silently dropped).
_TMPTOML=$(mktemp --suffix=.toml)
trap 'rm -f "$_TMPTOML"' EXIT
git show "${NEW_TAG}:pyproject.toml" > "$_TMPTOML"

# A single Python script handles both reads and the semantic version comparison.
#
# For each hyprland-* dep the comparison rule is:
#   new_minimum > current_pin  →  must update (pyproject.toml bumped the floor)
#   new_minimum ≤ current_pin  →  current pin already satisfies the new release
#
# This avoids false positives where the pin (e.g. 0.9.12) exceeds the lower
# bound in pyproject.toml (e.g. >=0.9.10) and no actual change is required.
#
# Output format per line:  STATUS:PNAME:CURRENT_PIN:NEW_PIN
#   CHANGED:hyprland-config:0.9.12:0.9.15
#   SAME:hyprland-socket:0.12.2:0.12.2
#   HYPRMOD:0.4.0:0.5.0
COMPARISON=$(python3 - "$_TMPTOML" "$NEW_VERSION" <<'PYEOF'
import re, sys, tomllib

NIX_FILE    = "nix/hyprmod.nix"
TOML_FILE   = sys.argv[1]
new_hyprmod = sys.argv[2]

HYPRLAND_DEPS = [
    "hyprland-socket",
    "hyprland-config",
    "hyprland-schema",
    "hyprland-monitors",
    "hyprland-state",
]

def ver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))

# ── current pins from nix/hyprmod.nix ─────────────────────────────────────────
content = open(NIX_FILE).read()
current = {}
for dep in HYPRLAND_DEPS:
    m = re.search(
        r'pname\s*=\s*"' + re.escape(dep) + r'";\s*\n\s*version\s*=\s*"([^"]+)"',
        content,
    )
    if not m:
        print(f"ERROR: could not parse pinned version for {dep}", file=sys.stderr)
        sys.exit(1)
    current[dep] = m.group(1)

m = re.search(r'pname\s*=\s*"hyprmod";\s*\n\s*version\s*=\s*"([^"]+)"', content)
if not m:
    print("ERROR: could not parse hyprmod version", file=sys.stderr)
    sys.exit(1)
current_hyprmod = m.group(1)

# ── minimum required versions from pyproject.toml at the new tag ──────────────
data     = tomllib.loads(open(TOML_FILE).read())
minimums = {}
for dep in data["project"]["dependencies"]:
    dep = dep.strip()
    if ">=" in dep:
        name, ver = dep.split(">=", 1)
        name = name.strip(); ver = ver.strip()
        if name.startswith("hyprland-"):
            minimums[name] = ver

# ── emit comparison lines ──────────────────────────────────────────────────────
for dep in HYPRLAND_DEPS:
    cur = current[dep]
    new_min = minimums.get(dep, cur)
    if ver_tuple(new_min) > ver_tuple(cur):
        print(f"CHANGED:{dep}:{cur}:{new_min}")
    else:
        print(f"SAME:{dep}:{cur}:{cur}")   # keep current pin

# hyprmod version: simple string comparison (no semantic logic needed here)
if new_hyprmod != current_hyprmod:
    print(f"HYPRMOD:{current_hyprmod}:{new_hyprmod}")
else:
    print(f"HYPRMOD_SAME:{current_hyprmod}:{new_hyprmod}")
PYEOF
)

# Parse the structured comparison output, print the table, collect changed deps.
HYPRLAND_DEPS=("hyprland-socket" "hyprland-config" "hyprland-schema" "hyprland-monitors" "hyprland-state")
declare -A CHANGED_DEPS   # hyprland-* packages that need a new hash
HYPRMOD_CHANGED=false

echo ""
while IFS=: read -r status pname cur new; do
    case "$status" in
        CHANGED)
            printf "      ${GREEN}%-22s${RESET}  %-10s  →  ${BOLD}%-10s${RESET}  ${GREEN}changed${RESET}\n" \
                "$pname" "$cur" "$new"
            CHANGED_DEPS["$pname"]="$new"
            ;;
        SAME)
            printf "      ${DIM}%-22s  %-10s  unchanged${RESET}\n" "$pname" "$cur"
            ;;
        HYPRMOD)
            # pname holds old version, cur holds new version in this layout
            printf "      ${GREEN}%-22s${RESET}  %-10s  →  ${BOLD}%-10s${RESET}  ${GREEN}changed${RESET}\n" \
                "hyprmod" "$pname" "$cur"
            HYPRMOD_CHANGED=true
            ;;
        HYPRMOD_SAME)
            printf "      ${DIM}%-22s  %-10s  unchanged${RESET}\n" "hyprmod" "$pname"
            ;;
    esac
done <<< "$COMPARISON"
echo ""

if [[ ${#CHANGED_DEPS[@]} -eq 0 ]] && ! $HYPRMOD_CHANGED; then
    warn "No version changes detected — nothing to update."
    echo ""
    exit 0
fi

# ─── phase 2: fetch hashes ───────────────────────────────────────────────────────
info "2/4" "Fetching hashes for changed deps"
echo ""

# Tries the fast JSON method (Nix 2.19+) first; falls back to nix-prefetch-url.
fetch_hash() {
    local repo="$1" version="$2"
    local url="https://github.com/BlueManCZ/${repo}/archive/refs/tags/v${version}.tar.gz"
    local hash=""

    if $USE_NEW_PREFETCH; then
        hash=$(
            nix store prefetch-file --hash-type sha256 --unpack --json "$url" 2>/dev/null \
            | jq -r '.hash' 2>/dev/null \
            || true
        )
    fi

    # Fall back if the primary method failed or returned null/empty.
    if [[ -z "$hash" || "$hash" == "null" ]]; then
        local b32
        b32=$(nix-prefetch-url --unpack --type sha256 "$url" 2>/dev/null)
        hash=$(nix hash to-sri --type sha256 "$b32" 2>/dev/null)
    fi

    [[ -n "$hash" && "$hash" != "null" ]] \
        || die "Failed to fetch hash for ${repo} v${version}. Confirm the tag exists at github.com/BlueManCZ/${repo}."

    echo "$hash"
}

declare -A NEW_HASHES

if [[ ${#CHANGED_DEPS[@]} -eq 0 ]]; then
    skip "No hyprland-* deps changed — skipping hash fetch"
else
    for dep in "${!CHANGED_DEPS[@]}"; do
        new_ver="${CHANGED_DEPS[$dep]}"
        printf "      %-22s  %s  ... " "$dep" "$new_ver"
        hash=$(fetch_hash "$dep" "$new_ver")
        NEW_HASHES["$dep"]="$hash"
        printf "${GREEN}%s${RESET}\n" "$hash"
    done
fi
echo ""

# ─── phase 3: rewrite nix/hyprmod.nix ───────────────────────────────────────────
info "3/4" "$($DRY_RUN && echo 'Previewing changes to' || echo 'Rewriting') nix/hyprmod.nix"
echo ""

# Export update data for the embedded Python rewriter via env vars — avoids
# all quoting issues that arise from building shell-to-Python inline code.
export REWRITER_HYPRMOD_VERSION="$NEW_VERSION"
export REWRITER_DRY_RUN="$DRY_RUN"

for dep in "${!CHANGED_DEPS[@]}"; do
    key="${dep//-/_}"   # hyprland-socket  →  hyprland_socket
    key="${key^^}"       #                  →  HYPRLAND_SOCKET
    export "REWRITER_${key}_VER=${CHANGED_DEPS[$dep]}"
    export "REWRITER_${key}_HASH=${NEW_HASHES[$dep]}"
done

python3 - <<'PYEOF'
import difflib, os, re, sys

NIX_FILE = "nix/hyprmod.nix"

new_ver = os.environ["REWRITER_HYPRMOD_VERSION"]
dry_run = os.environ["REWRITER_DRY_RUN"].lower() == "true"

HYPRLAND_DEPS = [
    "hyprland-socket",
    "hyprland-config",
    "hyprland-schema",
    "hyprland-monitors",
    "hyprland-state",
]

# Collect which deps have updates from env vars.
updates = {}   # { pname: {version, hash} }
for dep in HYPRLAND_DEPS:
    key     = dep.upper().replace("-", "_")
    ver_env = f"REWRITER_{key}_VER"
    if ver_env in os.environ:
        updates[dep] = {
            "version": os.environ[ver_env],
            "hash":    os.environ[f"REWRITER_{key}_HASH"],
        }

content  = open(NIX_FILE).read()
original = content


def update_dep_block(text: str, pname: str, new_version: str, new_hash: str) -> str:
    """
    Locate the fetchFromGitHub block for `pname` and replace version, tag,
    and hash in one pass.  The boundary marker is the `build-system` attribute
    that immediately follows the src block — consistent for all five deps.
    """
    start = text.find(f'pname = "{pname}"')
    if start == -1:
        print(f"ERROR: could not locate pname = \"{pname}\" in {NIX_FILE}", file=sys.stderr)
        sys.exit(1)
    boundary = text.find("build-system", start)
    if boundary == -1:
        print(f"ERROR: could not find build-system boundary after {pname}", file=sys.stderr)
        sys.exit(1)
    block = text[start:boundary]
    block = re.sub(r'(version\s*=\s*")[^"]+(")', rf'\g<1>{new_version}\2', block, count=1)
    block = re.sub(r'(tag\s*=\s*"v)[^"]+(")',    rf'\g<1>{new_version}\2', block, count=1)
    block = re.sub(r'(hash\s*=\s*")[^"]+(")',     rf'\g<1>{new_hash}\2',   block, count=1)
    return text[:start] + block + text[boundary:]


# Apply each dep update.
for pname, info in updates.items():
    content = update_dep_block(content, pname, info["version"], info["hash"])

# Update the hyprmod main version.  pname = "hyprmod" appears exactly once
# (in buildPythonApplication); replace the very next version = "..." line.
idx = content.find('pname = "hyprmod"')
if idx == -1:
    print(f"ERROR: could not locate hyprmod pname in {NIX_FILE}", file=sys.stderr)
    sys.exit(1)
tail    = content[idx:]
tail    = re.sub(r'(version\s*=\s*")[^"]+(")', rf'\g<1>{new_ver}\2', tail, count=1)
content = content[:idx] + tail

# Update the "Last updated" comment near the top of the file.
content = re.sub(
    r'(# Last updated for hyprmod v)\d+\.\d+\.\d+',
    rf'\g<1>{new_ver}',
    content,
    count=1,
)

if dry_run:
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"{NIX_FILE} (original)",
        tofile=f"{NIX_FILE} (updated)",
    ))
    if not diff:
        print("      (no changes to nix/hyprmod.nix)")
    else:
        RED   = '\033[0;31m'
        GREEN = '\033[0;32m'
        CYAN  = '\033[0;36m'
        DIM   = '\033[2m'
        RESET = '\033[0m'
        for line in diff:
            if line.startswith("+++") or line.startswith("---"):
                print(f"      {DIM}{line}{RESET}", end="")
            elif line.startswith("@@"):
                print(f"      {CYAN}{line}{RESET}", end="")
            elif line.startswith("+"):
                print(f"      {GREEN}{line}{RESET}", end="")
            elif line.startswith("-"):
                print(f"      {RED}{line}{RESET}", end="")
            else:
                print(f"      {line}", end="")
else:
    open(NIX_FILE, "w").write(content)
    print(f"      nix/hyprmod.nix written")
PYEOF

echo ""

# ─── phase 4: nix operations ─────────────────────────────────────────────────────
info "4/4" "Running Nix operations"
echo ""

if $DRY_RUN; then
    warn "[dry run] Would run: nix flake update"
    warn "[dry run] Would run: nix build .#hyprmod"
    warn "[dry run] Would run: overlay sanity check"
    echo ""
else
    # nix flake update prints its own status lines to stderr; let them show.
    echo "      $ nix flake update"
    nix flake update
    ok "flake.lock updated"
    echo ""

    echo "      $ nix build .#hyprmod"
    if nix build .#hyprmod --no-link; then
        ok "build passed"
    else
        die "nix build .#hyprmod failed — review the output above before committing."
    fi
    echo ""

    # Overlay sanity check: builds hyprmod via pkgs.extend overlay to confirm
    # the overlay wiring is correct.  Uses the flake's own nixpkgs input to
    # avoid depending on NIX_PATH being set in the calling environment.
    CURRENT_SYSTEM=$(nix eval --impure --raw --expr 'builtins.currentSystem')
    OVERLAY_EXPR="
      let
        flake   = builtins.getFlake (builtins.toString ./.);
        pkgs    = flake.inputs.nixpkgs.legacyPackages.${CURRENT_SYSTEM};
        overlay = flake.overlays.default;
      in (pkgs.extend overlay).hyprmod
    "
    echo "      $ nix build (overlay sanity check)"
    if nix build --no-link --impure --expr "$OVERLAY_EXPR"; then
        ok "overlay check passed"
    else
        die "overlay check failed — review the output above before committing."
    fi
    echo ""
fi

# ─── done ─────────────────────────────────────────────────────────────────────────
sep

if $DRY_RUN; then
    printf "  ${BOLD}Dry run complete — no files were modified.${RESET}\n"
else
    # Build a concise list of changed hyprland-* deps for the commit body.
    CHANGED_DEP_LIST=""
    for dep in "${HYPRLAND_DEPS[@]}"; do
        if [[ -v CHANGED_DEPS["$dep"] ]]; then
            CHANGED_DEP_LIST="${CHANGED_DEP_LIST} ${dep},"
        fi
    done
    CHANGED_DEP_LIST="${CHANGED_DEP_LIST%,}"  # strip trailing comma

    printf "  ${BOLD}Done.${RESET} Review the changes, then run:\n"
    echo ""
    printf "    ${BOLD}git add nix/hyprmod.nix flake.lock${RESET}\n"
    printf "    ${BOLD}git commit -m \"nix: update to ${NEW_TAG}\"${RESET}\n"
    echo ""
    printf "  Commit body:\n\n"
    if [[ -n "$CHANGED_DEP_LIST" ]]; then
        printf "    Bump hyprmod to ${NEW_TAG} and update hashes for${CHANGED_DEP_LIST}.\n"
    else
        printf "    Bump hyprmod to ${NEW_TAG}.\n"
    fi
    printf "    Regenerate flake.lock.\n"
fi

sep
echo ""
