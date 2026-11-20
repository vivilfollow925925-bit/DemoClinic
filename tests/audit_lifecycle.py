import pathlib,tempfile,json,sys,copy
from unittest.mock import patch
import workflow_engine as wf,project_submission as intake
sys.stdout.reconfigure(encoding='utf8')
results=[]
def render(run,*args):
 p=pathlib.Path(run)/'体验与修复报告.html';p.write_text('audit',encoding='utf8');return p
for prior_exists in [False,True]:
 for status in ['verified','not_verified','verification_blocked']:
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);orig=root/'original';orig.mkdir();(orig/'app.js').write_text('const x=0;',encoding='utf8')
   prior_dir=root/'prior';prior_dir.mkdir();(prior_dir/'app.js').write_text('const x=1;',encoding='utf8')
   batch=root/'batch';card=batch/'ISSUE-002';cand=card/'candidate';cand.mkdir(parents=True);(cand/'app.js').write_text('const x=2;',encoding='utf8')
   prior={'status':'candidate','candidate':str(prior_dir),'selected_ids':['ISSUE-001'],'changes':[{'path':'app.js'}],'delivery_verified':True,'verification':{'status':'verified','verified_ids':['ISSUE-001']},'original_source':str(orig)} if prior_exists else {}
   wf.dump(batch/'accepted_before.json',prior)
   wf.dump(card/'repair.json',{'status':'candidate','candidate':str(cand),'selected_ids':['ISSUE-002'],'changes':[{'path':'app.js'}]})
   wf.dump(root/'repair.json',{'batch_dir':str(batch),'requested_ids':['ISSUE-002'],'verification':{'status':'pending_verification'}})
   wf.dump(root/'baseline.json',[]);wf.dump(root/'issues.json',[{'id':'ISSUE-001'},{'id':'ISSUE-002'}])
   cfg={'source_path':str(orig),'submission':{'project_name':'test'},'launch_kind':'static','start_command':['python'],'setup_command':[]}
   observed=[]
   def verify(c,r,b,i,repair):
    observed.extend(repair['selected_ids']);return {'status':status,'reason':'test','issues':[],'regressions':[]}
   with patch.object(wf,'verify',side_effect=verify),patch.object(wf,'syntax_check',return_value={'passed':True}),patch.object(wf,'render_report',side_effect=render),patch.object(wf,'ModelClient',side_effect=AssertionError('Model call forbidden')):
    repair,verdict,_=wf.reverify_saved(cfg,root)
   expected_source=cand if status=='verified' else (prior_dir if prior_exists else orig)
   expected_status=status if not prior_exists or status=='verified' else 'partial_verified'
   assert pathlib.Path(repair['candidate'])==expected_source
   assert verdict['status']==expected_status
   assert observed==(['ISSUE-001','ISSUE-002'] if prior_exists else ['ISSUE-002'])
   import zipfile
   zip_path=next((root/'交付结果').glob('*.zip'))
   with zipfile.ZipFile(zip_path) as z:assert z.read('app.js')==(expected_source/'app.js').read_bytes()
   assert (orig/'app.js').read_text()=='const x=0;'
   results.append({'prior_verified':prior_exists,'current_status':status,'result':'passed','export':zip_path.name})
print(json.dumps(results,ensure_ascii=False,indent=2))
pathlib.Path(__file__).with_name('lifecycle_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
