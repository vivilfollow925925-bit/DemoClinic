"""No network: verify independent routing, credentials and cumulative role accounting."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'implementation'))
import workflow_engine as wf

root = Path(tempfile.mkdtemp(prefix='api_roles_'))
config = {}
keys = {}
roles = ('analysis', 'test', 'fix')
for role in roles:
    config.update({role + '_model': role + '-model',
                   role + '_base_url': 'https://' + role + '.invalid/v1'})
    keys['DEMO_' + role.upper() + '_KEY'] = role + '-dummy-credential'

seen = []
def respond(request, **kwargs):
    payload = json.loads(request.data)
    seen.append((request.full_url, request.get_header('Authorization'), payload['model']))
    return io.BytesIO(json.dumps({'usage': {'prompt_tokens': 10, 'completion_tokens': 4},
        'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}]}).encode())

with patch.dict(os.environ, keys), patch.object(wf.urllib.request, 'urlopen', respond):
    for role in roles:
        client = wf.ModelClient(dict(config, usage_log_path=str(root / role / 'model_usage.jsonl')), role)
        (root / role).mkdir()
        client.request([{'role': 'user', 'content': 'connection check'}])
        assert seen[-1] == (config[role + '_base_url'] + '/chat/completions',
                           'Bearer ' + keys['DEMO_' + role.upper() + '_KEY'], role + '-model')
    assert all(key not in wf.redact(' '.join(keys.values())) for key in keys.values())
    with patch.dict(os.environ, {'DEMO_ANALYSIS_KEY': ''}):
        try: wf.ModelClient(config, 'analysis')
        except RuntimeError: pass
        else: raise AssertionError('Analysis silently reused another role key')

rows = wf.usage_summary(root, config)
assert all(rows[r]['requests'] == 1 and rows[r]['total_tokens'] == 14 for r in roles)
# Multiple batches count once each; rendering the summary does not inflate usage.
with (root / 'analysis/model_usage.jsonl').open('a', encoding='utf-8') as f:
    f.write(json.dumps({'role':'analysis','status':'response','usage':{}}) + '\n')
    f.write(json.dumps({'role':'fix','status':'http_error'}) + '\n')
rows = wf.usage_summary(root, config)
assert rows['analysis']['requests'] == 2 and rows['analysis']['missing_usage_responses'] == 1
assert rows['test']['requests'] == 1 and rows['fix']['errors'] == 1
assert wf.usage_summary(root, config) == rows
assert all(key not in ''.join(f.read_text('utf-8') for f in root.rglob('*.json*')) for key in keys.values())
print('PASS: three endpoints/models/keys, no key fallback, redaction, separate cumulative usage, missing-usage marker')
