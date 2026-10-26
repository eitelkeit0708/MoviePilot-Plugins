"""One returned MP result, durable independently of a temporarily busy SQLite."""
from hashlib import sha256
import json
import math
import os


def notice_fingerprint(notice):
    payload = {key: notice.get(key) for key in ('title', 'text', 'image')}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _sync_directory(directory):
    if os.name != 'nt':
        handle = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def stage_result(store, notice, success, at):
    """Caller holds notifications.lock, including across plugin reloads."""
    path = store.path.parent / 'notification-result.json'
    record = {'schema': 1, 'id': notice['id'], 'batch': notice['batch'],
              'attempts': notice['attempts'], 'fingerprint': notice_fingerprint(notice),
              'success': success, 'at': at}
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != record:
            raise ValueError('上一条通知回执尚未保存，已停止发送新通知')
        _sync_directory(path.parent)
        return
    temporary = path.with_suffix('.tmp')
    # A leftover temporary file is never treated as a returned host result.
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(record, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def recover_result(store):
    path = store.path.parent / 'notification-result.json'
    if not path.exists():
        return False
    record = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or record.get('schema') != 1 or type(record.get('id')) is not int or record['id'] <= 0
            or type(record.get('attempts')) is not int or record['attempts'] < 0
            or type(record.get('success')) is not bool or not isinstance(record.get('batch'), str)
            or not isinstance(record.get('fingerprint'), str) or len(record['fingerprint']) != 64
            or not isinstance(record.get('at'), (int, float)) or not math.isfinite(record['at']) or record['at'] <= 0):
        raise ValueError('通知回执格式不完整，已保留现场并停止发送')
    store.notice_result(record, record['success'], at=record['at'], fingerprint=record['fingerprint'])
    path.unlink()
    _sync_directory(path.parent)
    return True
