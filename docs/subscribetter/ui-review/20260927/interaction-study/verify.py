"""Validate original review images; no network access."""
import hashlib, json
from pathlib import Path
root = Path(__file__).resolve().parent
for row in json.loads((root / 'manifest.json').read_text(encoding='utf-8')):
    path = (root / row['path']).resolve()
    assert path.is_relative_to(root)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], row['path']
print('Verified screenshot hashes.')
