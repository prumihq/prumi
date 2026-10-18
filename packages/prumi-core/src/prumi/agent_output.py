"""What agents may see of a result (spec section 9).

Agent results go to the agent's LLM provider, so every result returned to an agent passes
through `for_agent`. The only policy in this milestone caps rows; later policies (masked
values, shape only) plug in here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prumi.engine import MAX_ROWS, BlockResult
from prumi.issues import warning


@dataclass(frozen=True)
class AgentPolicy:
    max_rows: int = 200


ROW_CAP = AgentPolicy()


class AgentResult(BlockResult):
    # Rows before the agent's cap: a count, or "more than 5000" past the engine's cap.
    row_count: int | str


def for_agent(
    result: BlockResult,
    policy: AgentPolicy = ROW_CAP,
    *,
    fetch_total: Callable[[], dict[str, Any] | None],
) -> AgentResult:
    """`result` as an agent may see it. Past the cap, rows are cut, `truncated` is set, and
    `total` comes from a separate source query (`fetch_total`) even if it wasn't requested."""
    row_count: int | str = f"more than {MAX_ROWS}" if result.truncated else len(result.rows)
    data = result.model_dump()
    if len(result.rows) > policy.max_rows:
        data["rows"] = data["rows"][: policy.max_rows]
        data["truncated"] = True
        if data["total"] is None:
            data["total"] = fetch_total()
        data["warnings"].append(
            warning(
                "result.truncated",
                "rows",
                f"Showing {policy.max_rows} of {row_count} rows: results for agents are capped. "
                "They're for exploring; the dashboard gets every row from the source.",
                "Use a coarser time_grain, top_n or total to look at the whole result.",
            )
        )
    return AgentResult(**data, row_count=row_count)
