"""Regressions for 1.4.1. External HTTP and host services use the existing fixture."""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from test_plugin import env, client, configured, http_error, NS  # noqa: F401


def test_candidate_info_and_debug_show_provenance(env, caplog):
    p = configured(env)
    title = 'Original [Corrected] 2024 S02E03'
    env.metadata[title] = NS(name='Original', year='2024', begin_season=2, begin_episode=3)
    env.replies.append('{"name":"Corrected","year":"2024"}')
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    info = '\n'.join(r.message for r in caplog.records if r.levelno == logging.INFO)
    assert 'name="Corrected"' in info and 'year="2024"' in info
    assert 'source=api' in info and 'id=' in info and 'elapsed_ms=' in info
    assert title in caplog.text
    assert 'season=2' in caplog.text and 'episode=3' in caplog.text
    assert 'season_episode_source=MetaInfo' in caplog.text
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'source=cache' in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    assert len(env.requests) == 1


def test_per_call_source_is_not_inferred_from_global_counters(env):
    c = client(env)
    assert hasattr(c, 'get_media_result'), 'diagnostic lookup must preserve get_media_name compatibility'
    a = c.get_media_result('Example')
    b = c.get_media_result('Example')
    assert a.identity == b.identity == {'name': 'Example', 'year': ''}
    assert (a.source, a.attempts, b.source, b.attempts) == ('api', 1, 'cache', 0)
    assert a.reason == b.reason == 'accepted'


@pytest.mark.parametrize('raw,reason', [
    ('{"name":"","year":""}', 'no_name'), ('{', 'invalid_json'),
    ('[]', 'not_object'), ('{"name":"Example","year":null}', 'field_type'),
    ('{"name":"Example","year":"","season":0}', 'field_set'),
    ('{"name":"Example","name":"B","year":""}', 'duplicate_key'),
    ('{"name":"Other","year":""}', 'name_not_in_input'),
    ('{"name":"Example","year":"2099"}', 'year_not_in_input'),
])
def test_rejection_reason_survives_negative_cache(env, raw, reason):
    c = client(env)
    assert hasattr(c, 'get_media_result')
    env.replies.append(raw)
    first = c.get_media_result('Example 2024')
    second = c.get_media_result('Example 2024')
    assert first.identity is None and second.identity is None
    assert first.reason == second.reason == reason
    assert first.source == 'api' and second.source == 'cache'
    assert len(env.requests) == 1
    assert c.key_health()[0]['disabled'] is False


def test_validation_warning_deduplicates_without_error_notification(env, caplog):
    p = configured(env, negative_ttl=0, notify=True)
    env.replies.extend(['[]', '[]'])
    with caplog.at_level(logging.DEBUG):
        for _ in range(2):
            p.recognize(NS(event_data={'title': 'Example'}))
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and 'not_object' in warnings[0]
    assert 'reason=not_object' in caplog.text
    assert env.notifications == []


def test_abstention_is_not_an_api_warning(env, caplog):
    p = configured(env)
    env.replies.append('{"name":"","year":""}')
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': 'Example'}))
    assert 'reason=no_name' in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_safe_details_redact_credentials_urls_and_control_characters(env, caplog):
    p = configured(env)
    title = '[Corrected] 2024 https://pt.example/announce?passkey=PRIVATE123 ' \
            'test-key-a\nAuthorization: Bearer SECRET456\u202e'
    env.replies.append('{"name":"Corrected","year":"2024"}')
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'Corrected' in caplog.text
    assert all(s not in caplog.text for s in ('test-key-a', 'PRIVATE123', 'SECRET456', '\u202e'))
    assert all('\n' not in r.message for r in caplog.records)
    assert '[REDACTED]' in caplog.text or '[URL]' in caplog.text


@pytest.mark.parametrize('original', ['', '   ', None])
def test_empty_prompt_restore_is_persisted(env, original):
    configured(env, customize_prompt=original, restore_prompt=True, private_extra='preserve')
    assert env.saved[-1]['customize_prompt'] == env.recognition.DEFAULT_PROMPT
    assert env.saved[-1]['private_extra'] == 'preserve'
    assert env.saved[-1]['restore_prompt'] is False


@pytest.mark.parametrize('name,year,title', [
    ('1917', '1917', '1917.1080p'),
    ('Class of 2024', '2024', 'Class.of.2024.1080p'),
    ('作品甲', '2024', '[作品甲] 2024-09-12 1080p'),
])
def test_ambiguous_year_not_accepted(env, name, year, title):
    assert env.recognition.parse_identity(json.dumps({'name':name,'year':year}), title) is None


def test_numeric_name_with_independent_year_still_valid(env):
    assert env.recognition.parse_identity('{"name":"1917","year":"2019"}', '1917.2019.1080p')
    assert env.recognition.parse_identity('{"name":"Class of 2024","year":"2025"}',
                                          'Class.of.2024.2025.1080p')


@pytest.mark.parametrize('name', ['1080p', 'WEB-DL', 'HDR10+', 'x265', '国语中字'])
def test_technical_label_cannot_be_candidate(env, name):
    assert env.recognition.parse_identity(json.dumps({'name':name,'year':''}), f'[{name}][作品甲]') is None


def test_release_group_cannot_replace_name_when_mp2_provides_team(env, caplog):
    p = configured(env)
    title = '[HHWEB][作品甲]'
    env.metadata[title] = NS(name='作品甲', year=None, begin_season=None,
                             begin_episode=None, resource_team='HHWEB')
    env.replies.append('{"name":"HHWEB","year":""}')
    event = NS(event_data={'title':title})
    with caplog.at_level(logging.DEBUG):
        p.recognize(event)
    assert event.event_data == {'title':title}
    assert 'name_is_release_group' in caplog.text


def test_cache_stats_exclude_expired_items(env):
    clock = [0]
    cache = env.cache.TTLCache(timer=lambda: clock[0])
    cache.get_or_load('a', lambda: ('value', 5), 1)
    clock[0] = 6
    assert cache.stats()['size'] == 0


def test_later_failure_cannot_shorten_retry_after(env, monkeypatch):
    c = client(env)
    clock = [1000.0]
    monkeypatch.setattr(env.api.time, 'monotonic', lambda: clock[0])
    for code, delay in [(429, 600), (503, None)]:
        response = http_error(code, retry_after=delay)
        response.request = httpx.Request('POST', 'https://example.test')
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            c._classify(exc, 0)
        clock[0] += 1
    assert c._provider_cooldown == (1600.0, 'rate_limit')


def test_usage_only_counts_actual_responses_not_cache_replays(env):
    c = client(env)
    env.replies.append(httpx.Response(200, json={
        'choices':[{'message':{'content':'{"name":"Example","year":""}'}, 'finish_reason':'stop'}],
        'usage':{'prompt_tokens':120, 'completion_tokens':12, 'prompt_cache_hit_tokens':80,
                 'prompt_cache_miss_tokens':40, 'not_usage':'SECRET'},
    }))
    c.get_media_name('Example')
    c.get_media_name('Example')
    stats = c.stats()
    assert stats.get('prompt_tokens') == 120 and stats.get('completion_tokens') == 12
    assert stats['prompt_cache_hit_tokens'] == 80
    assert stats['recognition_api_calls'] == 1
    assert 'SECRET' not in json.dumps(stats)


def test_singleflight_reports_follower_not_api(env):
    c = client(env)
    assert hasattr(c, 'get_media_result')
    started, release = threading.Event(), threading.Event()
    def reply(_):
        started.set()
        assert release.wait(3)
        return '{"name":"Example","year":""}'
    env.replies.append(reply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(c.get_media_result, 'Example')
        assert started.wait(2)
        follower = pool.submit(c.get_media_result, 'Example')
        deadline = time.monotonic() + 2
        while c.stats()['coalesced'] == 0 and time.monotonic() < deadline:
            release.wait(0.002)
        assert c.stats()['coalesced'] == 1
        release.set()
        a, b = leader.result(), follower.result()
    assert (a.source, b.source) == ('api', 'coalesced')
    assert (a.attempts, b.attempts) == (1, 0)
    assert a.identity == b.identity


def test_existing_result_has_skip_diagnostic_without_requests(env, caplog):
    p = configured(env)
    event = NS(event_data={'title':'Example', 'name':'Existing'})
    with caplog.at_level(logging.DEBUG):
        p.recognize(event)
    assert 'reason=existing_result' in caplog.text
    assert not env.requests


@pytest.mark.parametrize('header,secrets', [
    ('Authorization: Basic YWJjOjEyMw==', ['YWJjOjEyMw==']),
    ('Cookie: uid=USER_SECRET; session=SESSION_SECRET', ['USER_SECRET','SESSION_SECRET']),
])
def test_embedded_headers_fully_redacted(env, header, secrets):
    c = client(env)
    assert all(secret not in c.log_text('Example '+header) for secret in secrets)


def test_surrogate_input_is_skipped_without_throwing(env, caplog):
    p = configured(env)
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title':'Example\ud800'}))
    assert not env.requests
    assert 'reason=invalid_title' in caplog.text


def test_error_diagnostic_includes_http_status_without_body(env, caplog):
    p = configured(env)
    env.replies.append(http_error(402))
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title':'Example'}))
    assert 'http=402' in caplog.text
    assert 'must not leak' not in caplog.text


def test_negative_name_year_reason_is_specific(env):
    inspect = env.recognition.inspect_identity
    assert inspect('{"name":"1917","year":"1917"}', '1917.1080p')[1] == 'year_in_name'
    assert inspect('{"name":"Example","year":"2024"}', 'Example 2024-09-12')[1] == 'year_is_date'


def test_usage_ignores_non_integer_and_unknown_fields(env):
    c = client(env)
    env.replies.append(httpx.Response(200, json={
        'choices':[{'message':{'content':'{"name":"Example","year":""}'}}],
        'usage':{'prompt_tokens':True, 'completion_tokens':-5,
                 'prompt_cache_hit_tokens':'sensitive-text', 'secret':'no'},
    }))
    c.get_media_name('Example')
    data = c.stats()
    assert 'usage_responses' not in data
    assert 'sensitive-text' not in json.dumps(data)


def test_meta_date_cannot_restore_rejected_year(env):
    result = env.recognition.build_event_result('Example 2024-09-12',
        {'name':'Example','year':''}, NS(year='2024',begin_season=None,begin_episode=None))
    assert result['year'] is None
