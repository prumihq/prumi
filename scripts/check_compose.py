"""Run the shipped docker compose install on the Postgres demo, and check what it promises.

    uv run python scripts/check_compose.py --image REF --warehouse-host HOST --warehouse-port N

`deploy/compose/docker-compose.yml` runs unchanged, with an override that only swaps in the
image under test and points `env_file` at a generated `.env` (CRLF line endings, the way an
editor on Windows saves it). The dbt project is a copy of the demo's `target-postgres/` in a
folder whose name has a space. The checks: the home page answers, a dashboard published over
MCP is certified with `docker compose exec`, it lands in the registry container and not in the
warehouse, a rewritten manifest and a recreated `target/` are reloaded through the mount, the
dashboard survives `down` and `up`, and a project without `target/` says what's missing.

The warehouse is the demo built by `examples/demo-dbt-project/build.py --target postgres`, read
as the `prumi_reader` role (its password from PRUMI_TEST_READER_PASSWORD, or the test suite's).
HOST is the warehouse as the container reaches it, e.g. host.docker.internal. Every container,
volume and folder this script creates is removed at the end, also when a check fails or on
Ctrl-C. Needs `docker` with the compose plugin on PATH; compose pulls the images it doesn't
find locally (the registry's postgres), and nothing is pushed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, NoReturn

from check_images import _free_port, _get, _publish
from mcp import Client

REPO = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO / "deploy" / "compose" / "docker-compose.yml"
DEMO_TARGET = REPO / "examples" / "demo-dbt-project" / "target-postgres"
PROJECT_NAME = "my project"  # the space is the point: paths with one must survive .env
START_TIMEOUT_S = 120
RELOAD_TIMEOUT_S = 15
CLEANUP_TIMEOUT_S = 120  # `down` and `logs` in cleanup and failure messages: never hang on them
MCP_TIMEOUT_S = 180
METRIC = "revenue"  # the metric whose description the reload checks change
READER = "prumi_reader"
READER_PASSWORD = os.environ.get("PRUMI_TEST_READER_PASSWORD", "prumi-reader-test")

# Run inside the prumi container, with the warehouse settings compose gave it: whether the
# registry's tables were created in the warehouse by mistake.
WAREHOUSE_TABLES = """
import os, psycopg
e = os.environ
with psycopg.connect(
    host=e["PRUMI__SOURCE__DBT__CONNECTION__HOST"],
    port=e["PRUMI__SOURCE__DBT__CONNECTION__PORT"],
    dbname=e["PRUMI__SOURCE__DBT__CONNECTION__DBNAME"],
    user=e["PRUMI__SOURCE__DBT__CONNECTION__USER"],
    password=e["WAREHOUSE_PASSWORD"],
) as conn:
    row = conn.execute(
        "select to_regclass('public.dashboards'), to_regclass('main.dashboards')"
    ).fetchone()
print(row)
"""


class Run:
    """One compose project: its files, its commands, and how to remove all of it."""

    def __init__(self, image: str, project_root: Path | None) -> None:
        # Passed as -p, which wins over the compose file's `name: prumi`: each run gets its
        # own containers and volume, and never touches a real install's on this machine.
        self.name = f"prumi-check-{uuid.uuid4().hex[:8]}"
        self.work = Path(tempfile.mkdtemp(prefix="prumi-check-"))
        # The default root is ours to remove; a given one only loses the project folder.
        self.root = project_root or self.work
        self.project = self.root / PROJECT_NAME
        self.env_file = self.work / ".env"
        self.override = self.work / "override.yml"
        self.image = image
        self.port = _free_port()
        self.started = False

    def write_files(self, warehouse_host: str, warehouse_port: int, dbname: str) -> None:
        settings = {
            "DBT_PROJECT_DIR": str(self.project),
            "PRUMI_PORT": str(self.port),
            "PRUMI__SOURCE__DBT__CONNECTION__HOST": warehouse_host,
            "PRUMI__SOURCE__DBT__CONNECTION__PORT": str(warehouse_port),
            "PRUMI__SOURCE__DBT__CONNECTION__DBNAME": dbname,
            "PRUMI__SOURCE__DBT__CONNECTION__USER": READER,
            "PRUMI__SOURCE__DBT__CONNECTION__PASSWORD_ENV": "WAREHOUSE_PASSWORD",
            "WAREHOUSE_PASSWORD": READER_PASSWORD,
            "REGISTRY_PASSWORD": secrets.token_urlsafe(16),
        }
        text = "".join(f"{key}={value}\r\n" for key, value in settings.items())
        self.env_file.write_bytes(text.encode())
        # `!override` replaces the compose file's `env_file: .env`, which would otherwise be
        # appended to and still name a .env next to the shipped file.
        self.override.write_text(
            "services:\n"
            "  prumi:\n"
            f"    image: {json.dumps(self.image)}\n"
            "    env_file: !override\n"
            f"      - {json.dumps(str(self.env_file))}\n"
        )

    def compose(self, *args: str) -> list[str]:
        return [
            "docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(self.override),
            "--env-file", str(self.env_file), "-p", self.name, *args,
        ]  # fmt: skip

    def up(self, check: int) -> None:
        self.started = True
        _run(self.compose("up", "-d"), check, timeout=START_TIMEOUT_S * 2)

    def logs(self) -> str:
        try:
            result = subprocess.run(
                self.compose("logs", "--no-color", "prumi"),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=CLEANUP_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return f"(docker compose logs didn't finish in {CLEANUP_TIMEOUT_S} s)"
        return result.stdout

    def remove(self) -> None:
        if self.started:
            try:
                subprocess.run(
                    self.compose("down", "-v", "--remove-orphans", "--timeout", "5"),
                    capture_output=True,
                    timeout=CLEANUP_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"check_compose: docker compose down didn't finish in {CLEANUP_TIMEOUT_S} s;"
                    f" remove the project {self.name} by hand",
                    file=sys.stderr,
                )
        shutil.rmtree(self.project, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="the prumi image, e.g. prumi:dev")
    parser.add_argument(
        "--warehouse-host", required=True, help="the warehouse as the container reaches it"
    )
    parser.add_argument("--warehouse-port", type=int, required=True)
    parser.add_argument(
        "--warehouse-dbname", default="prumi_demo", help="the database the demo was built in"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        help=f"the folder to put {PROJECT_NAME!r} in (default: a new temporary folder)",
    )
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        sys.exit("check_compose: needs `docker` on PATH.")
    if not (DEMO_TARGET / "semantic_manifest.json").is_file():
        sys.exit(f"check_compose: build the Postgres demo first: no {DEMO_TARGET}.")
    root = args.project_root.resolve() if args.project_root else None
    if root is not None and (root / PROJECT_NAME).exists():
        sys.exit(f"check_compose: {root / PROJECT_NAME} exists; remove it or choose another root.")
    started = time.monotonic()
    run = Run(args.image, root)
    try:
        _copies_the_project(run)
        run.write_files(args.warehouse_host, args.warehouse_port, args.warehouse_dbname)
        _step(f"3. docker compose up -d ({run.name}, 127.0.0.1:{run.port})")
        run.up(3)
        dashboard_id = _serves_and_certifies(run)
        _registry_is_not_the_warehouse(run)
        _reloads_through_the_mount(run)
        _keeps_the_registry(run, dashboard_id)
        _names_a_missing_manifest(run)
    except KeyboardInterrupt:
        sys.exit("check_compose: interrupted")
    finally:
        _step("9. docker compose down -v, remove the folders")
        run.remove()
    print(f"check_compose: passed in {time.monotonic() - started:.0f} s")
    return 0


def _copies_the_project(run: Run) -> None:
    _step(f"1. copy the demo's target-postgres/ to {run.project / 'target'}")
    _step("2. write .env with CRLF line endings, and the compose override")
    run.project.mkdir(parents=True)
    run.project.chmod(0o755)  # the container's own user reads it
    shutil.copytree(DEMO_TARGET, run.project / "target")


def _serves_and_certifies(run: Run) -> str:
    base = f"http://127.0.0.1:{run.port}"
    _step(f"4. GET {base}/, publish over MCP, certify with docker compose exec")
    _wait_for_home(run, 4)
    try:
        out = asyncio.run(_publish(f"{base}/mcp"))
    except Exception as e:  # noqa: BLE001 - any failure to talk MCP fails the check
        _fail(4, f"publish_dashboard failed: {type(e).__name__}: {e}")
    dashboard_id = out.get("id")
    if out.get("status") != "draft" or not dashboard_id:
        _fail(4, f"the published dashboard isn't a draft: {out}")
    certify = ["prumi", "certify", dashboard_id, "1", "--by", "Smoke Test"]
    _run(run.compose("exec", "-T", "prumi", *certify), 4)
    status = _json(f"{base}/api/dashboards/{dashboard_id}/versions/1/status", 4)
    if status.get("status") != "certified" or status.get("certified_by") != "Smoke Test":
        _fail(4, f"the status API doesn't say certified by Smoke Test: {status}")
    return dashboard_id


def _registry_is_not_the_warehouse(run: Run) -> None:
    _step("5. the dashboard is in registry-db, and no registry table is in the warehouse")
    count = _run(
        run.compose(
            "exec", "-T", "registry-db",
            "psql", "-U", "prumi", "-d", "prumi", "-tAc", "select count(*) from dashboards",
        ),
        5,
    ).stdout.strip()  # fmt: skip
    if count != "1":
        _fail(5, f"registry-db has {count!r} dashboards, not 1")
    found = _run(
        run.compose("exec", "-T", "prumi", "python", "-c", WAREHOUSE_TABLES), 5
    ).stdout.strip()
    if found != "(None, None)":
        _fail(5, f"the warehouse has a dashboards table (public, main): {found}")


def _reloads_through_the_mount(run: Run) -> None:
    target = run.project / "target"
    manifest = json.loads((DEMO_TARGET / "semantic_manifest.json").read_text(encoding="utf-8"))
    _step(f"6. rewrite target/semantic_manifest.json; {METRIC}'s description within 15 s")
    first = f"Rewritten in place, {uuid.uuid4().hex[:8]}."
    _write_manifest(target, manifest, first)
    _wait_for_description(run, first, "after rewriting the manifest")
    _step(f"6. rm -rf target && mkdir target, write it again; within {RELOAD_TIMEOUT_S} s")
    second = f"Written into a new target/, {uuid.uuid4().hex[:8]}."
    shutil.rmtree(target)
    target.mkdir()
    target.chmod(0o755)
    _write_manifest(target, manifest, second)
    _wait_for_description(run, second, "after recreating target/")


def _keeps_the_registry(run: Run, dashboard_id: str) -> None:
    _step("7. docker compose down (keeping volumes), up -d: the dashboard is still listed")
    _run(run.compose("down", "--timeout", "5"), 7, timeout=START_TIMEOUT_S)
    run.up(7)
    _wait_for_home(run, 7)
    try:
        listed = asyncio.run(_call(run, "list_dashboards", {}))
    except Exception as e:  # noqa: BLE001 - any failure to talk MCP fails the check
        _fail(7, f"list_dashboards failed: {type(e).__name__}: {e}")
    ids = [dashboard.get("id") for dashboard in listed.get("dashboards", [])]
    if ids != [dashboard_id]:
        _fail(7, f"list_dashboards lists {ids}, not [{dashboard_id!r}]")


def _names_a_missing_manifest(run: Run) -> None:
    _step("8. docker compose down -v; up -d without target/: the logs name what's missing")
    _run(run.compose("down", "-v", "--timeout", "5"), 8, timeout=START_TIMEOUT_S)
    shutil.rmtree(run.project / "target")
    run.up(8)
    wanted = ("semantic_manifest.json", "/project/target")
    deadline = time.monotonic() + START_TIMEOUT_S
    while not all(text in (logs := run.logs()) for text in wanted):
        if time.monotonic() > deadline:
            _fail(8, f"in {START_TIMEOUT_S} s the logs didn't name {wanted}:\n{logs}")
        time.sleep(1)


def _write_manifest(target: Path, manifest: dict[str, Any], description: str) -> None:
    for metric in manifest["metrics"]:
        if metric["name"] == METRIC:
            metric["description"] = description
    (target / "semantic_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _wait_for_description(run: Run, description: str, after: str) -> None:
    written = time.monotonic()
    deadline = written + RELOAD_TIMEOUT_S
    while True:
        # A call that lands while the catalog swaps can fail; that's "not yet", and only the
        # deadline fails the check, naming the last thing seen.
        try:
            out = asyncio.run(_call(run, "describe_metric", {"name": METRIC}))
        except Exception as e:  # noqa: BLE001 - any failure to talk MCP counts as "not yet"
            seen = f"describe_metric failed: {type(e).__name__}: {e}"
        else:
            if out.get("description") == description:
                _step(f"6. reloaded in {time.monotonic() - written:.1f} s {after}")
                return
            seen = f"{METRIC}'s description is still {out.get('description')!r}"
        if time.monotonic() > deadline:
            _fail(6, f"{RELOAD_TIMEOUT_S} s {after}, {seen}:\n{run.logs()}")
        time.sleep(0.5)


def _wait_for_home(run: Run, check: int) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while _get(f"http://127.0.0.1:{run.port}/") != 200:
        if time.monotonic() > deadline:
            _fail(check, f"the home page didn't answer 200 in {START_TIMEOUT_S} s:\n{run.logs()}")
        time.sleep(0.5)


async def _call(run: Run, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(f"http://127.0.0.1:{run.port}/mcp") as mcp:
        result = await mcp.call_tool(tool, arguments, read_timeout_seconds=MCP_TIMEOUT_S)
    if result.is_error:
        raise RuntimeError(f"{tool} returned an error: {result.content}")
    return result.structured_content or {}


def _json(url: str, check: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)
    except (OSError, ValueError) as e:
        _fail(check, f"GET {url} failed: {e}")


def _step(name: str) -> None:
    print(f"check_compose: {name}", flush=True)


def _fail(check: int, message: str) -> NoReturn:
    sys.exit(f"check_compose: check {check} failed: {message}")


def _run(
    command: list[str], check: int, timeout: float = START_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Runs a command for its output; a timeout or a non-zero exit fails `check`."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _fail(check, f"no exit in {timeout:.0f} s: {' '.join(command)}")
    if result.returncode != 0:
        _fail(check, f"exit {result.returncode}: {' '.join(command)}\n{result.stderr}")
    return result


if __name__ == "__main__":
    sys.exit(main())
