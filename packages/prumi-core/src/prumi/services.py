"""What the CLI and the server build from prumi.toml: source, registry, engine, validation."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.config import DEFAULT_SOURCE, Config
from prumi.engine import Engine
from prumi.issues import Issue, Report
from prumi.registry import Published, Registry, publish
from prumi.source import Source, query_origin
from prumi.spec import DashboardSpec
from prumi.validate import AllowedHosts, validate
from prumi.validate.page import PageChecker, shared_checker

log = logging.getLogger("prumi.server")

FRESHNESS_TTL_S = 600


@dataclass
class Services:
    config: Config
    _freshness: tuple[float, dt.date | None] | None = field(default=None, init=False, repr=False)
    _freshness_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

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

    def data_through(self) -> dt.date | None:
        """The latest day with data of any metric, for the home page. Queried at startup and
        again when it's more than 10 minutes old, so the page stays cheap to load."""
        with self._freshness_lock:
            now = time.monotonic()
            if self._freshness is None or now - self._freshness[0] >= FRESHNESS_TTL_S:
                self._freshness = (now, self._latest_data())
            return self._freshness[1]

    def _latest_data(self) -> dt.date | None:
        latest = None
        for metric in self.source.metric_names:
            try:
                with query_origin("home page"):
                    through = self.engine.data_through(metric, LOCAL_VIEWER)
            except Exception as exc:  # one failing metric doesn't hide the others' date
                log.warning("freshness of %s failed: %s", metric, type(exc).__name__)
                continue
            if through is not None and (latest is None or through > latest):
                latest = through
        return latest

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
        layout_devices: Sequence[str] = (),
    ) -> Report:
        """Static checks, HTML checks, the dry run and the page check, against the current
        catalog; then the same for each of `check_states` (filter values). The layout is
        measured on desktop, and on `layout_devices` too."""
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
            layout_devices=layout_devices,
        )

    def check_page(
        self,
        spec: DashboardSpec,
        html: str,
        results: Mapping[str, dict[str, Any]],
        values: Mapping[str, Any] | None = None,
        devices: Sequence[str] = ("desktop",),
    ) -> list[Issue]:
        """Render the page with these block results, as a viewer's browser would."""
        from prumi.server import pages  # the server package imports this module

        labels, formats = pages.labels_and_formats(spec, self.source)
        hosts = self.hosts
        locale = self.config.server.locale
        return self.checker.check(
            spec,
            html,
            results,
            labels,
            formats,
            lambda origin: (
                pages.frame_html(html, origin, locale),
                pages.frame_csp(origin, hosts),
            ),
            frozenset(hosts.script) | frozenset(hosts.style) | frozenset(hosts.font),
            values,
            devices,
            locale,
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
