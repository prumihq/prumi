"""Puts prumi-runtime's browser assets (runtime.js, shell.js, provenance.js, theme.css,
home.css) into the prumi-core wheel (prumi/_runtime/).

There is one source of each, in packages/prumi-runtime/src/. A wheel built in the workspace
reads them there; a wheel built from the sdist reads the copies the sdist carries.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ASSETS = ("runtime.js", "shell.js", "provenance.js", "theme.css", "home.css")


class RuntimeAssets(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        if version == "editable":
            return  # the workspace install serves them from packages/prumi-runtime/src
        root = Path(self.root)
        for name in ASSETS:
            workspace = root.parent / "prumi-runtime" / "src" / name
            in_sdist = root / "_runtime" / name
            source = workspace if workspace.is_file() else in_sdist
            if not source.is_file():
                raise FileNotFoundError(f"{name} not found in {workspace} or {in_sdist}")
            if self.target_name == "wheel":
                build_data["force_include"][str(source)] = f"prumi/_runtime/{name}"
            else:
                build_data["force_include"][str(source)] = f"_runtime/{name}"
