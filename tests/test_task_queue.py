from pathlib import Path
import sys,tempfile,copy,json
p=Path(__file__).resolve().parents[1];sys.path.insert(0,str(p/'implementation'))
import task_queue as tq
root=Path(tempfile.mkdtemp(prefix='task_graph_tests_'))
def card(n,deps,ref):return {'id':f'T{n}','title':f'能力{n}','goal':'执行一个可观察流程','success_criteria':'应出现预期结果','setup':'从初始页面建立测试状态','dimensions':[4],'depends_on':deps,'dependency_reason':'必须先建立前置状态' if deps else '', 'requirement_indices':[ref]}
plan={'tasks':[card(1,[],1),card(2,['T1'],1),card(3,['T2'],1),card(4,[],2)],'out_of_scope':[]}
plan=tq.validate_plan(plan,2)
assert [t['layer'] for t in plan['tasks']]==[0,1,2,0]
for mutation in ['cycle','unknown','missing','duplicate']:
 bad=copy.deepcopy(plan)
 if mutation=='cycle':bad['tasks'][0]['depends_on']=['T3'];bad['tasks'][0]['dependency_reason']='cycle'
 elif mutation=='unknown':bad['tasks'][1]['depends_on']=['T99']
 elif mutation=='missing':bad['tasks']=bad['tasks'][:3]
 else:bad['tasks'][1]['id']='T1'
 try:tq.validate_plan(bad,2)
 except ValueError:pass
 else:raise AssertionError(mutation)
passed={'actions':[],'checks':[{'check_id':1,'evaluated':True,'passed':True}], 'issues':[],'task_completion':{'outcome':'passed','check_ids':[1]}}
failed={'actions':[],'checks':[],'issues':[{'kind':'bug','title':'基础能力失败','observation_check':{'evaluated':True,'passed':False}}]}
assert tq.task_verdict({'task_completion':{'outcome':'passed','check_ids':[1]},'checks':[]})!='passed'
seen=[]
def execute(task,budget,seconds,parents):
 seen.append(task['id']);return copy.deepcopy(failed if task['id']=='T1' else passed)
results=tq.dispatch(plan,execute,root/'failure',{'max_test_turns':60,'experience_workers':2,'task_max_turns':10})
assert set(seen)=={'T1','T4'},seen
assert results[1]['task_status']==results[2]['task_status']=='blocked'
assert results[2]['blocked_by']==['T1']
assert results[0]['issues'][0]['blocked_tasks']==['T2','T3']
assert sum(len(r['issues']) for r in results)==1
seen=[]
def success(task,budget,seconds,parents):
 assert all(d in seen for d in task['depends_on']);seen.append(task['id']);return copy.deepcopy(passed)
results=tq.dispatch(plan,success,root/'success',{'max_test_turns':60,'experience_workers':2,'task_max_turns':10})
assert all(r['task_status']=='passed' for r in results)
results=tq.dispatch(plan,lambda *a: (_ for _ in ()).throw(AssertionError('should not execute')),root/'budget',{'max_test_turns':0})
assert all(r['task_status'] in ('not_run','blocked') for r in results)
print('PASS: DAG validation, real dependency layers, failure propagation, single root issue, independent branch progress, evidence gate, zero budget')
