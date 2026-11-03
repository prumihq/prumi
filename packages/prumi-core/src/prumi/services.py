"""What the CLI and the server build from prumi.toml: source, registry, engine, validation."""

from __future__ import annotations

import datetime as dt
import functools
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


@dataclass(frozen=True)
class Snapshot:
    """A source and the engine built on it. Requests take one and use it to the end, so a
    reload in the middle never gives them a new catalog with an old engine."""

    source: Source
    engine: Engine


@dataclass
class Services:
    config: Config
    _snapshot: Snapshot | None = field(default=None, init=False, repr=False)
    _snapshot_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _freshness: tuple[Snapshot, float, dt.date | None] | None = field(
        default=None, init=False, repr=False
    )
    _freshness_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @cached_property
    def registry(self) -> Registry:
        return Registry(self.config.server.data_dir)

    def current(self) -> Snapshot:
        """The snapshot in use. Loads the adapter (and checks the source) on first use:
        AdapterStartupError. A request calls this once and passes the snapshot down."""
        snapshot = self._snapshot
        if snapshot is not None:
            return snapshot
        with self._snapshot_lock:
            if self._snapshot is None:
                source = Source.from_config(self.config.sources[DEFAULT_SOURCE])
                self._snapshot = Snapshot(source, Engine(source, self.config.server.as_of))
            return self._snapshot

    def swap(self, source: Source) -> None:
        """Use `source` from now on. Requests already running finish on the snapshot they
        took. One assignment replaces both source and engine, so a reader never sees one
        without the other."""
        self._snapshot = Snapshot(source, Engine(source, self.config.server.as_of))
        self._freshness = None

    @property
    def source(self) -> Source:
        """The current source, for one-off callers; a request takes `current()` instead."""
        return self.current().source

    @source.setter
    def source(self, value: Source) -> None:
        self.swap(value)

    @property
    def engine(self) -> Engine:
        """The current engine, for one-off callers; a request takes `current()` instead."""
        return self.current().engine

    @cached_property
    def checker(self) -> PageChecker:
        """Headless Chromium for the page check, shared by the whole process."""
        return shared_checker()

    def data_through(self) -> dt.date | None:
        """The latest day with data of any metric, for the home page. Queried at startup and
        again when it's more than 10 minutes old, so the page stays cheap to load."""
        with self._freshness_lock:
            snapshot = self.current()
            now = time.monotonic()
            cached = self._freshness
            if cached is not None and cached[0] is snapshot and now - cached[1] < FRESHNESS_TTL_S:
                return cached[2]
            through = self._latest_data(snapshot)
            # A reload during the queries made this date the old catalog's: don't keep it.
            if self._snapshot is snapshot:
                self._freshness = (snapshot, now, through)
            return through

    def _latest_data(self, snapshot: Snapshot) -> dt.date | None:
        latest = None
        for metric in snapshot.source.metric_names:
            try:
                with query_origin("home page"):
                    through = snapshot.engine.data_through(metric, LOCAL_VIEWER)
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
        second_render: bool = True,
    ) -> Report:
        """Static checks, HTML checks, the dry run and the page check, against the current
        catalog; then the same for each of `check_states` (filter values). The layout is
        measured on desktop, and on `layout_devices` too.

        `second_render=False` skips rendering the page again in another browser language
        (R8.browser_locale). Only the test suite passes it, to stay fast: the server, the
        MCP tools and the CLI always render twice."""
        if metric_request_ids is None:
            metric_request_ids = self.registry.metric_request_ids()
        snapshot = self.current()
        return validate(
            spec,
            html,
            snapshot.source,
            metric_request_ids,
            as_of=snapshot.engine.today(),
            hosts=self.hosts,
            engine=snapshot.engine,
            viewer=viewer,
            page_check=functools.partial(
                self.check_page, second_render=second_render, source=snapshot.source
            ),
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
        second_render: bool = True,
        source: Source | None = None,
    ) -> list[Issue]:
        """Render the page with these block results, as a viewer's browser would. Labels and
        formats come from `source` (the snapshot a validation runs on), else the current one."""
        from prumi.server import pages  # the server package imports this module

        labels, formats = pages.labels_and_formats(spec, source or self.current().source)
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
            second_render,
        )

    def publish(
        self,
        spec: Any,
        html: str,
        viewer: Viewer = LOCAL_VIEWER,
        check_states: Sequence[Mapping[str, Any]] = (),
        second_render: bool = True,
    ) -> tuple[Report, Published | None]:
        """Validate (the default filter state and `check_states`), then store.
        `second_render` as for validate: tests only."""
        return publish(
            self.registry,
            spec,
            html,
            lambda s, h: self.validate(
                s, h, viewer, check_states=check_states, second_render=second_render
            ),
            viewer,
        )
