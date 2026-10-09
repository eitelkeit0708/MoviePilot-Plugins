import importlib
import json
import time

import pytest

from test_plugin_contract import plugin, add_job


def walk(items):
    for item in items:
        yield item
        yield from walk(item.get("content", []))


def observe(p, index, *, route="/organized/a", state="waiting", **fields):
    job = p._store.observe(instance=p.__class__.__name__, download_hash=str(index), downloader="qb",
                           title=f"作品 {index}", history_id=index, route_name="同名路线",
                           routing={**p._runtime.config.routing(), "local_root": route})
    job.update(state=state, **fields)
    p._store.save(job)
    return job


def test_board_filters_before_pagination_and_counts_are_exclusive(plugin):
    p = plugin.p
    problem = observe(p, 1, state="handed_off", cleanup_error="保留唯一副本")
    for i in range(2, 28):
        observe(p, i, state="handed_off", route="/organized/b" if i < 15 else "/organized/a")
    observe(p, 28, state="waiting_instant", files=[{"instant_misses": 1}])
    result = p._store.board()
    assert result["jobs"][0]["id"] == problem["id"]
    assert result["counts"] == {"all": 28, "attention": 1, "active": 1, "done": 26}
    result = p._store.board(route="/organized/a", status="done", page=1)
    assert result["total"] == 13 and len(result["jobs"]) == 1
    assert all(job["routing"]["local_root"] == "/organized/a" for job in result["jobs"])
    assert p._store.board(route="' OR 1=1 --")["total"] == 0
    assert p._store.board(status="attention", page=1000)["page"] == 0


@pytest.mark.parametrize("fields", [
    {"state": "review"}, {"state": "retrying"}, {"attempts": 1}, {"late_history_ids": [2]},
    {"cleanup_error": "kept"}, {"files": [{"instant_error_since": 1}]},
    {"state": "waiting", "created": 1},
])
def test_board_bucket_matches_existing_attention_semantics(plugin, fields):
    job = observe(plugin.p, 1, **fields)
    assert plugin.modules.activity.attention(job)
    assert plugin.p._store.board(status="attention")["total"] == 1


def test_no_false_error_from_zero_or_null_error_since(plugin):
    observe(plugin.p, 1, files=[{"instant_error_since": 0}, {"instant_error_since": None}])
    assert plugin.p._store.board()["counts"].get("attention", 0) == 0


def test_page_actions_are_valid_native_requests_and_details_keep_context(plugin):
    p = plugin.p
    job = observe(p, 1, state="review", message="整理文件不存在或不在配置的本地目录中")
    p.view_records(plugin.modules.plugin.ViewRequest(route="/organized/a", status="attention", expanded=job["id"]))
    nodes = list(walk(p.get_page()))
    assert any(n.get("text") == "处理异常" or n.get("text") == "收起详情" for n in nodes)
    full = next(n for n in nodes if n.get("text") == "完整记录")
    request = plugin.modules.plugin.ViewRequest(**full["events"]["click"]["params"])
    assert request.route == "/organized/a" and request.status == "attention" and request.key == job["id"]
    for node in nodes:
        click = node.get("events", {}).get("click", {})
        if click.get("api", "").endswith("/view"):
            plugin.modules.plugin.ViewRequest(**click["params"])
    rendered = json.dumps(nodes, ensure_ascii=False)
    assert "不会搜索或接管其他位置的文件" in rendered and "test-secret" not in rendered
    assert "VTextField" not in rendered  # Native PageRender cannot submit dynamic input values.


@pytest.mark.parametrize(("message", "title"), [
    ("现存批次只有字幕，没有对应媒体文件", "缺少对应视频"),
    ("整理文件不存在或不在配置的本地目录中", "本地文件缺失或目录不可读"),
    ("存量文件在接管后发生变化，已暂停处理", "文件与原清单不一致"),
    ("CD2 令牌无效或已过期", "连接或权限需要检查"),
    ("115 接口限流", "115 正在冷却"),
    ("暂存批次出现清单外文件，已停止上传和移交", "目标文件存在冲突"),
    ("未来版本未知错误", "处理暂未完成"),
])
def test_specific_remedies_retain_manifest_without_guessing(plugin, message, title):
    module = importlib.import_module("app.plugins.symediabatchbridge.remedies")
    job = observe(plugin.p, 1, state="review", message=message)
    before = json.dumps(job, sort_keys=True)
    assert module.remedy(job)["title"] == title
    assert json.dumps(job, sort_keys=True) == before


def test_completed_problem_does_not_offer_upload_retry(plugin):
    p = plugin.p
    job = observe(p, 1, state="handed_off", cleanup_error="做种源已删")
    p.view_records(plugin.modules.plugin.ViewRequest(expanded=job["id"]))
    tree = p.get_page()
    calls = [n.get("events", {}).get("click", {}).get("api", "") for n in walk(tree)]
    assert not any(url.endswith("/retry") for url in calls)
    assert "本地副本保留" in json.dumps(tree, ensure_ascii=False)


def test_pending_move_offers_verification_not_a_fresh_upload(plugin):
    p = plugin.p
    job = observe(p, 1, state="review", move_requested=True)
    p.view_records(plugin.modules.plugin.ViewRequest(expanded=job["id"]))
    tree = p.get_page()
    assert any(n.get("events", {}).get("click", {}).get("api", "").endswith("/recovery") for n in walk(tree))
    assert "SHA1" in json.dumps(tree, ensure_ascii=False)


def test_requesting_retry_does_not_claim_verified_recovery(plugin):
    p = plugin.p
    job = observe(p, 1, state="review", message="本地文件缺失")
    assert p.retry_batch(plugin.modules.plugin.RetryRequest(key=job["id"])).success
    events = p._store.events(job["id"])
    assert events[0]["kind"] == "retry_requested"
    assert p._store.get(job['id'])['state'] == 'review'
    assert p._store.board(status='attention')['total'] == 1
    assert not any(e["kind"] == "recovered" for e in events)


def test_unsealed_inventory_files_can_be_paged(plugin):
    p = plugin.p
    job = observe(p, 1, state="review", inventory_files=[{"local": f"/organized/a/{i}.ass"} for i in range(21)])
    p.view_records(plugin.modules.plugin.ViewRequest(key=job["id"]))
    nodes = list(walk(p.get_page()))
    next_page = next(n for n in nodes if n.get("text") == "下一页文件")
    p.view_records(plugin.modules.plugin.ViewRequest(**next_page["events"]["click"]["params"]))
    content = json.dumps(p.get_page(), ensure_ascii=False)
    assert "/organized/a/20.ass" in content and "/organized/a/19.ass" not in content


def test_posters_reuse_saved_media_and_render_untrusted_titles_as_text(plugin):
    p = plugin.p
    job = observe(p, 1, title="<script>alert(1)</script>", media={"image": "https://image.tmdb.org/t/p/w300/test.jpg"})
    nodes = list(walk(p.get_page()))
    assert any(n.get("props", {}).get("src") == job["media"]["image"] for n in nodes)
    assert not any(n.get("html") for n in nodes)
    assert any(n.get("text") == job["title"] for n in nodes)
