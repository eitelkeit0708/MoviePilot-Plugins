"""Offline regressions. Mock only MP2 services and the external HTTP boundary."""
import importlib.util
import json
import logging
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'plugins.v2' / 'chatgptplusultra'
NS = types.SimpleNamespace


@pytest.fixture
def env(monkeypatch):
    metadata, notifications, saved, clients, requests = {}, [], [], [], []
    replies = []

    class Base:
        def __init__(self):
            pass
        def update_config(self, data):
            saved.append(data.copy())
        def post_message(self, **kwargs):
            notifications.append(kwargs)

    def meta(title, **kwargs):
        return metadata.get(title, NS(name='Original', year=None, begin_season=None,
                                      begin_episode=None, end_season=None, end_episode=None))

    modules = {
        'app': {}, 'app.core': {}, 'app.core.config': {'settings': NS(PROXY={})},
        'app.core.event': {'eventmanager': NS(register=lambda *a, **k: lambda f: f), 'Event': NS},
        'app.core.metainfo': {'MetaInfo': meta},
        'app.log': {'logger': logging.getLogger('test.chatgptplusultra')},
        'app.plugins': {'_PluginBase': Base},
        'app.schemas': {'NotificationType': NS(Plugin='plugin')},
        'app.schemas.types': {'EventType': NS(UserMessage='message'),
                              'ChainEventType': NS(NameRecognize='recognize')},
    }
    for name, attrs in modules.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        mod.__path__ = []
        monkeypatch.setitem(sys.modules, name, mod)
    for name in list(sys.modules):
        if name.startswith('app.plugins.chatgptplusultra'):
            monkeypatch.delitem(sys.modules, name)

    real_client = httpx.Client
    def client_factory(**kwargs):
        def handler(request):
            key = request.headers['Authorization'].removeprefix('Bearer ')
            params = json.loads(request.content)
            params['_request_timeout'] = request.extensions['timeout']['read']
            requests.append((key, params))
            reply = replies.pop(0) if replies else '{"name":"Example","year":""}'
            if isinstance(reply, Exception):
                raise reply
            if callable(reply):
                reply = reply(params)
            if isinstance(reply, httpx.Response):
                return reply
            return httpx.Response(200, json={'choices':[
                {'message':{'content':reply},'finish_reason':'stop'}]})
        options = dict(kwargs)
        # Proxy construction is observed, but external I/O always goes to MockTransport.
        kwargs.pop('proxy', None)
        obj = real_client(transport=httpx.MockTransport(handler), **kwargs)
        obj.options = options
        clients.append(obj)
        return obj

    monkeypatch.setattr(httpx, 'Client', client_factory)
    spec = importlib.util.spec_from_file_location('app.plugins.chatgptplusultra', PLUGIN / '__init__.py',
                                                submodule_search_locations=[str(PLUGIN)])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    imported = [name for name in sys.modules if name.startswith(spec.name + '.')]
    result = NS(plugin=module, api=sys.modules[spec.name + '.openai'],
                recognition=sys.modules[spec.name + '.recognition'],
                cache=sys.modules[spec.name + '.cache'], metadata=metadata,
                clients=clients, replies=replies, requests=requests,
                notifications=notifications, saved=saved)
    yield result
    for client in clients:
        client.close()
    for name in imported:
        sys.modules.pop(name, None)


def client(env, **kwargs):
    return env.api.OpenAi(api_key='test-key-a', api_url='https://api.deepseek.com',
                         model='deepseek-flash', **kwargs)


def configured(env, **kwargs):
    plugin = env.plugin.ChatGPTPlusUltra()
    plugin.init_plugin(dict(enabled=True, recognize=True, openai_url='https://api.deepseek.com',
                            openai_key='test-key-a,test-key-b', model='deepseek-flash', **kwargs))
    return plugin


def http_error(status, code='test_error', retry_after=None):
    headers = {'Retry-After': str(retry_after)} if retry_after is not None else {}
    return httpx.Response(status, headers=headers,
                          json={'error': {'code':code,'message':'must not leak test-key-a'}})


def test_distinct_titles_and_episodes_have_distinct_cache_keys(env):
    c = client(env)
    titles = ['Example S01E01', 'Example S01E02', 'Example S02E03',
              '[Group][作品甲][01][1080p]', '[Group][作品乙][01][1080p]']
    assert len({c._extract_cache_key(t) for t in titles}) == len(titles)


def test_cache_keys_include_configuration(env):
    a = client(env)
    b = client(env, customize_prompt='another extraction policy')
    c = env.api.OpenAi(api_key='test-key-a', api_url='https://other.example', model='other')
    assert len({obj._extract_cache_key('Example') for obj in [a, b, c]}) == 3


def test_negative_result_does_not_disable_keys_or_retry(env):
    p = configured(env)
    env.replies.append('{"name":"","year":""}')
    for _ in range(3):
        event = NS(event_data={'title': 'Unidentifiable'})
        p.recognize(event)
        assert event.event_data == {'title': 'Unidentifiable'}
    assert len(env.requests) == 1
    assert all(not k['disabled'] for k in p.openai.key_health())


@pytest.mark.parametrize('raw', ['[]', 'null', '42', '"hello"', '', None, '{',
    '{"name":null,"year":null}', '{"name":"Example","year":2024}',
    '{"name":"Example","year":"","season":0,"episode":0}',
    '{"name":"Example","year":"","title":"x"}',
    '{"name":"Example","year":"2099"}',
    '{"name":"A","name":"B","year":""}',
    '```json\n{"name":"Example","year":""}\n```'])
def test_invalid_output_is_safe_negative_cache(env, raw):
    c = client(env)
    env.replies.append(raw)
    assert c.get_media_name('Example 2024') is None
    assert c.get_media_name('Example 2024') is None
    assert len(env.requests) == 1
    assert not c.key_health()[0]['disabled']


def test_valid_year_requires_source_evidence(env):
    parse = env.recognition.parse_identity
    assert parse('{"name":"Example","year":"2024"}', 'Example (2024)') == {'name': 'Example', 'year': '2024'}
    assert parse('{"name":"Example","year":"1920"}', 'Example 1920x1080') is None
    assert parse('{"name":"Example","year":"2024"}', 'Example 20240101') is None
    assert parse('{"name":"Example","year":""}', 'Example') == {'name': 'Example', 'year': ''}


@pytest.mark.parametrize('season,episode', [(None,None), (0,1), (2,3), (2,None)])
def test_season_episode_come_from_current_mp2_metadata(env, season, episode):
    p = configured(env)
    title = 'Current resource 2024 [Corrected]'
    env.metadata[title] = NS(name='Original', year='2024', begin_season=season, begin_episode=episode)
    env.replies.append('{"name":"Corrected","year":""}')
    event = NS(event_data={'title': title, 'extension': 'preserve'})
    p.recognize(event)
    assert event.event_data == {'title': title, 'extension': 'preserve', 'name': 'Corrected',
                               'year': '2024', 'season': season, 'episode': episode}


def test_cached_name_never_carries_model_episode(env):
    p = configured(env)
    for title, season, episode in [('Show S01E01',1,1), ('Show S02E03',2,3)]:
        env.metadata[title] = NS(name='Original', year=None, begin_season=season, begin_episode=episode)
        env.replies.append('{"name":"Show","year":""}')
        event = NS(event_data={'title': title})
        p.recognize(event)
        assert (event.event_data['season'], event.event_data['episode']) == (season, episode)
    assert len(env.requests) == 2


def test_preserve_existing_result_and_disabled_state(env):
    p = configured(env)
    event = NS(event_data={'title': 'A', 'name': 'Other plugin', 'year': '2020'})
    p.recognize(event)
    assert event.event_data['name'] == 'Other plugin'
    p.init_plugin({'enabled': False, 'recognize': True})
    p.recognize(NS(event_data={'title':'A'}))
    assert env.requests == []
    assert p.openai is None


def test_authentication_failure_rotates_only_that_key(env):
    c = client(env, api_keys=['test-key-a','test-key-b'])
    env.replies.extend([http_error(401), '{"name":"Example","year":""}'])
    assert c.get_media_name('Example')['name'] == 'Example'
    assert [r[0] for r in env.requests] == ['test-key-a','test-key-b']
    assert [k['disabled'] for k in c.key_health()] == [True,False]


@pytest.mark.parametrize('error,expected', [
    (http_error(429,retry_after=120),'rate_limit'),
    (http_error(400),'configuration'),
    (http_error(403),'permission'),
    (httpx.ReadTimeout('test timeout',request=httpx.Request('POST','https://example.test')), 'timeout'),
    (httpx.ConnectError('test connection',request=httpx.Request('POST','https://example.test')), 'connection'),
])
def test_non_auth_errors_never_permanently_disable_key(env, error, expected):
    c = client(env)
    env.replies.append(error)
    with pytest.raises(env.api.ProviderError) as caught:
        c.get_media_name('Example')
    assert caught.value.code == expected
    assert 'test-key-a' not in str(caught.value)
    assert not c.key_health()[0]['disabled']
    with pytest.raises(env.api.ProviderError):
        c.get_media_name('Example')
    assert len(env.requests) == 1


def test_provider_parameters_and_no_hidden_retry(env):
    c = client(env)
    c.get_media_name('Example')
    opts, req = env.clients[0].options, env.requests[0][1]
    assert opts['base_url'] == 'https://api.deepseek.com/v1/'
    assert len(env.requests) == 1
    assert opts['follow_redirects'] is False
    assert req['model'] == 'deepseek-flash'
    assert req['response_format'] == {'type':'json_object'}
    assert req['thinking'] == {'type':'disabled'}
    assert req['temperature'] == 0
    assert req['max_tokens'] == 512
    assert req['_request_timeout'] <= 20
    assert 'timeout' not in req
    assert 'tools' not in req
    assert json.loads(req['messages'][1]['content']) == {'input_title': 'Example'}


def test_generic_profile_does_not_send_deepseek_extensions(env):
    c = env.api.OpenAi(api_key='test-key-a',api_url='https://gateway.example/v1/',model='custom')
    c.get_media_name('Example')
    assert env.clients[0].options['base_url'] == 'https://gateway.example/v1/'
    assert 'thinking' not in env.requests[0][1]
    assert 'response_format' not in env.requests[0][1]


def test_cache_defensive_copy_and_clear(env):
    c = client(env)
    data = c.get_media_name('Example')
    data['name'] = 'Mutated'
    assert c.get_media_name('Example')['name'] == 'Example'
    assert len(env.requests) == 1
    c.clear_media_cache()
    c.get_media_name('Example')
    assert len(env.requests) == 2


def test_singleflight_for_concurrent_identical_titles(env):
    c = client(env)
    started, release = threading.Event(), threading.Event()
    def response(_):
        started.set()
        assert release.wait(3)
        return '{"name":"Example","year":""}'
    env.replies.append(response)
    with ThreadPoolExecutor(max_workers=8) as pool:
        leader = pool.submit(c.get_media_name,'Example')
        assert started.wait(3)
        followers = [pool.submit(c.get_media_name,'Example') for _ in range(6)]
        release.set()
        assert leader.result()['name'] == 'Example'
        assert all(f.result()['name']=='Example' for f in followers)
    assert len(env.requests)==1


def test_cache_expiry_bound_and_inflight_clear(env):
    clock=[0.0]
    cache=env.cache.TTLCache(maxsize=2, timer=lambda: clock[0])
    assert cache.get_or_load('a',lambda: ('old',10),1)=='old'
    clock[0]=11
    assert cache.get_or_load('a',lambda: ('new',10),1)=='new'
    cache.get_or_load('b',lambda: ('b',10),1)
    cache.get_or_load('c',lambda: ('c',10),1)
    assert cache.stats()['size']==2
    started, release = threading.Event(), threading.Event()
    def load():
        started.set(); assert release.wait(3)
        return 'late',10
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(cache.get_or_load,'z',load,2)
        assert started.wait(2)
        cache.clear()
        release.set()
        assert future.result()=='late'
    assert cache.stats()['size']==0


def test_close_drains_inflight_client(env):
    c=client(env)
    started, release=threading.Event(),threading.Event()
    def response(_):
        started.set(); assert release.wait(3)
        return '{"name":"Example","year":""}'
    env.replies.append(response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result=pool.submit(c.get_media_name,'Example')
        assert started.wait(2)
        c.close()
        assert not env.clients[0].is_closed
        release.set()
        result.result()
    assert env.clients[0].is_closed
    with pytest.raises(env.api.ProviderError):
        c.get_media_name('Different')


def test_reconfiguration_prevents_old_result_from_mutating_event(env):
    p=configured(env)
    started, release=threading.Event(),threading.Event()
    def response(_):
        started.set(); assert release.wait(3)
        return '{"name":"Stale","year":""}'
    env.replies.append(response)
    event=NS(event_data={'title':'Example [Stale]'})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(p.recognize,event)
        assert started.wait(2)
        p.init_plugin({'enabled': False})
        release.set(); future.result()
    assert event.event_data=={'title':'Example [Stale]'}
    assert env.clients[0].is_closed


def test_chat_records_assistant_response_and_channel_scope(env):
    c=client(env)
    env.replies.extend(['actual answer','second answer','different channel'])
    assert c.get_response('first question','telegram:user')=='actual answer'
    c.get_response('second question','telegram:user')
    messages=env.requests[1][1]['messages']
    assert {'role':'assistant','content':'actual answer'} in messages
    assert {'role':'assistant','content':'first question'} not in messages
    c.get_response('new question','wechat:user')
    assert len(env.requests[2][1]['messages'])==2


def test_known_prompt_migration_and_custom_preservation(env):
    p=configured(env, customize_prompt=env.recognition.LEGACY_USER_PROMPT)
    assert p._customize_prompt==env.recognition.DEFAULT_PROMPT
    assert env.saved[-1]['previous_customize_prompt']==env.recognition.LEGACY_USER_PROMPT
    assert env.saved[-1]['openai_key']=='test-key-a,test-key-b'
    q=configured(env, customize_prompt='My deliberate custom prompt')
    assert q._customize_prompt=='My deliberate custom prompt'


def test_notifications_never_contain_credentials(env, caplog):
    p=configured(env, notify=True)
    env.replies.extend([http_error(401),http_error(401)])
    with caplog.at_level(logging.DEBUG):
        p.recognize(NS(event_data={'title':'Example'}))
    text=caplog.text+json.dumps(env.notifications)
    assert 'test-key-a' not in text and 'test-key-b' not in text


def test_manifest_version_and_unrelated_entries(env):
    manifest=json.loads((ROOT/'package.v2.json').read_text())
    entry=manifest['ChatGPTPlusUltra']
    assert entry['version']==env.plugin.ChatGPTPlusUltra.plugin_version=='1.4.0'
    assert next(iter(entry['history']))=='v1.4.0'
    assert manifest['SubscribeAutofill']['version']=='3.18'


def test_name_is_grounded_and_movie_identity_not_reduced_to_tv(env):
    parse = env.recognition.parse_identity
    assert parse('{"name":"Invented translation","year":""}', '[真实片名]') is None
    assert parse('{"name":"命运石之门","year":"2013"}', '[命运石之门剧场版：负荷领域的既视感][2013]') is None
    assert parse('{"name":"Example Show","year":""}', 'Example.Show.S01E01') == {'name':'Example Show','year':''}
    assert parse('{"name":"命运石之门剧场版：负荷领域的既视感","year":"2013"}', '[命运石之门剧场版：负荷领域的既视感][2013]') is not None


def test_numeric_work_name_is_not_filled_as_missing_year(env):
    meta = NS(begin_season=None,begin_episode=None,year='1917')
    result = env.recognition.build_event_result('1917.1080p',{'name':'1917','year':''},meta)
    assert result['year'] is None


def test_restore_prompt_persists_and_preserves_custom_backup(env):
    p = configured(env,customize_prompt='Custom',restore_prompt=True)
    assert p._customize_prompt == env.recognition.DEFAULT_PROMPT
    assert env.saved[-1]['customize_prompt'] == env.recognition.DEFAULT_PROMPT
    assert env.saved[-1]['previous_customize_prompt'] == 'Custom'
    assert env.saved[-1]['restore_prompt'] is False


def test_removed_credentials_close_old_runtime(env):
    p = configured(env)
    p.openai.get_media_name('Example')
    p.init_plugin({'enabled':True,'recognize':True,'openai_url':'https://api.deepseek.com','openai_key':''})
    assert p.openai is None
    assert env.clients[0].is_closed
    event = NS(event_data={'title':'Another title'})
    p.recognize(event)
    assert event.event_data == {'title':'Another title'}


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('season,episode', [(None,None),(0,1),(2,3),(2,None)])
def test_pinned_mp2_sync_async_application(env, asynchronous, season, episode):
    import asyncio
    from mp2_contract import make_contract
    p = configured(env)
    title = 'Original [Corrected] 2024'
    env.metadata[title] = NS(year='2024',begin_season=season,begin_episode=episode)
    env.replies.append('{"name":"Corrected","year":"2024"}')
    meta = NS(name='Original',year='2024',type='MOVIE',begin_season=9,begin_episode=9,
              end_season=None,end_episode=12)
    host = make_contract(p.recognize)
    kwargs = dict(title=title,org_meta=meta,source='tmdb',episode_group='group',share_meta='original')
    result = asyncio.run(host.async_recognize_help(**kwargs)) if asynchronous else host.recognize_help(**kwargs)
    assert result.name == 'Corrected'
    assert (result.begin_season,result.begin_episode) == (season,episode)
    assert result.type == ('TV' if season is not None or episode is not None else 'MOVIE')
    assert result.end_episode == 12
    assert host.calls[0]['source'] == 'tmdb'
    assert host.calls[0]['episode_group'] == 'group'
    assert host.calls[0]['share_meta'] == 'original'


@pytest.mark.parametrize('asynchronous', [False,True])
def test_pinned_host_same_identity_short_circuit_is_not_claimed_fixed(env, asynchronous):
    import asyncio
    from mp2_contract import make_contract
    p = configured(env)
    title = 'Example S00E01'
    env.metadata[title] = NS(year=None,begin_season=0,begin_episode=1)
    meta = NS(name='Example',year=None,type='MOVIE',begin_season=None,begin_episode=None)
    host = make_contract(p.recognize)
    kwargs = dict(title=title,org_meta=meta)
    result = asyncio.run(host.async_recognize_help(**kwargs)) if asynchronous else host.recognize_help(**kwargs)
    assert result is None
    assert host.calls == []
    assert meta.begin_season is None


def test_pinned_host_cannot_clear_preexisting_tv_type(env):
    from mp2_contract import make_contract
    p = configured(env)
    title = '[Example] 2024'
    env.metadata[title] = NS(year='2024',begin_season=None,begin_episode=None)
    meta = NS(name='Other',year=None,type='TV',begin_season=0,begin_episode=0)
    host = make_contract(p.recognize)
    result = host.recognize_help(title=title,org_meta=meta)
    assert result.type == 'TV'  # Host limitation, NOT a successful forced movie correction.
    assert result.begin_season is None and result.begin_episode is None
