"""Verify full-source delivery and detailed task validation without a paid API."""
from pathlib import Path
import tempfile, sys, copy, json
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'implementation'))
import task_queue as tq
import workflow_engine as wf

root = Path(tempfile.mkdtemp(prefix='analysis_contract_'))
src = root / 'source'; src.mkdir()
for i in range(161):
    (src / f'part{i:03}.js').write_text('x' * 1600 + f'END_{i}', encoding='utf-8')
for directory in ('evaluation','node_modules','dist'):
    (src/directory).mkdir(); (src/directory/'answer.js').write_text('EXCLUDED_SECRET')
config = {'mode':'source','source_path':str(src),'analysis_source_max_chars':400000}
sources, manifest = tq.read_project_sources(config, root/'full')
assert len(sources) == 161 and manifest['complete']
assert sources[-1]['content'].endswith('END_160')
assert 'EXCLUDED_SECRET' not in json.dumps(sources)
try: tq.read_project_sources(dict(config, analysis_source_max_chars=100), root/'limited')
except ValueError: pass
else: raise AssertionError('Must fail instead of silently truncating')
assert not wf.load(root/'limited/source_read_manifest.json')['complete']

card = dict(id='T1',title='Observe',goal='Inspect state',success_criteria='State matches expected',
    setup='Open a fresh page',scope='Only this state',preconditions=['Fresh browser'],
    test_inputs=[{'name':'data','value_or_rule':'No input required'}],
    steps=[{'action':'Observe page','input':'None','expected_result':'Expected heading visible'}],
    completion_criteria=['Verify heading'],blocking_conditions=['Page unavailable'],
    dimensions=[4],depends_on=[],dependency_reason='',requirement_indices=[1])
plan={'tasks':[card], 'out_of_scope':[]}
tq.validate_plan(plan, 1)
for field in ('scope','preconditions','test_inputs','steps','completion_criteria','blocking_conditions'):
    bad=copy.deepcopy(plan);del bad['tasks'][0][field]
    try: tq.validate_plan(bad,1)
    except ValueError: pass
    else: raise AssertionError(field)
bad=copy.deepcopy(plan);bad['tasks'][0]['steps'][0]['expected_result']=''
try: tq.validate_plan(bad,1)
except ValueError: pass
else: raise AssertionError('Blank expected result accepted')

class Fake:
    def __init__(self, config, role):
        assert role == 'analysis'; self.usage={}; self.calls=0
    def request(self, messages, tools):
        self.calls+=1
        payload=json.loads(messages[1]['content'])
        assert len(payload['source_contents'])==161
        assert payload['source_contents'][-1]['content'].endswith('END_160')
        if self.calls==1:
            incomplete=copy.deepcopy(plan);del incomplete['tasks'][0]['steps'];answer=incomplete
        else:
            assert 'steps' in messages[-1]['content'];answer=plan
        return {'role':'assistant','content':None,'tool_calls':[{'id':str(self.calls),
            'type':'function','function':{'name':'submit_plan','arguments':json.dumps(answer)}}]}

config.update(prompt_path=str(Path(__file__).resolve().parents[1]/'体验Agent检查维度.md'),
              requirements='Observe state',tasks=['Observe state'])
with patch.object(wf,'ModelClient',Fake):
    result=tq.analyze(config,root/'analysis',{'text':'Page'})
assert result['tasks'][0]['steps']==card['steps']
print('PASS: all 161 files and file tails delivered; exclusions; explicit overflow; detailed fields; invalid plan correction')
