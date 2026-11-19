import sys,pathlib,tempfile,copy,json
from unittest.mock import patch
p=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(p/'implementation'))
import workflow_engine as w
root=pathlib.Path(tempfile.mkdtemp(prefix='selection_test_'));src=root/'src';src.mkdir();(src/'a.js').write_text('original')
c={'repair_enabled':True,'mode':'source','source_path':str(src),'requirements':{},'max_fix_turns':14}
issues=[{'id':f'ISSUE-00{i}','kind':'bug','status':'已复现（按所填预期）'} for i in [1,2,3]]
assert w.select_repair_issues('1， ISSUE-002',c,issues)==['ISSUE-001','ISSUE-002']
for text in ['1,1','999']:
 try:w.select_repair_issues(text,c,issues)
 except ValueError:pass
 else:raise AssertionError(text)
sus=copy.deepcopy(issues);sus[1]['kind']='suspected'
try:w.select_repair_issues('2',c,sus)
except ValueError:pass
else:raise AssertionError('suspected accepted')
seen=[]
def fix(c,r,items):
 key=items[0]['id'];cand=r/'candidate';w.copy_project(c['source_path'],cand)
 before=(cand/'a.js').read_text();seen.append((key,before));(cand/'a.js').write_text(before+' '+key)
 return {'status':'candidate','candidate':str(cand),'changes':[{'path':'a.js','diff':'x'}],'syntax':{'passed':True},'usage':{'requests':1}}
def verify(c,r,b,i,a):
 ids=a['selected_ids'];return {'status':'not_verified' if ids[-1]=='ISSUE-002' else 'verified','reason':'test','issues':[{'id':x,'passed':True} for x in ids],'regressions':[]}
run=root/'run';run.mkdir()
with patch.object(w,'repair',fix),patch.object(w,'verify',verify),patch.object(w,'syntax_check',lambda x:{'passed':True}):
 out=w.repair_selected(c,run,[],issues,[i['id'] for i in issues])
assert out['verification']['status']=='partial_verified'
assert out['selected_ids']==['ISSUE-001','ISSUE-003']
assert seen[2][1]=='original ISSUE-001',seen
assert (pathlib.Path(out['candidate'])/'a.js').read_text()=='original ISSUE-001 ISSUE-003'
assert (src/'a.js').read_text()=='original'
print('PASS selection validation, ordered execution, failed-card exclusion, retained earlier fixes, partial status, original unchanged')
