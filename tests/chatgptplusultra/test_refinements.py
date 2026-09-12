"""1.4.2: source-scoped aliases and diagnostic-only identity comparison.

Uses the existing HTTP boundary fixture. Does not patch the host parser or
claim that current-title MetaInfo equals the original caller's metadata.
"""
import json
import logging

import pytest
from test_plugin import env, configured, client, NS  # noqa: F401


@pytest.mark.parametrize('title,name', [
    ('The Stain Directors Cut 2026 [污点 / บุปผาราตรี / To Be Named Movie / Lady of the Night / Buppha the Movie / The Stain]', '污点'),
    ('[污点/Buppha the Movie/The Stain] 2026', '污点'),
    ('【污点／Buppha the Movie／The Stain】 2026', '污点'),
    ('Buppha the Movie 2026 [污点]', '污点'),
    ('[The Stain / Buppha the Movie] 2026', 'The Stain'),
    ('The.Stain.2026 [Buppha the Movie]', 'The Stain'),
    ('[作品甲 / 作品乙剧场版] 2026', '作品甲'),
    ('[作品甲] [作品乙电影版] 2026', '作品甲'),
    ('[作品甲【1080p】 / 作品乙剧场版] 2026', '作品甲'),
])
def test_unrelated_alias_marker_does_not_veto_selected_source(env, title, name):
    identity, reason = env.recognition.inspect_identity(
        json.dumps({'name': name, 'year': '2026'}, ensure_ascii=False), title)
    assert identity == {'name': name, 'year': '2026'}, reason
    assert reason == 'accepted'


@pytest.mark.parametrize('title,name', [
    ('[命运石之门剧场版：负荷领域的既视感] 2013', '命运石之门'),
    ('[劇場版 命運石之門：負荷領域的既視感] 2013', '命運石之門'),
    ('Steins;Gate.the.Movie.2013', 'Steins Gate'),
    ('Example 2013 [Example The Movie]', 'Example'),
    ('[Example The Movie / Example] 2013', 'Example'),
    ('[剧场版][命运石之门] 2013', '命运石之门'),
    ('[命运石之门]【剧场版】 2013', '命运石之门'),
    ('【電影版】【作品甲】 2013', '作品甲'),
    ('Example [The Movie] 2013', 'Example'),
    ('[The Movie] Example 2013', 'Example'),
])
def test_selected_source_cannot_drop_its_own_movie_marker(env, title, name):
    identity, reason = env.recognition.inspect_identity(
        json.dumps({'name': name, 'year': ''}, ensure_ascii=False), title)
    assert identity is None
    assert reason == 'movie_marker_lost'


def test_preserving_movie_identity_is_still_accepted(env):
    name = '命运石之门剧场版：负荷领域的既视感'
    assert env.recognition.parse_identity(json.dumps({'name': name, 'year': '2013'}),
                                           f'[{name}] 2013') == {'name': name, 'year': '2013'}


def test_current_title_unchanged_is_visible_but_still_submitted_and_cached(env, caplog):
    p = configured(env)
    title = '[最后的同意书] 1080p'
    env.metadata[title] = NS(name='最后的同意书', year=None, begin_season=None, begin_episode=None)
    env.replies.append('{"name":"最后的同意书","year":""}')
    clock = [0.0]
    p.openai._cache._timer = lambda: clock[0]
    with caplog.at_level(logging.DEBUG):
        event = NS(event_data={'title': title, 'context': 'preserve'})
        p.recognize(event)
    assert event.event_data['name'] == '最后的同意书'
    assert event.event_data['context'] == 'preserve'
    assert 'status=submitted reason=unchanged_current_title' in caplog.text
    assert 'identity_scope=current_title' in caplog.text
    assert 'name="最后的同意书"' in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    caplog.clear()
    clock[0] = 601.0  # Must keep a valid positive identity; not a 600-second negative.
    with caplog.at_level(logging.DEBUG):
        repeat = NS(event_data={'title': title})
        p.recognize(repeat)
    assert repeat.event_data['name'] == '最后的同意书'
    assert 'source=cache' in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    assert len(env.requests) == 1


def test_changed_identity_keeps_accepted_diagnostic(env, caplog):
    p = configured(env)
    title = '[Corrected] 2024'
    env.metadata[title] = NS(name='Original', year='2024', begin_season=None, begin_episode=None)
    env.replies.append('{"name":"Corrected","year":"2024"}')
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'status=submitted reason=accepted' in caplog.text
    assert 'unchanged_current_title' not in caplog.text


def test_cached_identity_comparison_uses_this_calls_metadata(env, caplog):
    p = configured(env)
    title = 'Original [Corrected] 2024'
    env.replies.append('{"name":"Corrected","year":"2024"}')
    env.metadata[title] = NS(name='Corrected', year='2024', begin_season=None, begin_episode=None)
    p.recognize(NS(event_data={'title': title}))
    env.metadata[title] = NS(name='Original', year='2024', begin_season=None, begin_episode=None)
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'status=submitted reason=accepted source=cache' in caplog.text
    assert len(env.requests) == 1


def test_same_name_but_new_year_is_not_unchanged(env, caplog):
    p = configured(env)
    title = 'Example 2024'
    env.metadata[title] = NS(name='Example', year=None, begin_season=None, begin_episode=None)
    env.replies.append('{"name":"Example","year":"2024"}')
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'reason=accepted' in caplog.text
    assert 'reason=unchanged_current_title' not in caplog.text


def test_comparison_matches_mp2_returned_name_dot_normalization(env, caplog):
    p = configured(env)
    title = 'Example.Show.2024'
    env.metadata[title] = NS(name='Example Show', year='2024', begin_season=None, begin_episode=None)
    env.replies.append('{"name":"Example.Show","year":"2024"}')
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'reason=unchanged_current_title' in caplog.text


def test_native_episode_bug_is_not_silently_overridden(env):
    p = configured(env)
    title = 'GATE24 The Border S01E07 [大机场] 2026'
    env.metadata[title] = NS(name='Original', year='2026', begin_season=1, begin_episode=24)
    env.replies.append('{"name":"大机场","year":"2026"}')
    event = NS(event_data={'title': title})
    p.recognize(event)
    # Scope regression: retain existing adapter behavior, NOT an assertion E24 is correct.
    assert event.event_data['season'] == 1 and event.event_data['episode'] == 24


def test_different_quality_titles_remain_separate_cache_entries(env):
    c = client(env)
    assert c._extract_cache_key('[作品甲] 2026 1080p') != c._extract_cache_key('[作品甲] 2026 2160p')
    env.replies.extend(['{"name":"作品甲","year":"2026"}'] * 2)
    for title in ('[作品甲] 2026 1080p', '[作品甲] 2026 2160p'):
        assert c.get_media_name(title)['name'] == '作品甲'
        assert c.get_media_name(title)['name'] == '作品甲'
    assert len(env.requests) == 2


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('original_name,original_year', [('Other', '2024'), ('Example', None)])
def test_local_unchanged_cannot_suppress_host_identity_change(env, caplog, asynchronous,
                                                            original_name, original_year):
    import asyncio
    from mp2_contract import make_contract
    p = configured(env)
    title = 'Example 2024'
    env.metadata[title] = NS(name='Example', year='2024', begin_season=None, begin_episode=None)
    env.replies.append('{"name":"Example","year":"2024"}')
    host = make_contract(p.recognize)
    original = NS(name=original_name, year=original_year, type='MOVIE',
                  begin_season=None, begin_episode=None)
    with caplog.at_level(logging.DEBUG):
        args = dict(title=title, org_meta=original)
        result = (asyncio.run(host.async_recognize_help(**args)) if asynchronous
                  else host.recognize_help(**args))
    assert 'reason=unchanged_current_title' in caplog.text
    assert result.name == 'Example' and result.year == '2024'
    assert len(host.calls) == 1  # Actual org_meta is different, despite equal current-title parse.


@pytest.mark.parametrize('title', ['[污点/Buppha the Movie', '污点] Buppha the Movie'])
def test_malformed_source_boundaries_remain_conservative(env, title):
    identity, reason = env.recognition.inspect_identity('{"name":"污点","year":""}', title)
    assert identity is None and reason == 'movie_marker_lost'


def test_migrate_previous_builtin_only_and_keep_api_settings(env):
    old = env.recognition.LEGACY_EXTRACTION_PROMPT
    p = configured(env, customize_prompt=old, private_extra='untouched')
    assert p._customize_prompt == env.recognition.DEFAULT_PROMPT
    assert env.saved[-1]['previous_customize_prompt'] == old
    assert env.saved[-1]['openai_key'] == 'test-key-a,test-key-b'
    assert env.saved[-1]['model'] == 'deepseek-flash'
    assert env.saved[-1]['private_extra'] == 'untouched'
    custom = old + '\nMy special extraction policy.'
    assert env.recognition.resolve_prompt(custom) == custom


def test_new_prompt_and_validator_cache_revision_are_active(env):
    assert '其他独立别名中的电影版标记' in env.recognition.DEFAULT_PROMPT
    assert env.recognition.SCHEMA_VERSION == 'name-year-v4'
    assert env.recognition.DEFAULT_PROMPT != env.recognition.LEGACY_EXTRACTION_PROMPT


def test_missing_current_title_metadata_is_not_called_unchanged(env, caplog):
    p = configured(env)
    title = 'Example'
    env.metadata[title] = NS(begin_season=None, begin_episode=None)
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title': title}))
    assert 'reason=unchanged_current_title' not in caplog.text
    assert 'reason=accepted' in caplog.text
