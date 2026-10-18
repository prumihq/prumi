"""for_agent: the 200-row cap and the separate total (spec section 9, AC 28)."""

import datetime as dt

import duckdb
import pytest
from prumi.adapters import LOCAL_VIEWER
from prumi.agent_output import AgentPolicy, for_agent
from prumi.engine import BlockInputs, BlockResult, Engine
from prumi.spec import Block
from prumi.time import LastWindow

AS_OF = dt.date(2026, 10, 15)


def result(n_rows: int, **fields) -> BlockResult:
    return BlockResult(block="b", columns=[], rows=[{"v": i} for i in range(n_rows)], **fields)


def no_total():
    raise AssertionError("no total query expected")


def test_small_results_pass_unchanged():
    agent = for_agent(result(200), fetch_total=no_total)
    assert len(agent.rows) == 200 and not agent.truncated
    assert agent.row_count == 200 and agent.total is None


def test_past_the_cap_rows_are_cut_and_the_total_is_fetched():
    agent = for_agent(result(201), fetch_total=lambda: {"v": 42})
    assert len(agent.rows) == 200 and agent.truncated
    assert agent.row_count == 201
    assert agent.total == {"v": 42}
    assert [w.code for w in agent.warnings] == ["result.truncated"]


def test_a_requested_total_is_reused():
    agent = for_agent(result(300, total={"v": 7}), fetch_total=no_total)
    assert agent.total == {"v": 7}


def test_past_the_engine_cap_the_count_is_a_bound():
    agent = for_agent(result(5000, truncated=True), fetch_total=lambda: {"v": 1})
    assert agent.row_count == "more than 5000"


def test_the_policy_sets_the_cap():
    agent = for_agent(result(20), AgentPolicy(max_rows=10), fetch_total=lambda: None)
    assert len(agent.rows) == 10 and agent.truncated


def test_revenue_by_day_over_two_years(demo_source, demo_project):
    """AC 28's numbers, through the engine (the MCP query tool comes later)."""
    engine = Engine(demo_source, AS_OF)
    block = Block(id="query", metrics=("revenue",), time_grain="day", filters=())
    inputs = BlockInputs(LastWindow(last=2, grain="year"), ())
    full = engine.run(block, inputs, LOCAL_VIEWER)

    agent = for_agent(full, fetch_total=lambda: engine.total_for(block, inputs, LOCAL_VIEWER))
    assert len(agent.rows) == 200 and agent.truncated
    assert agent.row_count == 731  # 2024-01-01 to 2025-12-31
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        [(expected,)] = conn.execute(
            "SELECT sum(revenue)::double FROM main.order_items"
            " WHERE order_date BETWEEN '2024-01-01' AND '2025-12-31'"
        ).fetchall()
    assert agent.total == {"revenue": pytest.approx(expected)}
    assert agent.rows[0] == full.rows[0]
