from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from test_adapters import native, existing
from test_batches import batch, inventory_batch
from test_plugin_contract import plugin, add_job


POSTER = "https://image.tmdb.org/t/p/w500/poster.jpg"
MEDIA = dict(title="花儿与少年", year="2014", type="电视剧", tmdbid="121876", image=POSTER)


def handoff():
    return dict(kind="handoff", message="整目录已移交 Symedia 待归档目录")


@pytest.mark.parametrize("fixture", ["native", "existing"])
def test_actual_transfer_metadata_uses_selected_files_only(request, fixture):
    adapter = request.getfixturevalue(fixture)
    for row in adapter.rows:
        vars(row).update(MEDIA, seasons="S06", episodes="E13")
    # Another season from the same download is not part of the adopted manifest.
    if fixture == "existing":
        other = adapter.video.with_name("unselected.mkv")
        other.write_bytes(b"extra")
        adapter.rows.append(NS(**{**vars(adapter.rows[0]), "id": 99, "dest": str(other), "seasons": "S07"}))
    members = adapter.host.collect(adapter.job)
    assert len(members) == 2
    assert all(m["media"]["episodes"] == "E13" and m["media"]["seasons"] == "S06" for m in members)
    assert all(m["media"]["image"] == POSTER for m in members)


def test_resumed_inventory_keeps_hashes_and_persists_media_notice(inventory_batch, modules, monkeypatch):
    b = inventory_batch
    for row in b.inventory_rows:
        vars(row).update(MEDIA, seasons="S06", episodes="E13")
    # Simulate a 1.4.2 batch that already has HASH/receipts but no media context.
    b.engine._sync_candidates(b.job(), b.inventory_host.collect(b.job()))
    job = b.job()
    job.pop("media_files")
    b.store.save(job)
    monkeypatch.setattr(modules.engine, "freeze_file", Mock(side_effect=AssertionError("must reuse HASH")))
    b.run()
    assert b.job()["state"] == "handed_off"
    restored = modules.store.Store(b.store.path.parent)
    notice = restored.pending_notices()[0]
    assert notice["image"] == POSTER
    assert "花儿与少年 (2014) · 电视剧 · S06E13" in notice["text"]
    assert notice["text"].count("S06E13") == 1  # Subtitle does not duplicate episode scope.
    assert Path(b.paths[0]).name in notice["text"] and Path(b.paths[1]).name in notice["text"]
    restored.notice_result(notice, True)
    b.run()
    assert not restored.pending_notices()
    assert len(b.cloud.moves) == 1


def test_gaps_multiple_seasons_and_subtitles_are_not_reported_as_full_season(batch, modules):
    job = batch.job()
    job["media_files"] = [{**MEDIA, "file": f"{s}{e}{suffix}", "seasons": s, "episodes": e}
                          for s, e, suffix in [("S01", "E01", ".mkv"), ("S01", "E03", ".mkv"),
                                               ("S01", "E03", ".ass"), ("S02", "E02-E04", ".mkv")]]
    lines, image = modules.media.notification_media(job, handoff())
    assert lines == ["花儿与少年 (2014) · 电视剧 · S01E01、S01E03、S02E02-E04"]
    assert image == POSTER


def test_file_failure_and_fallback_show_only_affected_media(batch, modules):
    job = batch.job()
    job["media_files"] = [{**MEDIA, "file": "one.mkv", "seasons": "S06", "episodes": "E13"},
                          {**MEDIA, "file": "two.mkv", "seasons": "S06", "episodes": "E18"}]
    for kind in ("instant_error", "normal_start"):
        item = dict(kind=kind, message="正在处理", file="two.mkv")
        text = modules.activity.notice_text(job, item)
        assert "S06E18" in text and "S06E13" not in text
        assert "two.mkv" in text


def test_multiple_works_do_not_borrow_one_poster_or_merge_same_name_years(batch, modules):
    job = batch.job()
    job["media_files"] = [{**MEDIA, "file": "one.mkv"}, {**MEDIA, "year": "2026", "file": "two.mkv"}]
    lines, image = modules.media.notification_media(job, handoff())
    assert len(lines) == 2 and "2014" in lines[0] and "2026" in lines[1]
    assert image is None


def test_movie_has_year_and_poster_without_invented_episode(batch, modules):
    job = batch.job()
    job["media_files"] = [{**MEDIA, "title": "电影", "type": "电影", "file": "movie.mkv"}]
    assert modules.media.notification_media(job, handoff()) == (["电影 (2014) · 电影"], POSTER)


@pytest.mark.parametrize("image", ["", "/poster.jpg", "https://[invalid", "ftp://site/a.jpg", "https://user:secret@site/a.jpg"])
def test_bad_or_missing_poster_keeps_text_delivery(batch, modules, image):
    job = batch.job()
    job["media"] = {**MEDIA, "image": image, "seasons": "S06", "episodes": "E13"}
    lines, poster = modules.media.notification_media(job, handoff())
    assert poster is None
    assert "S06E13" not in lines[0]  # A first observation is not the complete batch.
    assert "花儿与少年" in lines[0]


def test_legacy_missing_media_uses_filenames_without_new_lookup(batch):
    batch.run()
    notice = batch.store.pending_notices()[0]
    assert "image" not in notice
    assert "S01E01.mkv" in notice["text"] and "S01E01.zh.ass" in notice["text"]


def test_legacy_subtitle_only_error_recovers_work_poster_but_not_batch_episode_scope(existing, modules):
    vars(existing.rows[1]).update(MEDIA, seasons="S06", episodes="E13")
    existing.job["inventory_files"] = existing.job["inventory_files"][1:]
    with pytest.raises(modules.domain.BridgeError, match="只有字幕"):
        existing.host.collect(existing.job)
    assert existing.job["media"]["image"] == POSTER
    lines, image = modules.media.notification_media(existing.job, handoff())
    assert image == POSTER and "S06E13" not in lines[0]


def test_mp_receives_durable_poster_and_retry_payload_unchanged(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    job["media_files"] = [{**MEDIA, "file": "video.mkv", "seasons": "S06", "episodes": "E13"}]
    now = 1_900_000_000
    monkeypatch.setattr(plugin.modules.store.time, "time", lambda: now)
    p.post_message.side_effect = RuntimeError("network down")
    job.update(state="handed_off", message="已移交")
    p._store.save(job)
    p._flush_notifications()
    first = p.post_message.call_args.kwargs.copy()
    assert first["image"] == POSTER and "S06E13" in first["text"]
    # Metadata changing after submission cannot alter a queued notification.
    job["media_files"][0]["episodes"] = "E99"
    p._store.save(job)
    now += 60
    p.post_message.side_effect = None
    p._flush_notifications()
    assert p.post_message.call_args.kwargs == first
    assert p.post_message.call_count == 2
    assert p._store.notice_counts(job["id"]) == (1, 1)


def test_initial_observation_stores_media_for_early_error(plugin):
    p = plugin.p
    row = NS(**MEDIA, seasons="S06", episodes="E13", id=42, download_hash="newhash", downloader="qb",
             dest="video.mkv", status=True)
    job = p._observe(p._runtime, row)
    assert p._store.get(job["id"])["media"]["image"] == POSTER
    job.update(state="review", message="缺少下载任务标识")
    p._store.save(job)
    p._flush_notifications()
    notice = p.post_message.call_args.kwargs
    assert notice["image"] == POSTER and "花儿与少年 (2014)" in notice["text"]
    assert "S06E13" not in notice["text"] and "0/0" not in notice["text"]
