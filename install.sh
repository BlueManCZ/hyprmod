#!/usr/bin/env sh
# Convenience installer for HyprMod.
#
# Default: detect uv or pipx (bootstrap uv from https://astral.sh/uv if
# neither is available), install or upgrade hyprmod, and register its
# desktop entry + icon under $XDG_DATA_HOME.
#
# Pass --uninstall to remove the desktop entry and uninstall the package.
#
# Override the install source with HYPRMOD_SOURCE, e.g.:
#   HYPRMOD_SOURCE=hyprmod sh install.sh   # once published to PyPI

set -eu

PACKAGE='hyprmod'
SOURCE="${HYPRMOD_SOURCE:-git+https://github.com/BlueManCZ/hyprmod.git}"
ACTION='install'

usage() {
    cat <<'EOF'
Usage: install.sh [--uninstall] [--help]

  (no args)       Install or upgrade hyprmod, then register desktop entry.
  --uninstall     Remove the desktop entry and uninstall hyprmod.

Environment:
  HYPRMOD_SOURCE  Override install source
                  (default: git+https://github.com/BlueManCZ/hyprmod.git)
EOF
}

for arg in "$@"; do
    case "$arg" in
        --uninstall) ACTION='uninstall' ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
    esac
done

err() { printf '%s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

bootstrap_uv() {
    err "Neither 'uv' nor 'pipx' is available."
    # Use /dev/tty so prompts work when this script is invoked via curl|sh.
    if [ ! -r /dev/tty ]; then
        err 'No terminal available for the bootstrap prompt.'
        err 'Install uv (https://astral.sh/uv) or pipx, then re-run this script.'
        exit 1
    fi
    printf 'Bootstrap uv from https://astral.sh/uv ? [Y/n] ' > /dev/tty
    read -r ans < /dev/tty
    case "$ans" in
        [Nn]*) err 'Aborted. Install uv or pipx, then re-run this script.'; exit 1 ;;
    esac
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck source=/dev/null
        . "$HOME/.local/bin/env"
    else
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    fi
    have uv || { err 'uv installation failed.'; exit 1; }
}

pick_installer() {
    if have uv; then echo uv; return; fi
    if have pipx; then echo pipx; return; fi
    bootstrap_uv
    echo uv
}

is_installed_uv() {
    uv tool list 2>/dev/null | awk '{print $1}' | grep -qx "$PACKAGE"
}

is_installed_pipx() {
    pipx list --short 2>/dev/null | awk '{print $1}' | grep -qx "$PACKAGE"
}

do_install() {
    INSTALLER="$(pick_installer)"
    case "$INSTALLER" in
        uv)
            if is_installed_uv; then
                echo ">> Upgrading $PACKAGE via uv tool..."
                uv tool upgrade "$PACKAGE"
            else
                echo ">> Installing $PACKAGE via uv tool ($SOURCE)..."
                uv tool install "$SOURCE"
            fi
            ;;
        pipx)
            if is_installed_pipx; then
                echo ">> Upgrading $PACKAGE via pipx..."
                pipx upgrade "$PACKAGE"
            else
                echo ">> Installing $PACKAGE via pipx ($SOURCE)..."
                pipx install "$SOURCE"
            fi
            ;;
    esac

    echo '>> Registering desktop entry and icon...'
    hyprmod --install

    echo
    echo '>> Done. Launch HyprMod from your app menu or run: hyprmod'
}

do_uninstall() {
    # Strip XDG entries first, while the binary still exists.
    if have hyprmod; then
        echo '>> Removing desktop entry and icon...'
        hyprmod --uninstall || true
    fi

    if have uv && is_installed_uv; then
        echo ">> Uninstalling $PACKAGE via uv tool..."
        uv tool uninstall "$PACKAGE"
    elif have pipx && is_installed_pipx; then
        echo ">> Uninstalling $PACKAGE via pipx..."
        pipx uninstall "$PACKAGE"
    else
        err "$PACKAGE doesn't appear to be installed via uv or pipx."
        err 'If it came from your distro package manager, remove it that way.'
        exit 1
    fi

    echo
    echo '>> Done.'
}

case "$ACTION" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
esac
