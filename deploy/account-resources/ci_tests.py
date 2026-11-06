"""Print every backend test module, including future apps/libs/consumer tests."""
import ast
from pathlib import Path


def discover(root):
    modules = []
    for path in sorted(Path(root).rglob('test*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and (node.name.startswith('test_') or node.name == 'load_tests')
               for node in ast.walk(tree)):
            modules.append('.'.join(path.relative_to(root).with_suffix('').parts))
    if not modules:
        raise ValueError('backend-test-discovery-empty')
    return modules


if __name__ == '__main__':
    print('\n'.join(discover(Path(__file__).resolve().parents[2] / 'spug_api')))
