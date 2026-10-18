"""runtime.js and shell.js, which live in the prumi-runtime package (one source of each).

Built wheels carry a copy under prumi/_runtime/ (hatch force-include); in the workspace they
are read from packages/prumi-runtime/src/.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

ASSETS = ("runtime.js", "shell.js")
_WORKSPACE = Path(__file__).resolve().parents[4] / "prumi-runtime" / "src"


def read_asset(name: str) -> bytes:
    if name not in ASSETS:
        raise KeyError(name)
    bundled = files("prumi").joinpath("_runtime", name)
    if bundled.is_file():
        return bundled.read_bytes()
    return (_WORKSPACE / name).read_bytes()
