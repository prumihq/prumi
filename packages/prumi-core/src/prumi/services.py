"""What the CLI and the server build from prumi.toml: source, registry, engine, validation."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.config import DEFAULT_SOURCE, Config
from prumi.engine import Engine
from prumi.issues import Issue, Report
from prumi.registry import Published, Registry, publish
from prumi.source import Source
from prumi.spec import DashboardSpec
from prumi.validate import AllowedHosts, validate
from prumi.validate.page import PageChecker, shared_checker


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

    @cached_property
    def checker(self) -> PageChecker:
        """Headless Chromium for the page check, shared by the whole process."""
        return shared_checker()

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
        check_states: Sequence[Mapping[str, Any]] = (),
    ) -> Report:
        """Static checks, HTML checks, the dry run and the page check, against the current
        catalog; then the same for each of `check_states` (filter values)."""
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
            page_check=self.check_page,
            check_states=check_states,
        )

    def check_page(
        self,
        spec: DashboardSpec,
        html: str,
        results: Mapping[str, dict[str, Any]],
        values: Mapping[str, Any] | None = None,
    ) -> list[Issue]:
        """Render the page with these block results, as a viewer's browser would."""
        from prumi.server import pages  # the server package imports this module

        labels, formats = pages.labels_and_formats(spec, self.source)
        hosts = self.hosts
        return self.checker.check(
            spec,
            html,
            results,
            labels,
            formats,
            lambda origin: (pages.frame_html(html, origin), pages.frame_csp(origin, hosts)),
            frozenset(hosts.script) | frozenset(hosts.style) | frozenset(hosts.font),
            values,
        )

    def publish(
        self,
        spec: Any,
        html: str,
        viewer: Viewer = LOCAL_VIEWER,
        check_states: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[Report, Published | None]:
        """Validate (the default filter state and `check_states`), then store."""
        return publish(
            self.registry,
            spec,
            html,
            lambda s, h: self.validate(s, h, viewer, check_states=check_states),
            viewer,
        )
