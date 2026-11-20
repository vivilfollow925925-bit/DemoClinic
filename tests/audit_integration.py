import sys,tempfile,pathlib,time,json
from unittest.mock import patch
import workflow_engine as wf
sys.stdout.reconfigure(encoding='utf8')
with tempfile.TemporaryDirectory() as tmp:
 root=pathlib.Path(tmp);orig=root/'original';fixed=root/'fixed';orig.mkdir();fixed.mkdir()
 html='<h1>Fixture</h1><button id="save">Save</button><p id="result">Waiting</p><script src="app.js"></script>'
 for folder in (orig,fixed): (folder/'index.html').write_text(html,encoding='utf8')
 (orig/'app.js').write_text('document.querySelector("#save").onclick=()=>{};',encoding='utf8')
 (fixed/'app.js').write_text('document.querySelector("#save").onclick=()=>setTimeout(()=>document.querySelector("#result").textContent=location.hash==="#/settings"?"Done":"Wrong route",800);',encoding='utf8')
 cfg={'mode':'source','source_path':str(orig),'start_command':['{python}','-m','http.server','{port}','--bind','127.0.0.1'],'headless':True,'_verification_deadline_wall':time.time()+90}
 actions=[{'action':'goto','url':'http://old/#/settings'},{'action':'click','selector':'#save'}]
 assertion={'op':'contains','selector':'#result','expected':'Done','comparator':'equal'}
 issues=[{'id':'I1','actions':actions,'assertion':assertion}]
 baseline=[{'persona':{'id':'T1','name':'Heading'},'actions':actions[:1],'checks':[{'passed':True,'after_action':1,'assertion':{'op':'contains','selector':'h1','expected':'Fixture','comparator':'equal'}}]}]
 with patch.object(wf,'ModelClient',side_effect=AssertionError('No model permitted')):
  original=wf.replay(cfg,root,orig,actions,[{'after_action':2,'assertion':assertion}],'original')
  assert original['actions_ok'] and original['checks'][0]['evaluated'] and not original['checks'][0]['passed']
  repair={'status':'candidate','candidate':str(fixed),'selected_ids':['I1'],'changes':[{'path':'app.js'}],'syntax':wf.syntax_check(fixed)}
  result=wf.verify(cfg,root,baseline,issues,repair)
  assert result['status']=='verified',result
  # Repeated invocation must use a fresh environment and never collide on folders.
  result2=wf.verify(cfg,root,baseline,issues,repair)
  assert result2['status']=='verified',result2
  (fixed/'app.js').write_text('document.querySelector("#save").onclick=()=>{};',encoding='utf8')
  failed=wf.verify(cfg,root,baseline,issues,repair)
  assert failed['status']=='not_verified',failed
print('PASS real servers + subprocess browsers: original fails, candidate passes, repeat passes, broken candidate fails')
pathlib.Path(__file__).with_name('integration_results.json').write_text(json.dumps({'original_bug_confirmed':True,'candidate_verified':True,'repeat_verified':True,'broken_candidate_rejected':True,'model_calls':0},indent=2),encoding='utf8')
