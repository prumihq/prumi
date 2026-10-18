"""What the CLI and the server build from prumi.toml: source, registry, engine, validation."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.config import DEFAULT_SOURCE, Config
from prumi.engine import Engine
from prumi.issues import Report
from prumi.registry import Published, Registry, publish
from prumi.source import Source
from prumi.validate import AllowedHosts, validate


@dataclass
class Services:
    config: Config

    @cached_property
    def registry(self) -> Registry:
        return Registry(self.config.server.data_dir)

    @cached_property
    def source(self) -> Source:
        """Loads the adapter (and checks the source) on first use: AdapterStartupError."""
        return Source.from_config(self.config.sources[DEFAULT_SOURCE])

    @cached_property
    def engine(self) -> Engine:
        return Engine(self.source, self.config.server.as_of)

    @property
    def hosts(self) -> AllowedHosts:
        server = self.config.server
        return AllowedHosts(server.script_hosts, server.style_hosts, server.font_hosts)

    def validate(
        self,
        spec: Any,
        html: str | None,
        viewer: Viewer = LOCAL_VIEWER,
        metric_request_ids: Collection[int] | None = None,
    ) -> Report:
        """Static checks, HTML checks and the dry run, against the current catalog."""
        if metric_request_ids is None:
            metric_request_ids = self.registry.metric_request_ids()
        return validate(
            spec,
            html,
            self.source,
            metric_request_ids,
            as_of=self.engine.today(),
            hosts=self.hosts,
            engine=self.engine,
            viewer=viewer,
        )

    def publish(self, spec: Any, html: str) -> tuple[Report, Published | None]:
        return publish(self.registry, spec, html, self.validate)
