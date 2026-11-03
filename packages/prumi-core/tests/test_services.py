"""The services hold one snapshot (source and engine together) that a swap replaces whole."""

import datetime as dt
from dataclasses import replace

from prumi.issues import Report
from prumi.source import Source

from .conftest import FakeAdapter


def test_swap_replaces_source_and_engine_together(demo_services, fake_source):
    other_source = fake_source
    before = demo_services.current()
    demo_services.swap(other_source)
    after = demo_services.current()
    assert after.source is other_source and after.engine.source is other_source
    assert before.engine.source is before.source  # the old snapshot stays whole


def test_current_is_the_same_snapshot_until_a_swap(demo_services, fake_source):
    assert demo_services.current() is demo_services.current()
    first = demo_services.current()
    demo_services.swap(fake_source)
    assert demo_services.current() is not first


def test_source_and_engine_read_the_current_snapshot(demo_services, fake_source):
    demo_services.swap(fake_source)
    assert demo_services.source is fake_source
    assert demo_services.engine is demo_services.current().engine


def test_the_engine_keeps_the_configured_as_of_after_a_swap(demo_services, fake_source):
    demo_services.swap(fake_source)
    assert demo_services.engine.as_of == demo_services.config.server.as_of


def _source_through(date):
    """A source over the suite's fake adapter whose freshness query answers `date`."""
    source = Source("default", FakeAdapter())
    execute = source.adapter.execute

    def run(query, viewer):
        result = execute(query, viewer)
        if query.window is not None:
            return result
        row = tuple(
            date if column.kind == "time" else value
            for column, value in zip(result.columns, result.rows[0], strict=True)
        )
        return replace(result, rows=(row,))

    source.adapter.execute = run
    return source


def test_a_swap_empties_the_data_through_cache(demo_services, fake_source):
    demo_services.swap(fake_source)
    assert demo_services.data_through() == dt.date(2026, 10, 14)
    # A later warehouse date shows on the next call, inside the 10-minute TTL.
    demo_services.swap(_source_through(dt.date(2026, 10, 20)))
    assert demo_services.data_through() == dt.date(2026, 10, 20)


def test_data_through_computed_on_an_old_snapshot_is_not_kept(
    demo_services, fake_source, monkeypatch
):
    demo_services.swap(fake_source)
    newer = _source_through(dt.date(2026, 11, 1))
    engine = demo_services.current().engine
    real = engine.data_through

    def swapping(metric, viewer):
        demo_services.swap(newer)  # a reload lands while the old snapshot is being asked
        return real(metric, viewer)

    monkeypatch.setattr(engine, "data_through", swapping)
    assert demo_services.data_through() == dt.date(2026, 10, 14)  # the old snapshot's answer
    # Not kept: the next call computes again, on the new snapshot.
    assert demo_services.data_through() == dt.date(2026, 11, 1)


def test_validation_uses_one_snapshot_even_if_a_swap_happens_meanwhile(
    demo_services, fake_source, monkeypatch
):
    other_source = fake_source
    seen = {}

    def fake_validate(spec, html, source, ids, *, engine, page_check, **kw):
        seen["source"], seen["engine_source"] = source, engine.source
        demo_services.swap(other_source)
        seen["page_check_source"] = page_check.keywords["source"]
        return Report()

    monkeypatch.setattr("prumi.services.validate", fake_validate)
    original = demo_services.current().source
    demo_services.validate({}, "")
    assert seen == {
        "source": original,
        "engine_source": original,
        "page_check_source": original,
    }
