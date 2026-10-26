import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_ownership_disposal import orphan, request, mod
from test_plugin_contract import plugin


def test_root_subtitle_is_not_blocked_by_unrelated_work_subdirectories(orphan):
    p, job, subtitle, modules = orphan
    root = Path(p._runtime.config.local_root)
    new = root / '[MT]Amadeus (1984).ass'
    subtitle.rename(new)
    job['inventory_files'] = [{'local': str(new), 'signature': modules.domain.file_signature(new)}]
    other = root / '电影' / 'Other movie'
    other.mkdir(parents=True)
    video = other / 'Other movie.mkv'
    video.write_bytes(b'unrelated media')
    p._store.save(job)
    assert request(p, modules, job, 'delete').success
    plan = p._store.get(job['id'])['disposal_plan']
    assert [e['local'] for e in plan['files']] == [str(new)]
    assert new.exists() and video.exists()  # Preview never deletes.
    assert request(p, modules, job, 'confirm', plan['token']).success
    p.check_batches()
    assert not new.exists() and video.read_bytes() == b'unrelated media'


def test_explicit_batch_video_in_child_folder_still_blocks_deletion(orphan):
    p, job, subtitle, modules = orphan
    folder = subtitle.parent / 'Season 1'
    folder.mkdir()
    video = folder / 'episode.mkv'
    video.write_bytes(b'video')
    job['inventory_files'].append({'local': str(video)})
    p._store.save(job)
    assert not request(p, modules, job, 'delete').success
    assert subtitle.exists()


@pytest.mark.parametrize('video_name', ['other.mkv', 'BDMV', 'VIDEO_TS'])
def test_direct_neighbour_guard_names_the_actual_blocker(orphan, video_name):
    p, job, subtitle, modules = orphan
    video = subtitle.parent / video_name
    video.mkdir() if '.' not in video_name else video.write_bytes(b'media')
    result = request(p, modules, job, 'delete')
    assert not result.success and video_name in result.message
    assert subtitle.exists()


def test_missing_file_and_missing_root_are_not_the_same_diagnostic(modules, config_values):
    config = modules.domain.Config.parse(config_values)
    missing = Path(config.local_root) / 'Season 1' / 'episode.mkv'
    with pytest.raises(modules.domain.BridgeError, match='整理目录可访问，但文件已缺失：Season 1/episode.mkv'):
        config.relative(str(missing))
    Path(config.local_root).rmdir()
    with pytest.raises(modules.domain.BridgeError, match='本地整理目录暂不可用'):
        config.relative(str(missing))


def test_unreadable_root_is_never_reported_as_missing_media(modules, config_values, monkeypatch):
    config = modules.domain.Config.parse(config_values)
    monkeypatch.setattr(mod('localfiles').os, 'scandir', Mock(side_effect=PermissionError('denied')))
    with pytest.raises(modules.domain.BridgeError, match='本地整理目录无法读取'):
        config.relative(str(Path(config.local_root) / 'episode.mkv'))


def test_legacy_generic_error_does_not_claim_a_mount_failure(modules):
    remedy = mod('remedies').remedy
    job = {'state': 'review', 'message': '整理文件不存在或不在配置的本地目录中'}
    assert '挂载恢复后' not in ' '.join(remedy(job)['steps'])
    job['message'] = '整理目录可访问，但文件已缺失：电影/episode.mkv'
    assert remedy(job)['title'] == '整理文件已缺失'
