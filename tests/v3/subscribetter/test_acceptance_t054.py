"""T054 revised D07 asset scope and unverified archive boundary."""
import json
from pathlib import Path
import unittest

from test_planner import load


ROOT = Path(__file__).resolve().parents[3]


def scenario():
    candidates, repository = load('candidates'), load('repository')
    target = repository.Target('电影', 'themoviedb', '42')
    table = [
        ('Pack/Movie.mkv', 100), ('Pack/Movie.ass', 2), ('Pack/Movie.srt', 2),
        ('Pack/Movie.ssa', 2), ('Pack/Movie.vtt', 2), ('Pack/Movie.sup', 2),
        ('Pack/Movie.idx', 2), ('Pack/Movie.sub', 2), ('Pack/font.ttf', 2),
        ('Pack/LICENSE.txt', 2), ('Pack/Extras.zip', 2),
    ]
    files = candidates.bind_files(table, target, dependencies={0: [8], 1: [8]})
    selected = [item['index'] for item in files if item['targets']]
    ignored = [item['index'] for item in files if not item['targets']]
    traversal = []
    for path in ('../Extras.zip', '/Extras.zip', 'C:/Extras.zip', 'Pack\\Extras.zip'):
        try:
            candidates.bind_files([('Pack/Movie.mkv', 100), (path, 2)], target)
        except ValueError:
            traversal.append(path)
    source = '\n'.join((ROOT / path).read_text(encoding='utf-8') for path in (
        'plugins.v3/subscribetter/candidates.py', 'plugins.v3/subscribetter/planner.py',
        'plugins.v3/subscribetter/execution.py'))
    return {
        'selected_indices': selected, 'ignored_indices': ignored,
        'roles': {Path(item['path']).suffix.casefold(): item['role'] for item in files},
        'ignored_are_unbound': all(not files[index]['targets'] and not files[index]['requires']
                                   for index in ignored),
        'selected_require_no_ignored_asset': all(not item['requires'] for item in files if item['targets']),
        'zip': {'role': files[10]['role'], 'selected': 10 in selected,
                'targets': files[10]['targets'], 'requires': files[10]['requires']},
        'traversal_rejected': traversal,
        'archive_extraction_implemented': any(token in source for token in
            ('zipfile', 'extractall(', 'unpack_archive(')),
    }


def verify(proof):
    assert proof['selected_indices'] == [0, 1, 2, 3, 4, 5]
    assert proof['ignored_indices'] == [6, 7, 8, 9, 10]
    assert {suffix: proof['roles'][suffix] for suffix in
            ('.ass', '.srt', '.ssa', '.vtt', '.sup')} == {
                '.ass': 'subtitle', '.srt': 'subtitle', '.ssa': 'subtitle',
                '.vtt': 'subtitle', '.sup': 'subtitle'}
    assert all(proof['roles'][suffix] == 'other' for suffix in
               ('.idx', '.sub', '.ttf', '.txt', '.zip'))
    assert proof['ignored_are_unbound'] and proof['selected_require_no_ignored_asset']
    assert proof['zip'] == {'role': 'other', 'selected': False, 'targets': [], 'requires': []}
    assert proof['traversal_rejected'] == [
        '../Extras.zip', '/Extras.zip', 'C:/Extras.zip', 'Pack\\Extras.zip']
    assert proof['archive_extraction_implemented'] is False


class T054IntegrationTests(unittest.TestCase):
    def test_d07_supported_subtitles_ignore_removed_assets_and_zip(self):
        verify(scenario())


if __name__ == '__main__':
    result = scenario()
    verify(result)
    print(json.dumps(result, ensure_ascii=False))
