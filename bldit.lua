bldit_version = "0.1.3"
dependencies = {}

targets = {
    default = {
        -- install.sh builds + installs; pkgit still needs a build step to exist.
        build = function()
            return 0
        end,
        install = function()
            return os.execute(
                "UV_TOOL_BIN_DIR='" .. prefix .. "/bin' PIPX_BIN_DIR='" .. prefix .. "/bin' "
                .. "HYPRMOD_SOURCE=. sh install.sh"
            )
        end,
        uninstall = function()
            return os.execute(
                "UV_TOOL_BIN_DIR='" .. prefix .. "/bin' PIPX_BIN_DIR='" .. prefix .. "/bin' "
                .. "sh install.sh --uninstall"
            )
        end,
    },
    quiet = {
        build = function()
            return 0
        end,
        install = function()
            return os.execute(
                "UV_TOOL_BIN_DIR='" .. prefix .. "/bin' PIPX_BIN_DIR='" .. prefix .. "/bin' "
                .. "HYPRMOD_SOURCE=. sh install.sh >/dev/null 2>&1"
            )
        end,
        uninstall = function()
            return os.execute(
                "UV_TOOL_BIN_DIR='" .. prefix .. "/bin' PIPX_BIN_DIR='" .. prefix .. "/bin' "
                .. "sh install.sh --uninstall >/dev/null 2>&1"
            )
        end,
    },
}
