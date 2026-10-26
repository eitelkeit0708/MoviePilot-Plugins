"""Retire owned empty trees before recycling; never delete from an active inbox."""
from pathlib import PurePosixPath
import time

from .activity import event
from .domain import Awaiting, BridgeError, child_path, check_stop, nested
from .handoff import destination


HOUR = 3600


def _cd2_empty_hint(cloud, path):
    hint = getattr(cloud, 'directory_only_children', None)
    return hint(path) if callable(hint) else not cloud.tree(path)


def _mark_work_released(job, record, store):
    """Record a proven name release; physical recycle still continues separately."""
    ident = str(record.get('id') or '')
    if not ident or not record.get('holding'):
        return False
    if (not record.get('inbox_released_at')
            or str(record.get('released_directory_id') or '') != ident):
        record.update(inbox_released_at=time.time(), released_directory_id=ident)
        store.save(job)
    return True


def _clear_release(record):
    record.pop('inbox_released_at', None)
    record.pop('released_directory_id', None)


def _step(job, record, config, host, cloud, store, stop, kind):
    now = time.time()
    interval = max(300, record.get('interval', HOUR))
    nonempty_wait = interval if kind.startswith('work') or kind == 'sweep' else 6 * HOUR
    if record.get('done') or record.get('next_at', 0) > now:
        return
    source, ident = record['source'], record['id']
    holding_parent = child_path(child_path(child_path(config.cd2_staging, '_空目录清理'), job['id']), kind)
    holding = child_path(holding_parent, PurePosixPath(source).name)
    if record.get('holding') not in (None, holding):
        raise BridgeError('空目录清理目标越出原批次范围', review=True)
    record['holding'] = holding
    phase = record.get('phase', 'observing')
    record['next_at'] = now + interval
    check_stop(stop)
    host.gate_cloud()
    if phase == 'observing':
        if not cloud.exists(source):
            if record.get('absent_at') and now - record['absent_at'] >= interval:
                record.update(done=True, phase='already_absent')
            else:
                record['absent_at'] = now
            return
        record.pop('absent_at', None)
        actual_id = cloud.directory_id(source)
        if actual_id != ident:
            if kind == 'date' and actual_id:
                # Finish only this old identity's cleanup record. A replacement
                # date directory belongs to a different generation; never adopt,
                # inspect or delete its contents through the old receipt.
                record.update(done=True, phase='replaced', observed_id=actual_id,
                              note='原日期目录已被同名新目录替换，已保留新目录')
                return
            raise BridgeError('已移交目录被同名新目录替换，保留并停止清理', review=True)
        if (not _cd2_empty_hint(cloud, source) or (kind == 'date' and cloud._children(source))
                or not host.empty_directory(source, ident)):
            record.pop('empty_since', None)
            record['next_at'] = now + nonempty_wait
            raise Awaiting('SA 输入目录仍有文件，已保留；不会按保留期删除媒体或字幕')
        if not record.get('empty_since'):
            record['empty_since'] = now
            return
        if now - record['empty_since'] < interval:
            return
        cloud.ensure_directory(holding_parent)
        record.update(phase='claiming', claim_requested_at=now)
        if kind.startswith('work'):
            # Two empty observations already span the full safety interval.
            # The move acknowledgement needs a prompt identity check, not a
            # second five-minute name reservation before physical recycling.
            record['next_at'] = now + 60
        store.save(job)
        cloud.move_directory(source, holding_parent)
        return
    source_exists, retired_exists = cloud.exists(source), cloud.exists(holding)
    source_id = cloud.directory_id(source) if source_exists else None
    # The old identity is already outside SA. A replacement at its former date
    # path cannot turn that retired identity back into the active directory.
    # Unique batch paths and unknown IDs remain conflicts; never merge trees.
    replaced_date = ((kind == 'date' or kind.startswith('work') or kind == 'sweep')
                     and source_exists and bool(source_id) and source_id != ident)
    if retired_exists:
        if cloud.directory_id(holding) != ident:
            raise BridgeError('退役空目录身份不一致，已停止清理', review=True)
        if source_exists and not replaced_date:
            raise Awaiting('空目录退役两侧视图正在同步，暂不删除')
        holding_hint_empty = _cd2_empty_hint(cloud, holding)
        holding_native_empty = host.empty_directory(holding, ident) if holding_hint_empty else False
        if not holding_hint_empty or not holding_native_empty:
            if replaced_date:
                raise Awaiting('退役日期目录出现文件，原位置已有同名新目录；两侧均保留，不覆盖或合并')
            # A late file is preserved and the entire tree goes back to SA.
            _clear_release(record)
            record.update(phase='restoring', restore_requested_at=now)
            store.save(job)
            cloud.ensure_directory(str(PurePosixPath(source).parent))
            cloud.move_directory(holding, str(PurePosixPath(source).parent))
            raise Awaiting('退役目录出现文件，已安排整目录恢复到 SA 输入目录')
        # Exact holding ID, old ID absent from its original SA path, and fresh
        # native emptiness are the release proof. Recycling below still owns
        # the physical deletion; the name is reusable immediately after this.
        if kind.startswith('work'):
            _mark_work_released(job, record, store)
        if phase == 'restoring':
            raise Awaiting('正在核对目录恢复结果，不执行删除')
        if not record.get('retired_at'):
            record.update(phase='holding', retired_at=now)
            return
        if now - record['retired_at'] < interval:
            return
        record.update(phase='recycling', delete_requested_at=now)
        store.save(job)
        # The directory has spent an hour outside all SA inboxes. Only this
        # plugin owns this holding path; fresh native identity/emptiness is
        # checked again immediately before the non-permanent 115 recycle call.
        host.delete_retired_empty(holding, ident, job['id'])
        return
    if source_exists and not (replaced_date and phase == 'recycling'):
        if source_id != ident:
            raise BridgeError('原位置目录身份改变，保留清理记录等待核对', review=True)
        if phase == 'restoring':
            record.update(phase='observing', next_at=now + nonempty_wait)
            _clear_release(record)
            for key in ('empty_since','retired_at','claim_requested_at'):
                record.pop(key, None)
            return
        if phase == 'claiming':
            if not _cd2_empty_hint(cloud, source) or not host.empty_directory(source, ident):
                record.update(phase='observing', next_at=now + nonempty_wait)
                record.pop('empty_since', None)
                return
            cloud.ensure_directory(holding_parent)
            cloud.move_directory(source, holding_parent)
            return
        raise Awaiting('等待核对空目录退役结果，保留记录')
    if phase == 'recycling':
        if record.get('absent_at') and now - record['absent_at'] >= interval:
            record.update(done=True, phase='recycled', completed_at=now)
        else:
            record['absent_at'] = now
        return
    raise Awaiting('空目录两端暂不可见，稍后按原 ID 核对，不重建或删除')


def process_work(job, config, host, cloud, store, stop):
    """Clean each arrived unit independently; one sibling never hides another."""
    from .workhandoff import validate
    state = job.setdefault('inbox_cleanup', {})
    units = job.get('work_handoffs') or []
    old = str(state)
    now = time.time()
    pending, errors = [], []
    for index, unit in enumerate(units):
        try:
            validate(job, unit, config)
        except BridgeError as error:
            errors.append(str(error))
            continue
        if unit.get('phase') != 'arrived':
            continue
        record = unit.setdefault('cleanup', {'source':unit['destination'], 'id':unit['id'], 'interval':300})
        if record['source'] != unit['destination'] or record['id'] != unit['id']:
            errors.append('作品清理身份与移动回执不一致')
            record['next_at'] = max(record.get('next_at', 0), now + 300)
            pending.append(record['next_at'])
            continue
        try:
            _step(job, record, config, host, cloud, store, stop, 'work'+str(index))
        except BridgeError as error:
            errors.append(str(error))
            delay = 60 if isinstance(error, Awaiting) and record.get('phase') == 'claiming' else 300
            until = max(now + delay, float(getattr(error, 'next_at', 0) or 0))
            record['next_at'] = max(record.get('next_at', 0), until)
        if not record.get('done'):
            pending.append(record.get('next_at', now + 300))
    # A stale top-level done must never hide an arrived unit without its own
    # discharge. Prepared siblings keep the conservative false value.
    done = bool(units) and all(unit.get('cleanup', {}).get('done') for unit in units)
    state.update(done=done, next_at=min(pending) if pending else now + 300)
    if errors:
        state['error'] = '；'.join(dict.fromkeys(errors))[:1000]
    else:
        state.pop('error', None)
    store.save(job)
    if old != str(state) and (state.get('done') or state.get('error')):
        store.record(job, event('inbox_cleanup', state.get('error') or '已完成已移交空目录的安全清理',
                                level='warning' if state.get('error') else 'info'))


def process(job, config, host, cloud, store, stop, *, protected_routings=()):
    if job.get('state') in ('cancelled', 'deleting') or not config.cleanup_inbox:
        return  # Disabled cleanup must never move or delete anything.
    if job.get('work_handoffs'):
        for route in protected_routings:
            if route.get('cd2_address') == config.cd2_address and nested(config.cd2_staging, route['inbox']):
                return  # Holding must remain outside every active or historical watcher.
        return process_work(job, config, host, cloud, store, stop)
    if job['state'] != 'handed_off':
        return
    now = time.time()
    if now - job.get('handed_off_at', now) < config.inbox_retention_days * 86400:
        return
    state = job.setdefault('inbox_cleanup', {})
    old = str(state)
    try:
        for route in protected_routings:
            if route.get('cd2_address') == config.cd2_address and route.get('cd2_prefix') == config.cd2_prefix:
                if (nested(config.cd2_staging, route['inbox']) or
                        nested(config.inbox, route['cd2_prefix'].rstrip('/') + route['staging'])):
                    raise BridgeError('新旧路线的暂存与 SA 输入目录重叠，保留目录并暂停清理', review=True)
        target, parent = destination(job, config, store)
        ident = job.get('source_directory_id')
        if not ident:
            state.update(done=True, note='旧批次缺少稳定目录 ID，保留目录')
            state.pop('error', None)
            return
        record = state.setdefault('batch', {'source':target, 'id':ident})
        if record['source'] != target or record['id'] != ident:
            raise BridgeError('清理记录与批次移交身份不一致', review=True)
        _step(job, record, config, host, cloud, store, stop, 'batch')
        if record.get('done'):
            owned = store.owned_inbox_date(parent, job.get('delivery_parent_id'))
            if parent != config.inbox and owned and not store.pending_destination(parent):
                date_record = state.setdefault('date', {'source':parent, 'id':owned})
                if date_record['source'] != parent or date_record['id'] != owned:
                    raise BridgeError('日期目录清理身份不一致', review=True)
                _step(job, date_record, config, host, cloud, store, stop, 'date')
                state['done'] = bool(date_record.get('done'))
                if date_record.get('note'):
                    state['note'] = date_record['note']
            else:
                state['done'] = parent == config.inbox or not owned
        state.pop('error', None)
    except BridgeError as error:
        state['error'] = str(error)
        if getattr(error, 'next_at', 0):
            state['next_at'] = error.next_at
    finally:
        pending = [r.get('next_at', now + HOUR) for k,r in state.items()
                   if k in ('batch','date') and not r.get('done')]
        state['next_at'] = max(state.get('next_at', 0), min(pending) if pending else now + HOUR)
        store.save(job)
        if old != str(state) and (state.get('done') or state.get('error')):
            store.record(job, event('inbox_cleanup', state.get('error') or state.get('note') or '已完成已移交空目录的安全清理',
                                    level='warning' if state.get('error') else 'info'))
