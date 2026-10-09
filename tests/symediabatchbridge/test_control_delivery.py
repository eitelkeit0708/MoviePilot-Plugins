"""Real SQLite and OS-lock tests for reception/delivery during slow media work."""
import json
from threading import Event, Thread
from types import SimpleNamespace as NS
from unittest.mock import Mock

from test_plugin_contract import plugin, add_job


def test_intake_and_notification_continue_while_media_owner_is_blocked(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    entered, release = Event(), Event()
    errors = []

    def process(self, item):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(plugin.modules.plugin.Engine, "process", process)
    plugin.host.transfers.get.return_value = NS(id=8, title="新作品", download_hash="new", downloader="qb")

    def work():
        try:
            p.check_batches()
        except Exception as error:
            errors.append(error)

    worker = Thread(target=work)
    worker.start()
    try:
        assert entered.wait(3)
        p.on_transfer(NS(event_data={"transfer_history_id": 8}))
        assert p._store.incoming_count() == 1
        plugin.host.transfers.get.assert_not_called()
        assert "已接收 1 条整理记录" in json.dumps(p.get_page(), ensure_ascii=False)
        p._store.record(job, plugin.modules.activity.event(
            "attention", "接口暂不可用", level="warning", notice="处理异常", scope="issue"))
        p.post_message.assert_not_called()
        p._flush_notifications()
        p.post_message.assert_called_once()
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert p._store.incoming_count() == 0
    assert {j["title"] for j in p._store.jobs()} == {job["title"], "新作品"}


def test_slow_notification_does_not_hold_media_lock_or_replay_on_reload(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    job.update(state="review", message="等待授权恢复")
    p._store.save(job)
    entered, release = Event(), Event()

    def post(**kwargs):
        entered.set()
        assert release.wait(5)

    p.post_message.side_effect = post
    sender = Thread(target=p._flush_notifications)
    sender.start()
    try:
        assert entered.wait(3)
        with p._store.worker_lock() as acquired:
            assert acquired
        old = p._runtime
        p.init_plugin(plugin.values)
        assert old.stop.is_set()
        # Even a separate plugin object at the same data path cannot duplicate
        # a still-running delivery after reload.
        other = plugin.modules.plugin.SymediaBatchBridge()
        other.init_plugin(plugin.values)
        other._flush_notifications()
        assert p.post_message.call_count == 1
    finally:
        release.set()
        sender.join(5)
    assert not sender.is_alive()
    p._flush_notifications()
    assert p.post_message.call_count == 1
    assert not p._store.pending_notices()
    other.stop_service()


def test_received_events_survive_restart_and_preserve_route(plugin, monkeypatch):
    p = plugin.p
    plugin.host.transfers.get.return_value = NS(id=9, title="已接收", download_hash="incoming", downloader="qb")
    p.on_transfer(NS(event_data={"transfer_history_id": 9}))
    p.on_transfer(NS(event_data={"transfer_history_id": 9}))
    assert p._store.incoming_count() == 1
    assert "test-secret" not in str(p._store.pending_transfers())
    p.init_plugin({**plugin.values, "inbox": "/115/changed", "delete_local": True})
    processed = []
    monkeypatch.setattr(plugin.modules.plugin.Engine, "process", lambda self, item: processed.append(item))
    p.check_batches()
    assert len(processed) == 1
    assert processed[0]["routing"]["inbox"] == plugin.values["inbox"]
    assert processed[0]["cleanup_local"] is False
    assert p._store.incoming_count() == 0


def test_receipt_ack_cannot_drop_a_new_event_and_failures_retry(plugin, monkeypatch):
    store = plugin.p._store
    store.receive_transfer(5)
    identifier, revision, _ = store.pending_transfers()[0]
    store.receive_transfer(5)
    store.transfer_result(identifier, revision, True)
    assert store.incoming_count() == 1
    now = 1_900_000_000
    monkeypatch.setattr(plugin.modules.store.time, "time", lambda: now)
    plugin.host.transfers.get.side_effect = RuntimeError("connection token secret")
    plugin.p._receive_pending(plugin.p._runtime)
    assert store.incoming_count() == 1 and not store.pending_transfers()
    now += 61
    plugin.host.transfers.get.side_effect = None
    plugin.host.transfers.get.return_value = NS(id=5, title="作品", download_hash="new", downloader="qb")
    plugin.p._receive_pending(plugin.p._runtime)
    assert store.incoming_count() == 0 and len(store.jobs()) == 1


def test_cooldown_extension_is_monotonic_and_survives_reopen(plugin):
    first = plugin.p._store
    second = plugin.modules.store.Store(first.path.parent)
    assert first.extend_cloud_cooldown(9000, "115 接口限流")["until"] == 9000
    assert second.extend_cloud_cooldown(8000, "较早响应")["until"] == 9000
    assert first.meta("u115_cloud_cooldown")["reason"] == "115 接口限流"
    assert second.extend_cloud_cooldown(10000, "延长冷却")["until"] == 10000
    assert plugin.modules.store.Store(first.path.parent).meta("u115_cloud_cooldown")["until"] == 10000


def test_services_and_receivers_stop_without_new_side_effects(plugin):
    p = plugin.p
    services = p.get_service()
    assert len(services) == 2 and len({s["id"] for s in services}) == 2
    assert services[0]["func"] == p.check_batches
    assert services[1]["func"] == p._flush_notifications
    p.stop_service()
    p.on_transfer(NS(event_data={"transfer_history_id": 42}))
    p._flush_notifications()
    assert p._store.incoming_count() == 0
    p.post_message.assert_not_called()
