"""Journal and preserve old out-of-scope attachments before delivering valid media.

No accepted SHA or receipt is discarded. Cloud files move by immutable ID into a
batch-owned staging tree; only verified isolation permits reducing active scope.
"""
from copy import deepcopy
from pathlib import Path, PurePosixPath
import re
import time

from .activity import event
from .domain import Awaiting, BridgeError, child_path, check_stop, freeze_file
from .duplicates import Isolation, identifier, matches
from .ownership import SUBTITLES
from .localfiles import available


def _paths(config, job, relative):
    source = child_path(config.staging, job['id'])
    quarantine = child_path(child_path(config.staging, '_范围隔离'), job['id'])
    target = child_path(quarantine, relative)
    return source, quarantine, target


def _uncertain(entry):
    # A confirmed instant miss never created a file. An unknown result/receipt
    # remains protected and cannot be dismissed by one empty directory listing.
    return (entry.get('uploaded') or entry.get('receipt', {}).get('fileid') or entry.get('normal_requests')
            or entry.get('instant_error_since')
            or entry.get('instant_requests', 0) > entry.get('instant_misses', 0))


def _accepted_relative(config, local):
    """Cloud-only isolation may use a captured SHA even if another batch unlinked
    the local subtitle. It never authorizes reading or deleting another path.
    """
    available(config)
    path, root = Path(local), Path(config.local_root)
    try:
        relative = path.relative_to(root)
        if '..' in relative.parts:
            raise ValueError()
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        raise BridgeError('旧附件的记录路径越出原整理范围，已停止隔离', review=True) from None
    return relative.as_posix()


def prepare(job, candidates, config, store, stop):
    """An additive snapshot precedes any cloud change, including after restart."""
    if job.get('scope_repair'):
        return job['scope_repair']
    from . import subtitle_scope
    partition = subtitle_scope.partition
    kept, unmatched = partition(candidates)
    # Old versions could record a new MP display label over already frozen
    # filenames. Preserve a proven old companion pair when both SHA entries
    # exist and their own filename/source evidence agrees. This never admits
    # a fresh attachment or overrides a conflict between the two MP identities.
    known = {e['local']: e for e in job.get('files', [])}
    videos = [c for c in candidates if Path(c['local']).suffix.lower() not in SUBTITLES]
    reason = getattr(subtitle_scope, 'reason', None)
    matcher = getattr(subtitle_scope, 'matches', None)
    def old_pair(subtitle):
        if not callable(reason) or not callable(matcher) or reason(subtitle, videos) != 'metadata_filename_conflict':
            return False
        for video in videos:
            if not all(re.fullmatch(r'[0-9a-f]{40}', str(known.get(c['local'], {}).get('sha1', '')))
                       for c in (subtitle, video)):
                continue
            sm, vm = subtitle.get('media') or {}, video.get('media') or {}
            if any(sm.get(k) and vm.get(k) and str(sm[k]).casefold() != str(vm[k]).casefold()
                   for k in ('tmdbid', 'type', 'title', 'year', 'seasons', 'episodes')):
                continue
            if matcher({**subtitle, 'media': {}}, [{**video, 'media': {}}]):
                return True
        return False
    unmatched = [c for c in unmatched if not old_pair(c)]
    if not unmatched:
        return None
    if job.get('move_requested'):
        raise BridgeError('移交意图已封存，不能变更附件范围；保留全部文件等待核对', review=True)
    if not kept or not any(Path(c['local']).suffix.lower() not in SUBTITLES for c in kept):
        raise BridgeError('附件范围核对后没有可交付视频，保留原清单', review=True)
    items = []
    for candidate in unmatched:
        check_stop(stop)
        local = candidate['local']
        relative = _accepted_relative(config, local)
        if Path(local).suffix.lower() not in SUBTITLES:
            raise BridgeError('范围隔离只允许保留不匹配的字幕，不能移除视频', review=True)
        entry = deepcopy(known.get(local))
        if entry is None:
            # Legacy scopes may have been accepted before every subtitle was hashed.
            # Hash its organized copy once; never require a seeding source to exist.
            config.relative(local)
            entry = freeze_file(local, relative, stop)
        if entry.get('relative') != relative or not entry.get('sha1'):
            raise BridgeError('旧附件清单缺少完整身份，保留原批次等待复核', review=True)
        _, _, target = _paths(config, job, relative)
        items.append({'entry': entry, 'candidate': deepcopy(candidate), 'relative': relative,
                      'destination': target, 'state': 'planned',
                      'reason': '字幕的作品、季度或集数与本批视频不对应'})
    journal = {'schema': 1, 'phase': 'planned', 'created_at': time.time(),
               'original_count': len(candidates), 'items': items}
    job['scope_repair'] = journal
    store.save(job)
    store.record(job, event('scope_repair_planned',
        f'已封存 {len(items)} 个不匹配字幕的原清单与回执；隔离保留后交付对应视频及附件'))
    return journal


class ScopeIsolation:
    def __init__(self, config, stop, request, folder):
        self.config, self.stop = config, stop
        self.request, self.folder = request, folder
        self.reader = Isolation(request, folder, config, stop)

    def _validate(self, job, item):
        entry = item['entry']
        relative = _accepted_relative(self.config, entry['local'])
        _, _, target = _paths(self.config, job, relative)
        if (Path(entry['local']).suffix.lower() not in SUBTITLES
                or item['relative'] != relative or entry['relative'] != relative
                or item['candidate']['local'] != entry['local'] or item['destination'] != target):
            raise BridgeError('附件隔离清单越出本批范围，已停止操作', review=True)
        return entry

    def resume(self, job, journal, store, cloud, limit=16):
        if journal.get('schema') != 1 or not journal.get('items'):
            raise BridgeError('附件隔离记录格式不完整，保留现场', review=True)
        for item in journal['items']:
            self._validate(job, item)
        source, _, _ = _paths(self.config, job, journal['items'][0]['relative'])
        cd2_source = self.config.cd2_prefix.rstrip('/') + source
        if not journal.get('source_directory_id'):
            # If nothing was uploaded, absence is permitted only for unuploaded
            # entries. A receipt or an unknown in-flight request needs real proof.
            if not cloud.exists(cd2_source):
                if any(_uncertain(i['entry']) for i in journal['items']):
                    raise Awaiting('等待旧附件暂存目录及上传回执恢复，不重新上传或丢弃')
                for item in journal['items']:
                    item.update(state='unuploaded', confirmed_at=time.time())
                store.save(job)
                return
            journal['source_directory_id'] = identifier(cloud.directory_id(cd2_source))
            store.save(job)
        if journal['phase'] == 'planned':
            inventory = self.reader.inventory(journal['source_directory_id'])
            for item in journal['items']:
                entry = item['entry']
                rows = [r for r in inventory if r['relative'] == item['relative']]
                receipt = str(entry.get('receipt', {}).get('fileid') or '')
                if len(rows) > 1:
                    raise BridgeError('旧附件存在同名副本，保留清单等待唯一身份核对', review=True)
                if not rows:
                    if _uncertain(entry):
                        raise Awaiting('等待原生 115 核实旧附件上传结果，不撤销原回执')
                    item.update(state='unuploaded', confirmed_at=time.time())
                    continue
                row = rows[0]
                if row['directory'] or not matches(row, entry) or (receipt and row['id'] != receipt):
                    raise BridgeError('旧附件云端身份或 HASH 与回执不一致，已停止隔离', review=True)
                item['cloud'] = {k: row[k] for k in ('id', 'parent_id', 'name', 'size', 'sha1')}
            journal.update(phase='isolating', planned_at=time.time())
            store.save(job)
        if (not cloud.exists(cd2_source)
                or cloud.directory_id(cd2_source) != journal['source_directory_id']):
            raise Awaiting('旧批次暂存目录身份尚未恢复，保留附件隔离意图')
        operations = 0
        for item in journal['items']:
            if item['state'] in ('done', 'unuploaded'):
                continue
            check_stop(self.stop)
            if operations >= limit:
                raise Awaiting('不匹配字幕隔离进度已保存，下轮自动继续；本地文件均保留')
            entry, proof = self._validate(job, item), item['cloud']
            if not matches(proof, entry):
                raise BridgeError('附件隔离证据已变化，保留原清单', review=True)
            parent = str(PurePosixPath(item['destination']).parent)
            if not item.get('destination_id'):
                item['destination_id'] = identifier(self.folder(parent))
                if item['destination_id'] in (proof['parent_id'], journal['source_directory_id']):
                    raise BridgeError('字幕隔离目录与原批次目录身份冲突', review=True)
                store.save(job)
            src = {r['id']: r for r in self.reader.files(proof['parent_id'])}
            dst = {r['id']: r for r in self.reader.files(item['destination_id'])}
            current, moved = src.get(proof['id']), dst.get(proof['id'])
            for observed in (current, moved):
                if observed and (observed['directory'] or observed['name'] != proof['name']
                                 or not matches(observed, entry)):
                    raise BridgeError('字幕隔离两侧身份或内容不一致，已停止操作', review=True)
            if moved and current:
                raise Awaiting('字幕隔离两侧视图正在同步，保留回执等待复核')
            if not moved:
                if not current:
                    raise Awaiting('字幕隔离两侧暂不可见，保留原 ID，不重新上传')
                if any(r['name'] == proof['name'] and r['id'] != proof['id'] for r in dst.values()):
                    raise BridgeError('字幕隔离位置有同名文件，保留两侧，不覆盖', review=True)
                item.update(state='moving', requested_at=time.time())
                store.save(job)
                self.request('POST', '/open/ufile/move', data={
                    'file_ids': int(identifier(proof['id'])), 'to_cid': int(item['destination_id'])})
                operations += 1
                src = {r['id']: r for r in self.reader.files(proof['parent_id'])}
                dst = {r['id']: r for r in self.reader.files(item['destination_id'])}
                moved = dst.get(proof['id'])
                if (proof['id'] in src or not moved or moved['directory']
                        or moved['name'] != proof['name'] or not matches(moved, entry)):
                    raise Awaiting('字幕隔离移动结果尚未一致，稍后按原文件 ID 自动恢复')
            item.update(state='done', confirmed_at=time.time())
            store.save(job)
            store.record(job, event('scope_subtitle_isolated', '不匹配字幕已隔离保留；未交给 SA、未删除本地文件',
                                    file=item['relative']))


def repair(job, candidates, config, host, cloud, store, stop):
    journal = prepare(job, candidates, config, store, stop)
    if not journal or journal.get('phase') == 'complete':
        return candidates
    operations = getattr(host, '_native_operations', lambda: None)()
    if operations is None:
        raise BridgeError('等待原生 115 核对旧附件范围，原清单和文件均已保留', review=True)

    def folder(path):
        item = operations.call('get_folder', path=Path(path))
        get = lambda key: item.get(key) if isinstance(item, dict) else getattr(item, key, None)
        if not item or get('type') != 'dir' or str(PurePosixPath(str(get('path')))) != path:
            raise Awaiting('等待确认本批次专属字幕隔离目录')
        return get('fileid')

    ScopeIsolation(config, stop, operations.request, folder).resume(job, journal, store, cloud)
    if not all(i['state'] in ('done', 'unuploaded') for i in journal['items']):
        raise Awaiting('旧附件范围仍在隔离核对，暂不移交')
    excluded = {i['entry']['local'] for i in journal['items']}
    # Only now narrow the active manifest; every removed entry remains verbatim
    # in the journal, including its SHA, requests, receipt and original path.
    job['files'] = [e for e in job.get('files', []) if e['local'] not in excluded]
    if job.get('owned_candidates'):
        job['owned_candidates'] = [c for c in job['owned_candidates'] if c['local'] not in excluded]
    if job.get('inventory_files'):
        job['inventory_files'] = [c for c in job['inventory_files'] if c['local'] not in excluded]
    relative = {e['relative'] for e in job['files']}
    job['media_files'] = [m for m in job.get('media_files', []) if m.get('file') in relative]
    journal.update(phase='complete', completed_at=time.time(), excluded_count=len(excluded))
    store.save(job)
    store.record(job, event('scope_repair_complete',
        f'已保留隔离 {len(excluded)} 个不匹配字幕；继续交付对应视频与附件', notice='附件范围已修复', scope='scope_repair'))
    return [c for c in candidates if c['local'] not in excluded]
