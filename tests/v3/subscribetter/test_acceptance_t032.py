"""T032 integrated legacy-name cutover and durable retry semantics."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import httpx

from test_configuration import PrivateFixture
from test_meta import native
from test_planner import load


def scenario():
    configuration, migration, ai, meta, repository = [load(name) for name in
        ('configuration', 'migration', 'ai', 'meta', 'repository')]
    with tempfile.TemporaryDirectory() as folder:
        repo = repository.Repository(Path(folder) / 'state.db')
        store, saved = PrivateFixture(), []
        config = configuration.Configuration(repo, 'SubscriBetter', store, saved.append)
        config.initialize({})
        current = config.view()
        desired = config.preview({'enabled': True, 'dry_run': False, 'ai_assist': {
            'enabled': True, 'endpoint_ref': store.put('https://api.deepseek.com'),
            'credential_refs': [store.put('fiction-key')], 'model': 'fixture',
            'name_assistance_enabled': True, 'name_recognize_bridge': True,
            'negative_ttl': 600}}, current['revision'], current['digest'], 'tester')
        own = dict(id='SubscriBetter', source='SubscriBetter', prefix='fixture.New',
            config=current['config'], active=True, loaded=True, version='1')
        old = dict(id='Old', source='ChatGPTPlusUltra', prefix='fixture.Old',
            config={'enabled': True, 'recognize': True, 'chat_enabled': True},
            active=True, loaded=True, version='1.4.2')
        inventory = {'generation': 1, 'plugins': [own, old], 'services': [], 'jobs': [],
            'event_types': {'name_bridge': 'NameRecognize'}, 'handlers': [
                {'event_type': 'NameRecognize', 'handler_identifier': 'fixture.New.ai_name', 'status': 'enabled'},
                {'event_type': 'NameRecognize', 'handler_identifier': 'fixture.Old.recognize', 'status': 'enabled'}]}
        migrate = migration.Migration(repo, config, store, lambda: copy.deepcopy(inventory))
        feature = migrate.feature('name_bridge', {'event': 'NameRecognize'}, desired['config'])
        receipt = migrate.preview_cutover([feature], [{
            'instance_id': 'Old', 'module': 'name_bridge',
            'config_digest': configuration.digest(old['config'])}], 'tester', desired['receipt_id'])

        requests, replies, clients, clock = [], [], [], [1000.0]
        def factory(**kwargs):
            def handler(request):
                requests.append(request)
                reply = replies.pop(0)
                response = reply if isinstance(reply, httpx.Response) else httpx.Response(200, json={
                    'choices': [{'message': {'content': reply},
                    'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 2}})
                return (httpx.Response(response.status_code, headers=response.headers,
                    stream=httpx.ByteStream(response.content))
                    if response.is_stream_consumed else response)
            client = httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client
        def runtime():
            return ai.AIService(repo, ai.AIConfig.model_validate(desired['config']['ai_assist']),
                factory, store.resolve, generation=1, current=lambda: True, clock=lambda: clock[0],
                owner_check=migrate.unique_owner, owner_snapshot=migrate.owner_snapshot)
        service = runtime()
        meta_service = meta.MetaService(repo, meta.MetaCorrector(),
            parser=lambda *args, **kwargs: native('Wrong', begin_episode=None))
        old_calls = [0]
        def dispatch(event, provider):
            handler = inventory['handlers'][1]
            if (handler['status'] == 'enabled' and old['config']['enabled']
                    and old['config']['recognize']):
                old_calls[0] += 1
                event.event_data = {**event.event_data, 'name': '片名乙', 'year': 2024}
            provider.name_event(event, meta_service)

        before = SimpleNamespace(event_data={'title': '[片名乙] 2024'})
        dispatch(before, service)
        waiting = migrate.advance(receipt['receipt_id'], receipt['revision'], receipt['digest'],
            'activate', 'wait-old-save', 'tester')
        old['config']['recognize'] = False
        still_running = migrate.advance(receipt['receipt_id'], waiting['revision'], receipt['digest'],
            'activate', 'flag-readback', 'tester')
        inventory['handlers'][1]['status'] = 'disabled'
        ready = migrate.advance(receipt['receipt_id'], still_running['revision'], receipt['digest'],
            'activate', 'runtime-stopped', 'tester')
        config.initialize(desired['config'])
        own['config'] = config.view()['config']
        active = migrate.advance(receipt['receipt_id'], ready['revision'], receipt['digest'],
            'activate', 'new-active', 'tester')

        replies.append('{"name":"片名乙","year":"2024"}')
        queued = SimpleNamespace(event_data={'title': '[片名乙] 2024'})
        dispatch(queued, service)
        service.drain(meta_service)
        after = SimpleNamespace(event_data={'title': '[片名乙] 2024'})
        dispatch(after, service)
        accepted = service.stats()['counts'].get('name_accepted', 0)

        replies.append(httpx.Response(429, headers={'Retry-After': '600'}))
        failed = SimpleNamespace(event_data={'title': '[失败作品] 2024'})
        dispatch(failed, service)
        service.drain(meta_service)
        request_count = len(requests)
        repeat = SimpleNamespace(event_data={'title': '[失败作品] 2024'})
        dispatch(repeat, service)
        service.drain(meta_service)
        repeat_count = len(requests)
        service.close()
        restarted = runtime()
        after_restart = SimpleNamespace(event_data={'title': '[失败作品] 2024'})
        dispatch(after_restart, restarted)
        restarted.drain(meta_service)
        restart_count = len(requests)
        correction = meta_service.corrector.correct(native('Example', begin_episode=None), 'Example 2024')
        unchanged, diagnostic = restarted.assist('Example 2024', '', correction,
            corrector=meta_service.corrector)
        final = restarted.stats()
        restarted.close()
        for client in clients:
            client.close()
        return {
            'pre_cutover': {'old_calls': 1, 'effective_name': before.event_data.get('name'),
                'new_http_calls': 0},
            'cutover': {'states': [waiting['state'], still_running['state'], ready['state'], active['state']],
                'runtime_flag_gap_detected': 'LEGACY_RUNTIME_FLAGS_UNVERIFIED:Old' in still_running.get('diagnostics', []),
                'old_recognize_disabled': old['config']['recognize'] is False,
                'old_handler_disabled': inventory['handlers'][1]['status'] == 'disabled',
                'unrelated_old_chat_retained': old['config']['chat_enabled'] is True,
                'active_owner': migrate.unique_owner(**feature) is not None},
            'post_cutover': {'old_calls': old_calls[0], 'effective_name': after.event_data.get('name'),
                'http_calls': request_count, 'accepted_count': accepted},
            'failure': {'reason': final['cooldown_reason'], 'cooldown_remaining': final['cooldown_remaining'],
                'first_request_count': request_count, 'repeat_request_count': repeat_count,
                'restart_request_count': restart_count},
            'unchanged': {'status': unchanged.status, 'diagnostic': diagnostic.reason,
                'identity': diagnostic.identity, 'request_count': len(requests),
                'accepted_count': final['counts'].get('name_accepted', 0)},
        }


def verify(proof):
    assert proof['pre_cutover'] == {'old_calls': 1, 'effective_name': '片名乙', 'new_http_calls': 0}
    assert proof['cutover']['states'] == ['WAIT_HOST_SAVE', 'WAIT_OWNER', 'READY_CONFIG', 'ACTIVE']
    assert all(proof['cutover'][key] for key in ('runtime_flag_gap_detected', 'old_recognize_disabled',
        'old_handler_disabled', 'unrelated_old_chat_retained', 'active_owner'))
    assert proof['post_cutover']['old_calls'] == 1
    assert proof['post_cutover']['effective_name'] == '片名乙'
    assert proof['post_cutover']['http_calls'] == 2 and proof['post_cutover']['accepted_count'] == 1
    assert proof['failure']['reason'] == 'rate_limit' and proof['failure']['cooldown_remaining'] >= 600
    assert proof['failure']['first_request_count'] == proof['failure']['repeat_request_count'] == proof['failure']['restart_request_count'] == 2
    assert proof['unchanged'] == {'status': 'OK', 'diagnostic': 'deterministic', 'identity': None,
        'request_count': 2, 'accepted_count': 1}


class T032IntegrationTests(unittest.TestCase):
    def test_dual_responder_cutover_retry_and_unchanged(self):
        proof = scenario()
        verify(proof)


if __name__ == '__main__':
    result = scenario()
    verify(result)
    print(json.dumps(result, ensure_ascii=False))
