"""The browser assets: one source, served from the workspace or the wheel's copy."""

from pathlib import Path

import pytest
from prumi.server import assets

RUNTIME_SRC = Path(__file__).parents[2] / "prumi-runtime" / "src"


@pytest.mark.parametrize("name", ["runtime.js", "shell.js", "provenance.js"])
def test_served_from_the_runtime_package(client, name):
    response = client.get(f"/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/javascript; charset=utf-8"
    assert response.content == (RUNTIME_SRC / name).read_bytes()


def test_the_wheels_copy_comes_first(tmp_path, monkeypatch):
    bundled = tmp_path / "prumi" / "_runtime"
    bundled.mkdir(parents=True)
    (bundled / "runtime.js").write_text("// from the wheel")
    monkeypatch.setattr(assets, "files", lambda package: tmp_path / package)
    assert assets.read_asset("runtime.js") == b"// from the wheel"
    assert assets.read_asset("shell.js") == (RUNTIME_SRC / "shell.js").read_bytes()


def test_only_the_assets(tmp_path):
    with pytest.raises(KeyError):
        assets.read_asset("../../pyproject.toml")


def test_the_runtime_has_no_aggregation_helpers():
    # R1: the runtime never sums, averages or re-groups rows.
    source = (RUNTIME_SRC / "runtime.js").read_text()
    for helper in ("sum", "average", "mean", "groupBy", "aggregate", "reduce("):
        assert f"prumi.{helper}" not in source and f"{helper}:" not in source
