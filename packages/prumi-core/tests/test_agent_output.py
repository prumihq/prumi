"""for_agent: what an agent sees of a result is its profile, never its values."""

import datetime as dt

import duckdb
import pytest
from prumi.adapters import LOCAL_VIEWER
from prumi.agent_output import for_agent
from prumi.engine import BlockInputs, BlockResult, ColumnInfo, Engine
from prumi.spec import Block
from prumi.time import LastWindow

AS_OF = dt.date(2026, 10, 15)
WINDOW = "order_date BETWEEN '2026-07-20' AND '2026-10-11'"  # {last: 12, grain: week}


def profile(demo_source, **block):
    engine = Engine(demo_source, AS_OF)
    query = Block(id="query", metrics=("revenue",), filters=(), **block)
    inputs = BlockInputs(LastWindow(last=12, grain="week"), ())
    return for_agent(engine.run(query, inputs, LOCAL_VIEWER))


def hand(demo_project, sql):
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        return conn.execute(sql).fetchall()


def digits(values):
    counts = [len(str(int(abs(v)))) for v in values]
    return {"min": min(counts), "max": max(counts)}


@pytest.mark.parametrize("weekly", [False, True], ids=["by_region", "by_region_and_week"])
def test_the_profile_of_revenue_by_region(demo_source, demo_project, weekly):
    week = ", date_trunc('week', i.order_date)" if weekly else ""
    rows = hand(
        demo_project,
        f"""SELECT c.region, sum(i.revenue)::double FROM main.order_items i
            LEFT JOIN main.customers c USING (customer_id) WHERE {WINDOW}
            GROUP BY c.region{week}""",
    )
    found = profile(
        demo_source, dimensions=("customer__region",), time_grain="week" if weekly else None
    )
    columns = {c.name: c for c in found.columns}
    assert found.row_count == len(rows)
    region = columns["customer__region"]
    named = [r for r, _ in rows if r is not None]
    assert region.distinct == len(set(named))
    assert region.nulls == sum(r is None for r, _ in rows)
    assert region.label_length.model_dump() == {
        "min": min(map(len, named)),
        "max": max(map(len, named)),
    }
    revenue = columns["revenue"]
    assert revenue.magnitude.model_dump() == digits([v for _, v in rows])
    assert (revenue.sign, revenue.decimals, revenue.nulls) == ("positive", 2, 0)
    assert revenue.format == {"type": "currency", "unit": "USD", "decimals": 2}
    if weekly:
        assert columns["metric_time"].distinct == 12


def test_the_profile_has_no_values(demo_source):
    found = profile(demo_source, dimensions=("customer__region",), total=True)
    data = found.model_dump()
    assert "rows" not in data and "total" not in data and "others" not in data
    assert (found.has_total, found.has_others, found.has_comparison) == (True, False, False)


def column(kind, fmt=None):
    return ColumnInfo(name="v", kind=kind, label="V", format=fmt)


@pytest.mark.parametrize(
    ("values", "fmt", "sign", "magnitude", "decimals"),
    [
        ([1234.5, 98765.25], {"type": "currency", "unit": "USD", "decimals": None}, "positive",
         (4, 5), 2),
        ([0.251, 0.3], {"type": "percent", "unit": None, "decimals": None}, "positive", (1, 1), 1),
        ([0.25, 0.3], {"type": "percent", "unit": None, "decimals": None}, "positive", (1, 1), 0),
        ([-3.5, 2.25, 7.125], {"type": "number", "unit": None, "decimals": None}, "mixed",
         (1, 1), 2),
        ([-10, -200], {"type": "integer", "unit": None, "decimals": None}, "negative", (2, 3), 0),
        ([0, 0], {"type": "number", "unit": None, "decimals": 1}, "zero", (1, 1), 0),
    ],
)  # fmt: skip
def test_sign_magnitude_and_decimals(values, fmt, sign, magnitude, decimals):
    result = BlockResult(
        block="b", columns=[column("metric", fmt)], rows=[{"v": v} for v in [*values, None]]
    )
    [found] = for_agent(result).columns
    assert (found.sign, (found.magnitude.min, found.magnitude.max), found.decimals) == (
        sign,
        magnitude,
        decimals,
    )
    assert found.nulls == 1


def test_past_the_engine_cap_the_count_is_a_bound():
    result = BlockResult(block="b", columns=[], rows=[{}] * 5000, truncated=True)
    assert for_agent(result).row_count == "more than 5000"
