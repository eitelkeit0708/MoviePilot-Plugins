"""A received route snapshot must win over subsequent history backfill."""

from threading import Event, Thread
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from test_plugin_contract import plugin


def row(identifier, *, date):
    return NS(id=identifier, title=f"作品 {identifier}", download_hash=f"torrent-{identifier}",
              downloader="qb", date=date)


def test_backfill_waits_for_more_than_one_page_of_received_snapshots(plugin):
    p = plugin.p
    activated = p._store.meta("activated_at")
    for identifier in range(1, 102):
        p.on_transfer(NS(event_data={"transfer_history_id": identifier}))
    p.init_plugin({**plugin.values, "inbox": "/115/new-inbox", "delete_local": True})
    plugin.host.transfers.get.side_effect = lambda identifier: row(identifier, date=activated)
    plugin.host.histories_since.return_value = [row(101, date=activated)]

    p._receive_pending(p._runtime)
    assert p._store.incoming_count() == 1
    cursor = p._store.meta("history_cursor")
    p._recover_history(p._runtime)
    plugin.host.histories_since.assert_not_called()
    assert p._store.meta("history_cursor") == cursor
    assert not any(job["history_id"] == 101 for job in p._store.jobs())

    p._receive_pending(p._runtime)
    p._recover_history(p._runtime)
    jobs = p._store.jobs()
    assert len(jobs) == 101
    assert all(job["routing"]["inbox"] == plugin.values["inbox"] for job in jobs)
    assert all(job["cleanup_local"] is False for job in jobs)
    assert p._store.incoming_count() == 0


def test_delayed_intake_cannot_be_overtaken_by_backfill(plugin, monkeypatch):
    p = plugin.p
    activated = p._store.meta("activated_at")
    now = plugin.modules.store.time.time()
    monkeypatch.setattr(plugin.modules.store.time, "time", lambda: now)
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    p.init_plugin({**plugin.values, "inbox": "/115/new-inbox", "delete_local": True})
    plugin.host.transfers.get.side_effect = RuntimeError("temporary DB unavailability")
    plugin.host.histories_since.return_value = [row(7, date=activated), row(8, date=activated)]

    p._receive_pending(p._runtime)
    assert p._store.incoming_count() == 1 and not p._store.pending_transfers()
    p._recover_history(p._runtime)
    plugin.host.histories_since.assert_not_called()
    assert not p._store.jobs()

    now += 61
    plugin.host.transfers.get.side_effect = lambda identifier: row(identifier, date=activated)
    p._receive_pending(p._runtime)
    p._recover_history(p._runtime)
    jobs = {job["history_id"]: job for job in p._store.jobs()}
    assert jobs[7]["routing"]["inbox"] == plugin.values["inbox"]
    assert jobs[7]["cleanup_local"] is False
    assert jobs[8]["routing"]["inbox"] == "/115/new-inbox"
    assert jobs[8]["cleanup_local"] is True


@pytest.mark.parametrize("failure", ["missing", "exception"])
def test_later_event_for_same_torrent_cannot_change_first_route(plugin, monkeypatch, failure):
    p = plugin.p
    now = plugin.modules.store.time.time()
    monkeypatch.setattr(plugin.modules.store.time, "time", lambda: now)
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    p.init_plugin({**plugin.values, "inbox": "/115/new-inbox", "delete_local": True})
    now += 1
    p.on_transfer(NS(event_data={"transfer_history_id": 8}))

    def native_row(identifier):
        return NS(id=identifier, title="同一种子的两集", download_hash="shared-torrent", downloader="qb")

    def read(identifier):
        if identifier == 7:
            if failure == "exception":
                raise RuntimeError("temporary DB failure")
            return None
        return native_row(identifier)

    plugin.host.transfers.get.side_effect = read
    p._receive_pending(p._runtime)
    assert [call.args[0] for call in plugin.host.transfers.get.call_args_list] == [7]
    assert p._store.incoming_count() == 2 and not p._store.jobs()
    # A second drain in this media round must not skip the older backoff record.
    p._receive_pending(p._runtime)
    assert not p._store.pending_transfers()
    assert plugin.host.transfers.get.call_count == 1

    now += 61
    plugin.host.transfers.get.side_effect = native_row
    p._receive_pending(p._runtime)
    assert p._store.incoming_count() == 0
    assert len(p._store.jobs()) == 1
    saved = p._store.jobs()[0]
    assert saved["history_id"] == 7
    assert saved["routing"]["inbox"] == plugin.values["inbox"]
    assert saved["cleanup_local"] is False


def test_stalled_recovery_cannot_skip_an_earlier_unresolved_record(plugin):
    p = plugin.p
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    p.on_transfer(NS(event_data={"transfer_history_id": 8}))
    # A persisted later retry must not overtake an earlier newly received/replayed
    # event, even when the later record already qualifies for a full-history read.
    for _ in range(2):
        p._store.transfer_result(8, 1, False)
    assert p._store.stalled_transfers() == []
    p._recover_intake(p._runtime)
    plugin.host.histories_since.assert_not_called()
    assert p._store.incoming_count() == 2 and not p._store.jobs()


def test_full_history_recovery_stops_at_first_observation_failure(plugin, monkeypatch):
    p = plugin.p
    for identifier in (7, 8):
        p.on_transfer(NS(event_data={"transfer_history_id": identifier}))
        for _ in range(2):
            p._store.transfer_result(identifier, 1, False)
    activated = p._store.meta("activated_at")
    plugin.host.histories_since.return_value = [row(7, date=activated), row(8, date=activated)]
    observe = Mock(side_effect=RuntimeError("temporary observation failure"))
    monkeypatch.setattr(p, "_observe_received", observe)

    with pytest.raises(RuntimeError):
        p._recover_intake(p._runtime)
    assert observe.call_count == 1 and observe.call_args.args[1].id == 7
    assert p._store.incoming_count() == 2 and not p._store.jobs()


def test_unconfirmed_full_history_does_not_mark_events_missing(plugin, monkeypatch):
    p = plugin.p
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    for _ in range(2):
        p._store.transfer_result(7, 1, False)
    # Use the production host wrapper so None cannot silently become an empty
    # successful listing before the intake recovery sees it.
    native = plugin.modules.host.MPHost.__new__(plugin.modules.host.MPHost)
    native.transfers = NS(list_by_date=Mock(return_value=None))
    monkeypatch.setattr(plugin.host, "histories_since", native.histories_since)
    cursor = p._store.meta("history_cursor")
    with pytest.raises(plugin.modules.domain.Awaiting):
        p._recover_history(p._runtime)
    assert p._store.incoming_count() == 1 and p._store.missing_transfer_count() == 0
    assert p._store.meta("history_cursor") == cursor

    native.transfers.list_by_date.return_value = []
    p._recover_history(p._runtime)
    assert p._store.incoming_count() == 0 and p._store.missing_transfer_count() == 1


def test_unresolved_intake_does_not_stop_existing_media_batches(plugin, monkeypatch):
    from test_plugin_contract import add_job
    p = plugin.p
    existing = add_job(p)
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    p.on_transfer(NS(event_data={"transfer_history_id": 8}))
    plugin.host.transfers.get.return_value = None
    processed = []
    monkeypatch.setattr(plugin.modules.plugin.Engine, "process", lambda self, job: processed.append(job["id"]))

    p.check_batches()

    assert processed == [existing["id"]]
    assert p._store.incoming_count() == 2
    assert [call.args[0] for call in plugin.host.transfers.get.call_args_list] == [7]


def test_new_intake_during_backfill_keeps_cursor_for_retry(plugin):
    p = plugin.p
    activated = p._store.meta("activated_at")
    cursor = p._store.meta("history_cursor")

    def history_rows():
        yield row(1, date=activated)
        p.on_transfer(NS(event_data={"transfer_history_id": 2}))
        yield row(2, date=activated)

    plugin.host.histories_since.return_value = history_rows()
    p._recover_history(p._runtime)

    assert {job["history_id"] for job in p._store.jobs()} == {1}
    assert p._store.incoming_count() == 1
    assert p._store.meta("history_cursor") == cursor
    plugin.host.transfers.get.return_value = row(2, date=activated)
    p._receive_pending(p._runtime)
    assert {job["history_id"] for job in p._store.jobs()} == {1, 2}


def test_deleted_native_history_is_preserved_without_blocking_backfill(plugin, monkeypatch):
    p = plugin.p
    activated = p._store.meta("activated_at")
    now = plugin.modules.store.time.time()
    monkeypatch.setattr(plugin.modules.store.time, "time", lambda: now)
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    plugin.host.transfers.get.return_value = None
    plugin.host.histories_since.return_value = [row(8, date=activated)]
    p._receive_pending(p._runtime)
    p._recover_history(p._runtime)
    plugin.host.histories_since.assert_not_called()
    now += 61
    p._receive_pending(p._runtime)
    p._recover_history(p._runtime)
    assert p._store.incoming_count() == 0 and p._store.missing_transfer_count() == 1
    assert {job["history_id"] for job in p._store.jobs()} == {8}
    # Snapshot is still available if MP sends this event again.
    p.init_plugin({**plugin.values, "inbox": "/115/new-inbox"})
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    assert p._store.pending_transfers()[0][2]["routes"][0]["inbox"] == plugin.values["inbox"]


def test_history_fallback_uses_original_event_route_after_get_errors(plugin, monkeypatch):
    p = plugin.p
    now = plugin.modules.store.time.time()
    monkeypatch.setattr(plugin.modules.store.time, "time", lambda: now)
    p.on_transfer(NS(event_data={"transfer_history_id": 7}))
    p.init_plugin({**plugin.values, "inbox": "/115/new-inbox"})
    plugin.host.transfers.get.side_effect = RuntimeError("temporary database failure")
    plugin.host.histories_since.side_effect = RuntimeError("database failure")
    p._receive_pending(p._runtime)
    now += 61
    p._receive_pending(p._runtime)
    import pytest
    with pytest.raises(RuntimeError):
        p._recover_history(p._runtime)
    assert p._store.incoming_count() == 1 and p._store.missing_transfer_count() == 0
    plugin.host.histories_since.side_effect = None
    plugin.host.histories_since.return_value = [row(7, date=p._store.meta("activated_at"))]
    p._recover_history(p._runtime)
    assert p._store.incoming_count() == 0
    assert p._store.jobs()[0]["routing"]["inbox"] == plugin.values["inbox"]


def test_reload_cannot_mix_route_and_cleanup_options_in_intake(plugin, monkeypatch):
    p = plugin.p
    original_receive = p._store.receive_transfer
    entered, release, reloaded = Event(), Event(), Event()
    errors = []

    def blocked_receive(identifier, context):
        entered.set()
        assert release.wait(5)
        original_receive(identifier, context)

    monkeypatch.setattr(p._store, "receive_transfer", blocked_receive)

    def receive():
        try:
            p.on_transfer(NS(event_data={"transfer_history_id": 42}))
        except Exception as error:
            errors.append(error)

    def reload():
        try:
            p.init_plugin({**plugin.values, "inbox": "/115/new-inbox", "delete_local": True})
            reloaded.set()
        except Exception as error:
            errors.append(error)

    intake = Thread(target=receive)
    reload_worker = Thread(target=reload)
    intake.start()
    try:
        assert entered.wait(3)
        acquired = p._lifecycle.acquire(blocking=False)
        if acquired:
            p._lifecycle.release()
        assert not acquired, "intake snapshot must exclude concurrent reconfiguration"
        # Holding this short lifecycle section does not take the long media lock.
        with p._store.worker_lock() as acquired:
            assert acquired
        reload_worker.start()
    finally:
        release.set()
        intake.join(5)
        if reload_worker.ident is not None:
            reload_worker.join(5)

    assert not intake.is_alive() and not reload_worker.is_alive() and not errors
    assert reloaded.is_set()
    identifier, _, context = p._store.pending_transfers()[0]
    assert identifier == 42
    assert context["routes"][0]["inbox"] == plugin.values["inbox"]
    assert context["cleanup_local"] is False
