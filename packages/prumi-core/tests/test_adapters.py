"""The adapter protocol types and entry-point discovery."""

import dataclasses
import datetime as dt
import textwrap

import pytest
from prumi.adapters import (
    LOCAL_VIEWER,
    Adapter,
    AdapterNotFoundError,
    Capabilities,
    Catalog,
    DateRange,
    Filter,
    SemanticQuery,
    Viewer,
    adapter_names,
    load_adapter_factory,
)

FAKE_ADAPTER = """
from prumi.adapters import Capabilities, Catalog, Result


class FakeAdapter:
    name = "fake"

    def __init__(self, config, config_dir):
        self.config = config
        self.config_dir = config_dir

    def capabilities(self):
        return Capabilities(identity_mode="shared_account")

    def discover(self):
        return Catalog(metrics=(), dimensions=())

    def relations(self, metrics):
        return frozenset()

    def execute(self, query, viewer):
        return Result(columns=(), rows=(), row_count=0)


def create(config, config_dir):
    return FakeAdapter(config, config_dir)
"""


@pytest.fixture
def fake_adapter_installed(tmp_path, monkeypatch):
    """Install a package that registers `fake` under the prumi.adapters entry point group."""
    (tmp_path / "fake_prumi_adapter.py").write_text(FAKE_ADAPTER)
    dist_info = tmp_path / "fake_prumi_adapter-0.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fake-prumi-adapter\nVersion: 0.0.0\n"
    )
    (dist_info / "entry_points.txt").write_text(
        textwrap.dedent(
            """
            [prumi.adapters]
            fake = fake_prumi_adapter:create
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def test_a_registered_adapter_is_found_by_name(fake_adapter_installed):
    assert "fake" in adapter_names()
    factory = load_adapter_factory("fake")
    adapter = factory({"option": 1}, fake_adapter_installed)
    assert isinstance(adapter, Adapter)
    assert adapter.name == "fake"
    assert adapter.config == {"option": 1}
    assert adapter.config_dir == fake_adapter_installed
    assert adapter.capabilities() == Capabilities(identity_mode="shared_account")
    assert adapter.discover() == Catalog(metrics=(), dimensions=())


def test_an_unknown_adapter_name_says_what_is_installed(fake_adapter_installed):
    with pytest.raises(AdapterNotFoundError, match=r"'nope'.*installed: .*fake"):
        load_adapter_factory("nope")


def test_capabilities_defaults():
    capabilities = Capabilities(identity_mode="shared_account")
    assert capabilities.time_grains == ("day", "week", "month", "quarter", "year")
    assert capabilities.calendars == ("standard",)


@pytest.mark.parametrize(
    ("op", "values"),
    [("in", ()), ("not_in", ()), ("eq", ()), ("eq", ("a", "b")), ("ne", ()), ("lt", (1, 2))],
)
def test_filter_value_counts_are_checked(op, values):
    with pytest.raises(ValueError):
        Filter("customer__region", op, values)


@pytest.mark.parametrize(
    ("op", "values"), [("in", ("a",)), ("not_in", ("a", None)), ("eq", (None,)), ("ge", (3,))]
)
def test_valid_filters(op, values):
    assert Filter("customer__region", op, values).values == values


def test_dimension_values_query_needs_exactly_one_dimension_and_no_grain():
    assert SemanticQuery(metrics=(), dimensions=("customer__region",)).is_dimension_values
    assert not SemanticQuery(metrics=("revenue",)).is_dimension_values
    with pytest.raises(ValueError):
        SemanticQuery(metrics=())
    with pytest.raises(ValueError):
        SemanticQuery(metrics=(), dimensions=("a", "b"))
    with pytest.raises(ValueError):
        SemanticQuery(metrics=(), dimensions=("a",), time_grain="day")


def test_types_are_immutable():
    query = SemanticQuery(metrics=("revenue",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        query.limit = 5
    context = {"tenant": "a"}
    viewer = Viewer(id="someone", security_context=context)
    context["tenant"] = "b"
    assert viewer.security_context["tenant"] == "a"
    with pytest.raises(TypeError):
        viewer.security_context["tenant"] = "c"


def test_local_viewer():
    assert LOCAL_VIEWER.id == "local"
    assert dict(LOCAL_VIEWER.security_context) == {}


def test_date_range_is_inclusive_and_ordered():
    day = dt.date(2026, 10, 1)
    assert DateRange(day, day).end == day
    with pytest.raises(ValueError):
        DateRange(day, day - dt.timedelta(days=1))
