"""Before a release builds anything: the tag is v<version> of all three packages, the version
is 0.x, and CHANGELOG.md has a release-please heading for it.

The versions live in the two `__init__.py` files and in the runtime's package.json.

    python scripts/check_release.py v0.1.0   # a pushed tag
    python scripts/check_release.py          # a manual run: the version prumi has

Exits 1 and says what to fix when something doesn't match.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Package name → the file that holds its version. All three are released together.
PACKAGES = {
    "prumi": "packages/prumi-core/src/prumi/__init__.py",
    "prumi-adapter-dbt": "packages/prumi-adapter-dbt/src/prumi_adapter_dbt/__init__.py",
    "prumi-runtime": "packages/prumi-runtime/package.json",
}


def _read_version(file: str) -> str:
    text = (REPO / file).read_text(encoding="utf-8")
    if file.endswith(".json"):
        return json.loads(text)["version"]
    found = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if found is None:
        raise SystemExit(f"check_release: no __version__ in {file}.")
    return found.group(1)


def problems(tag: str | None) -> list[str]:
    versions = {name: _read_version(file) for name, file in PACKAGES.items()}
    if tag is None:
        version = versions["prumi"]
    elif tag.startswith("v"):
        version = tag.removeprefix("v")
    else:
        return [f"The tag {tag} isn't v<version>, e.g. v{versions['prumi']}."]
    found = [
        f"{name} is version {have} in {PACKAGES[name]}, not {version}."
        for name, have in versions.items()
        if have != version
    ]
    # Releases stay 0.x until the maintainer decides otherwise.
    if version.split(".")[0] != "0":
        found.append(f"Version {version} is 1.0 or above; Prumi releases stay 0.x.")
    heading = re.compile(
        rf"^## \[{re.escape(version)}\]\(\S+\) \((\d{{4}}-\d{{2}}-\d{{2}})\)\s*$", re.M
    )
    match = heading.search((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    if match is None or not _is_date(match.group(1)):
        found.append(
            f"CHANGELOG.md has no heading for {version}: "
            f"release-please writes `## [{version}](link) (YYYY-MM-DD)`."
        )
    return found


def _is_date(text: str) -> bool:
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is not None


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: check_release.py [v<version>]", file=sys.stderr)
        return 2
    found = problems(argv[0] if argv else None)
    for problem in found:
        print(f"check_release: {problem}", file=sys.stderr)
    if not found:
        print("check_release: the tag, all three versions and CHANGELOG.md match.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
