"""Real temp files, original-piece proofs, and persisted old batches; no cloud SDK."""
from hashlib import sha1
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import Mock
import sys

import pytest

from test_batches import batch


def mod(name):
    return importlib.import_module('app.plugins.symediabatchbridge.' + name)


def encode(value):
    if isinstance(value, int):
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, list):
        return b'l' + b''.join(map(encode, value)) + b'e'
    return b'd' + b''.join(encode(k) + encode(value[k]) for k in sorted(value)) + b'e'


def original(files, piece=4):
    payload = b''.join(data for _, data in files)
    info = {b'name': b'Release', b'piece length': piece,
            b'pieces': b''.join(sha1(payload[i:i + piece]).digest() for i in range(0, len(payload), piece)),
            b'files': [{b'path': [name.encode()], b'length': len(data)} for name, data in files]}
    key = sha1(encode(info)).hexdigest()
    raw = encode({b'announce': b'https://tracker.invalid/private-secret', b'info': info})
    return key, raw


def touch(path):
    s = path.stat()
    os.utime(path, ns=(s.st_atime_ns, s.st_mtime_ns + 10000000))


@pytest.fixture
def pending(batch, modules, tmp_path):
    b = batch
    source = tmp_path / 'downloads' / 'Release'
    source.mkdir(parents=True)
    data = [(Path(p).name, Path(p).read_bytes()) for p in b.paths]
    key, raw = original(data)
    candidates = []
    for p, (name, content) in zip(b.paths, data):
        src = source / name
        src.write_bytes(content)
        candidates.append({'local': p, 'source': str(src).replace('\\', '/'), 'history_id': len(candidates) + 1})
    job = b.job()
    job.update(download_hash=key, download_manifest={c['source']: Path(c['local']).stat().st_size for c in candidates})
    mod('ownership').accept(job, candidates)
    b.store.save(job)
    b.host.collect = lambda j: mod('ownership').collect_owned(j, b.config)
    proof = mod('contentproof').evidence(raw, key)
    b.host.torrent_evidence = Mock(return_value=proof)
    b.raw, b.proof, b.key, b.candidates = raw, proof, key, candidates
    return b


def test_same_content_timestamp_change_reuses_one_local_read_and_caches_only_info(pending, modules, monkeypatch):
    b = pending
    touch(Path(b.paths[0]))
    freeze = Mock(wraps=modules.engine.freeze_file)
    monkeypatch.setattr(modules.engine, 'freeze_file', freeze)
    b.run()
    job = b.job()
    assert job['state'] == 'handed_off', job['message']
    assert len(job['files']) == len(freeze.call_args_list) == 2
    assert len(b.host.uploads) == 2 and len(b.cloud.moves) == 1
    entry = job['files'][0]
    assert entry['content_revalidation']['basis'] == 'torrent_pieces'
    assert entry['sha1'] == sha1(Path(b.paths[0]).read_bytes()).hexdigest()
    cached = b.store.meta('content-proof:' + b.key)
    assert 'private-secret' not in json.dumps(cached)
    assert any(e['kind'] == 'content_revalidated' for e in b.store.events(job['id']))


def test_rewrite_preserving_mtime_is_still_rejected_by_ctime(pending, modules, monkeypatch):
    b = pending
    path = Path(b.paths[0])
    old = path.stat()
    path.write_bytes(b'wrong')
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    # Windows exposes creation time as st_ctime; emulate Linux inode ctime only.
    actual_signature = modules.domain.file_signature
    def signature(p):
        result = actual_signature(p)
        if str(p) == str(path):
            result[2] += 10000000
        return result
    monkeypatch.setattr(modules.domain, 'file_signature', signature)
    monkeypatch.setattr(mod('contentproof'), 'file_signature', signature)
    b.run()
    assert b.job()['state'] == 'review' and '原种子不一致' in b.job()['message']
    assert not b.job()['files'] and not b.host.uploads


def test_original_evidence_cached_before_hash_and_task_expiry(pending, modules, monkeypatch):
    b = pending
    def crash(*args, **kwargs):
        raise SystemExit('stopped before first SHA')
    monkeypatch.setattr(b.engine, '_hash_file', crash)
    with pytest.raises(SystemExit):
        b.run()
    assert b.store.meta('content-proof:' + b.key) and not b.job()['files']
    b.host.torrent_evidence.assert_called_once()


def test_changed_content_is_blocked_without_cloud_write_or_replacing_snapshot(pending, modules, monkeypatch):
    b = pending
    before = b.job()['owned_candidates'][0]['accepted_signature']
    path = Path(b.paths[0])
    path.write_bytes(b'wrong')
    touch(path)
    b.run()
    job = b.job()
    assert job['state'] == 'review' and '原种子不一致' in job['message']
    assert not b.host.uploads and not b.cloud.moves and not job['files']
    assert job['owned_candidates'][0]['accepted_signature'] == before
    assert job['owned_candidates'][0]['content_rejection']['signature'] == modules.domain.file_signature(path)
    monkeypatch.setattr(modules.engine, 'freeze_file', Mock(side_effect=AssertionError('unchanged rejected content must not reread')))
    b.run()
    assert b.job()['state'] == 'review'


def test_rejected_original_restoration_recovers_after_restart_without_downloader(pending, modules):
    b = pending
    path = Path(b.paths[0])
    path.write_bytes(b'wrong')
    touch(path)
    b.run()
    assert b.job()['state'] == 'review'
    store = modules.store.Store(b.store.path.parent)
    b.engine.store = store
    b.host.torrent_evidence.side_effect = AssertionError('use durable original proof')
    path.write_bytes(b'video')
    touch(path)
    b.engine.process(store.get(b.id))
    assert store.get(b.id)['state'] == 'handed_off'
    b.host.torrent_evidence.assert_called_once()


def test_missing_proof_is_not_permission_to_accept_but_recovers_when_service_returns(pending, modules):
    b = pending
    touch(Path(b.paths[0]))
    b.host.torrent_evidence.side_effect = modules.domain.BridgeError('无法取得原种子校验证据', review=True)
    before = b.job()['owned_candidates']
    b.run()
    assert b.job()['state'] == 'review' and b.job()['owned_candidates'] == before
    assert not b.job()['files'] and not b.host.uploads
    b.host.torrent_evidence.side_effect = None
    b.run()
    assert b.job()['state'] == 'handed_off'


def test_missing_boundary_holds_then_recovers_and_boundary_change_invalidates_rejection(pending, modules):
    b = pending
    path, neighbor = map(Path, b.paths)
    original_bytes = neighbor.read_bytes()
    neighbor.write_bytes(b'corrupt!')  # Same size: only cross-file pieces expose the mismatch.
    touch(path)
    b.run()
    assert b.job()['state'] == 'review' and not b.job()['files']
    neighbor.write_bytes(original_bytes)
    touch(neighbor)
    b.run()
    assert b.job()['state'] == 'handed_off', b.job()['message']


def test_deleted_originals_use_owned_boundary_copies_and_persisted_proof(pending):
    b = pending
    b.store.set_meta('content-proof:' + b.key, b.proof)
    for c in b.candidates:
        Path(c['source']).unlink()
    Path(b.candidates[0]['source']).parent.rmdir()
    touch(Path(b.paths[0]))
    b.host.torrent_evidence.side_effect = AssertionError('downloader expired')
    b.run()
    assert b.job()['state'] == 'handed_off', b.job()['message']


def test_unproven_cached_evidence_and_other_same_filename_never_recover(pending):
    b = pending
    wrong_key, raw = original([(Path(b.paths[0]).name, b'other'), (Path(b.paths[1]).name, b'subtitle')])
    b.host.torrent_evidence.return_value = mod('contentproof').evidence(raw, wrong_key)
    touch(Path(b.paths[0]))
    b.run()
    assert b.job()['state'] == 'review' and not b.job()['files']
    assert b.store.meta('content-proof:' + b.key) is None
    assert not b.host.uploads


def test_identical_replacement_inode_requires_original_pieces_then_recovers(pending):
    b = pending
    path = Path(b.paths[0])
    replacement = path.with_suffix('.new')
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    touch(path)
    b.run()
    assert b.job()['state'] == 'handed_off'
    assert b.job()['files'][0]['content_revalidation']['basis'] == 'torrent_pieces'


def test_change_during_validation_is_not_cached_as_permanent_corruption(pending, modules, monkeypatch):
    b = pending
    path = Path(b.paths[0])
    touch(path)
    freeze = modules.engine.freeze_file
    def metadata_race(*args, **kwargs):
        touch(path)
        return freeze(*args, **kwargs)
    monkeypatch.setattr(modules.engine, 'freeze_file', metadata_race)
    b.run()
    assert b.job()['state'] == 'waiting'
    assert not b.job()['files'] and not b.job()['owned_candidates'][0].get('content_rejection')
    monkeypatch.setattr(modules.engine, 'freeze_file', freeze)
    b.run()
    assert b.job()['state'] == 'handed_off'


def test_missing_unowned_boundary_never_uploads_and_recovers_when_restored(pending):
    b = pending
    neighbor = Path(b.paths[1])
    job = b.job()
    job['owned_candidates'] = job['owned_candidates'][:1]
    b.store.save(job)
    source_neighbor = Path(b.candidates[1]['source'])
    data = source_neighbor.read_bytes()
    source_neighbor.unlink()
    touch(Path(b.paths[0]))
    b.run()
    assert b.job()['state'] == 'waiting' and '跨文件分块' in b.job()['message']
    assert not b.job()['files'] and not b.host.uploads
    source_neighbor.write_bytes(data)
    b.run()
    assert b.job()['state'] == 'handed_off'
    assert len(b.job()['files']) == 1 and len(b.host.uploads) == 1


def test_single_file_torrent_and_partial_last_piece(modules, config_values, tmp_path):
    from threading import Event
    config = modules.domain.Config.parse(config_values)
    path = Path(config.local_root) / 'single.mkv'
    data = b'123456789'
    path.write_bytes(data)
    info = {b'name': b'single.mkv', b'length': len(data), b'piece length': 4,
            b'pieces': b''.join(sha1(data[i:i+4]).digest() for i in range(0, len(data), 4))}
    key = sha1(encode(info)).hexdigest()
    proof = mod('contentproof').evidence(encode({b'info': info}), key)
    source = str(tmp_path / 'downloads' / 'single.mkv').replace('\\', '/')
    candidate = {'local': str(path), 'source': source}
    job = {'download_hash': key, 'download_manifest': {source: len(data)}, 'owned_candidates': [candidate]}
    validator = mod('contentproof').PieceValidator(proof, job, candidate, Event(), config)
    result = modules.domain.freeze_file(str(path), 'single.mkv', Event(), validator=validator)
    assert result['sha1'] == sha1(data).hexdigest() and validator.checked == 3


def test_authenticated_padding_does_not_require_a_fake_file_on_disk(modules, config_values, tmp_path):
    from threading import Event
    config = modules.domain.Config.parse(config_values)
    path = Path(config.local_root) / 'video.mkv'
    path.write_bytes(b'video')
    data = b'video' + b'\x00' * 3
    info = {b'name': b'Release', b'piece length': 4,
            b'pieces': sha1(data[:4]).digest() + sha1(data[4:]).digest(),
            b'files': [{b'path': [b'video.mkv'], b'length': 5},
                       {b'path': [b'.pad', b'3'], b'length': 3, b'attr': b'p'}]}
    key = sha1(encode(info)).hexdigest()
    proof = mod('contentproof').evidence(encode({b'info': info}), key)
    source = str(tmp_path / 'downloads' / 'Release' / 'video.mkv').replace('\\', '/')
    candidate = {'local': str(path), 'source': source}
    job = {'download_hash': key, 'download_manifest': {source: 5}, 'owned_candidates': [candidate]}
    validator = mod('contentproof').PieceValidator(proof, job, candidate, Event(), config)
    result = modules.domain.freeze_file(str(path), 'video.mkv', Event(), validator=validator)
    assert result['sha1'] == sha1(b'video').hexdigest() and validator.checked == 2


@pytest.mark.parametrize('change', ['same', 'different'])
def test_sealed_legacy_revalidation_preserves_sha_receipts_and_clocks(batch, modules, change):
    b = batch
    job = b.job()
    job['files'] = [modules.domain.freeze_file(p, b.config.relative(p), b.stop) for p in b.paths]
    first = job['files'][0]
    first.update(uploaded=True, receipt={'fileid': 'receipt-123', 'method': 'instant', 'size': first['size']},
                 instant_requests=9, instant_misses=7, instant_next_at=9999999999,
                 first_upload_after=8888888888, hash_completed_at=1234)
    b.store.save(job)
    before = json.loads(json.dumps(first))
    path = Path(first['local'])
    if change == 'different':
        path.write_bytes(b'wrong')
    touch(path)
    if change == 'different':
        with pytest.raises(modules.domain.BridgeError, match='内容被替换'):
            b.engine._sync_candidates(job, [{'local': p} for p in b.paths])
        assert first['receipt'] == before['receipt'] and first['sha1'] == before['sha1']
    else:
        b.engine._sync_candidates(job, [{'local': p} for p in b.paths])
        assert {k: v for k, v in first.items() if k not in ('signature','content_revalidation')} == {
            k: v for k, v in before.items() if k != 'signature'}
        assert first['content_revalidation']['basis'] == 'sha1'


def test_stop_never_seals_partial_verification_and_resumes_with_cached_evidence(pending, modules, monkeypatch):
    b = pending
    touch(Path(b.paths[0]))
    original_freeze = modules.engine.freeze_file
    def stop_after_read(*args, **kwargs):
        entry = original_freeze(*args, **kwargs)
        b.stop.set()
        raise modules.domain.Stopped()
    monkeypatch.setattr(modules.engine, 'freeze_file', stop_after_read)
    b.run()
    assert not b.job()['files'] and not b.host.uploads
    assert b.store.meta('content-proof:' + b.key)
    b.stop.clear()
    monkeypatch.setattr(modules.engine, 'freeze_file', original_freeze)
    b.host.torrent_evidence.side_effect = AssertionError('cached evidence survives restart')
    b.run()
    assert b.job()['state'] == 'handed_off'


@pytest.mark.parametrize('problem', ['traversal','duplicate','wrong_hash','v2_only','oversized_piece','trailing','malformed'])
def test_invalid_torrent_evidence_is_rejected(modules, problem):
    key, raw = original([('video.mkv', b'video')])
    cp = mod('contentproof')
    info, _ = cp._decode(raw)
    data = info[b'info']
    if problem == 'traversal':
        data[b'files'][0][b'path'] = [b'..', b'secret']
    elif problem == 'duplicate':
        data[b'files'].append(data[b'files'][0])
    elif problem == 'v2_only':
        del data[b'pieces']
    elif problem == 'oversized_piece':
        data[b'piece length'] = cp.MAX_PIECE + 1
    raw = encode(info)
    if problem == 'wrong_hash':
        key = '0' * 40
    else:
        key = sha1(encode(data)).hexdigest()
    if problem == 'trailing':
        raw += b'extra'
    if problem == 'malformed':
        raw = b'd4:infod1:xi123456789012345678901234e'
    with pytest.raises(modules.domain.BridgeError):
        cp.evidence(raw, key)


def test_native_export_uses_configured_client_only_and_does_not_leak_sdk_error(modules, monkeypatch):
    key, raw = original([('video.mkv', b'video')])
    client = NS(torrents_export=Mock(return_value=raw))
    helper = ModuleType('app.helper.downloader')
    service = NS(type='qbittorrent', instance=NS(qbc=client))
    getter = Mock(return_value=service)
    helper.DownloaderHelper = lambda: NS(get_service=getter)
    monkeypatch.setitem(sys.modules, 'app.helper.downloader', helper)
    result = mod('contentproof').export_native({'download_hash': key, 'downloader': 'qb'})
    assert result['hash'] == key
    getter.assert_called_once_with(name='qb')
    client.torrents_export.assert_called_once_with(torrent_hash=key)
    client.torrents_export.side_effect = RuntimeError('https://user:secret@downloader/')
    with pytest.raises(modules.domain.BridgeError) as error:
        mod('contentproof').export_native({'download_hash': key, 'downloader': 'qb'})
    assert 'secret' not in str(error.value)
