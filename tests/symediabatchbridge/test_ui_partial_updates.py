import json
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_plugin_contract import plugin, add_job
from test_dashboard import walk


def test_view_is_request_local_and_cannot_change_other_browser(plugin):
    p, m = plugin.p, plugin.modules.plugin
    job = add_job(p)
    old = p._view.model_dump()
    result = p.ui_action(m.UIRequest(params={'key': job['id']}))
    assert result['view']['key'] == job['id'] and result['page']
    assert p._view.model_dump() == old
    other = p.ui_action(m.UIRequest(params={'status': 'done'}))
    assert other['view']['status'] == 'done' and not other['view']['key']
    assert p._view.model_dump() == old


def test_expansion_is_native_local_and_has_no_api_event(plugin):
    p = plugin.p
    job = add_job(p)
    nodes = list(walk(p.get_page()))
    disclosure = next(n for n in nodes if n['component'] == 'details')
    assert disclosure['props']['id'] == job['id'] and not disclosure['props']['open']
    summary = disclosure['content'][0]
    assert summary['component'] == 'summary'
    assert not any(n.get('events') for n in walk([summary]))
    assert any(n.get('text') == '完整记录' for n in walk(disclosure['content']))


def test_ui_endpoint_enforces_admin_and_request_validation(plugin):
    p = plugin.p
    app = FastAPI()
    route = next(r for r in p.get_api() if r['path'] == '/ui')
    app.add_api_route('/ui', route['endpoint'], methods=route['methods'])
    client = TestClient(app)
    for token in ('', 'regular-login'):
        assert client.post('/ui', json={}, headers={'Authorization': 'Bearer ' + token}).status_code == 403
    auth = {'Authorization': 'Bearer test-login'}
    result = client.post('/ui', json={}, headers=auth)
    assert result.status_code == 200 and result.json()['page']
    assert 'test-secret' not in result.text
    assert client.post('/ui', json={'action': 'dispose', 'params': {}}, headers=auth).status_code == 422
    assert client.post('/ui', json={'action': 'execute'}, headers=auth).status_code == 422


def test_ui_mutation_preserves_legacy_progress_and_requires_confirmation(plugin):
    p, m = plugin.p, plugin.modules.plugin
    job = add_job(p)
    job.update(state='review', files=[{'relative': 'film.mkv', 'sha1': 'a'*40, 'uploaded': True}],
               next_check=42, message='整理文件不存在或不在配置的本地目录中')
    p._store.save(job)
    result = p.ui_action(m.UIRequest(action='dispose', params={'key': job['id'], 'action': 'stop'}))
    assert result['success'] and result['view']['expanded'] == job['id']
    saved = p._store.get(job['id'])
    assert saved['files'] == job['files'] and saved['next_check'] == 42 and saved['state'] == 'review'
    assert '确认终止' in json.dumps(result, ensure_ascii=False)
    assert not p._view.expanded and not p._action_message


def test_federation_artifacts_exist_and_do_not_bundle_host_styles(plugin):
    from pathlib import Path
    p = plugin.p
    mode, directory = p.get_render_mode()
    assert mode == 'vue'
    root = Path(__file__).resolve().parents[2] / 'plugins.v2/symediabatchbridge' / directory
    entry = (root / 'remoteEntry.js').read_text(encoding='utf-8')
    assert './Page' in entry and './Config' in entry
    assert not list(root.glob('*.css'))


def test_old_v2_clone_keeps_native_instance_scoped_controls(plugin):
    clone = type('SymediaBatchBridgeCopy', (type(plugin.p),), {})()
    clone.init_plugin(plugin.values)
    assert clone.get_render_mode()[0] == 'vuetify'
    calls = [n.get('events', {}).get('click', {}).get('api', '') for n in walk(clone.get_page())]
    assert all('plugin/SymediaBatchBridgeCopy/' in path for path in calls if path)
