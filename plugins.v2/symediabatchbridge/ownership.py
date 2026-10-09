"""A completed MP transfer hands ownership of its local output to this plugin."""
from pathlib import Path
import time

from .domain import BridgeError, file_signature

SUBTITLES = {'.srt', '.ass', '.ssa', '.sub', '.idx', '.sup', '.vtt'}


def capture(candidates):
    return [{**{k: c[k] for k in ('local', 'history_id', 'media', 'source') if k in c},
             'accepted_signature': c.get('inventory_signature') or file_signature(Path(c['local']))}
            for c in candidates]


def accept(job, candidates):
    job['owned_candidates'] = capture(candidates)
    job['owned_at'] = time.time()
    return job['owned_candidates']


def collect_owned(job, config):
    candidates = job['owned_candidates']
    sealed = {e['local']: e for e in job.get('files', []) if e.get('local')}
    for candidate in candidates:
        config.relative(candidate['local'])
        # Once hashed, Engine validates against that hash. Before hashing, keep
        # the accepted file identity; unlinking another hardlink only changes ctime.
        if candidate['local'] not in sealed:
            current = file_signature(Path(candidate['local']))
            expected = candidate['accepted_signature']
            if any(current[i] != expected[i] for i in (0, 1, 3, 4)):
                raise BridgeError('接管后的整理文件已变化：' + Path(candidate['local']).name, review=True)
    return candidates


def migrate_sealed(job):
    """Only upgrade old jobs whose full captured scope was already hashed."""
    files, scope = job.get('files', []), job.get('media_files', [])
    if (not job.get('owned_candidates') and files and scope
            and all(e.get('sha1') and e.get('signature') for e in files)
            and {e['relative'] for e in files} == {e.get('file') for e in scope}):
        job['owned_candidates'] = [{'local': e['local'], 'accepted_signature': e['signature'],
                                    'media': next((m for m in scope if m.get('file') == e['relative']), {})}
                                   for e in files]
        job['owned_at'] = time.time()


def append_transfer(job, config, candidate):
    """New completed events may append files; existing accepted identities never change."""
    if (not job.get('owned_candidates') or job.get('move_requested')
            or job['state'] in ('handed_off', 'cancelled', 'deleting')):
        return False
    if any(c['local'] == candidate['local'] for c in job['owned_candidates']):
        return False
    config.relative(candidate['local'])
    job['owned_candidates'].extend(capture([candidate]))
    job['next_check'] = 0
    return True
