import sys,pathlib,tempfile,copy,json
from unittest.mock import patch
P=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'implementation'))
import workflow_engine as w,browser_worker as bw,task_queue as tq
root=pathlib.Path(tempfile.mkdtemp(prefix='layered_e2e_'));src=root/'source';src.mkdir();(src/'index.html').write_text('<title>Queue fixture</title><h1>Independent page</h1><button id="gate" disabled>Start</button>',encoding='utf-8')
def card(i,deps,ref):return {'id':f'T{i}','title':f'Task {i}','goal':'Check a capability','success_criteria':'UI matches requested criterion','setup':'Open a fresh page','scope':'Only the assigned capability', 'preconditions':['Fresh browser'], 'test_inputs':[{'name':'input','value_or_rule':'No input needed'}], 'steps':[{'action':'Observe the assigned capability','input':'None','expected_result':'Matches task success criterion'}], 'completion_criteria':['Record a real browser check'], 'blocking_conditions':['Required page unavailable'], 'dimensions':[4],'depends_on':deps,'dependency_reason':'Requires preceding capability' if deps else '', 'requirement_indices':[ref]}
plan={'tasks':[card(1,[],1),card(2,['T1'],1),card(3,[],2)],'out_of_scope':[]}
def call(name,args):return {'id':name,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}
class Fake:
 def __init__(self,config,role):self.config=config;self.turn=0;self.usage={'requests':0,'prompt_tokens':0,'completion_tokens':0}
 def request(self,messages,tools=None):
  self.turn+=1;self.usage['requests']+=1
  if not self.config.get('task_card'):calls=[call('submit_plan',plan)]
  else:
   bad=self.config['task_card']['id']=='T1'
   assertion={'op':'disabled','selector':'#gate','expected':False} if bad else {'op':'contains','selector':'h1','expected':'Independent page'}
   if self.turn==1:calls=[call('stage',{'name':'discovery','note':'Observed page'}),call('stage',{'name':'core','note':'Check assigned goal'})]
   elif self.turn==2:calls=[call('read_skill',{'topic':'issue'}),call('check',{'assertion':assertion,'description':'criterion'})]
   elif self.turn==3 and bad:calls=[call('record_issue',{'dimension':4,'title':'Start disabled','kind':'bug','severity':'P2','requirement':'Start must be enabled','expected':'enabled','actual':'disabled','steps':'Open page','assertion':assertion})]
   else:calls=[call('coverage',{'items':[{'dimension':4,'applicability':'适用','result':'发现问题' if bad else '通过','reason':'Observed criterion','check_ids':[1],'issue_ids':[1] if bad else []}]}),call('finish',{'summary':'Task finished','outcome':'failed' if bad else 'passed','check_ids':[] if bad else [1]})]
  return {'role':'assistant','content':None,'tool_calls':calls}
def local_worker(job,folder,config,credentials=True):return bw.main({'config':config,**job})
c={'mode':'source','source_path':str(src),'start_command':[sys.executable,'-m','http.server','{port}','--bind','127.0.0.1'],'requirements':'A start capability and independent page','tasks':['Start then proceed','Read independent page'], 'prompt_path':str(P/'体验Agent检查维度.md'),'test_model':'mock','fix_model':'mock','vision':False,'headless':True,'max_test_turns':16,'task_max_turns':5,'max_test_seconds':120,'experience_workers':2,'generate_report_content':False}
run=root/'run';run.mkdir()
with patch.object(w,'ModelClient',Fake),patch.object(bw,'ModelClient',Fake),patch.object(w,'worker',local_worker):
 results=w.run_baseline(c,run)
assert [r['task_status'] for r in results]==['failed','blocked','passed'],[(r.get('task_status'),r.get('model_error')) for r in results]
assert sum(len(r['issues']) for r in results)==1
assert results[1]['actions']==[]
assert results[0]['issues'][0]['blocked_tasks']==['T2']
issues=w.reproduce_issues(c,run,results)
assert len(issues)==1 and issues[0]['status']=='已复现（按所填预期）'
w.render_report(run,c,results,issues)
print('PASS real browser + mocked models: analysis -> graph -> concurrent isolated workers -> blocked dependent -> one root issue -> reproduction -> HTML')
print(root)
