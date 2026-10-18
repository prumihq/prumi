"""A source: its adapter and its catalog, read once at startup."""

from __future__ import annotations

from collections.abc import Iterable
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

    def execute(self, query: SemanticQuery, viewer: Viewer) -> Result:
        return self.adapter.execute(query, viewer)
