"""Export actual mounted DTOs for the native UI; local disposable host fixture only.

Run with the repository's test Python from the repository root. No network.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'tests/v3/subscribetter'))
from test_management import ManagementAPITests
from test_planner import load
from pydantic import TypeAdapter


def contract(fixture=None):
    owned = fixture is None
    if owned:
        ManagementAPITests.setUpClass()
        fixture = ManagementAPITests()
        fixture.setUp()
    try:
        api = fixture.client.app.openapi()
        definitions = api['components']['schemas']
        c = load('configuration')
        for model in (c.Config, c.DeliveryConfig, load('ai').AIConfig,
                      load('discovery').DiscoveryConfig):
            schema = model.model_json_schema(ref_template='#/components/schemas/{model}')
            definitions.update(schema.pop('$defs', {}))
            definitions[model.__name__] = schema
        definitions['ScheduleConfig'] = TypeAdapter(load('scheduler').ScheduleConfig).json_schema()
        for field, model in [('ai_assist', 'AIConfig'), ('discovery', 'DiscoveryConfig'),
                             ('delivery', 'DeliveryConfig'), ('schedule', 'ScheduleConfig')]:
            definitions['Config']['properties'][field] = {'$ref': '#/components/schemas/' + model}
        return dict(paths=api['paths'], schemas=definitions, defaults=c.Config().model_dump())
    finally:
        if owned:
            fixture.doCleanups()


if __name__ == '__main__':
    output = Path(__file__).resolve().parents[1] / 'src/contract.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contract(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
