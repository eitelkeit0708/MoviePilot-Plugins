"""Persist a destination once; midnight and upgrades never reroute an intent."""
from datetime import datetime, timezone, timedelta
from pathlib import PurePosixPath
import re
import time

from .domain import Awaiting, BridgeError, child_path, cloud_path


def destination(job, config, store):
    if job.get('destination'):
        target = job['destination']
    elif job.get('handoff_layout') == 'date':
        day = job.setdefault('handoff_date', datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
        target = child_path(child_path(config.inbox, day), job['id'])
        job['destination'] = target
        store.save(job)
    else:
        target = child_path(config.inbox, job['id'])
    parent = str(PurePosixPath(target).parent)
    try:
        if cloud_path(target) != target:
            raise ValueError()
    except ValueError:
        raise BridgeError('移交目录格式不合法，保留原记录等待核对', review=True) from None
    if (PurePosixPath(target).name != job['id'] or
            (parent != config.inbox and (str(PurePosixPath(parent).parent) != config.inbox or
             not re.fullmatch(r'\d{4}-\d{2}-\d{2}', PurePosixPath(parent).name)))):
        raise BridgeError('已记录的移交目录越出本批次范围', review=True)
    return target, parent


def stable_observation(job, key, store):
    now = time.time()
    old = job.get('move_observation', {})
    if old.get('key') != key:
        old = {'key': key, 'at': now, 'count': 1}
    elif now - old['at'] >= 60:
        old.update(at=now, count=old['count'] + 1)
    job['move_observation'] = old
    store.save(job)
    if old['count'] < 3:
        raise Awaiting('云端目录视图暂未稳定，已保留移动意图，稍后再次核对')
