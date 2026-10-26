export const defaultView = () => ({key: '', page: 0, files: 0, events: 0, route: '', status: 'all', expanded: '', panel: 'jobs'});

// One outstanding action per page prevents duplicate destructive submissions and
// stale responses overwriting newer navigation. It never retries a mutation.
export function createClient(api, pluginId, accept) {
  let pending = false;
  let view = defaultView();
  return {
    async run(action = 'view', params = view) {
      if (pending) return {success: false, message: '上一项操作尚未完成，请稍候'};
      pending = true;
      try {
        const result = await api.post(`plugin/${pluginId}/ui`, {action, params, view});
        if (!result || !Array.isArray(result.page) || !result.view || typeof result.success !== 'boolean') {
          throw new Error('页面响应无效，请重试');
        }
        view = result.view;
        accept(result.page);
        return result;
      } finally { pending = false; }
    },
  };
}

export function removeRoute(model, index) {
  const fields = ['route_name', 'local_root', 'staging', 'inbox'];
  const count = Number(model.route_count || 1);
  if (count <= 1 || index < 1 || index > count) return;
  const prefix = i => i === 1 ? '' : `route_${i}_`;
  for (let i = index; i < count; i++) for (const field of fields) model[prefix(i) + field] = model[prefix(i + 1) + field];
  Object.assign(model, {[prefix(count) + 'route_name']: `路线 ${count}`, [prefix(count) + 'local_root']: '',
    [prefix(count) + 'staging']: `/MP暂存/路线${count}`, [prefix(count) + 'inbox']: '', route_count: count - 1});
}
