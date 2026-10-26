"""A completed MP transfer hands ownership of its local output to this plugin."""
from pathlib import Path
import time

from .domain import BridgeError, file_signature
from . import subtitle_scope

SUBTITLES = {'.srt', '.ass', '.ssa', '.sub', '.idx', '.sup', '.vtt'}


def _is_subtitle(candidate):
    return Path(str(candidate.get('local') or candidate.get('source') or '')).suffix.lower() in SUBTITLES


def _record_excluded(job, candidate, detail):
    """Durably-shaped journal entry; the controller persists the job."""
    entry = {key: candidate[key] for key in ('local', 'source', 'history_id', 'media', 'date')
             if key in candidate}
    entry['reason'] = detail
    existing = job.setdefault('excluded_attachments', [])
    identity = (entry.get('local'), entry.get('history_id'), detail)
    if not any((row.get('local'), row.get('history_id'), row.get('reason')) == identity
               for row in existing):
        existing.append(entry)


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
    for candidate in candidates:
        config.relative(candidate['local'])
        # Engine checks each file against its SHA or authenticated torrent pieces.
        # A metadata change alone is neither proof of corruption nor permission
        # to replace the accepted signature. Intake never performs slow hashing.
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
    if _is_subtitle(candidate):
        videos = [c for c in job['owned_candidates'] if not _is_subtitle(c)]
        detail = subtitle_scope.reason(candidate, videos)
        if detail is not None:
            # Never expand accepted ownership with an unattributed subtitle.
            # Do not touch the accepted files or their receipts.
            _record_excluded(job, candidate, detail)
            return False
    config.relative(candidate['local'])
    job['owned_candidates'].extend(capture([candidate]))
    job['next_check'] = 0
    return True
