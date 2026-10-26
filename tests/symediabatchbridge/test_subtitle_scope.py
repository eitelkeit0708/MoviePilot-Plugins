"""Conservative subtitle attribution: live-shaped S01 folder with an S02 stray."""
import importlib
from pathlib import Path, PurePosixPath
from types import SimpleNamespace as NS

import pytest

from test_adapters import native


@pytest.fixture
def scope(modules):
    return importlib.import_module('app.plugins.symediabatchbridge.subtitle_scope')


def candidate(name, media=None, *, parent='Release', local_parent=None, history_id=1, **extra):
    local_parent = local_parent or parent
    result = {'local': f'C:/library/{local_parent}/{name}',
              'source': f'C:/downloads/{parent}/{name}',
              'history_id': history_id, 'media': media or {}}
    result.update(extra)
    return result


SHOW = {'tmdbid': '100', 'type': '电视剧', 'title': '同名剧'}
MOVIE = {'tmdbid': '200', 'type': '电影', 'title': '电影作品'}


def show(season='S01', episode='E01', **extra):
    return {**SHOW, 'seasons': season, 'episodes': episode, **extra}


def movie(**extra):
    return {**MOVIE, **extra}


def test_live_shaped_s01_folder_with_s02_subtitle_is_definitively_unmatched(scope):
    first = candidate('Show.S01E01.mkv', show(), history_id=1)
    second = candidate('Show.S01E02.mkv', show('S01', 'E02'), history_id=2)
    valid = candidate('Show.S01E01.zh.ass', show(), history_id=3)
    stray = candidate('Show.S02E01.ass', show('S02', 'E01'), history_id=4)
    candidates = [first, valid, second, stray]
    kept, unmatched = scope.partition(candidates)
    assert kept == [first, valid, second]
    assert unmatched == [stray]
    assert kept[0] is first and kept[1] is valid and unmatched[0] is stray
    assert scope.matches(valid, [first, second])
    assert not scope.matches(stray, [first, second])
    assert scope.reason(stray, [first, second]) == 'season_conflict'
    assert scope.evidence(stray, [first, second])['matched'] is False
    assert scope.evidence(stray, [first, second])['reason'] == 'season_conflict'


def test_s01_same_episode_zh_subtitle_is_retained_with_identity(scope):
    video = candidate('Show.S01E01.mkv', show())
    subtitle = candidate('Show.S01E01.zh.ass', show())
    assert scope.matches(subtitle, [video])
    detail = scope.evidence(subtitle, [video])
    assert detail['matched'] is True and detail['basis'] == 'exact_media_scope'
    assert detail['video']['history_id'] == video['history_id']
    assert scope.partition([video, subtitle]) == ([video, subtitle], [])


@pytest.mark.parametrize('release,wrong_episode', [('Scene2', 'E02'), ('Scene02', 'E02'),
                                                  ('Scene20', 'E20'), ('Title3', 'E03')])
def test_release_word_ending_in_e_digits_is_not_an_extra_episode(scope, release, wrong_episode):
    video = candidate(f'Show.S01E01.{release}.mkv', show())
    correct = candidate(f'Show.S01E01.{release}.zh.ass', show())
    stray = candidate(f'Show.S01{wrong_episode}.ass', show(episode=wrong_episode))
    assert scope.matches(correct, [video])
    assert not scope.matches(stray, [video])


def test_authenticated_episode_without_season_cannot_cross_unrelated_parents(scope):
    video = candidate('Show.S01E01.mkv', show(), parent='Current')
    subtitle = candidate('caption.ass', {**SHOW, 'episodes': 'E01'}, parent='Unrelated')
    assert not scope.matches(subtitle, [video])


def test_external_scan_never_adopts_old_source_even_with_same_authenticated_media(modules):
    video = candidate('Show.S01E01.mkv', show(), parent='Current')
    old_subtitle = candidate('Show.S01E01.ass', show(), parent='PreviousDownload')
    assert not modules.host.current_attachment(old_subtitle['source'], [video['source']],
                                               media=old_subtitle['media'], videos=[video])


def test_different_tmdb_and_type_are_never_overridden_by_parent(scope):
    video = candidate('Show.S01E01.mkv', show())
    other_show = candidate('Show.S01E01.zh.ass', show(tmdbid='999'))
    other_type = candidate('Show.S01E01.zh.ass', show(type='电影'))
    assert scope.reason(other_show, [video]) == 'tmdb_conflict'
    assert scope.reason(other_type, [video]) == 'type_conflict'


def test_metadata_and_filename_conflict_fails_closed(scope):
    video = candidate('Show.S01E01.mkv', show())
    conflicting = candidate('Show.S02E01.ass', show('S01', 'E01'))
    assert scope.reason(conflicting, [video]) == 'metadata_filename_conflict'


def test_episode_ranges_multiseason_and_gap_conflict(scope):
    ranged = candidate('Show.S01E04.mkv', show('S01', 'E03-E05'))
    middle = candidate('Show.S01E04.zh.ass', show('S01', 'E04'))
    assert scope.matches(middle, [ranged])
    broad = candidate('Show.S01-S02.E01.mkv', show('S01-S02', 'E01'))
    multi = candidate('Show.S02E01.zh.ass', show('S02', 'E01'))
    assert scope.matches(multi, [broad])
    gap = candidate('Show.S01E06.zh.ass', show('S01', 'E06'))
    assert scope.reason(gap, [ranged]) == 'episode_conflict'


def test_movie_requires_identity_and_meaningful_filename_or_parent(scope):
    video = candidate('Movie.mkv', movie())
    companion = candidate('Movie.en.ass', movie())
    unrelated = candidate('OtherMovie.ass', movie())
    other_tmdb = candidate('Movie.en.ass', movie(tmdbid='999'))
    assert scope.matches(companion, [video])
    assert scope.reason(unrelated, [video]) == 'no_attribution_evidence'
    assert scope.reason(other_tmdb, [video]) == 'tmdb_conflict'
    # Authenticated identity plus the matching filename is meaningful even
    # when local/source roots differ.
    cross = candidate('Movie.en.ass', movie(), parent='archive/Movie',
                      local_parent='archive/Movie')
    assert scope.matches(cross, [video])


def test_unknown_metadata_uses_only_same_parent_filename_companion(scope):
    video = candidate('Show.S01E01.mkv')
    companion = candidate('Show.S01E01.zh.ass')
    unrelated = candidate('English.ass')
    cross_parent = candidate('Show.S01E01.zh.ass', parent='OtherRelease',
                             local_parent='elsewhere')
    assert scope.matches(companion, [video])
    assert scope.reason(unrelated, [video]) == 'no_attribution_evidence'
    assert scope.reason(cross_parent, [video]) == 'no_attribution_evidence'


def test_unrelated_parent_title_only_is_rejected_but_authenticated_tmdb_can_cross(scope):
    video = candidate('Show.S01E01.mkv', show(), parent='library/Show')
    title_only = candidate('Show.S01E01.zh.ass', show(tmdbid=''), parent='other/Show',
                           local_parent='other/Show')
    authenticated = candidate('Show.S01E01.zh.ass', show(), parent='other/Show',
                              local_parent='other/Show')
    assert scope.reason(title_only, [video]) == 'no_attribution_evidence'
    assert scope.matches(authenticated, [video])


def test_partition_preserves_videos_and_never_mutates_original_dicts(scope):
    video = candidate('Show.S01E01.mkv', show())
    valid = candidate('Show.S01E01.zh.ass', show())
    stray = candidate('Show.S02E01.ass', show('S02', 'E01'))
    before = [dict(item) for item in (video, valid, stray)]
    kept, unmatched = scope.partition([video, valid, stray])
    assert kept == [video, valid] and unmatched == [stray]
    assert (video, valid, stray) == tuple(before)
    assert kept[0] is video and unmatched[0] is stray


def test_explicit_selected_crossseason_subtitle_is_excluded_and_journaled(native):
    for row in native.rows:
        vars(row).update(show())
    source = Path(str(PurePosixPath(native.rows[0].src).parent / 'Show.S02E01.ass'))
    target = native.video.with_name('Show.S02E01.ass')
    data = b'cross-season subtitle'
    source.write_bytes(data)
    target.write_bytes(data)
    native.files.append(NS(name='Release/Show.S02E01.ass', size=len(data), priority=1, progress=1))
    native.rows.append(NS(**{**vars(native.rows[0]), 'id': 99, 'src': str(source),
                             'dest': str(target), 'seasons': 'S02', 'episodes': 'E01'}))
    result = native.host.collect(native.job)
    assert str(target) not in {item['local'] for item in result}
    assert target.read_bytes() == data
    entry = native.job['excluded_attachments'][0]
    assert entry['local'] == str(target)
    assert entry['source'].replace('\\', '/') == str(source).replace('\\', '/')
    assert entry['history_id'] == 99
    assert entry['media'] == {'tmdbid': '100', 'type': '电视剧', 'title': '同名剧',
                              'seasons': 'S02', 'episodes': 'E01'}
    assert entry['reason'] == 'season_conflict'


def test_external_s01_and_s02_subtitles_are_classified_separately(native):
    for row in native.rows:
        vars(row).update(show())
    release = Path(str(PurePosixPath(native.rows[0].src).parent))
    valid_source, valid_target = release / 'Show.S01E01.zh.ass', native.video.with_name('Show.S01E01.zh.ass')
    stray_source, stray_target = release / 'Show.S02E01.ass', native.video.with_name('Show.S02E01.ass')
    for path, data in ((valid_source, b'valid'), (stray_source, b'stray'),
                       (valid_target, b'valid'), (stray_target, b'stray')):
        path.write_bytes(data)
    native.rows.append(NS(**{**vars(native.rows[0]), 'id': 98, 'src': str(valid_source),
                             'dest': str(valid_target)}))
    native.rows.append(NS(**{**vars(native.rows[0]), 'id': 99, 'src': str(stray_source),
                             'dest': str(stray_target), 'seasons': 'S02', 'episodes': 'E01'}))
    result = native.host.collect(native.job)
    assert str(valid_target) in {item['local'] for item in result}
    assert str(stray_target) not in {item['local'] for item in result}
    assert stray_target.exists()
    excluded = native.job['excluded_attachments']
    assert [entry['source'].replace('\\', '/') for entry in excluded] == [
        str(stray_source).replace('\\', '/')]
    assert excluded[0]['reason'] == 'season_conflict'
    assert excluded[0]['local'] == str(stray_target)


def test_late_append_rejects_crossseason_without_touching_accepted_scope(native, modules):
    for row in native.rows:
        vars(row).update(show())
    native.host.collect(native.job)
    ownership = importlib.import_module('app.plugins.symediabatchbridge.ownership')
    original = [dict(item) for item in native.job['owned_candidates']]
    native.job['state'] = 'waiting'
    stray = native.video.with_name('Show.S02E01.ass')
    stray.write_bytes(b'stray')
    assert not ownership.append_transfer(native.job, native.config, {
        'local': str(stray), 'history_id': 99, 'media': show('S02', 'E01')})
    assert native.job['owned_candidates'] == original
    assert native.job['excluded_attachments'][-1]['reason'] == 'season_conflict'
    good = native.video.with_name('Show.S01E01.en.ass')
    good.write_bytes(b'good')
    assert ownership.append_transfer(native.job, native.config, {
        'local': str(good), 'history_id': 100, 'media': show()})
    assert len(native.job['owned_candidates']) == 3
    native.transfers.list_by_hash.side_effect = AssertionError('accepted scope is durable')
    assert len(native.host.collect(native.job)) == 3


def test_movie_without_metadata_still_uses_legacy_stem_companion(scope):
    video = candidate('Movie.2020.1080p.mkv', movie())
    companion = candidate('Movie.2020.1080p.zh.ass')
    unrelated = candidate('Unrelated.ass')
    assert scope.matches(companion, [video])
    assert scope.reason(unrelated, [video]) == 'no_attribution_evidence'


def test_renamed_local_output_cannot_hide_source_season_conflict(scope):
    video = {'local': 'C:/library/作品.mkv', 'source': 'C:/downloads/Release/Show.S01E01.mkv',
             'history_id': 1, 'media': show()}
    stray = {'local': 'C:/library/作品.S02E01.ass',
             'source': 'C:/downloads/Release/Show.S02E01.ass',
             'history_id': 2, 'media': show('S02', 'E01')}
    assert scope.reason(stray, [video]) == 'season_conflict'
    assert scope.partition([video, stray]) == ([video], [stray])


def test_partition_never_shrinks_accepted_scope(native, scope):
    for row in native.rows:
        vars(row).update(show())
    owned = [dict(item) for item in native.host.collect(native.job)]
    accepted = [dict(item) for item in native.job['owned_candidates']]
    stray = candidate('Show.S02E01.ass', show('S02', 'E01'))
    kept, unmatched = scope.partition([*owned, stray])
    assert kept == owned and unmatched == [stray]
    assert native.job['owned_candidates'] == accepted
    assert native.host.collect(native.job) == owned
    assert native.job['owned_candidates'] == accepted


def test_current_attachment_two_arg_api_remains_conservative(native, modules):
    video = str(native.video)
    good = str(native.video.with_suffix('.zh.srt'))
    stray = str(native.video.with_name('Show.S02E01.ass'))
    assert modules.host.current_attachment(good, [video])
    assert not modules.host.current_attachment(stray, [video])
    # Optional metadata keeps the same matcher authoritative.
    assert not modules.host.current_attachment(stray, [video], media=show('S02', 'E01'))
