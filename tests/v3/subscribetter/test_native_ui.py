"""Native federation registration and generated contract, no host/network IO."""
import importlib.util
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
import test_management


class NativeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_management.ManagementAPITests.setUpClass()
        cls.mod = test_management.ManagementAPITests.mod

    def setUp(self):
        test_management.ManagementAPITests.setUp(self)

    def test_discovery_history_is_serializable_through_public_response_model(self):
        from unittest.mock import AsyncMock, patch
        history={'items':1,'states':['DEFERRED'],
                 'errors':[{'record_id':2,'reason':'HISTORY_REPROCESS_FAILED'}]}
        result={'sources':{'weekly':{'state':'PARTIAL','reason':'ITEM_BUDGET','items':0,'history':history}}}
        with patch.object(self.plugin,'_writes_enabled'), patch.object(self.plugin,'discovery_tick',AsyncMock(return_value=result)):
            response=self.client.post('/discovery/run',headers=self.headers,json={'source_ids':['weekly']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['sources']['weekly']['history'],history)

    def test_removed_chat_has_no_registration_routes_or_schema(self):
        from unittest.mock import patch
        from test_planner import load
        current=self.plugin.configuration.view()
        ai=dict(enabled=True,model='fixture',endpoint_ref=self.store.put('https://fixture.invalid'),
            credential_refs=[self.store.put('fiction-key')],name_recognize_bridge=True,
            chat_enabled=True,chat_routes=[{'legacy':'route'}])
        preview=self.plugin.configuration.preview({'ai_assist':ai},current['revision'],current['digest'],'admin')
        self.assertTrue(preview['valid'])
        with patch.object(self.mod,'SecretStore',return_value=self.store), \
                patch.object(self.mod.ChainEventType,'NameRecognize','name',create=True), \
                patch.object(self.mod.eventmanager,'add_event_listener') as register:
            self.plugin.init_plugin(preview['config'])
        self.addCleanup(self.plugin.stop_service)
        self.assertEqual([],self.plugin.ai_errors)
        names=[call.args[1].__name__ for call in register.call_args_list]
        self.assertIn('ai_name',names)
        self.assertNotIn('ai_message',names)
        self.assertFalse(hasattr(self.plugin,'ai_message'))
        for path in ('/ai/sessions/clear/preview','/ai/sessions/clear/apply'):
            self.assertEqual(404,self.client.post(path,headers=self.headers,json={}).status_code)
        self.assertNotIn('chat',self.client.get('/ai',headers=self.headers).json())
        schema=load('ai').AIConfig.model_json_schema()
        self.assertFalse(any(k.startswith('chat_') for k in schema['properties']))

    def test_removed_feature_receipt_remains_readable_but_not_selectable(self):
        feature=dict(module='chat',instance_id='SubscriBetter',config_digest='a'*64,route_scope={})
        self.plugin.migration._new('old-chat','CUTOVER','a'*64,'ACTIVE',
            dict(features=[feature],steps=[],operations={},next_changes=[]))
        response=self.client.get('/migration/receipts/old-chat',headers=self.headers)
        self.assertEqual(200,response.status_code,response.text)
        self.assertEqual([feature],response.json()['features'])
        with self.plugin.repository.connection() as db:before='\n'.join(db.iterdump())
        for action in ('activate','rollback','rollback_readback'):
            response=self.client.post('/migration/cutover',headers=self.headers,json=dict(
                receipt_id='old-chat',revision=1,digest='a'*64,action=action,operation_id='attempt',confirm=True))
            self.assertEqual(409,response.status_code,response.text)
        with self.plugin.repository.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))
        response=self.client.post('/migration/cutover/preview',headers=self.headers,json={'features':[feature],'selected':[]})
        self.assertEqual(422,response.status_code,response.text)

    def test_native_hooks_and_assets(self):
        self.assertEqual(('vue', 'dist/assets'), self.plugin.get_render_mode())
        form, defaults = self.plugin.get_form()
        self.assertIsNone(form)
        self.assertFalse(defaults['enabled'])
        self.assertTrue(defaults['dry_run'])
        self.assertFalse(any(defaults['permissions'].values()))
        self.assertEqual([], self.plugin.get_page())
        response = self.client.get('/diagnostics', headers=self.headers)
        self.assertFalse(response.json()['foundation_only'])
        root = Path(__file__).resolve().parents[3]
        remote = root/'plugins.v3/subscribetter/dist/assets/remoteEntry.js'
        self.assertTrue(remote.is_file())
        content = remote.read_text(encoding='utf-8')
        self.assertIn('./Page', content)
        self.assertIn('./Config', content)

    def test_scan_item_projection_auth_pagination_and_private_omission(self):
        repo = self.plugin.repository
        with repo.connection(write=True) as db:
            db.execute('INSERT INTO archive_scans VALUES(?,?,?,?,?)', ('scan', 'Emby test', 'Library', 'COLLECTING', '{}'))
            for n in range(27):
                db.execute('INSERT INTO archive_scan_items VALUES(?,?,?,?)', ('scan', str(n), '{"Path":"/private/SENTINEL","token":"SENTINEL"}', '[]' if n else None))
        with repo.connection() as db: before = '\n'.join(db.iterdump())
        url = '/archive/scans/scan/items'
        self.assertEqual(401, self.client.get(url).status_code)
        response = self.client.get(url+'?limit=25', headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        page = response.json()
        self.assertEqual((27, 25, 25, True), (page['total'], len(page['items']), page['next_offset'], page['truncated']))
        self.assertEqual('0', page['items'][0]['id'])
        self.assertEqual('PENDING', page['items'][0]['state'])
        self.assertEqual('0', page['items'][0]['data']['item_id'])
        self.assertEqual('PROCESSED', page['items'][1]['state'])
        self.assertNotIn('SENTINEL', response.text)
        self.assertNotIn('Path', response.text)
        second = self.client.get(url+'?limit=25&offset=25', headers=self.headers).json()
        self.assertEqual(2, len(second['items']))
        self.assertFalse({r['id'] for r in page['items']} & {r['id'] for r in second['items']})
        self.assertEqual(422, self.client.get(url+'?limit=101', headers=self.headers).status_code)
        self.assertEqual(404, self.client.get('/archive/scans/missing/items', headers=self.headers).status_code)
        with repo.connection() as db: self.assertEqual(before, '\n'.join(db.iterdump()))

    def test_diagnostics_exposes_actual_worker_rule_revision_without_paths(self):
        self.plugin.delivery_worker = SimpleNamespace(rules={'规则/one': {'id':'规则/one', 'revision':'runtime-revision', 'enabled':True, 'local_root':'/private/SENTINEL'}})
        response = self.client.get('/diagnostics', headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([{'id':'规则/one', 'revision':'runtime-revision', 'enabled':True}], response.json()['services']['local_rules'])
        self.assertNotIn('SENTINEL', response.text)

    def test_generated_models_match_actual_mounted_contract(self):
        root = Path(__file__).resolve().parents[3]
        script = root/'plugins.v3/subscribetter/frontend/tools/export_contract.py'
        spec = importlib.util.spec_from_file_location('native_contract', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        saved = json.loads((script.parents[1]/'src/contract.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, module.contract(self))


if __name__ == '__main__':
    unittest.main()
