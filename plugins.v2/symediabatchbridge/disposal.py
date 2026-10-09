"""Explicit, replayable exception resolution. No cloud or downloader mutations."""
from pathlib import Path
import os
import time
import secrets

from .activity import attention, event
from .domain import BridgeError, file_signature
from .localfiles import available, remove_file
from .ownership import SUBTITLES

MEDIA = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.ts', '.m2ts', '.iso', '.strm',
         '.mpg', '.mpeg', '.m4v', '.flv', '.webm', '.vob', '.rmvb', '.mp3', '.flac', '.m4a', '.wav'}


def eligible(job):
    return (attention(job) and job['state'] not in ('handed_off', 'cancelled', 'deleting')
            and not job.get('move_requested'))


def members(job):
    result = {}
    for collection in ('inventory_files', 'owned_candidates', 'orphan_files', 'files'):
        for entry in job.get(collection, []):
            if entry.get('local'):
                result[entry['local']] = entry
    return result


def no_media(job, config, extensions):
    available(config)
    paths = [Path(name) for name in members(job)]
    if not paths:
        raise BridgeError('没有可核实的本地文件清单，不能删除字幕', review=True)
    for path in paths:
        try:
            path.relative_to(Path(config.local_root))
            path.parent.resolve(strict=True).relative_to(Path(config.local_root))
        except (ValueError, OSError):
            raise BridgeError('原文件目录不可读，不能判断视频是否缺失', review=True) from None
        if path.is_symlink() or path.parent.is_symlink():
            raise BridgeError('文件路径包含符号链接，不能删除字幕', review=True)
        if path.suffix.lower() not in SUBTITLES and path.exists():
            raise BridgeError('本批次仍有媒体文件，不能删除字幕', review=True)
    # Look only in known file directories; never locate or adopt relocated files.
    for parent in {p.parent for p in paths}:
        def fail(error):
            raise error
        for folder, dirs, files in os.walk(parent, onerror=fail, followlinks=False):
            if any((Path(folder) / name).is_symlink() for name in [*dirs, *files]):
                raise BridgeError('同目录含符号链接，无法确认媒体范围', review=True)
            if any(Path(name).suffix.lower() in extensions - SUBTITLES for name in files):
                raise BridgeError('同目录仍有媒体文件，请保留对应字幕', review=True)


def subtitle_plan(job, config, extensions=()):
    if not (job.get('owned_candidates') or job.get('inventory_files') or job.get('orphan_files')):
        raise BridgeError('尚未取得完整接管清单，不能判断字幕是否无主', review=True)
    media = MEDIA | set(extensions)
    no_media(job, config, media)
    result = []
    for name, entry in members(job).items():
        path = Path(name)
        if path.suffix.lower() not in SUBTITLES or not path.exists():
            continue
        relative = config.relative(name)
        current = file_signature(path)
        expected = entry.get('signature') or entry.get('accepted_signature')
        if not expected or any(current[i] != expected[i] for i in (0, 1, 3, 4)):
            raise BridgeError('字幕与接管清单不一致，保留文件：' + path.name, review=True)
        result.append({'local': name, 'relative': relative, 'signature': current,
                       'download_source': entry.get('download_source') or entry.get('source', '')})
    if not result:
        raise BridgeError('没有可删除的孤立字幕；可终止此批次并保留记录', review=True)
    return sorted(result, key=lambda entry: entry['local'])


def prepare(store, job, config, kind, extensions=()):
    if not eligible(job):
        raise BridgeError('当前批次不能终止或删除；移交中的批次须先确认结果', review=True)
    files = subtitle_plan(job, config, extensions) if kind == 'delete' else []
    job['disposal_plan'] = dict(kind=kind, token=secrets.token_urlsafe(24),
                                expires=time.time() + 600, files=files,
                                extensions=sorted(MEDIA | set(extensions)))
    store.save(job)


def finish(store, job, message):
    job.update(state='cancelled', next_check=0, attempts=0, cancelled_at=time.time(),
               message=message, disposal_error='')
    job.pop('disposal_plan', None)
    job.pop('retry_requested', None)
    store.save(job)


def confirm(store, job, config, token):
    if token and token == job.get('disposed_token'):
        return  # An HTTP response can be lost after committing the action.
    plan = job.get('disposal_plan', {})
    if not token or token != plan.get('token') or plan.get('expires', 0) < time.time() or not eligible(job):
        raise BridgeError('确认已过期或批次状态已变化，请重新查看待处理清单', review=True)
    if plan['kind'] == 'delete':
        current = subtitle_plan(job, config, plan['extensions'])
        if current != plan['files']:
            raise BridgeError('文件清单已变化，请重新查看后确认删除', review=True)
        job.update(state='deleting', deletion_files=current, deletion_extensions=plan['extensions'],
                   next_check=0, attempts=0, message='已确认删除孤立字幕，等待执行')
        job.pop('disposal_plan', None)
    job['disposed_token'] = token
    if plan['kind'] == 'stop':
        finish(store, job, '已终止处理；本地和云端文件保留，不再自动重试')
    else:
        store.save(job)


def process(store, job, config, stop):
    if job['state'] != 'deleting':
        return
    try:
        no_media(job, config, set(job['deletion_extensions']))
        for entry in job['deletion_files']:
            if entry.get('deleted'):
                continue
            remove_file(store, job, entry, config, stop, pending_key='delete_pending', done_key='deleted')
            store.record(job, event('subtitle_deleted', '已按确认清单删除孤立字幕', file=entry['relative']))
        finish(store, job, '孤立字幕已删除，批次已终止；处理记录保留')
    except (BridgeError, OSError) as error:
        message = str(error) if isinstance(error, BridgeError) else '字幕暂时无法删除，保留删除进度，一小时后自动重试'
        job.update(disposal_error=message, message=message, next_check=time.time() + 3600)
        store.save(job)
        store.record(job, event('delete_error', message, level='warning', notice='清理未完成',
                                scope='issue', next_at=job['next_check']))
