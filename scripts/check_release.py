"""Before a release builds anything: the tag is v<version> of both packages, and CHANGELOG.md
has a dated heading for that version.

    python scripts/check_release.py v0.1.0   # a pushed tag
    python scripts/check_release.py          # a manual run: the version both packages have

Exits 1 and says what to fix when something doesn't match.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Package name → its folder. Both are released together, always with the same version.
PACKAGES = {"prumi": "packages/prumi-core", "prumi-adapter-dbt": "packages/prumi-adapter-dbt"}


def problems(tag: str | None) -> list[str]:
    versions = {}
    for name, folder in PACKAGES.items():
        with (REPO / folder / "pyproject.toml").open("rb") as f:
            versions[name] = tomllib.load(f)["project"]["version"]
    if tag is None:
        version = versions["prumi"]
    elif tag.startswith("v"):
        version = tag.removeprefix("v")
    else:
        return [f"The tag {tag} isn't v<version>, e.g. v{versions['prumi']}."]
    found = [
        f"{name} is version {have} in {PACKAGES[name]}/pyproject.toml, not {version}."
        for name, have in versions.items()
        if have != version
    ]
    heading = re.compile(rf"^## {re.escape(version)} — (\S+)\s*$", re.M)
    match = heading.search((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    if match is None or not _is_date(match.group(1)):
        found.append(
            f"CHANGELOG.md has no dated heading for {version}: write `## {version} — YYYY-MM-DD`."
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
        print("check_release: the tag, both versions and CHANGELOG.md match.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
