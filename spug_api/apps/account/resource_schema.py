"""Frozen legacy source contract; independent of the current Django state."""
import ast
import hashlib
import json
from pathlib import Path


def model_fields(source):
    result = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields, options = {}, {}
        for item in node.body:
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                call = item.value.func
                if isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name) and call.value.id == 'models':
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            fields[target.id] = ast.dump(item.value, include_attributes=False)
            elif isinstance(item, ast.ClassDef) and item.name == 'Meta':
                for option in item.body:
                    if isinstance(option, ast.Assign):
                        for target in option.targets:
                            if isinstance(target, ast.Name):
                                options[target.id] = ast.dump(option.value, include_attributes=False)
        if fields:
            result[node.name] = hashlib.sha256(json.dumps(
                {'fields': fields, 'options': options}, sort_keys=True).encode()).hexdigest()
    return result


def verify_legacy_models(apps_root, overrides=None):
    contract = json.loads(Path(__file__).with_name('resource_model_contract.json').read_text(encoding='utf-8'))
    for relative, expected in contract['models'].items():
        source = (overrides or {}).get(relative)
        if source is None:
            source = Path(apps_root, relative).read_text(encoding='utf-8')
        actual = model_fields(source)
        if any(actual.get(name) != digest for name, digest in expected.items()):
            raise ValueError('Legacy model fields changed: ' + relative)
    return contract['reference']
