import sys, tempfile, pathlib, time, json
from unittest.mock import patch
import workflow_engine as wf, project_submission as intake
from verification_contract import compare_values
sys.stdout.reconfigure(encoding='utf8')
results=[]
def ok(name):
 results.append(name); print('PASS '+name,flush=True)
assert compare_values('5',10,'less_equal') and compare_values('12',10,'greater_equal')
assert not compare_values('19','9','equal') and compare_values('19','9','contains')
try: compare_values(True,1,'less_equal'); raise AssertionError('bool accepted')
except ValueError: pass
ok('比较器与非法类型')
with tempfile.TemporaryDirectory() as tmp:
 root=pathlib.Path(tmp);src=root/'src';src.mkdir();(src/'app.js').write_text('let x=1;')
 first=wf.verification_inputs({'source_path':str(src)},src,[],[],['I1'])
 (src/'app.js').write_text('let x=2;')
 assert first!=wf.verification_inputs({'source_path':str(src)},src,[],[],['I1'])
 ok('输入文件内容指纹检测变更')
 (root/'repair.json').write_text('broken-json')
 try: wf.reverify_saved({},root); raise AssertionError('accepted bad JSON')
 except json.JSONDecodeError: pass
 assert not (root/'.verification.lock').exists()
 ok('读取失败释放复测锁')
 wf.dump(root/'repair.json',{'status':'old'})
 (root/'体验与修复报告.html').write_text('old-report',encoding='utf8')
 def broken(*args):
  wf.dump(root/'repair.json',{'status':'new'})
  (root/'体验与修复报告.html').write_text('new-report',encoding='utf8')
  raise OSError('simulated export failure')
 with patch.object(wf,'_reverify_saved_impl',side_effect=broken):
  try: wf.reverify_saved({},root); raise AssertionError('failure swallowed')
  except OSError: pass
 assert wf.load(root/'repair.json')=={'status':'old'}
 assert (root/'体验与修复报告.html').read_text(encoding='utf8')=='old-report'
 ok('发布异常恢复原有元数据和报告')
 (root/'.verification.lock').write_text('test')
 with patch.object(wf,'_reverify_saved_impl',side_effect=AssertionError('concurrent run')):
  try: wf.reverify_saved({},root); raise AssertionError('lock ignored')
  except RuntimeError: pass
 (root/'.verification.lock').unlink();ok('并发复测被拒绝')
 dest=root/'交付结果';dest.mkdir();(dest/'old.zip').write_bytes(b'previous')
 def export_fail(run,config,repair,verdict,stage):
  stage.mkdir();(stage/'partial.zip').write_bytes(b'partial');raise OSError('export failed')
 with patch.object(intake,'_export_project_files',side_effect=export_fail):
  try: intake.export_project(root,{}, {},{});raise AssertionError('export failure swallowed')
  except OSError: pass
 assert (dest/'old.zip').read_bytes()==b'previous' and len(list(dest.iterdir()))==1
 assert not list(root.glob('.交付结果.*.tmp'));ok('导出失败保留上一次交付目录')
 fake=root/'fake';fake.mkdir();(fake/'browser_worker.py').write_text('import time; time.sleep(30)')
 started=time.monotonic()
 with patch.object(wf,'ROOT',fake):
  try: wf.worker({'operation':'replay'},root/'worker',{'verification_timeout_seconds':1},credentials=False);raise AssertionError('timeout ignored')
  except TimeoutError: pass
 assert time.monotonic()-started<10;ok('真实子进程超时被终止')
 with patch.object(wf.shutil,'which',return_value=None):
  result=wf.syntax_check(src)
 assert not result['passed'] and result['unavailable'];ok('缺少语法检查器明确受阻')
 (src/'app.ts').write_text('const a: number = 1;')
 assert not wf.syntax_check(src)['passed'];ok('未支持编译器不冒充检查通过')
with tempfile.TemporaryDirectory() as tmp:
 root=pathlib.Path(tmp);orig=root/'original';orig.mkdir();(orig/'app.js').write_text('let x=0;')
 card=root/'batch'/'I1';cand=card/'candidate';cand.mkdir(parents=True);(cand/'app.js').write_text('let x=1;')
 cfg={'source_path':str(orig)};issues=[{'id':'I1'}]
 wf.dump(root/'issues.json',issues);wf.dump(root/'baseline.json',[])
 wf.dump(root/'repair.json',{'batch_dir':str(card.parent),'requested_ids':['I1']})
 wf.dump(card/'repair.json',{'status':'candidate','candidate':str(cand),'selected_ids':['I1']})
 wf.dump(card/'verification_inputs.json',wf.verification_inputs(cfg,cand,[],issues,['I1']))
 (cand/'app.js').write_text('let x=2;')
 with patch.object(wf,'verify',side_effect=AssertionError('stale candidate reached verifier')):
  try: wf.reverify_saved(cfg,root);raise AssertionError('changed candidate accepted')
  except ValueError: pass
 assert not (root/'.verification.lock').exists();ok('候选变更实际阻止复测采用')
 assert wf.syntax_check(cand)['passed'];ok('有效JavaScript通过真实Node检查')
 (cand/'app.js').write_text('function {')
 assert not wf.syntax_check(cand)['passed'];ok('无效JavaScript未通过真实Node检查')
print('FINISHED',len(results),'guard cases')
pathlib.Path(__file__).with_name('guard_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
