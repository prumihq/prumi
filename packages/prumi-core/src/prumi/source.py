"""A source: its adapter and its catalog, read once at startup."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from prumi.adapters import (
    Adapter,
    Catalog,
    CatalogWarning,
    Dimension,
    Metric,
    Result,
    SemanticQuery,
    Viewer,
    load_adapter_factory,
)
from prumi.config import SourceConfig

log = logging.getLogger("prumi.query")

# Who a query runs for: "block <dashboard>/<v>/<block>", "agent query", ... Set by the caller
# around the engine, read when each query is logged.
_origin: ContextVar[str] = ContextVar("prumi_query_origin", default="query")


@contextmanager
def query_origin(label: str | None) -> Iterator[None]:
    """Label the queries run inside the block (None keeps the current label)."""
    if label is None:
        yield
        return
    token = _origin.set(label)
    try:
        yield
    finally:
        _origin.reset(token)


class Source:
    def __init__(self, name: str, adapter: Adapter) -> None:
        self.name = name
        self.adapter = adapter
        self.catalog: Catalog = adapter.discover()
        self._metrics = {m.name: m for m in self.catalog.metrics}
        self._dimensions = {d.name: d for d in self.catalog.dimensions}
        self._relations = lru_cache(maxsize=1024)(self._compute_relations)

    @classmethod
    def from_config(cls, config: SourceConfig) -> Source:
        """Load the configured adapter through its entry point (AdapterStartupError on failure)."""
        factory = load_adapter_factory(config.adapter)
        return cls(config.name, factory(config.settings, config.config_dir))

    @property
    def metric_names(self) -> list[str]:
        return list(self._metrics)

    def metric(self, name: str) -> Metric | None:
        return self._metrics.get(name)

    def dimension(self, name: str) -> Dimension | None:
        return self._dimensions.get(name)

    def warnings_for(self, subject: str) -> tuple[CatalogWarning, ...]:
        return tuple(w for w in self.catalog.warnings if w.subject == subject)

    def relations(self, metrics: Iterable[str]) -> frozenset[str]:
        """Categorical dimensions valid for all of `metrics`, which must be in the catalog."""
        return self._relations(tuple(sorted(set(metrics))))

    def _compute_relations(self, metrics: tuple[str, ...]) -> frozenset[str]:
        return self.adapter.relations(metrics)

    def execute(self, query: SemanticQuery, viewer: Viewer, step: str = "query") -> Result:
        """Run a query and log its origin, step, viewer, rows and duration. Never values."""
        started = time.perf_counter()
        try:
            result = self.adapter.execute(query, viewer)
        except Exception as exc:
            log.warning(
                "%s %s failed: viewer=%s error=%s duration_ms=%.0f",
                _origin.get(),
                step,
                viewer.id,
                type(exc).__name__,
                (time.perf_counter() - started) * 1000,
            )
            raise
        log.info(
            "%s %s: viewer=%s rows=%d duration_ms=%.0f",
            _origin.get(),
            step,
            viewer.id,
            result.row_count,
            (time.perf_counter() - started) * 1000,
        )
        return result
