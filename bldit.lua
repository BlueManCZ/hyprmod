bldit_version = "0.1.3"
package_name = "hyprmod"
package_version = "1.0.0"
global_dependencies = {}
dependencies = {}

targets = {
    default = {
        build = function()
            os.execute("rm -rf dist")
            os.execute("python -m build --wheel")
            return 0
        end,
        install = function()
            os.execute("sudo python -m installer --overwrite-existing dist/*.whl")
            os.execute("hyprmod --install")
            return 0
        end,
        uninstall = function()
            os.execute("hyprmod --uninstall")
            os.execute("sudo rm -f /usr/local/bin/hyprmod /usr/bin/hyprmod")
            return 0
        end
    },
    quiet = {
        build = function()
            os.execute("rm -rf dist >/dev/null 2>&1")
            os.execute("python -m build --wheel >/dev/null 2>&1")
            return 0
        end,
        install = function()
            os.execute("sudo python -m installer --overwrite-existing dist/*.whl >/dev/null 2>&1")
            os.execute("hyprmod --install >/dev/null 2>&1")
            return 0
        end,
        uninstall = function()
            os.execute("hyprmod --uninstall >/dev/null 2>&1")
            os.execute("sudo rm -f /usr/local/bin/hyprmod /usr/bin/hyprmod >/dev/null 2>&1")
            return 0
        end
    }
}
