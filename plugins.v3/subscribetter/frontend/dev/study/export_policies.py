"""Export offline design examples from the real policy formatter; never connect to MP."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
plugin = here.parents[2]
spec = importlib.util.spec_from_file_location('study_policy', plugin / 'policy.py')
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)
categories = [('tv-west', '欧美剧', '电视剧'), ('tv-cn', '国产剧', '电视剧'), ('movie-animation', '动画电影', '电影')]
engine = policy.Policy({key: name for key, name, _ in categories}, 1)
contract = json.loads((plugin / 'frontend/src/contract.json').read_text(encoding='utf-8'))
snapshot = {
    'source': 'Offline examples: Policy.describe + repository defaults; not a live MP configuration',
    'policy_source_sha256': hashlib.sha256((plugin / 'policy.py').read_text(encoding='utf-8').encode()).hexdigest(),
    'categories': [dict(id=key, name=name, media_type=kind) for key, name, kind in categories],
    'policies': [dict(id='policy-' + key, category_id=key, name=name + '默认策略', revision=engine.semantic_hash,
                      summary=engine.describe(name), configuration=dict(locks={}, admission=None, overrides={}))
                 for key, name, _ in categories],
    'schedule': contract['defaults']['schedule'],
    'rules': engine.rules,
}
(here / 'policies.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Exported three offline policy snapshots from Policy.describe.')
