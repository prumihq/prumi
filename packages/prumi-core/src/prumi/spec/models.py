"""Dashboard spec, format version 0 (spec section 6).

These models check shape only: types, ranges and patterns. Rules that relate fields to each
other or to the catalog are the validator's, so the agent gets one coded issue per problem.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from prumi.time import Compare, Grain, Window

DashboardId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
# Filter and block ids. Names starting with `_` are reserved for Prumi.
ItemId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
FilterScalar = str | int | float | bool | None


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DashboardInfo(SpecModel):
    id: DashboardId
    title: str = Field(min_length=1, max_length=200)


class TimeFilter(SpecModel):
    id: ItemId
    type: Literal["time"]
    default: Window
    calendar: str = "standard"  # the only accepted value in this milestone (validator)


class DimensionFilter(SpecModel):
    """An `in` filter on one dimension. `[]` means no filter; `null` matches NULL."""

    id: ItemId
    type: Literal["dimension"]
    dimension: str
    default: tuple[FilterScalar, ...] = ()


FilterDef = Annotated[TimeFilter | DimensionFilter, Field(discriminator="type")]


class Sort(SpecModel):
    by: str  # a metric, a dimension or metric_time
    descending: bool = False


class Block(SpecModel):
    id: ItemId
    title: str | None = None  # informational
    metrics: tuple[str, ...] = Field(min_length=1, max_length=10)
    dimensions: tuple[str, ...] = Field(default=(), max_length=3)  # categorical
    time_grain: Grain | None = None
    filters: tuple[str, ...]  # required, so a block's scope is always explicit (R5)
    window: Window | None = None  # a fixed window instead of a time filter
    compare: Compare | None = None
    sort: Sort | None = None
    top_n: int | None = Field(default=None, ge=1, le=100)
    others: bool = False
    total: bool = False


class Interaction(SpecModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    on: Literal["click"]
    sets_filter: str


class Presentation(SpecModel):
    bindings: dict[str, str] = {}  # "#element-id" -> block id


class DashboardSpec(SpecModel):
    spec_version: Literal[0]
    dashboard: DashboardInfo
    filters: tuple[FilterDef, ...] = ()
    data: tuple[Block, ...]
    interactions: tuple[Interaction, ...] = ()
    metric_requests: tuple[int, ...] = ()  # ids returned by request_metric
    presentation: Presentation = Presentation()

    def block(self, block_id: str) -> Block | None:
        return next((b for b in self.data if b.id == block_id), None)

    def filter(self, filter_id: str) -> TimeFilter | DimensionFilter | None:
        return next((f for f in self.filters if f.id == filter_id), None)
