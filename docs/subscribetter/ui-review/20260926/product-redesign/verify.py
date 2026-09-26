"""Verify committed screenshot bytes. No external services or dependencies required."""
from pathlib import Path
import hashlib, json
root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
for row in manifest['images']:
    path = (root / row['path']).resolve()
    assert path.is_relative_to(root), row['path']
    data = path.read_bytes()
    assert len(data) == row['bytes'], row['path']
    assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
print(f"Verified {len(manifest['images'])} original screenshots for {manifest['product_commit']}")
