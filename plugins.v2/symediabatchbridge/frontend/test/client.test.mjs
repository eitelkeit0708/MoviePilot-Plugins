import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createClient, defaultView, removeRoute} from '../src/client.mjs';

test('pending actions cannot be duplicated; a failure does not clear the page or retry', async () => {
  let reject, calls = 0, renders = 0;
  const client = createClient({post: () => {calls++; return new Promise((_, fail) => {reject = fail;});}}, 'Clone', () => renders++);
  const active = client.run('dispose', {key: 'job', action: 'confirm', token: 'once'});
  assert.equal((await client.run('dispose', {})).success, false);
  reject(new Error('network'));
  await assert.rejects(active);
  assert.equal(calls, 1); assert.equal(renders, 0);
});

test('view state stays in this page and clone requests use the provided instance', async () => {
  const requests = [], pages = [];
  const client = createClient({post: async (url, body) => {
    requests.push([url, body]);
    return {success: true, view: {...defaultView(), ...body.params}, page: [{component: 'p'}]};
  }}, 'SymediaBatchBridgeCopy', page => pages.push(page));
  await client.run('view', {route: '/route', status: 'attention'});
  await client.run();
  assert.equal(requests[1][0], 'plugin/SymediaBatchBridgeCopy/ui');
  assert.equal(requests[1][1].params.route, '/route');
  assert.equal(requests[1][1].view.status, 'attention');
  assert.equal(pages.length, 2);
});

test('removing a route preserves all remaining destinations and unknown old configuration', () => {
  const model = {route_count: 3, route_name: 'A', local_root: '/A', staging: '/sa', inbox: '/ia',
    route_2_route_name: 'B', route_2_local_root: '/B', route_2_staging: '/sb', route_2_inbox: '/ib',
    route_3_route_name: 'C', route_3_local_root: '/C', route_3_staging: '/sc', route_3_inbox: '/ic', legacy_setting: 'keep'};
  removeRoute(model, 2);
  assert.equal(model.route_count, 2);
  assert.equal(model.route_2_route_name, 'C'); assert.equal(model.route_2_inbox, '/ic');
  assert.equal(model.inbox, '/ia'); assert.equal(model.legacy_setting, 'keep');
  assert.equal(model.route_3_local_root, '');
});
