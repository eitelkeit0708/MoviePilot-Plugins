"""Explicit one-time authorization for legacy empty inbox trees, journaled in meta."""
from hashlib import sha256
from pathlib import PurePosixPath
import time

from .domain import BridgeError, Awaiting, nested, check_stop
from .housekeeping import _step

KEY = 'empty_inbox_sweep_v1'
SCAN_DIRECTORIES_PER_ROUND = 10


def _candidates(config, nodes, ids, files, protected):
    empty = {}
    for path in sorted(nodes, key=lambda p:len(PurePosixPath(p).parts), reverse=True):
        empty[path] = path not in files and all(empty[c] for c in nodes[path])
    candidates = []
    for path in sorted(nodes):
        if (path == config.inbox or not empty[path] or any(nested(path, p) for p in protected)
                or (str(PurePosixPath(path).parent) != config.inbox and empty[str(PurePosixPath(path).parent)])):
            continue
        candidates.append({'source':path, 'id':ids[path], 'interval':300,
                           'directory_count':sum(p == path or p.startswith(path+'/') for p in nodes)})
    return candidates


def discover(config, cloud, protected=()):
    """Fresh CD2 inventory, all file types. Return maximal empty trees, never roots."""
    pending, nodes, ids, files = [(config.inbox, 0)], {}, {}, set()
    while pending:
        path, depth = pending.pop()
        if path in nodes or depth > 24 or len(nodes) >= 1000:
            raise BridgeError('空目录检查超过安全遍历范围，未执行清理', review=True)
        rows = cloud._children(path)
        if rows is None:
            raise Awaiting('目录清单不可读，未执行清理')
        nodes[path] = []
        for item in rows:
            if item.isDirectory:
                nodes[path].append(item.fullPathName)
                ids[item.fullPathName] = str(item.id)
                pending.append((item.fullPathName, depth+1))
            else:
                files.add(path)
    return _candidates(config, nodes, ids, files, protected), len(nodes)-1, len(files)


def discover_step(store, plan, configs, host, cloud, stop):
    """Bounded native reads in the host scheduler; checkpoint each directory."""
    from .duplicates import Isolation
    from .domain import child_path
    scan = plan['discovery']
    if scan.get('next_at', 0) > time.time():
        return
    try:
        for _ in range(SCAN_DIRECTORIES_PER_ROUND):
            check_stop(stop)
            pending = scan['pending']
            if not pending:
                break
            item = pending[0]
            config = next((c for c in configs if c.routing() == item['routing']), None)
            if config is None or (item['path'] != config.inbox and not item['path'].startswith(config.inbox+'/')):
                raise BridgeError('存量清理扫描路线已变化，保留原授权范围', review=True)
            nodes = scan['nodes'].setdefault(config.inbox, {})
            ids = scan['ids'].setdefault(config.inbox, {})
            files = scan['files'].setdefault(config.inbox, [])
            if item['path'] in nodes or item['depth'] > 24 or len(nodes) >= 5000:
                raise BridgeError('存量目录扫描超过安全范围，保留进度', review=True)
            h = host.for_config(config)
            h.gate_cloud()
            if item['path'] not in ids:
                ids[item['path']] = cloud.directory_id(item['path'])
                store.set_meta(KEY, plan)
            operations = h._native_operations()
            rows = Isolation(operations.request, None, config, stop).files(ids[item['path']])
            children, new = [], []
            for row in rows:
                path = child_path(item['path'], row['name'])
                if row['directory']:
                    if path in ids and ids[path] != row['id']:
                        raise BridgeError('扫描目录身份重复或变化，保留现场', review=True)
                    children.append(path)
                    ids[path] = row['id']
                    new.append({'path':path,'depth':item['depth']+1,'routing':item['routing']})
                elif item['path'] not in files:
                    files.append(item['path'])
            # Save the complete validated directory response before popping its work item.
            nodes[item['path']] = children
            pending.pop(0)
            pending.extend(new)
            scan['next_at'] = time.time()+60
            scan.pop('error', None)
            store.set_meta(KEY, plan)
        if not scan['pending']:
            plan['entries'] = []
            plan['roots'] = []
            for config in configs:
                nodes = scan['nodes'].get(config.inbox, {})
                if config.inbox not in nodes:
                    continue
                # Old and newly received move intents are protected again by advance().
                protected = scan.get('protected', [])
                candidates = _candidates(config,nodes,scan['ids'][config.inbox],scan['files'][config.inbox],protected)
                plan['entries'].extend({**r,'routing':config.routing()} for r in candidates)
                plan['roots'].append({'root':config.inbox,'directories':len(nodes)-1,'candidates':len(candidates)})
            plan.update(inventory_complete=True, inventory_at=time.time())
    except BridgeError as error:
        scan.update(error=str(error),next_at=max(time.time()+300,getattr(error,'next_at',0)))
    finally:
        store.set_meta(KEY, plan)


class Journal:
    def __init__(self, store, plan):
        self.store, self.plan = store, plan

    def save(self, job):
        self.store.set_meta(KEY, self.plan)


def advance(store, configs, host, cloud, stop, limit=3):
    plan = store.meta(KEY)
    if not isinstance(plan, dict) or plan.get('schema') != 1 or not plan.get('authorized_at'):
        return
    if plan.get('discovery') and not plan.get('inventory_complete'):
        return discover_step(store, plan, configs, host, cloud, stop)
    journal, now = Journal(store, plan), time.time()
    due = [r for r in plan['entries'] if not r.get('done') and r.get('next_at', 0) <= now]
    due.sort(key=lambda r:(r.get('next_at', 0),r['source']))
    for record in due[:limit]:
        check_stop(stop)
        config = next((r for r in configs if r.routing() == record['routing']), None)
        if config is None:
            record.update(error='原路线已变化，保留原清理范围', next_at=now+3600)
            continue
        if (record['source'] == config.inbox or not record['source'].startswith(config.inbox+'/')
                or any(nested(config.cd2_staging, r.inbox) for r in configs)):
            record.update(error='清理范围与原授权不一致，停止操作', next_at=now+3600)
            continue
        # A new move intent can arrive after discovery. Never retire its target or ancestor.
        if store.pending_inbox_path(record['source']):
            record.update(error='目录关联尚未完成的移交，保留', next_at=now+300)
            continue
        token = 'symediabatchbridge-sweep-' + sha256((plan['id']+record['source']).encode()).hexdigest()[:32]
        job = {'id':token}
        try:
            _step(job, record, config, host.for_config(config), cloud, journal, stop, 'sweep')
            record.pop('error', None)
        except BridgeError as error:
            record.update(error=str(error), next_at=max(now+300, getattr(error, 'next_at', 0)))
        finally:
            journal.save(job)
    plan['done'] = all(r.get('done') for r in plan['entries'])
    store.set_meta(KEY, plan)
