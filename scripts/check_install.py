"""Install Prumi from its built packages the way the README says, and start it on the demo.

    uv run python scripts/check_install.py [--with-deps] [--dist DIR]

Builds `prumi` and `prumi-adapter-dbt` (or takes the files already built in --dist), runs
`uv tool install "prumi[dbt]"` into a temporary tool directory, with both packages taken from
the built files and never from PyPI, then `prumi install-chromium`, `prumi serve` on the demo
project and `GET /`, and `twine check` on the built files so their READMEs render on PyPI.
Nothing is installed into your own uv tools, and nothing is uploaded.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "examples" / "demo-dbt-project"
PACKAGES = ("prumi", "prumi-adapter-dbt")
START_TIMEOUT_S = 120


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--with-deps",
        action="store_true",
        help="pass --with-deps to `prumi install-chromium` (CI: installs system libraries)",
    )
    parser.add_argument(
        "--dist", type=Path, help="check the files already built here instead of building"
    )
    args = parser.parse_args(argv)
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("check_install: needs `uv` on PATH.")
    # The workspace's environment must not leak into the tool install or the server.
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT")}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="prumi-install-check-") as tmp:
        tmp = Path(tmp)
        dist = args.dist.resolve() if args.dist else _build(uv, tmp / "dist", env)
        prumi = _install(uv, dist, tmp, env)
        _step("prumi install-chromium")
        _run([str(prumi), "install-chromium", *(["--with-deps"] if args.with_deps else [])], env)
        _serve_demo(uv, prumi, tmp, env)
        _step("twine check")
        files = sorted(str(p) for p in dist.iterdir() if p.name.endswith((".whl", ".tar.gz")))
        _run([uv, "run", "--locked", "twine", "check", "--strict", *files], env, cwd=REPO)
    print(f"check_install: passed in {time.monotonic() - started:.0f} s")
    return 0


def _build(uv: str, out: Path, env: dict[str, str]) -> Path:
    for package in PACKAGES:
        _step(f"uv build --package {package}")
        _run([uv, "build", "--package", package, "--out-dir", str(out), "--quiet"], env, cwd=REPO)
    return out


def _install(uv: str, dist: Path, tmp: Path, env: dict[str, str]) -> Path:
    wheels = {}
    for package in PACKAGES:
        found = sorted(dist.glob(f"{package.replace('-', '_')}-*-py3-none-any.whl"))
        if len(found) != 1:
            sys.exit(f"check_install: expected one {package} wheel in {dist}, found {len(found)}")
        wheels[package] = found[0]
    # The README's command, with both names pinned to the built wheels: an override replaces
    # every requirement on its package, so the index serves only the other dependencies. It
    # replaces the requested extras too, hence `prumi[dbt]` here.
    overrides = tmp / "overrides.txt"
    overrides.write_text(
        f"prumi[dbt] @ {wheels['prumi'].as_uri()}\n"
        f"prumi-adapter-dbt[duckdb] @ {wheels['prumi-adapter-dbt'].as_uri()}\n"
    )
    tool_env = env | {
        "UV_TOOL_DIR": str(tmp / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp / "bin"),
        "PATH": os.pathsep.join([str(tmp / "bin"), env.get("PATH", "")]),
    }
    _step('uv tool install "prumi[dbt]"')
    _run([uv, "tool", "install", "prumi[dbt]", "--overrides", str(overrides)], tool_env)
    for package, wheel in wheels.items():
        dist_info = f"**/{package.replace('-', '_')}-*.dist-info/direct_url.json"
        sources = [p.read_text() for p in (tmp / "tools" / "prumi").glob(dist_info)]
        if len(sources) != 1 or wheel.as_uri() not in sources[0]:
            sys.exit(f"check_install: {package} wasn't installed from {wheel}")
    prumi = tmp / "bin" / ("prumi.exe" if os.name == "nt" else "prumi")
    if not prumi.is_file():
        sys.exit(f"check_install: `uv tool install` made no prumi command in {prumi.parent}")
    return prumi


def _serve_demo(uv: str, prumi: Path, tmp: Path, env: dict[str, str]) -> None:
    if not all((DEMO / p).is_file() for p in ("demo.duckdb", "target/semantic_manifest.json")):
        _step("build the demo project")
        _run([uv, "run", "--project", str(DEMO), "python", str(DEMO / "build.py")], env, cwd=REPO)
    port = _free_port()
    config = tmp / "prumi.toml"
    # The demo's own source, on a free port with a data dir that goes away afterwards.
    config.write_text(
        f"""
[server]
port = {port}
data_dir = "data"
as_of = "2026-10-15"

[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "{(DEMO / "target" / "semantic_manifest.json").as_posix()}"

[source.dbt.connection]
type = "duckdb"
path = "{(DEMO / "demo.duckdb").as_posix()}"
"""
    )
    _step(f"prumi serve on the demo, GET http://127.0.0.1:{port}/")
    server = subprocess.Popen([str(prumi), "serve", "--config", str(config)], env=env)
    try:
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            if server.poll() is not None:
                sys.exit(f"check_install: `prumi serve` exited with {server.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            if time.monotonic() > deadline:
                sys.exit(f"check_install: no answer from `prumi serve` in {START_TIMEOUT_S} s")
            time.sleep(0.5)
    finally:
        server.terminate()
        server.wait(30)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _step(name: str) -> None:
    print(f"check_install: {name}", flush=True)


def _run(command: list[str], env: dict[str, str], cwd: Path | None = None) -> None:
    result = subprocess.run(command, env=env, cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"check_install: failed ({result.returncode}): {' '.join(command)}")


if __name__ == "__main__":
    sys.exit(main())
