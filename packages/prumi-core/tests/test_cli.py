"""The prumi CLI."""

import copy
import getpass
import json

import pytest
import yaml
from prumi.cli import main
from prumi.config import load_config
from prumi.services import Services


@pytest.fixture
def config(tmp_path, make_demo_config):
    return make_demo_config(tmp_path)


@pytest.fixture
def services(config):
    return Services(load_config(config))


def prumi(capsys, *args) -> tuple[int, str]:
    code = main([*args])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_validate_a_spec_file(capsys, config, tmp_path, demo_dashboard):
    spec, html = demo_dashboard
    (tmp_path / "sales.yaml").write_text(yaml.safe_dump(spec))
    (tmp_path / "sales.html").write_text(html)
    code, out = prumi(
        capsys,
        "validate",
        str(tmp_path / "sales.yaml"),
        "--html",
        str(tmp_path / "sales.html"),
        "--config",
        str(config),
    )
    assert (code, out.strip()) == (0, "OK: 0 errors, 0 warnings")

    spec = copy.deepcopy(spec)
    spec["data"][0]["metrics"] = ["revnue"]
    (tmp_path / "broken.json").write_text(json.dumps(spec))
    code, out = prumi(capsys, "validate", str(tmp_path / "broken.json"), "--config", str(config))
    assert code == 1
    assert "error    R7.unknown_metric  data[0].metrics[0]" in out
    assert "Did you mean revenue?" in out and "1 error, 0 warnings" in out


def test_certification_lifecycle(capsys, config, services, demo_dashboard):
    spec, html = demo_dashboard
    report, published = services.publish(spec, html)
    assert report.ok and published.version == 1

    assert prumi(capsys, "list", "--config", str(config)) == (
        0,
        "ID     TITLE  VERSION  STATUS\nsales  Sales  1        draft\n",
    )
    code, out = prumi(capsys, "certify", "sales", "1", "--by", "ana", "--config", str(config))
    assert code == 0
    assert "Certified sales version 1 by ana." in out
    assert f"Fingerprint: {services.registry.version('sales', 1).fingerprint}" in out
    assert "certified" in prumi(capsys, "list", "--config", str(config))[1]

    # An edit makes the current version a draft.
    services.publish(spec, html + "<!-- edited -->")
    assert prumi(capsys, "list", "--config", str(config))[1].endswith(
        "sales  Sales  2        draft\n"
    )
    # Republishing what was certified is certified again.
    services.publish(spec, html)
    assert prumi(capsys, "list", "--config", str(config))[1].endswith("3        certified\n")

    assert prumi(capsys, "uncertify", "sales", "--reason", "review", "--config", str(config)) == (
        0,
        "Revoked 1 certification of sales.\n",
    )
    assert prumi(capsys, "list", "--config", str(config))[1].endswith("3        draft\n")


def test_certify_defaults_to_the_os_user(capsys, config, services, demo_dashboard):
    services.publish(*demo_dashboard)
    code, out = prumi(capsys, "certify", "sales", "1", "--config", str(config))
    assert code == 0 and f"by {getpass.getuser()}." in out


def test_certify_refuses_a_version_that_no_longer_validates(
    capsys, tmp_path, make_demo_config, demo_project, services, demo_dashboard
):
    spec, html = demo_dashboard
    spec = copy.deepcopy(spec)
    spec["data"][0]["metrics"] = ["revenue", "discount_rate"]
    report, published = services.publish(spec, html)
    assert report.ok, report.issues

    # The data team removes discount_rate from the manifest.
    manifest = json.loads(demo_project.manifest_path.read_text())
    manifest["metrics"] = [m for m in manifest["metrics"] if m["name"] != "discount_rate"]
    changed = tmp_path / "semantic_manifest.json"
    changed.write_text(json.dumps(manifest))
    later = make_demo_config(tmp_path, manifest=changed)

    code, out = prumi(capsys, "certify", "sales", "1", "--config", str(later))
    assert code == 1
    assert "Not certified: sales version 1 has validation errors now." in out
    assert "R7.unknown_metric  data[0].metrics[1]" in out
    assert services.registry.status("sales", 1).status == "draft"


def test_delete_asks_first_and_keeps_metric_requests(
    capsys, config, services, demo_dashboard, monkeypatch
):
    services.publish(*demo_dashboard)
    services.registry.request_metric("Returns", "Finance asked", "sales")

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert prumi(capsys, "delete", "sales", "--config", str(config)) == (1, "Not deleted.\n")
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert prumi(capsys, "delete", "sales", "--config", str(config)) == (0, "Deleted sales.\n")
    services.publish(*demo_dashboard)
    assert prumi(capsys, "delete", "sales", "--yes", "--config", str(config)) == (
        0,
        "Deleted sales.\n",
    )

    code, out = prumi(capsys, "requests", "--config", str(config))
    assert code == 0
    row = out.splitlines()[1].split()
    assert row[0] == "1" and row[2] == "-"  # its dashboard is gone
    assert "Returns" in out and "Finance asked" in out


@pytest.mark.parametrize(
    "args",
    [
        ["certify", "sales", "1"],
        ["certify", "sales", "9"],
        ["uncertify", "nope"],
        ["delete", "nope", "--yes"],
        ["validate", "missing.yaml"],
    ],
)
def test_refused_actions_exit_1(capsys, config, args):
    code, out = prumi(capsys, *args, "--config", str(config))
    assert code == 1 and out.startswith("prumi: ")


def test_usage_errors_exit_2(capsys, config):
    for args in (["certify", "sales", "one"], ["frobnicate"], []):
        with pytest.raises(SystemExit) as exit_:
            main(args)
        assert exit_.value.code == 2


def test_a_bad_config_exits_1(capsys, tmp_path):
    (tmp_path / "prumi.toml").write_text('[server]\nhost = "0.0.0.0"\n[source]\nadapter = "dbt"\n')
    code, out = prumi(capsys, "list", "--config", str(tmp_path / "prumi.toml"))
    assert code == 1 and "isn't a loopback address" in out


def test_no_dashboards_and_no_requests(capsys, config):
    assert prumi(capsys, "list", "--config", str(config)) == (0, "No dashboards.\n")
    assert prumi(capsys, "requests", "--config", str(config)) == (0, "No metric requests.\n")


def test_certify_refuses_a_version_with_problems_found_in_browsers(
    capsys, config, services, demo_dashboard
):
    from prumi.registry import PageCheck

    services.publish(*demo_dashboard)
    services.registry.record_check("sales", 1, PageCheck({"region": []}, [], False, []))
    flagged = [{"text": "$1,234.56", "path": "p#extra"}]
    services.registry.record_check("sales", 1, PageCheck({"region": ["North"]}, flagged, False, []))
    services.registry.record_check("sales", 1, PageCheck({"region": ["West"]}, [], True, ["boom"]))

    code, out = prumi(capsys, "certify", "sales", "1", "--config", str(config))
    assert code == 1
    assert "Not certified: viewers' browsers found problems in sales version 1" in out
    assert '{"region": ["North"]}' in out and "R8.unverified_number  $1,234.56 in p#extra" in out
    assert '{"region": ["West"]}' in out and "html.canvas" in out and "boom" in out
    assert '{"region": []}' not in out
    assert services.registry.status("sales", 1).status == "draft"


def test_certify_runs_the_server_check_again(capsys, config, services, demo_dashboard, monkeypatch):
    from prumi.issues import error

    services.publish(*demo_dashboard)
    shown = error("R8.unverified_number", "html", "The page shows $5 (in p), which isn't ...")
    monkeypatch.setattr(Services, "check_page", lambda self, spec, html, results, **_: [shown])
    code, out = prumi(capsys, "certify", "sales", "1", "--config", str(config))
    assert code == 1 and "R8.unverified_number  html" in out
