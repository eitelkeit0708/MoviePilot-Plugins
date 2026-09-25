"""Verify the published review package using only GitHub checkout files + stdlib."""
from hashlib import sha256
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    errors = []
    for item in manifest['files']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            errors.append('Missing/unsafe path: ' + item['path'])
        else:
            content = path.read_bytes()
            if item.get('line_endings') == 'lf': content = content.replace(b'\r\n', b'\n')
            if sha256(content).hexdigest() != item['sha256']:
                errors.append('Hash mismatch: ' + item['path'])
    acceptance = json.loads((HERE / 'acceptance.json').read_text(encoding='utf-8'))
    evidence = json.loads((HERE / 'evidence-index.json').read_text(encoding='utf-8'))['entries']
    cases = acceptance['cases']
    ids = [case['id'] for case in cases]
    if sorted(ids) != [f'T{i:03d}' for i in range(1, 201)]: errors.append('Incomplete/duplicate T matrix')
    if [r['id'] for r in acceptance['requirements']] != [f'R{i:02d}' for i in range(1, 66)]: errors.append('Incomplete R matrix')
    active = [c for c in cases if c['active']]
    opened = [c['id'] for c in active if c['status'] != 'passed']
    if len(active) != 199 or opened != ['T003','T006','T060','T081','T092','T200']: errors.append('Unexpected acceptance totals')
    if len(evidence) != 829: errors.append('Unexpected evidence count')
    for case in cases:
        for key in case.get('evidence', []):
            if key not in evidence: errors.append(case['id'] + ' missing evidence ID: ' + key)
    receipt = json.loads((HERE / 'architecture-receipt.json').read_text(encoding='utf-8'))
    for filename, key in [('architecture.json','specification'),('architecture.html','artifact')]:
        if sha256((HERE / filename).read_bytes()).hexdigest() != receipt[key]['sha256']:
            errors.append('Archify receipt mismatch: ' + filename)
    print(json.dumps({'ok':not errors,'verified_files':len(manifest['files']),
                      'active_cases':len(active),'open':opened,'errors':errors},ensure_ascii=False,indent=2))
    return bool(errors)


if __name__ == '__main__': sys.exit(main())
