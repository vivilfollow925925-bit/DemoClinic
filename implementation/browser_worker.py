"""Browser worker: real model-driven actions and deterministic evidence replay."""
from __future__ import annotations
import base64, copy, hashlib, json, sys, time, urllib.parse
from pathlib import Path
from workflow_engine import ModelClient, dump, load, redact, say, tool

OBSERVE = r'''() => {
  const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  window.__wfCounter = window.__wfCounter || 0;
  const elements = Array.from(document.querySelectorAll('button,a,input,textarea,select,[role="button"],form,[id]'))
    .filter(visible).slice(0,180).map(e => {
      if (!e.dataset.wfId) e.dataset.wfId = String(++window.__wfCounter);
      return {selector:'[data-wf-id="'+e.dataset.wfId+'"]', tag:e.tagName.toLowerCase(),
        id:e.id, role:e.getAttribute('role'), type:e.type,
        text:(e.innerText||e.getAttribute('aria-label')||e.placeholder||'').slice(0,160),
        value:(e.type==='password'?'[hidden]':('value' in e ? e.value : undefined)), disabled:e.disabled,
        options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,text:o.text})):undefined};
    });
  return {title:document.title, text:document.body.innerText.slice(0,14000),elements};
}'''

ASSERTION = {'type': 'object', 'properties': {
    'op': {'type': 'string', 'enum': ['visible', 'contains', 'count', 'value', 'disabled', 'selected_text', 'inspection']},
    'selector': {'type': 'string'}, 'path': {'type':'array','items':{'type':'string'}}, 'comparator': {'type':'string','enum':['equal','contains','less_equal','greater_equal']}, 'expected': {'anyOf':[{'type':'string'}, {'type':'integer'}, {'type':'boolean'}]}},
    'required': ['op','expected']}

TOOLS = [
 tool('act', '操作当前网页。selector 必须来自当前观察，可用稳定的 #id；每次只做一个动作。', {
    'action': {'type':'string','enum':['goto','click','fill','fill_form','select','press','scroll','wait','resize','reload','back','forward','hover','check','uncheck','drag','mouse_click','mouse_drag','tap','wait_for','upload','download']},
    'fields': {'type':'array','maxItems':8,'items':{'type':'object','properties':{'selector':{'type':'string'},'value':{'type':'string'}},'required':['selector','value']}},
    'selector': {'type':'string'}, 'value': {'type':'string'}, 'key': {'type':'string'},
    'url': {'type':'string'}, 'amount': {'type':'integer'}, 'width': {'type':'integer'},
    'height': {'type':'integer'}, 'target': {'type':'string'}, 'state': {'type':'string','enum':['visible','hidden','attached','detached']}, 'timeout_ms': {'type':'integer'}, 'x': {'type':'number'}, 'y': {'type':'number'}, 'to_x': {'type':'number'}, 'to_y': {'type':'number'}, 'file_ids': {'type':'array','items':{'type':'string'}}, 'accept_dialog': {'type':'boolean'}}, ['action']),
 tool('check', '检查正常流程。disabled直接检查按钮是否禁用，expected为布尔值；selected_text检查下拉框显示文字，value检查内部值。expected必须是产品约定的正确结果。',
    {'assertion': ASSERTION, 'description': {'type':'string'}}, ['assertion','description']),
 tool('record_issue', '提交问题。bug 必须给出当前不满足的正确预期断言；建议标 usability。工具会保存证据和操作。', {
    'dimension': {'type':'integer'}, 'impact': {'type':'string'}, 'title': {'type':'string'}, 'kind': {'type':'string','enum':['bug','usability','suspected']},
    'severity': {'type':'string','enum':['P1','P2','P3']},
    'requirement': {'type':'string'}, 'expected': {'type':'string'}, 'actual': {'type':'string'},
    'steps': {'type':'string'}, 'assertion': ASSERTION,
    'preconditions': {'type':'array','items':{'type':'string'}},
    'initial_recommendation': {'type':'string'}, 'uncertainties': {'type':'array','items':{'type':'string'}},
    'page': {'type':'string'}, 'task_ref': {'type':'string'}},
    ['title','kind','severity','requirement','expected','actual','steps']),
 tool('finish', '结束体验，说明完成与未完成的任务。没有证据时不要声称任务完成或发现故障。',
    {'summary': {'type':'string'}, 'outcome':{'type':'string','enum':['passed','failed','blocked','incomplete']},
     'check_ids':{'type':'array','items':{'type':'integer'}}}, ['summary'])]

class BrowserSession:
    def __init__(self, page, url, output, config=None):
        self.page, self.url, self.output = page, url, Path(output)
        self.actions, self.checks, self.issues, self.errors = [], [], [], []
        self.snapshots, self.counter = [], 0
        page.on('pageerror', lambda e: self.errors.append(str(e)[:1000]))
        origin = urllib.parse.urlparse(url)
        self.origin = (origin.scheme, origin.netloc)
        self.extra_origins = set((config or {}).get('extra_allowed_origins', []))
        self.accept_dialog = False
        self.setup_context(page.context)
        page.on('dialog', lambda d: d.accept() if self.accept_dialog else d.dismiss())
        page.set_default_timeout(4500)

    def setup_context(self, context):
        def route(r):
            u=urllib.parse.urlsplit(r.request.url)
            origins={f'{self.origin[0]}://{self.origin[1]}', *self.extra_origins}
            same=f'{u.scheme}://{u.netloc}' in origins
            asset=(not r.request.is_navigation_request() and r.request.resource_type in ('script','stylesheet','image','font','media'))
            if not (same or asset or u.scheme in ('data','blob')):
                if hasattr(self,'_event'):self._event({'type':'blocked_by_test_environment','url':self._url(r.request.url)})
                return r.abort()
            fault=getattr(self,'injected_failure',None)
            if fault and same and u.path==fault['path']:
                if hasattr(self,'_event'):self._event({'type':'injected_failure','path':u.path,'status':fault['status']})
                return r.fulfill(status=fault['status'],content_type='application/json',body='{"test_injected_failure":true}')
            return r.continue_()
        context.route('**/*',route)

    def observe(self):
        self.counter += 1
        info = self.page.evaluate(OBSERVE)
        path = self.output / 'screenshots' / f'{self.counter:03d}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=False, timeout=10000)
        info.update({'url': self.page.url, 'screenshot': str(path), 'page_errors': self.errors[-5:]})
        self.snapshots.append(str(path))
        return info

    def locator(self, selector):
        if not selector or len(selector) > 500: raise ValueError('请指定当前页面提供的 selector。')
        loc = self.page.locator(selector)
        if loc.count() != 1: raise ValueError(f'该 selector 匹配 {loc.count()} 个元素，请选择唯一元素。')
        return loc

    def prepare_action(self, original):
        """Capture stable identity before mutation; refuse changed targets on replay."""
        args = copy.deepcopy(original)
        for field in ('selector', 'target'):
            selector = args.get(field)
            if not selector or args.get('action', '').startswith('_'):
                continue
            if args['action'] == 'wait_for':
                continue  # A wait may legitimately target an absent element.
            loc = self.locator(selector)
            identity = loc.evaluate('''e => ({tag:e.tagName.toLowerCase(),
                type:e.getAttribute('type')||'', name:e.getAttribute('name')||'',
                text:['BUTTON','A'].includes(e.tagName)?e.textContent.trim():'',
                aria:e.getAttribute('aria-label')||''})''')
            expected = args.get('_target_identity', {}).get(field)
            if expected is not None and identity != expected:
                raise ValueError('定位失败：控件身份与记录不一致，停止重放：' + selector)
            if field == 'selector' and args['action'] == 'fill':
                if not loc.evaluate("e => ['INPUT','TEXTAREA'].includes(e.tagName) || e.isContentEditable"):
                    raise ValueError('定位失败：输入操作的目标不是输入框：' + selector)
            if expected is None:
                candidates = loc.evaluate('''e => {
                    const q=JSON.stringify, tag=e.tagName.toLowerCase(), out=[];
                    if(e.id) out.push('#'+CSS.escape(e.id));
                    for(const a of ['data-testid','aria-label','name','placeholder','data-page'])
                        if(e.getAttribute(a)) out.push(tag+'['+a+'='+q(e.getAttribute(a))+']');
                    if(['BUTTON','A'].includes(e.tagName) && e.textContent.trim())
                        out.push(tag+':text-is('+q(e.textContent.trim())+')');
                    const parent=e.parentElement?.closest('[id],dialog,form');
                    if(parent){const scope=parent.id?'#'+CSS.escape(parent.id):parent.tagName.toLowerCase();
                        out.unshift(...out.map(s=>scope+' '+s));}
                    return out;
                }''')
                for candidate in candidates:
                    found = self.page.locator(candidate)
                    if found.count() == 1:
                        handle = loc.element_handle()
                        try: same = found.evaluate('(e, original) => e === original', handle)
                        finally: handle.dispose()
                        if same:
                            args[field] = candidate
                            break
                if 'data-wf-id' in args[field]:
                    args['_replay_unstable'] = True
                args.setdefault('_target_identity', {})[field] = identity
        return args

    def act(self, args):
        args = self.prepare_action(args)
        a = args['action']; p = self.page
        self.accept_dialog = bool(args.get('accept_dialog', False))
        if a == 'goto':
            value = args.get('url') or self.url
            # Recorded absolute URLs are remapped onto the fresh replay server.
            u = urllib.parse.urlparse(value)
            if u.netloc and (u.scheme, u.netloc) != self.origin:
                raise ValueError('不允许离开本次目标站点。')
            p.goto(urllib.parse.urljoin(self.url, value), wait_until='domcontentloaded', timeout=20000)
        elif a == 'click': self.locator(args.get('selector')).click()
        elif a == 'fill': self.locator(args.get('selector')).fill(args.get('value',''))
        elif a == 'select': self.locator(args.get('selector')).select_option(args['value'])
        elif a == 'press': self.locator(args.get('selector')).press(args.get('key','Enter'))
        elif a == 'scroll': p.mouse.wheel(0, max(-3000,min(3000,args.get('amount',600))))
        elif a == 'wait': p.wait_for_timeout(max(0,min(4000,args.get('amount',1000))))
        elif a == 'resize': p.set_viewport_size({'width':max(320,min(1920,args['width'])),
                                                'height':max(480,min(1440,args.get('height',900)))})
        else: raise ValueError('未知操作')
        p.wait_for_timeout(250)
        self.actions.append(copy.deepcopy(args))
        return self.observe()

    def assertion(self, spec):
        try:
            if 'expected' not in spec: raise ValueError('检查缺少 expected（正确预期），不要使用 value 代替。')
            op, loc, expected = spec['op'], self.page.locator(spec['selector']), spec['expected']
            if op == 'count': actual = loc.count()
            elif op == 'visible': actual = bool(loc.count() and loc.first.is_visible())
            elif op == 'contains':
                if not loc.count(): raise ValueError('断言元素不存在，不能用缺失元素证明文字错误。')
                actual = loc.first.inner_text(timeout=3000)
            elif op == 'value': actual = loc.first.input_value(timeout=3000)
            elif op == 'disabled':
                if type(expected) is not bool: raise ValueError('disabled 的 expected 必须为 true 或 false，不是字符串')
                actual = loc.first.is_disabled(timeout=3000)
            elif op == 'selected_text':
                if loc.count() != 1: raise ValueError('下拉框定位必须唯一')
                actual = loc.evaluate("e => {if(e.tagName!=='SELECT')throw Error('目标不是下拉框');return e.selectedOptions[0]?.textContent.trim()||''}")
            else: raise ValueError('未知断言类型')
            passed = str(expected) in actual if op == 'contains' else actual == expected
            return {'evaluated':True, 'passed':passed, 'actual':actual,
                    'expected':expected, 'assertion':spec, 'after_action':len(self.actions)}
        except Exception as e:
            return {'evaluated':False, 'passed':False, 'error':redact(e),
                    'assertion':spec,'after_action':len(self.actions)}

    def record_issue(self, args):
        issue = copy.deepcopy(args)
        if not 1 <= issue.get('dimension', 1) <= 42: raise ValueError('维度编号错误')
        if getattr(self,'failures_since_action',False):
            issue['replay_warning']='此前存在未成功记录的操作，独立重放可能不完整'
            if issue['kind']=='bug': issue['kind']='suspected'
        if issue['kind'] == 'bug':
            if 'assertion' not in issue: raise ValueError('功能故障必须提供正确预期的断言。')
            result = self.assertion(issue['assertion'])
            if not result['evaluated'] or result['passed']:
                raise ValueError('当前证据没有显示该断言失败，不能记录为已观察到的功能故障。')
            issue['observation_check'] = result
        observation = self.observe()
        issue.update({'actions':copy.deepcopy(self.actions), 'screenshot':observation['screenshot']})
        self.issues.append(issue)
        return {'recorded':True, 'issue_number':len(self.issues), 'confirmation':'尚需独立重放确认'}

def content_with_image(observation, vision):
    # One current observation only. Long text remains available via inspect(text).
    compact = {k: v for k, v in observation.items() if k not in ('network', 'screenshot')}
    compact['text'] = observation.get('text', '')[:5000]
    compact['elements'] = observation.get('elements', [])[:100]
    compact['truncated'] = len(observation.get('text','')) > 5000 or len(observation.get('elements',[])) > 100
    text = json.dumps(compact, ensure_ascii=False, separators=(',', ':'))
    if not vision: return text
    path = Path(observation['screenshot'])
    return [{'type':'text','text':text}, {'type':'image_url','image_url':{
        'url':'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode(), 'detail':'high'}}]

def trim_old_images(messages):
    candidates = [m for m in messages if isinstance(m.get('content'),list)]
    for m in candidates[:-2]:
        m['content'] = [c for c in m['content'] if c.get('type') != 'image_url']

from browser_capabilities import extend_session, schemas, decimal_expression, STAGES
BrowserSession = extend_session(BrowserSession)
TOOLS += schemas()
from experience_skill import (ExperienceSkill, skill_schemas, checkpoint, read_memory,
                              bounded_messages, generate_report_content)
TOOLS += skill_schemas()

def load_protocol(config):
    explicit=config.get('prompt_path')
    candidates=[Path(explicit)] if explicit else [Path(__file__).with_name('体验Agent检查维度.md')]
    path=next((p for p in candidates if p.is_file()),None)
    if path is None:raise FileNotFoundError('未找到体验Agent检查维度.md，请配置 prompt_path')
    text=path.read_text(encoding='utf-8')
    if '## 检查维度' not in text:raise ValueError('提示词文件缺少检查维度章节')
    return text,hashlib.sha256(text.encode()).hexdigest()

def test_agent(session, config, persona=None):
    protocol,digest=load_protocol(config)
    client=ModelClient(dict(config, usage_log_path=str(session.output/'model_usage.jsonl')),'test')
    skill=ExperienceSkill(protocol, session.output, TOOLS)
    system=skill.core(config)
    task=config.get('task_card')
    if task:
        skill.read({'topic':'dimension','dimensions':task['dimensions']})
        system += ('\n只执行当前任务卡，不自行扩展到整个项目。前置任务成功不表示当前浏览器已准备好数据：'
                   '当前是独立副本和全新浏览器，必须按setup建立前置状态。父任务页面记忆只是线索，操作前核对当前控件。'
                   '完成后finish必须给outcome和证明本卡成功标准的check_ids；未完成或证据不足不能passed。'
                   '如前置能力再次异常，只记录受阻和证据，不把同一前置故障改名报成新的后续故障。')
    messages=[{'role':'system','content':system},{'role':'user','content':json.dumps({
        'target_url':session.url,'step_budget':config['max_test_turns'],
        'tool_directory':[{ 'name':t['function']['name'], 'description':t['function']['description'][:130]} for t in TOOLS],
        'task_card':task,'prerequisite_context':config.get('prerequisite_context',{}),
        'instruction':'先打开页面观察用途；调用stage(name=discovery,note=你的初步理解)，然后再读产品要求。'},ensure_ascii=False)}]
    observation=session.act({'action':'goto','url':session.url})
    session.current_observation=observation
    messages.append({'role':'user','content':content_with_image(observation,config['vision'])})
    initial=messages[1]
    batches=[[messages[2]]]
    summary,stop,fatal='','达到轮数上限，未自动认定完成',None
    task_completion = None
    deadline=time.monotonic()+config.get('max_test_seconds',1800)
    tool_budget=config.get('max_tool_calls',180);tool_count=0
    for turn in range(config['max_test_turns']):
        if time.monotonic()>deadline or tool_count>=tool_budget:
            stop='达到时间或工具调用上限';break
        say(f'体验检查：第 {turn+1} 轮；阶段 {STAGES[session.stage_index]}；操作 {len(session.actions)}')
        session.budget_state = {'turn': turn+1, 'remaining_turns': config['max_test_turns']-turn,
            'remaining_seconds': max(0,int(deadline-time.monotonic())), 'tool_calls':tool_count,
            'instruction': '剩余5轮时停止新任务，登记覆盖并finish' if config['max_test_turns']-turn <= 5 else '先覆盖各核心任务；同类工具错误只重试一次'}
        messages=bounded_messages(system,initial,batches,session,skill,config)
        observation=session.current_observation
        last_action=session.actions[-1].get('action') if session.actions else ''
        send_image = bool(config['vision'] and observation.get('screenshot') != getattr(session,'last_sent_image',None)
            and (turn == 0 or last_action not in ('fill','select','_fixture') or session.failures_since_action or getattr(session,'force_image',False)))
        messages.append({'role':'user','content':content_with_image(observation,send_image)})
        if send_image:
            session.last_sent_image=observation.get('screenshot')
            session.force_image=False
        enabled=skill.tools()
        try:message=client.request(messages,enabled)
        except Exception as e:
            fatal=redact(e);stop='模型接口失败，保留已完成证据';break
        batch=[message];batches.append(batch);calls=message.get('tool_calls') or []
        if not calls:
            batch.append({'role':'user','content':'请使用工具执行或登记检查；结束时调用finish。'});continue
        finished=False;changed=False;fresh=None
        for call in calls:
            name=call['function']['name'];tool_count+=1
            try:
                args=json.loads(call['function']['arguments'])
                if name not in {t['function']['name'] for t in enabled}:raise ValueError('本轮未启用该工具，请先read_skill读取对应方法或工具。')
                mutating=name in ('act','environment','session','fixture')
                if tool_count>tool_budget:raise ValueError('工具预算已耗尽，请结束并说明未测项')
                if mutating and changed:raise ValueError('本轮已执行一个动作，请根据新观察决定下一动作')
                if name=='read_skill':answer=skill.read(args)
                elif name=='read_memory':answer=read_memory(session.output,args)
                elif name=='act':
                    if args.get('action','').startswith('_'):raise ValueError('内部重放动作不可直接调用')
                    if session.initial_understanding is not None and not skill.active_dimensions:raise ValueError('先read_skill读取当前维度要求。')
                    answer=session.act(args);changed=True;fresh=answer
                elif name=='observe':
                    answer=session.observe();fresh=answer;session.force_image=True
                elif name=='inspect':answer=session.inspect(args)
                elif name=='environment':answer=session.environment(args);changed=True
                elif name=='session':answer=session.session_tool(args);changed=True;fresh=answer if 'screenshot' in answer else None
                elif name=='fixture':answer=session.fixture(args);changed=True
                elif name=='calculate':answer=decimal_expression(args['expression'])
                elif name=='stage':answer=session.set_stage(args)
                elif name=='coverage':answer=session.set_coverage(args)
                elif name=='check':
                    answer=session.assertion(args['assertion']);answer['description']=args['description']
                    answer['check_id']=len(session.checks)+1;session.checks.append(answer)
                elif name=='record_issue':answer=session.record_issue(args)
                elif name=='finish':
                    if session.initial_understanding is None:raise ValueError('尚未记录首次理解与读取任务，请先调用stage')
                    if session.stage_index<1:raise ValueError('尚未进入core阶段，请先记录核心任务检查或受阻原因')
                    if not session.coverage_rows:raise ValueError('请先用coverage记录已测和未测范围')
                    if task:
                        outcome=args.get('outcome')
                        if outcome not in ('passed','failed','blocked','incomplete'):raise ValueError('请填写本任务outcome')
                        check_ids=args.get('check_ids',[])
                        actual_checks={c.get('check_id'):c for c in session.checks}
                        if outcome=='passed' and (not check_ids or any(
                                i not in actual_checks or not actual_checks[i].get('evaluated') or not actual_checks[i].get('passed') for i in check_ids)):
                            raise ValueError('通过必须引用本任务已执行且通过的check_ids')
                        task_completion={'outcome':outcome,'check_ids':check_ids}
                    summary=args['summary'];finished=True;stop='模型结束；通过范围以检查证据为准';answer={'finished':True}
                else:raise ValueError('未知工具')
            except Exception as e:
                answer={'error':redact(e),'note':'工具/环境错误不等于产品故障'}
                if name in ('act','environment','session','fixture'):
                    changed=True;session.failures_since_action=True
                    session.action_failures.append({'tool':name,'error':redact(e),'after_action':len(session.actions)})
                    try:fresh=session.observe()
                    except Exception:pass
            checkpoint(session,skill)
            batch.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(answer,ensure_ascii=False)})
        if fresh and 'screenshot' in fresh:
            session.current_observation=fresh
            batch.append({'role':'user','content':content_with_image(fresh,config['vision'])})
        if finished:break
    session.restore_environment()
    checkpoint(session,skill,stop)
    result={'actions':session.actions,'checks':session.checks,'issues':session.issues,
        'task_completion':task_completion,
        'summary':summary,'stop_reason':stop,'usage':client.usage,'page_errors':session.errors,
        'screenshot':session.snapshots[-1], 'coverage':session.coverage_result(),
        'stages':session.stage_events,'initial_understanding':session.initial_understanding,
        'tool_directory':[{ 'name':t['function']['name'], 'description':t['function']['description'][:130]} for t in TOOLS],'protocol_sha256':digest,'action_trace':session.trace,
        'tool_failures':session.action_failures,'model_error':fatal,'tool_calls':tool_count}
    current=session.current_observation
    result['page_memory']={'url_path':urllib.parse.urlsplit(current.get('url','')).path,
        'title':current.get('title',''),'text':current.get('text','')[:1000],
        'controls':[{'id':e.get('id'),'tag':e.get('tag'),'text':e.get('text','')[:80]}
                    for e in current.get('elements',[])[:30]],
        'note':'历史观察，仅供定位参考；必须核对当前页面。'}
    # Commit evidence before the extra reporting call; interruption cannot erase cards.
    dump(session.output/'result.json',result)
    if fatal:
        result['report_content']={'status':'skipped','reason':'体验接口失败，已保存原始记录，未继续消耗报告调用'}
    else:
        say('体验记录已保存，开始按需读取报告规范并整理内容')
        try:result['report_content']=generate_report_content(session,config,skill,result)
        except Exception as e:result['report_content']={'status':'incomplete','error':redact(e)}
    return result

def replay_actions(session, job):
    results, errors = [], []
    checks = job.get('checks',[])
    for i, original in enumerate(job['actions'],1):
        action = copy.deepcopy(original)
        if action['action'] == 'goto':
            parsed = urllib.parse.urlparse(action.get('url','/'))
            action['url'] = parsed.path + ('?' + parsed.query if parsed.query else '') or '/'
        try:
            if action.get('_replay_unstable') or any('data-wf-id' in str(action.get(k,'')) for k in ('selector','target')):
                raise ValueError('定位失败：旧临时编号缺少稳定定位，请先在原始项目上核对并迁移操作记录')
            session.act(action)
        except Exception as e:
            errors.append({'action':i,'kind':'replay_execution_error','error':redact(e)}); break
        for c in checks:
            if c['after_action'] == i:
                r = session.assertion(c['assertion']); r['description'] = c.get('description','')
                results.append(r)
    expected_checks=len(checks)
    session.restore_environment()
    return {'actions_ok':not errors and len(results)==expected_checks, 'errors':errors, 'checks':results,
            'screenshot':session.snapshots[-1] if session.snapshots else None}

def main(job):
    from playwright.sync_api import sync_playwright
    output = Path(job['output']); output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        engine=job['config'].get('browser_engine','chromium')
        if engine not in ('chromium','firefox','webkit'):raise ValueError('不支持的浏览器')
        browser = getattr(p,engine).launch(headless=job['config'].get('headless',True))
        try:
            context = browser.new_context(viewport={'width':1280,'height':900}, locale='zh-CN',
                                          timezone_id='Asia/Shanghai',service_workers='block')
            session = BrowserSession(context.new_page(),job['url'],output,job['config'])
            if job['operation'] == 'discover':
                observation=session.act({'action':'goto','url':job['url']})
                result={k:observation[k] for k in ('title','text','elements')}
                result['text']=result['text'][:6000];result['elements']=result['elements'][:80]
            elif job['operation'] == 'test': result = test_agent(session,job['config'],job.get('persona'))
            else: result = replay_actions(session,job)
        finally: browser.close()
    dump(output/'result.json',result)
    return result

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    job = load(sys.argv[1])
    try: main(job)
    except Exception as e:
        dump(Path(job['output'])/'result.json',{'fatal_error':redact(e)})
        say('阶段停止：'+redact(e)); sys.exit(1)
