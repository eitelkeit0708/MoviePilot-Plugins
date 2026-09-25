"""T081 selective coexistence of the three legacy plugin families."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_configuration import PrivateFixture
from test_planner import load


ROOT = Path(__file__).resolve().parents[3]


def scenario():
    configuration, migration, repository = [load(name) for name in
        ('configuration', 'migration', 'repository')]
    sources = {
        'chatgpt': ROOT / 'plugins.v2/chatgptplusultra/__init__.py',
        'douban': ROOT / 'plugins.v2/doubanrankplusoptimized/__init__.py',
        'autofill': ROOT / 'plugins.v2/subscribeautofill/__init__.py',
    }
    text = {name: path.read_text(encoding='utf-8') for name, path in sources.items()}
    assert "plugin_version = '1.4.2'" in text['chatgpt']
    assert 'plugin_version = "1.0.7"' in text['douban']
    assert 'plugin_version = "3.18"' in text['autofill']
    with tempfile.TemporaryDirectory() as folder:
        repo = repository.Repository(Path(folder) / 'state.db')
        store, saved = PrivateFixture(), []
        config = configuration.Configuration(repo, 'SubscriBetter', store, saved.append)
        config.initialize({})
        current = config.view()
        desired = config.preview({'enabled': True, 'dry_run': False,
            'ai_assist': {'enabled': True, 'endpoint_ref': store.put('https://api.invalid'),
                'credential_refs': [store.put('fiction')], 'model': 'fixture',
                'name_recognize_bridge': True},
            'discovery': {'enabled': True, 'rsshub_base_url': 'https://rss.invalid',
                'sources': [{'id': 'weekly', 'kind': 'rsshub',
                             'route_key': 'movie_weekly_best'}]}},
            current['revision'], current['digest'], 'tester')
        assert desired['valid'], desired
        own = dict(id='SubscriBetter', source='SubscriBetter', prefix='fixture.New',
            config=current['config'], active=True, loaded=True, version='1.0.0',
            api_paths=[], commands=[])
        chat_config = {'enabled': True, 'recognize': True, 'chat_enabled': True,
            'notify': True, 'model': 'legacy-chat', 'timeout': 60}
        chat = dict(id='LegacyChat', source='ChatGPTPlusUltra', prefix='fixture.Chat',
            config=chat_config, active=True, loaded=True, version='1.4.2',
            api_paths=['/history'], commands=['/chatgpt'])
        douban_config = {'enabled': True, 'ranks': ['movie-real-time'],
            'rss_addrs': 'http://rss.invalid/custom', 'cron': '0 8 * * *',
            'proxy': True, 'sleep_time': 5}
        douban = dict(id='LegacyDouban', source='DoubanRankPlusOptimized',
            prefix='fixture.Douban', config=douban_config, active=True, loaded=True,
            version='1.0.7', api_paths=['/delete_history', '/migrate-config',
                '/migrate-history'], commands=[])
        autofill_config = {'enabled': True, 'respect_rules': True,
            'override_mode': False, 'update_details': True}
        autofill = dict(id='LegacyAutofill', source='SubscribeAutoFill',
            prefix='fixture.Autofill', config=autofill_config, active=True,
            loaded=True, version='3.18', api_paths=['/preview'], commands=[])
        own_service = dict(instance_id='SubscriBetter', id='SubscriBetter_discovery',
            callable=True, handler='fixture.New.discovery_tick')
        old_service = dict(instance_id='LegacyDouban', id='legacy_discovery',
            callable=True, handler='fixture.Douban.__start_task')
        handlers = [
            dict(event_type='NameRecognize', handler_identifier='fixture.New.ai_name', status='enabled'),
            dict(event_type='NameRecognize', handler_identifier='fixture.Chat.recognize', status='enabled'),
            dict(event_type='PluginAction', handler_identifier='fixture.Chat.chat', status='enabled'),
            dict(event_type='SubscribeAdded', handler_identifier='fixture.Autofill.fill', status='enabled'),
        ]
        inventory = {'generation': 1, 'plugins': [own, chat, douban, autofill],
            'handlers': handlers, 'services': [own_service, old_service],
            'jobs': [{'id': 'SubscriBetter_SubscriBetter_discovery', 'status': 'normal'},
                     {'id': 'LegacyDouban_legacy_discovery', 'status': 'normal'}],
            'event_types': {'name_bridge': 'NameRecognize'}}
        migrate = migration.Migration(repo, config, store, lambda: copy.deepcopy(inventory))
        features = [migrate.feature('name_bridge', {'event': 'NameRecognize'}, desired['config']),
                    migrate.feature('discovery', 'weekly', desired['config'])]
        receipt = migrate.preview_cutover(features, [
            {'instance_id': 'LegacyChat', 'module': 'name_bridge',
             'config_digest': configuration.digest(chat_config)},
            {'instance_id': 'LegacyDouban', 'module': 'discovery',
             'config_digest': configuration.digest(douban_config),
             'whole_instance': True, 'all_capabilities': ['discovery']}],
            'tester', desired['receipt_id'])
        first = migrate.advance(receipt['receipt_id'], receipt['revision'], receipt['digest'],
            'activate', 'await-host-save', 'tester')
        chat['config']['recognize'] = False
        douban['config']['enabled'] = False
        config_only = migrate.advance(receipt['receipt_id'], first['revision'], receipt['digest'],
            'activate', 'config-readback', 'tester')
        inventory['handlers'][1]['status'] = 'disabled'
        inventory['services'] = [own_service]
        inventory['jobs'] = [inventory['jobs'][0]]
        ready = migrate.advance(receipt['receipt_id'], config_only['revision'], receipt['digest'],
            'activate', 'runtime-readback', 'tester')
        config.initialize(desired['config'])
        own['config'] = config.view()['config']
        active = migrate.advance(receipt['receipt_id'], ready['revision'], receipt['digest'],
            'activate', 'new-owner', 'tester')
        owners = {feature['module']: migrate.unique_owner(**feature)['status'] for feature in features}
        enabled_name_handlers = [row['handler_identifier'] for row in inventory['handlers']
            if row['event_type'] == 'NameRecognize' and row['status'] == 'enabled']
        discovery_schedulers = [row['instance_id'] for row in inventory['services']
            if row['id'].endswith('discovery')]
        with repo.connection() as db:
            business_rows = {'tasks': db.execute('SELECT count(*) FROM tasks').fetchone()[0],
                'actions': db.execute('SELECT count(*) FROM plan_actions').fetchone()[0]}
        return {
            'states': [first['state'], config_only['state'], ready['state'], active['state']],
            'selected_changes': {step['instance_id']: step['changes'] for step in receipt['steps']},
            'unique_owners': owners, 'enabled_name_handlers': enabled_name_handlers,
            'discovery_schedulers': discovery_schedulers,
            'legacy_chat': {'enabled': chat['config']['enabled'],
                'recognize': chat['config']['recognize'],
                'chat_enabled': chat['config']['chat_enabled'], 'notify': chat['config']['notify'],
                'model': chat['config']['model'], 'chat_handler': inventory['handlers'][2]['status']},
            'legacy_douban': copy.deepcopy(douban['config']),
            'legacy_autofill': {'config_unchanged': autofill['config'] == autofill_config,
                'active': autofill['active'], 'handler': inventory['handlers'][3]['status']},
            'business_rows': business_rows,
            'source_paths': {name: path.relative_to(ROOT).as_posix() for name, path in sources.items()},
        }


def verify(proof):
    assert proof['states'] == ['WAIT_HOST_SAVE', 'WAIT_OWNER', 'READY_CONFIG', 'ACTIVE']
    assert proof['selected_changes'] == {
        'LegacyChat': {'recognize': False}, 'LegacyDouban': {'enabled': False}}
    assert proof['unique_owners'] == {'name_bridge': 'ACTIVE', 'discovery': 'ACTIVE'}
    assert proof['enabled_name_handlers'] == ['fixture.New.ai_name']
    assert proof['discovery_schedulers'] == ['SubscriBetter']
    assert proof['legacy_chat'] == {'enabled': True, 'recognize': False,
        'chat_enabled': True, 'notify': True, 'model': 'legacy-chat', 'chat_handler': 'enabled'}
    assert proof['legacy_douban'] == {'enabled': False, 'ranks': ['movie-real-time'],
        'rss_addrs': 'http://rss.invalid/custom', 'cron': '0 8 * * *',
        'proxy': True, 'sleep_time': 5}
    assert proof['legacy_autofill'] == {'config_unchanged': True, 'active': True,
        'handler': 'enabled'}
    assert proof['business_rows'] == {'tasks': 0, 'actions': 0}


class T081IntegrationTests(unittest.TestCase):
    def test_selective_three_legacy_plugin_cutover(self):
        verify(scenario())


if __name__ == '__main__':
    result = scenario()
    verify(result)
    print(json.dumps(result, ensure_ascii=False))
