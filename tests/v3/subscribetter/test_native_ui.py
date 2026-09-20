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
