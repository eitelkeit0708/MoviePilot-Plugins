"""Whole MP work directories; receipts survive consumption and path reuse."""
from pathlib import PurePosixPath
import re
import time

from .domain import Awaiting, BridgeError, child_path, verify_tree, check_stop

MARKER = re.compile(r'\{tmdb(?:id)?[-=]([1-9][0-9]*)\}', re.I)


def plan(job, config, cloud, store):
    """All units are sealed before the first move, including multi-work torrents."""
    if job.get('work_handoffs'):
        return
    groups = {}
    names = set()
    for entry in job['files']:
        parts = PurePosixPath(entry['relative']).parts
        matches = [(i, MARKER.search(p)) for i, p in enumerate(parts[:-1]) if MARKER.search(p)]
        if len(matches) != 1:
            raise BridgeError('MP 整理路径缺少唯一 TMDB 作品目录，保留暂存文件等待核对', review=True)
        index, match = matches[0]
        prefix = '/'.join(parts[:index+1])
        group = groups.setdefault(prefix, {'name':parts[index], 'tmdbid':match.group(1), 'files':[]})
        group['files'].append({**entry, 'relative':'/'.join(parts[index+1:])})
    if not groups:
        raise BridgeError('没有可移交的作品文件', review=True)
    if len(groups) > 64:
        raise BridgeError('单批作品目录超过 64 个，请拆分后处理', review=True)
    units = []
    for prefix, group in sorted(groups.items()):
        name = group['name']
        if name.casefold() in names:
            raise BridgeError('同批作品目录同名，已停止自动合并', review=True)
        names.add(name.casefold())
        source = child_path(child_path(config.cd2_staging, job['id']), prefix)
        ident = cloud.directory_id(source)
        if not ident:
            raise Awaiting('等待 CD2 提供作品目录 ID，再整目录移交')
        verify_tree(group['files'], cloud.tree(source))
        if cloud.directory_id(source) != ident:
            raise Awaiting('作品目录身份正在变化，稍后重新核对')
        units.append({'source':source, 'destination':child_path(config.inbox, name),
                      'prefix':prefix, 'id':ident, 'tmdbid':group['tmdbid'],
                      'files':group['files'], 'phase':'prepared'})
    job.update(work_handoffs=units, work_plan_at=time.time(), move_requested=True,
               state='moving', message='作品目录已封存，准备整目录移交', next_check=0)
    # Single destination remains useful in old notification/page consumers.
    job['destination'] = units[0]['destination'] if len(units) == 1 else config.inbox
    store.save(job)


def validate(job, unit, config):
    expected = child_path(child_path(config.cd2_staging, job['id']), unit['prefix'])
    parts = PurePosixPath(unit['prefix']).parts
    if (unit['source'] != expected or not MARKER.search(parts[-1])
            or unit['destination'] != child_path(config.inbox, parts[-1])
            or not re.fullmatch(r'[1-9][0-9]{0,39}', str(unit['id']))):
        raise BridgeError('作品移交清单路径或身份不一致，已停止操作', review=True)
    originals = {e['relative']:e for e in job['files']}
    for entry in unit['files']:
        original = originals.get(unit['prefix'] + '/' + entry['relative'])
        if not original or any(original.get(k) != entry.get(k) for k in ('sha1','size','local')):
            raise BridgeError('作品移交文件与封存清单不一致', review=True)


def _blocked_message(detail, name):
    reason = detail.get('reason')
    if reason == 'retired_pending_recycle':
        return '上一批已移出 SA 输入目录，等待回收确认后复用名称：' + name
    if reason == 'cleanup_disabled':
        return '上一批空目录清理已关闭，无法确认名称可复用：' + name
    if reason == 'requested_move_pending':
        return '上一批移动请求尚未按目录 ID 核实，等待确认：' + name
    return '同一作品的上一批尚未消费清理，等待后顺序移交：' + name


def _remember_blocked(job, detail, store):
    previous = job.get('blocked_by') or {}
    if (previous.get('batch') == detail.get('batch')
            and previous.get('destination') == detail.get('destination')):
        detail = {**detail, 'since': previous.get('since') or time.time()}
    else:
        now = time.time()
        planned = job.get('work_plan_at')
        # Old prepared batches already waited under the pre-upgrade rule.
        # Their sealed plan is a conservative, durable start for that wait.
        since = planned if isinstance(planned, (int, float)) and 0 < planned <= now else now
        detail = {**detail, 'since': since}
    job['blocked_by'] = detail
    store.save(job)


def _clear_blocked(job, destination, store):
    blocked = job.get('blocked_by') or {}
    if blocked.get('destination') == destination:
        job.pop('blocked_by', None)
        store.save(job)


def reconcile(job, config, cloud, store, stop):
    units = job['work_handoffs']
    manifest = [u['prefix']+'/'+e['relative'] for u in units for e in u['files']]
    if len(set(manifest)) != len(manifest) or set(manifest) != {e['relative'] for e in job['files']}:
        raise BridgeError('作品分组与原始文件清单不完整对应，停止移动', review=True)
    for unit in units:
        validate(job, unit, config)
    for unit in units:
        if unit['phase'] == 'arrived':
            _clear_blocked(job, unit['destination'], store)
            continue
        check_stop(stop)
        detail = (store.work_blocker_details(job, unit['destination'], cleanup_enabled=config.cleanup_inbox)
                  if hasattr(store, 'work_blocker_details') else None)
        if detail is None and not hasattr(store, 'work_blocker_details'):
            batch_id = store.work_blocker(job, unit['destination'])
            detail = ({'batch': batch_id, 'destination': unit['destination'],
                       'reason': 'arrived_cleanup_pending'} if batch_id else None)
        if detail:
            _remember_blocked(job, detail, store)
            raise Awaiting(_blocked_message(detail, PurePosixPath(unit['destination']).name))
        _clear_blocked(job, unit['destination'], store)
        src, dst = unit['source'], unit['destination']
        source_exists, target_exists = cloud.exists(src), cloud.exists(dst)
        if unit['phase'] == 'requested' and target_exists and not source_exists:
            if cloud.directory_id(dst) != unit['id']:
                raise BridgeError('作品目标目录 ID 与移动意图不同，不能确认移交', review=True)
            unit.update(phase='arrived', arrived_at=time.time())
            unit.pop('observation', None)
            store.save(job)
            continue
        if target_exists:
            # Skip must never merge two generations, even when the name is equal.
            raise Awaiting('待归档目录已有同名作品，保留两侧，等待上一批消费清理')
        if not source_exists:
            now = time.time()
            observation = unit.setdefault('observation', {'at':now, 'count':1})
            if now - observation['at'] >= 60:
                observation.update(at=now, count=observation['count']+1)
            store.save(job)
            if observation['count'] >= 3:
                raise BridgeError('作品目录两端均不可见，保留原 ID 和移动意图，不重复上传', review=True)
            raise Awaiting('等待作品目录视图恢复，保留移动意图')
        if cloud.directory_id(src) != unit['id']:
            raise BridgeError('作品源目录已被替换，停止移动', review=True)
        verify_tree(unit['files'], cloud.tree(src))
        cloud.require_inbox(config.inbox)
        if time.time() - unit.get('requested_at', 0) < 60:
            raise Awaiting('移动请求已保存，等待下轮按目录 ID 核对')
        if cloud.directory_id(src) != unit['id']:
            raise Awaiting('作品目录身份变化，保留现场重新核对')
        unit.update(phase='requested', requested_at=time.time())
        store.save(job)  # No network mutation inside a database retry boundary.
        cloud.move_directory(src, config.inbox)
        # One RPC per round. Do not let a large multi-work torrent monopolize intake.
        raise Awaiting('已提交作品整目录移动，下一轮按目录 ID 核实')
    job.pop('blocked_by', None)
    job.update(state='handed_off', message='已按作品目录 ID 核实全部移交完成',
               completion_basis='work_directory_identity', handed_off_at=time.time(),
               next_check=0, attempts=0)
    store.save(job)
