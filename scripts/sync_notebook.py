"""Rebuild the embedded runtime from reviewed sources; clear saved notebook output."""
from pathlib import Path
import ast
import base64
import io
import json
import re
import zipfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / 'Demo体验与自动修复工作流.ipynb'
PROMPTS = ['体验Agent检查维度.md', '修复Agent执行规范.md']


def expected_files():
    files = {p.name: p.read_bytes() for p in sorted((ROOT / 'implementation').glob('*.py'))}
    files['requirements.txt'] = (ROOT / 'implementation/requirements.txt').read_bytes()
    files.update({name: (ROOT / name).read_bytes() for name in PROMPTS})
    for name, data in files.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check without rewriting')
    args = parser.parse_args()
    nb = json.loads(NOTEBOOK.read_text(encoding='utf-8'))
    cells = [c for c in nb['cells'] if c['cell_type'] == 'code' and '_bundle = ' in ''.join(c['source'])]
    assert len(cells) == 1, 'Expected one embedded runtime'
    cell = cells[0]
    source = ''.join(cell['source'])
    pattern = r"(?m)^_bundle = (.+)$"
    match = re.search(pattern, source)
    files = expected_files()
    if args.check:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(ast.literal_eval(match.group(1))))) as archive:
            assert set(archive.namelist()) == set(files), 'Bundle file list differs'
            for name, data in files.items():
                assert archive.read(name) == data, f'Stale embedded file: {name}'
        for c in nb['cells']:
            if c['cell_type'] == 'code':
                assert not c.get('outputs') and c.get('execution_count') is None, 'Saved execution output'
                compile(''.join(c['source']), '<notebook>', 'exec')
        print('PASS: runtime bundle matches sources; notebook code compiles; outputs cleared')
        return
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    cell['source'] = re.sub(pattern, '_bundle = ' + repr(encoded), source).splitlines(keepends=True)
    nb['metadata'].pop('widgets', None)
    for c in nb['cells']:
        if c['cell_type'] == 'code':
            c['outputs'] = []
            c['execution_count'] = None
        c.get('metadata', {}).pop('execution', None)
    NOTEBOOK.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('Rebuilt embedded runtime and cleared notebook outputs')


if __name__ == '__main__':
    main()
