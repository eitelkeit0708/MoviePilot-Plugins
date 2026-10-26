"""Narrow, additive queue visibility for blocked serial handoffs and scope repair."""
import copy
import json
import time
import pytest

from test_dashboard import observe, walk
from test_plugin_contract import plugin  # noqa: F401 - pytest fixture


def rendered(p):
    return json.dumps(p.get_page(), ensure_ascii=False)


@pytest.mark.parametrize('state,phase,label', [
    ('waiting', 'planned', '核对字幕范围'),
    ('waiting', 'isolating', '隔离错配字幕'),
    ('waiting', 'complete', '等待齐套'),
    ('review', 'isolating', '异常待确认'),
    ('handed_off', 'complete', '已移交'),
])
def test_scope_repair_status_does_not_hide_errors_or_terminal_result(plugin, state, phase, label):
    job = observe(plugin.p, 1, state=state,
                  scope_repair={'schema': 1, 'phase': phase, 'items': []})
    assert any(node.get('text') == label for node in walk(plugin.p.get_page()))


def test_legacy_jobs_without_new_fields_keep_existing_rendering(plugin):
    p, activity = plugin.p, plugin.modules.activity
    job = observe(p, 1, state="waiting", files=[{"relative": "film.mkv", "uploaded": False}])
    assert activity.blocked_info(job) is None
    assert activity.scope_repair_info(job) is None
    assert not activity.attention(job)
    page = rendered(p)
    assert job["title"] in page and "文件 0/1" in page
    assert "队列等待" not in page and "范围修复" not in page


def test_season_context_comes_only_from_video_entries_and_keeps_poster(plugin):
    p, media = plugin.p, plugin.modules.media
    job = observe(
        p, 1, state="waiting",
        media={"image": "https://image.tmdb.org/t/p/w300/poster.jpg"},
        media_files=[
            {"file": "作品/Season 2/film.mkv", "title": "作品", "year": "2024",
             "type": "电视剧", "seasons": "S02", "episodes": "E03",
             "image": "https://image.tmdb.org/t/p/w300/poster.jpg"},
            {"file": "作品/Season 9/film.ass", "title": "作品", "year": "2024",
             "type": "电视剧", "seasons": "S09", "episodes": "E99",
             "image": "https://image.tmdb.org/t/p/w300/other.jpg"},
        ],
        files=[{"relative": "作品/Season 2/film.mkv", "uploaded": True},
               {"relative": "作品/Season 9/film.ass", "uploaded": False}])
    assert media.scope_context(job) == "S02 · E03"
    assert "S09" not in media.scope_context(job)
    line, poster = media.notification_media(job, {})
    assert "S02" in line[0] and "S09" not in line[0]
    assert poster == job["media"]["image"]
    nodes = list(walk(p.get_page()))
    assert any(n.get("text") == "作品 1" for n in nodes)
    assert any(n.get("props", {}).get("src") == job["media"]["image"] for n in nodes)
    assert "S02 · E03" in rendered(p) and "S09" not in rendered(p)

    path_job = observe(p, 2, media_files=[{"file": "作品/Season 4/E05.mkv", "title": "作品"}])
    assert media.scope_context(path_job) == "S04 · E05"


def test_blocked_wait_distinguishes_unsent_from_sent_move(plugin):
    p = plugin.p
    now = int(time.time())
    job = observe(p, 1, state="moving", created=now,
                  blocked_by={"batch": "previous-batch", "destination": "/115/inbox/同名作品",
                              "reason": "cleanup_pending", "since": now - 1860},
                  move_requested=True, work_handoffs=[{"phase": "prepared", "destination": "/115/inbox/同名作品",
                                                       "id": "1", "source": "/115/staging/x"}])
    p.view_records(plugin.modules.plugin.ViewRequest(key=job["id"]))
    page = rendered(p)
    assert "等待前序批次" in page and "previous-batch" in page
    assert "cleanup_pending" in page and "清理未完成" in page
    assert "已等待 31 分钟" in page
    assert "尚未发出移动" in page and "移动已发出" not in page

    job = p._store.get(job["id"])
    job["move_last_requested_at"] = now - 60
    job["work_handoffs"][0].update(phase="requested", requested_at=now - 60)
    p._store.save(job)
    page = rendered(p)
    assert "核对已发送移动" in page
    assert "移动已发出" in page and "尚未发出移动" not in page


def test_blocked_unknown_reason_and_missing_fields_do_not_crash(plugin):
    p, activity = plugin.p, plugin.modules.activity
    job = observe(p, 1, state="moving", move_requested=True,
                  blocked_by={"batch": "previous-batch", "reason": "future_reason"})
    info = activity.blocked_info(job)
    assert info["reason"] == "future_reason" and info["wait"] is None
    page = rendered(p)
    assert "future_reason" in page and "已等待时间：未记录" in page
    assert "前序批次" in page


def test_prolonged_block_attention_without_per_tick_notice_flood(plugin, monkeypatch):
    p, activity = plugin.p, plugin.modules.activity
    now = 1_900_000_000
    job = observe(p, 1, state="waiting", created=now,
                  blocked_by={"batch": "previous-batch", "reason": "prepared", "since": now - 1799})
    old = copy.deepcopy(job)
    monkeypatch.setattr(activity.time, "time", lambda: now)
    assert not activity.attention(job)
    job["blocked_by"]["since"] = now - 1800
    assert activity.attention(job)
    # The same durable block is not a new event on every tick; existing notice
    # dedupe therefore remains untouched and no network work is introduced.
    pending = p._store.pending_notices()
    for offset in range(0, 3600, 60):
        monkeypatch.setattr(activity.time, "time", lambda offset=offset: now + offset)
        assert activity.changes(old, job) == []
    assert p._store.pending_notices() == pending


def test_scope_repair_shows_isolated_subtitles_and_paths_but_never_delivers(plugin):
    p, activity = plugin.p, plugin.modules.activity
    job = observe(p, 1, state="waiting", files=[{"relative": "作品/视频.mkv", "uploaded": True}],
                  scope_repair={
                      "schema": 1, "phase": "complete",
                      "items": [
                          {"entry": {"local": "/organized/作品/字幕/a.ass", "relative": "作品/字幕/a.ass"},
                           "candidate": {"local": "/organized/.quarantine/作品/字幕/a.ass"},
                           "reason": "orphan_subtitle", "state": "done"},
                          {"entry": {"local": "/organized/作品/字幕/b.srt", "relative": "作品/字幕/b.srt"},
                           "candidate": {"quarantine": "/quarantine/作品/字幕/b.srt"},
                           "reason": "outside_scope", "state": "unuploaded"},
                          {"entry": {"local": "/organized/作品/视频.mkv"},
                           "candidate": {"local": "/quarantine/作品/视频.mkv"},
                           "reason": "duplicate", "state": "planned"},
                      ]})
    info = activity.scope_repair_info(job)
    assert info["subtitles"] == 2 and info["isolated"] == 1 and info["unuploaded"] == 1
    p.view_records(plugin.modules.plugin.ViewRequest(key=job["id"]))
    nodes = list(walk(p.get_page()))
    page = json.dumps(nodes, ensure_ascii=False)
    assert "范围修复" in page and "字幕 2/3 项" in page
    assert "/organized/作品/字幕/a.ass" in page
    assert "/organized/.quarantine/作品/字幕/a.ass" in page
    assert "/quarantine/作品/字幕/b.srt" in page
    assert "已隔离（未交付）" in page and "未上传，保留（未交付）" in page
    assert "均未交付，不纳入本次移交，也不会自动删除" in page
    assert "交付完成" not in page and "已上传" not in page
    calls = [n.get("events", {}).get("click", {}).get("api", "") for n in nodes]
    assert not any(url.endswith("/dispose") for url in calls)
    assert not any(url.endswith("/execute") for url in calls)


def test_scope_repair_unknown_phase_state_and_reason_are_tolerated(plugin):
    p, activity = plugin.p, plugin.modules.activity
    job = observe(p, 1, state="waiting",
                  scope_repair={"schema": 1, "phase": "future_phase",
                                "items": [{"entry": {"local": "/x/film.ass"},
                                           "state": "future_state", "reason": "future_reason"}]})
    info = activity.scope_repair_info(job)
    assert info["phase_label"] == "future_phase"
    assert info["rows"][0]["state_label"].startswith("future_state")
    page = rendered(p)
    assert "future_phase" in page and "future_state" in page and "future_reason" in page
    assert "未交付" in page


def test_cleanup_disabled_reason_is_truthful_for_retired_directories(plugin):
    p = plugin.p
    job = observe(p, 1, state="handed_off", cleanup_local=False, cleanup_reason="retired")
    p.view_records(plugin.modules.plugin.ViewRequest(key=job["id"]))
    page = rendered(p)
    assert "本地副本：保留 · 原因：retired" in page
    assert "目录已退役或回收" in page
    assert "SA 未消费" not in page

    job = p._store.get(job["id"])
    job.pop("cleanup_reason")
    job["inbox_cleanup"] = {"reason": "recycle"}
    p._store.save(job)
    page = rendered(p)
    assert "本地副本：保留 · 原因：recycle" in page and "SA 未消费" not in page
