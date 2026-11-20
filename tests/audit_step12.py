import sys,json,tempfile,pathlib,copy
from unittest.mock import patch
sys.stdout.reconfigure(encoding='utf8')
import workflow_engine as wf, browser_worker as bw
results=[]
def note(name,actual,expected):
 row={'case':name,'actual':actual,'expected':expected,'problem_reproduced':actual!=expected};results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
class Loc:
 @property
 def first(self):return self
 def count(self):return 1
 def inner_text(self,**kw):return '19'
 def input_value(self,**kw):return '5'
class Page:
 def locator(self,s):return Loc()
s=object.__new__(bw.BrowserSession);s.page=Page();s.actions=[];s.prepare_action=lambda x:x
note('文本要求完全相等，实际19、预期9',s.assertion({'op':'contains','comparator':'equal','selector':'#n','expected':'9'})['passed'],False)
note('输入值要求≤10，实际5',s.assertion({'op':'value','comparator':'less_equal','selector':'#n','expected':10})['passed'],True)
class Replay:
 def __init__(self):self.actions=[];self.snapshots=[]
 def act(self,a):self.actions.append(a)
 def restore_environment(self):pass
x=Replay();bw.replay_actions(x,{'actions':[{'action':'goto','url':'http://old/#/settings'}],'checks':[]})
note('SPA hash 路由重放',x.actions[0]['url'],'/#/settings')
with tempfile.TemporaryDirectory() as tmp:
 p=pathlib.Path(tmp);(p/'bad.js').write_text('function {',encoding='utf8')
 with patch.object(wf.shutil,'which',return_value=None):v=wf.syntax_check(p)
 note('无Node时JS语法检查被标为通过',v['passed'],False)
 baseline=[{'persona':{'id':'T1','name':'regression'},'actions':[{'action':'goto'}],'checks':[{'passed':True,'after_action':1,'assertion':{'op':'visible','selector':'#x','expected':True}}]}]
 issues=[{'id':'ISSUE-001','actions':[{'action':'goto'}],'assertion':{'op':'visible','selector':'#x','expected':True}}]
 repair={'status':'candidate','candidate':tmp,'selected_ids':['ISSUE-001'],'changes':[{'path':'bad.js'}],'syntax':{'passed':True}}
 for name,response,expected in [
 ('检查定位失败的归类',{'actions_ok':True,'errors':[],'checks':[{'evaluated':False,'passed':False,'error':'selector missing'}]},'verification_blocked'),
 ('操作定位失败的归类',{'actions_ok':False,'errors':[{'error':'selector missing'}],'checks':[]},'verification_blocked'),
 ('真正结果不符的归类',{'actions_ok':True,'errors':[],'checks':[{'evaluated':True,'passed':False}]},'not_verified'),
 ('全部检查通过的归类',{'actions_ok':True,'errors':[],'checks':[{'evaluated':True,'passed':True}]},'verified')]:
  with patch.object(wf,'replay',return_value=copy.deepcopy(response)):v=wf.verify({},p,baseline,issues,repair)
  note(name,v['status'],expected)
from playwright.sync_api import sync_playwright
with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True);ctx=browser.new_context();page=ctx.new_page();s=bw.BrowserSession(page,'http://127.0.0.1:9999',tmp,{})
 # Same-name controls inside plain table rows; no heading or stable ID.
 page.set_content('<table><tbody><tr><td>Record A</td><td><button>Delete</button></td></tr><tr><td>Record B</td><td><button>Delete</button></td></tr></tbody></table>')
 recorded=s.prepare_action({'action':'click','selector':'tr:nth-child(1) button'})
 page.set_content('<table><tbody><tr><td>Record B</td><td><button>Delete</button></td></tr><tr><td>Record A</td><td><button>Delete</button></td></tr></tbody></table>')
 try:
  target=s.prepare_action(recorded)['selector'];actual=page.locator(target).evaluate("e=>e.closest('tr').innerText")
 except ValueError:actual='blocked'
 note('相同按钮重排后仍接受了另一条记录',actual,'blocked')
 # A hidden element was observed; removal is also a valid not-visible result.
 page.set_content('<div id="dialog" style="display:none">Dialog</div>')
 recorded=s.assertion({'op':'visible','selector':'#dialog','expected':False})['assertion']
 page.locator('#dialog').evaluate('e=>e.remove()')
 v=s.assertion(recorded);note('隐藏检查遇到节点被移除',{'evaluated':v['evaluated'],'passed':v['passed']},{'evaluated':True,'passed':True})
 # Collection counting bypasses identity and fallback.
 page.set_content('<button id="random1">Confirm</button>')
 v=s.prepare_action({'action':'assertion','selector':'#random1'})
 spec={k:x for k,x in v.items() if k!='action'};spec.update(op='count',expected=0)
 page.set_content('<button id="random2">Confirm</button>')
 note('count忽略备用定位，ID变化被误认为元素消失',s.assertion(spec)['passed'],False)
 # Ordinary scalar check silently picks first of multiple matches.
 page.set_content('<p class="value">expected</p><p class="value">wrong</p>')
 v=s.assertion({'op':'contains','selector':'.value','expected':'expected'})
 note('非集合检查匹配多个元素仍取第一个通过',v['evaluated'],False)
 # Wait action has no stabilization path.
 page.set_content('<button id="old">Confirm</button>');s.observe();old=page.locator('button').get_attribute('data-wf-id')
 v=s.prepare_action({'action':'wait_for','selector':'[data-wf-id="'+old+'"]','state':'visible'})
 note('wait_for不保存稳定定位','data-wf-id' in v['selector'],False)
 # Scalar result is sampled immediately: delayed valid transition is rejected.
 page.set_content('<p id="status">Loading</p><script>setTimeout(()=>document.querySelector("#status").textContent="Done",800)</script>')
 first=s.assertion({'op':'contains','selector':'#status','expected':'Done'})
 page.wait_for_timeout(1000);later=s.assertion({'op':'contains','selector':'#status','expected':'Done'})
 note('异步结果立即检查与等待后不一致',[first['passed'],later['passed']],[True,True])
 # Positive controls: changed IDs must still resolve by unique meaning.
 page.set_content('<button id="before">Save</button>')
 recorded=s.prepare_action({'action':'click','selector':'#before'})
 page.set_content('<button id="after" onclick="this.dataset.clicked=1">Save</button>')
 s.act(recorded)
 note('ID改变但唯一语义一致仍可点击',page.locator('#after').get_attribute('data-clicked'),'1')
 page.set_content('<span class="item">A</span><span class="item">B</span>')
 note('集合计数保留全部元素',s.assertion({'op':'count','selector':'.item','expected':2})['passed'],True)
 page.set_content('<div id="dialog">Dialog</div>')
 note('可见弹窗不得通过隐藏检查',s.assertion({'op':'visible','selector':'#dialog','expected':False})['passed'],False)
 page.set_content('<p id="status">Waiting</p>')
 note('异步等待有上限且未完成不通过',s.assertion({'op':'contains','selector':'#status','expected':'Done'})['passed'],False)
 browser.close()
pathlib.Path(__file__).with_name('audit_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
print('FINISHED',len(results),'cases',flush=True)

assert not any(r["problem_reproduced"] for r in results), "Verification regressions failed"
