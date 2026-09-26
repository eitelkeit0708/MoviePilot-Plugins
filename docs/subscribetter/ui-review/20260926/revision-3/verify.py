"""Verify every original screenshot against the committed capture manifest."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
rows = json.loads((root / 'capture-metadata.json').read_text('utf-8'))
listed = {row['file'] for row in rows}
assert len(listed) == len(rows), 'duplicate capture records'
actual = {p.relative_to(root).as_posix() for folder in ('host', 'synthetic') for p in (root / folder).glob('*.jpg')}
assert listed == actual, 'missing or unlisted screenshots'
for row in rows:
    path = (root / row['file']).resolve()
    assert path.is_relative_to(root), 'invalid screenshot path'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], row['file']
print(f'{len(rows)} original screenshots verified')
