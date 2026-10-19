"""The `prumi` command. Exit codes: 0 done, 1 refused, 2 usage error."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from prumi.adapters import AdapterStartupError
from prumi.config import ConfigError, load_config
from prumi.issues import Report
from prumi.registry import PageCheck
from prumi.services import Services
from prumi.spec import SpecFileError, load_spec_file
from prumi.validate.page import BrowserStartupError

OK, REFUSED, USAGE = 0, 1, 2


class Refused(Exception):
    """The command can't do what was asked; the message says why."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)  # exits with 2 on usage errors
    try:
        services = Services(load_config(args.config))
        return args.run(services, args)
    except (ConfigError, AdapterStartupError, BrowserStartupError, SpecFileError, Refused) as exc:
        print(f"prumi: {exc}", file=sys.stderr)
        return REFUSED


def _parser() -> argparse.ArgumentParser:
    config = argparse.ArgumentParser(add_help=False)
    config.add_argument(
        "--config", type=Path, default=Path("prumi.toml"), help="default: ./prumi.toml"
    )
    parser = argparse.ArgumentParser(prog="prumi", description="Governed AI dashboards.")
    commands = parser.add_subparsers(required=True, metavar="command")

    def command(name: str, run, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[config], help=help, description=help)
        sub.set_defaults(run=run)
        return sub

    command("serve", _serve, "Start the server.")
    command("list", _list, "List dashboards with their current version and status.")
    certify = command("certify", _certify, "Re-validate a version and certify it.")
    certify.add_argument("id")
    certify.add_argument("version", type=int)
    certify.add_argument("--by", default=None, help="default: the OS user name")
    uncertify = command("uncertify", _uncertify, "Revoke a dashboard's certifications.")
    uncertify.add_argument("id")
    uncertify.add_argument("--reason", default=None)
    delete = command("delete", _delete, "Delete a dashboard, its versions and certifications.")
    delete.add_argument("id")
    delete.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    command("requests", _requests, "List metric requests.")
    check = command("validate", _validate, "Validate a spec (YAML or JSON) and its HTML.")
    check.add_argument("spec", type=Path)
    check.add_argument("--html", type=Path, default=None)
    return parser


# Commands


def _serve(services: Services, args: argparse.Namespace) -> int:
    from prumi.server import serve

    serve(services)
    return OK


def _list(services: Services, args: argparse.Namespace) -> int:
    rows = []
    for d in services.registry.dashboards():
        status = services.registry.status(d.id, d.current_version)
        rows.append((d.id, d.title, str(d.current_version), status.status))
    if not rows:
        print("No dashboards.")
        return OK
    _table(("ID", "TITLE", "VERSION", "STATUS"), rows)
    return OK


def _certify(services: Services, args: argparse.Namespace) -> int:
    version = services.registry.version(args.id, args.version)
    if version is None:
        raise Refused(f"There's no version {args.version} of the dashboard {args.id!r}.")
    # Validation renders the page again and reads its numbers (the server check).
    report = services.validate(version.spec, version.html)
    if not report.ok:
        print(f"Not certified: {args.id} version {args.version} has validation errors now.")
        _print_report(report)
        return REFUSED
    # Viewers' browsers checked filter states the server didn't render.
    found = [c for c in services.registry.checks(args.id, args.version) if not c.clean]
    if found:
        print(
            f"Not certified: viewers' browsers found problems in {args.id} version "
            f"{args.version}, with these filters:"
        )
        for check in found:
            _print_check(check)
        return REFUSED
    _print_report(report, quiet_if_ok=True)
    status = services.registry.certify(args.id, args.version, by=args.by or getpass.getuser())
    print(f"Certified {args.id} version {args.version} by {status.certified_by}.")
    print(f"Fingerprint: {status.fingerprint}")
    return OK


def _uncertify(services: Services, args: argparse.Namespace) -> int:
    if services.registry.dashboard(args.id) is None:
        raise Refused(f"There's no dashboard {args.id!r}.")
    revoked = services.registry.uncertify(args.id, args.reason)
    if revoked:
        print(f"Revoked {revoked} certification{'s' * (revoked > 1)} of {args.id}.")
    else:
        print(f"{args.id} has no active certification.")
    return OK


def _delete(services: Services, args: argparse.Namespace) -> int:
    dashboard = services.registry.dashboard(args.id)
    if dashboard is None:
        raise Refused(f"There's no dashboard {args.id!r}.")
    if not args.yes:
        answer = input(
            f"Delete {args.id} ({dashboard.title!r}), its {dashboard.current_version} "
            "version(s) and certifications? Metric requests are kept. [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Not deleted.")
            return REFUSED
    services.registry.delete(args.id)
    print(f"Deleted {args.id}.")
    return OK


def _requests(services: Services, args: argparse.Namespace) -> int:
    requests = services.registry.metric_requests()
    if not requests:
        print("No metric requests.")
        return OK
    rows = [
        (str(r.id), r.created_at[:10], r.dashboard_id or "-", r.description, r.reason)
        for r in requests
    ]
    _table(("ID", "DATE", "DASHBOARD", "DESCRIPTION", "REASON"), rows)
    return OK


def _validate(services: Services, args: argparse.Namespace) -> int:
    spec = load_spec_file(args.spec)
    html = None
    if args.html is not None:
        try:
            html = args.html.read_text(encoding="utf-8")
        except OSError as exc:
            raise Refused(f"Can't read {args.html}: {exc.strerror}") from None
    report = services.validate(spec, html)
    _print_report(report)
    return OK if report.ok else REFUSED


# Output


def _print_report(report: Report, quiet_if_ok: bool = False) -> None:
    for issue in report.issues:
        print(f"{issue.severity:<8} {issue.code}  {issue.path}")
        print(f"         {issue.message}")
        if issue.hint:
            print(f"         hint: {issue.hint}")
    errors, warnings = len(report.errors), len(report.warnings)
    if quiet_if_ok and not report.issues:
        return
    summary = f"{errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)}"
    print(("OK: " if report.ok else "") + summary)


def _print_check(check: PageCheck) -> None:
    state = json.dumps(check.filters, ensure_ascii=False, sort_keys=True)
    print(f"  {state}  (reported {check.reported_at})")
    for item in check.flagged:
        print(f"    {item['code']}  {item['path']}  (×{item['count']})")
    if check.canvas:
        print("    html.canvas  the page uses <canvas>")
    for message in check.errors:
        print(f"    html.runtime_error  {message}")


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [max(len(x) for x in column) for column in zip(header, *rows, strict=True)]
    for row in (header, *rows):
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())


if __name__ == "__main__":
    sys.exit(main())
