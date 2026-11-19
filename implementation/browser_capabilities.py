"""Bounded browser tools. No arbitrary JavaScript, shell, or source-file access."""
import ast
import copy
import csv
import io
import json
import math
import re
import time
import urllib.parse
from decimal import Decimal
from pathlib import Path
from workflow_engine import tool

S = {'type': 'string'}
I = {'type': 'integer'}
B = {'type': 'boolean'}
STAGES = ['discovery', 'core', 'interaction', 'specialist', 'resilience', 'review']

def schemas():
    return [
        tool('observe', '重新观察当前页面并截图。', {}),
        tool('inspect', '只读检查。elements 可看位置/样式/焦点；table 看表格；accessibility 为基础启发式；performance 为浏览器计时；media 仅媒体状态。不会运行任意脚本。',
             {'kind': {'type':'string','enum':['elements','table','accessibility','performance','network','media','text','files','file']},
              'selector': S, 'file_id': S}, ['kind']),
        tool('environment', '仅授权测试环境可模拟离线或请求错误；每次扰动后用 reset 恢复。权限/定位限当前目标 origin。',
             {'operation': {'type':'string','enum':['offline','request_failure','reset','permissions','geolocation']},
              'enabled': B, 'path': S, 'status': I, 'permissions': {'type':'array','items':S},
              'latitude': {'type':'number'}, 'longitude': {'type':'number'}}, ['operation']),
        tool('session', '创建隔离会话或同会话标签页、切换、关闭。只在允许的站点上操作；不同账号需要自己按界面登录测试账号。',
             {'operation': {'type':'string','enum':['new','tab','switch','close','list']},
              'name': S, 'locale': S, 'timezone': S, 'touch': B}, ['operation']),
        tool('fixture', '创建有限大小的纯文本/CSV/JSON测试文件，供上传；不可读取电脑任意文件。',
             {'name':S, 'content':S, 'repeat':I}, ['name','content']),
        tool('calculate', '独立精确计算，只接受数字与 + - * / 括号；最多500字符，无函数或变量。',
             {'expression':S}, ['expression']),
        tool('stage', '记录阶段与范围。首次调用提交首次理解，再提供产品要求。阶段只能前进，不允许跳过 core。',
             {'name': {'type':'string','enum':STAGES}, 'note':S}, ['name','note']),
        tool('coverage', '登记维度的适用性/结果/证据。通过必须有可核验检查点；功能不存在要说明查过的入口。一次可提交多项。',
             {'items': {'type':'array','items': {'type':'object','properties': {
                 'dimension':I,'applicability':{'type':'string','enum':['适用','功能不存在','不适用','尚未确认']},
                 'result':{'type':'string','enum':['通过','发现问题','待确认','未测试']},
                 'reason':S,'check_ids':{'type':'array','items':I},'issue_ids':{'type':'array','items':I}},
                 'required':['dimension','applicability','result','reason']}}}, ['items'])]

def decimal_expression(expression):
    if not isinstance(expression,str) or len(expression)>500: raise ValueError('表达式过长')
    tree=ast.parse(expression,mode='eval')
    if len(list(ast.walk(tree)))>100: raise ValueError('表达式过于复杂')
    def evaluate(n):
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):
            return Decimal(ast.get_source_segment(expression,n))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            v=evaluate(n.operand); return v if isinstance(n.op,ast.UAdd) else -v
        if isinstance(n,ast.BinOp):
            a,b=evaluate(n.left),evaluate(n.right)
            if isinstance(n.op,ast.Add):return a+b
            if isinstance(n.op,ast.Sub):return a-b
            if isinstance(n.op,ast.Mult):return a*b
            if isinstance(n.op,ast.Div):return a/b
        raise ValueError('只支持数字、四则运算和括号')
    value=evaluate(tree.body)
    if not value.is_finite() or abs(value.adjusted())>1000:raise ValueError('结果超出范围')
    return {'expression':expression,'result':str(value),'precision':'Decimal 28位有效数字；不代替业务舍入规则'}

def extend_session(Base):
    class ExtendedSession(Base):
        def __init__(self,page,url,output,config=None):
            self.config=config or {}
            self.network_events=[]; self.injected_failure=None
            self.offline=False; self.files={}; self.last_inspection={}
            self.trace=[]; self.stage_index=0; self.stage_events=[]
            self.coverage_rows={}; self.initial_understanding=None
            self.action_failures=[]; self.failures_since_action=False
            super().__init__(page,url,output,config)
            self.pages={'main':page}; self.contexts=[page.context]; self.active_name='main'
            self._watch(page)

        def _watch(self,page):
            page.on('response',lambda r:self._event({'type':'response','status':r.status,'url':self._url(r.url)}))
            page.on('requestfailed',lambda r:self._event({'type':'requestfailed','url':self._url(r.url),'error':r.failure}))
            def popup(p):
                name='popup_'+str(len(self.pages))
                self.pages[name]=p
                p.set_default_timeout(4500)
                p.on('dialog',lambda d:d.accept() if self.accept_dialog else d.dismiss())
            page.on('popup',popup)

        def _url(self,url):
            u=urllib.parse.urlsplit(url)
            return urllib.parse.urlunsplit((u.scheme,u.netloc,u.path,'',''))

        def _event(self,event):
            self.network_events.append(event); self.network_events=self.network_events[-100:]

        def allowed_url(self,value):
            url=urllib.parse.urljoin(self.url,value)
            u=urllib.parse.urlsplit(url)
            if u.scheme not in ('http','https') or u.username or u.password: raise ValueError('仅允许目标 HTTP(S) 网址')
            origins={f'{self.origin[0]}://{self.origin[1]}',*self.config.get('extra_allowed_origins',[])}
            if f'{u.scheme}://{u.netloc}' not in origins:raise ValueError('导航超出允许域名')
            return url

        def capabilities(self):
            return {
                'actions':['click','fill','select','press','scroll','wait','resize','reload','back','forward',
                    'hover','check','uncheck','drag','mouse_click','mouse_drag','tap','wait_for','upload','download'],
                'inspection':['text','elements','table','accessibility (heuristic only)','performance','network','media','files','file'],
                'files':'生成测试文件；上传；下载；解析UTF-8文本/CSV/JSON。PDF/XLSX/音视频内容解析未接入。',
                'sessions':'独立会话、标签页；locale/timezone/touch 在创建会话时指定',
                'fault_injection_enabled':bool(self.config.get('allow_fault_injection',False)),
                'permissions':bool(self.config.get('allow_permissions',False)),
                'browser':self.config.get('browser_engine','chromium'),
                'unavailable':['真机、读屏器、短信邮箱服务、支付模拟服务、任意后端审计、媒体音轨分析、自动事实检索、axe认证、网络限速、时钟快进'],
                'note':'目录中列出的建议不等于可用工具；当前只可调用实际注册工具。'}

        def observe(self):
            info=super().observe()
            info['active_session']=getattr(self,'active_name','main')
            info['network']=self.network_events[-8:]
            info['injected_conditions']={'offline':self.offline,'request_failure':self.injected_failure}
            return info

        def _commit(self,args,start):
            self.actions.append(copy.deepcopy(args))
            observation=self.observe()
            self.trace.append({'action_number':len(self.actions),'session':self.active_name,
                               'elapsed_ms':round((time.perf_counter()-start)*1000),
                               'screenshot':observation['screenshot']})
            return observation

        def act(self,args):
            args=self.prepare_action(args)
            a=args['action']; start=time.perf_counter(); p=self.page
            if a=='fill_form':
                fields=args.get('fields',[])
                if not 1<=len(fields)<=8: raise ValueError('一次填写1至8个同一表单中的输入框。')
                handles=[]
                try:
                    for field in fields:
                        loc=self.locator(field['selector'])
                        handle=loc.element_handle();handles.append(handle)
                        if not loc.is_visible() or not loc.is_editable(): raise ValueError('表单字段不可见或不可编辑')
                        if not handle.evaluate("e=>!!e.closest('form')"): raise ValueError('批量填写仅支持同一form内的字段；其他页面逐个填写')
                        if not handle.evaluate("(e,first)=>e.closest('form')===first.closest('form')",handles[0]):
                            raise ValueError('批量字段不在同一个表单内')
                    for field in fields:
                        result=self.act({'action':'fill','selector':field['selector'],'value':field['value']})
                    return result
                finally:
                    for handle in handles:handle.dispose()
            if a=='_inspect':return self.inspect(args['args'])
            if a=='_environment':return self.environment(args['args'])
            if a=='_session':return self.session_tool(args['args'])
            if a=='_fixture':return self.fixture(args['args'])
            self.accept_dialog=bool(args.get('accept_dialog',False))
            if a=='goto':
                p.goto(self.allowed_url(args.get('url','/')),wait_until='domcontentloaded',timeout=20000)
            elif a in ('reload','back','forward'):
                method={'reload':p.reload,'back':p.go_back,'forward':p.go_forward}[a]
                method(wait_until='domcontentloaded',timeout=20000)
            elif a=='hover':self.locator(args['selector']).hover()
            elif a in ('check','uncheck'):self.locator(args['selector']).set_checked(a=='check')
            elif a=='drag':self.locator(args['selector']).drag_to(self.locator(args['target']))
            elif a in ('mouse_click','mouse_drag','tap'):
                size=p.viewport_size or {'width':1280,'height':900}
                def point(x,y):
                    if not (0<=x<size['width'] and 0<=y<size['height']):raise ValueError('坐标超出视口')
                    return x,y
                x,y=point(args['x'],args['y'])
                if a=='tap':p.touchscreen.tap(x,y)
                elif a=='mouse_click':p.mouse.click(x,y)
                else:
                    x2,y2=point(args['to_x'],args['to_y']);p.mouse.move(x,y);p.mouse.down()
                    try:p.mouse.move(x2,y2,steps=10)
                    finally:p.mouse.up()
            elif a=='wait_for':
                self.page.locator(args['selector']).wait_for(state=args.get('state','visible'),timeout=min(15000,max(100,args.get('timeout_ms',5000))))
            elif a=='upload':
                ids=args.get('file_ids',[])
                if not ids or len(ids)>10:raise ValueError('需提供1至10个已生成文件ID')
                paths=[str(self.files[i]) for i in ids]
                self.locator(args['selector']).set_input_files(paths)
            elif a=='download':
                with p.expect_download(timeout=15000) as event:self.locator(args['selector']).click()
                download=event.value
                dest=self.output/'downloads'/('download_'+str(len(self.files))+Path(download.suggested_filename).suffix[:15])
                dest.parent.mkdir(parents=True,exist_ok=True);download.save_as(str(dest))
                fid='file_'+str(len(self.files)+1);self.files[fid]=dest
            else:
                result=super().act(args)
                self.trace.append({'action_number':len(self.actions),'session':self.active_name,
                                   'elapsed_ms':round((time.perf_counter()-start)*1000),'screenshot':result['screenshot']})
                return result
            p.wait_for_timeout(150)
            result=self._commit(args,start)
            if a=='download':result['download']={'file_id':fid,'name':download.suggested_filename,'bytes':dest.stat().st_size}
            return result

        def fixture(self,args):
            name=args['name']
            if Path(name).name!=name or '/' in name or '\\' in name or name in ('.','..'):raise ValueError('只接受文件名')
            if Path(name).suffix.lower() not in ('.txt','.csv','.json'):raise ValueError('只生成 txt/csv/json 测试文件')
            repeat=max(1,min(1000,int(args.get('repeat',1))))
            raw=args['content'].encode('utf-8')
            if len(raw)*repeat>1024*1024:raise ValueError('测试文件最多1MB')
            fid='file_'+str(len(self.files)+1);dest=self.output/'fixtures'/fid/name
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw*repeat)
            self.files[fid]=dest
            self.actions.append({'action':'_fixture','args':copy.deepcopy(args)})
            return {'file_id':fid,'name':name,'bytes':dest.stat().st_size}

        def inspect(self,args):
            kind=args['kind'];p=self.page;selector=args.get('selector','body')
            if kind=='files':result={k:{'name':v.name,'bytes':v.stat().st_size} for k,v in self.files.items()}
            elif kind=='file':
                f=self.files[args['file_id']]
                if f.stat().st_size>2*1024*1024:raise ValueError('解析上限2MB，请记录未测试')
                if f.suffix.lower() not in ('.txt','.csv','.json'):raise ValueError('此格式解析工具未接入')
                content=f.read_text(encoding='utf-8-sig')
                if f.suffix.lower()=='.json':result={'data':json.loads(content)}
                elif f.suffix.lower()=='.csv':
                    rows=list(csv.reader(io.StringIO(content)));result={'row_count':len(rows),'rows':rows[:100]}
                else:result={'text':content[:20000],'characters':len(content)}
            elif kind=='text':result={'texts':p.locator(selector).all_text_contents()[:100]}
            elif kind=='table':result={'rows':p.locator(selector).locator('tr').evaluate_all('(rows)=>rows.slice(0,100).map(r=>Array.from(r.querySelectorAll("th,td")).map(c=>c.innerText))')}
            elif kind=='elements':result=p.locator(selector).evaluate_all('''els=>els.slice(0,100).map(e=>{
                const r=e.getBoundingClientRect(),s=getComputedStyle(e),c=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                return {tag:e.tagName,id:e.id,text:(e.innerText||'').slice(0,300),role:e.getAttribute('role'),
                ariaLabel:e.getAttribute('aria-label'),focused:document.activeElement===e,disabled:!!e.disabled,
                checked:'checked' in e?e.checked:null,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
                color:s.color,background:s.backgroundColor,fontSize:s.fontSize,overflowX:s.overflowX,
                centerCovered:!!c&&c!==e&&!e.contains(c),scrollWidth:e.scrollWidth,clientWidth:e.clientWidth};})''')
            elif kind=='accessibility':result=p.evaluate('''()=>({scope:'启发式线索，不是完整无障碍认证',
                focused:{tag:document.activeElement?.tagName,id:document.activeElement?.id,role:document.activeElement?.getAttribute('role')},
                imagesWithoutAlt:Array.from(document.images).filter(e=>!e.hasAttribute('alt')).map(e=>({src:e.getAttribute('src')})).slice(0,40),
                unnamedControls:Array.from(document.querySelectorAll('button,input,select,textarea')).filter(e=>e.type!=='hidden'&&!e.getAttribute('aria-label')&&!e.getAttribute('aria-labelledby')&&!e.labels?.length&&!e.innerText&&!['submit','button'].includes(e.type)).map(e=>({tag:e.tagName,id:e.id})).slice(0,40)})''')
            elif kind=='performance':result=p.evaluate('''()=>({scope:'浏览器计时，不包含模型等待；没有性能阈值时仅报告测量',
                navigation:performance.getEntriesByType('navigation').map(n=>({domContentLoadedMs:n.domContentLoadedEventEnd,loadMs:n.loadEventEnd,responseMs:n.responseEnd,transferBytes:n.transferSize})),
                resources:performance.getEntriesByType('resource').slice(-40).map(n=>({name:n.name.split('?')[0],durationMs:n.duration,transferBytes:n.transferSize}))})''')
            elif kind=='network':result={'events':self.network_events[-80:],'scope':'不记录请求体、响应体或凭证；无完整后台状态'}
            elif kind=='media':result=p.locator('audio,video').evaluate_all('''els=>els.map(e=>({tag:e.tagName,currentTime:e.currentTime,duration:Number.isFinite(e.duration)?e.duration:null,paused:e.paused,muted:e.muted,volume:e.volume,readyState:e.readyState,error:e.error?.code||null}))''')
            else:raise ValueError('未知检查类型')
            # Prevent huge tool output; mark truncation instead of claiming completeness.
            raw=json.dumps(result,ensure_ascii=False)
            if len(raw)>22000:result={'truncated':True,'preview':raw[:22000]}
            self.last_inspection={'kind':kind,'result':result}
            self.actions.append({'action':'_inspect','args':copy.deepcopy(args)})
            return self.last_inspection

        def environment(self,args):
            op=args['operation'];context=self.page.context
            if op in ('offline','request_failure') and not self.config.get('allow_fault_injection',False):
                raise ValueError('未启用故障注入；记录未测试或先由用户配置授权测试环境')
            if op in ('permissions','geolocation') and not self.config.get('allow_permissions',False):
                raise ValueError('未启用权限/定位模拟')
            if op=='offline':self.offline=bool(args.get('enabled',True));context.set_offline(self.offline)
            elif op=='request_failure':
                path=args.get('path','')
                if not path.startswith('/') or path=='/':raise ValueError('请指定明确的请求路径，不能拦截全站')
                status=args.get('status',503)
                if status not in (400,401,403,404,408,429,500,502,503,504):raise ValueError('无效的故障状态码')
                self.injected_failure={'path':path,'status':status}
            elif op=='reset':
                for c in self.contexts:c.set_offline(False);c.clear_permissions();c.set_geolocation(None)
                self.offline=False;self.injected_failure=None
            elif op=='permissions':
                permissions=args.get('permissions',[])
                if any(x not in ('geolocation','notifications','clipboard-read','clipboard-write') for x in permissions):raise ValueError('权限类型不在模拟范围')
                context.clear_permissions()
                context.grant_permissions(permissions,origin=f'{self.origin[0]}://{self.origin[1]}')
            elif op=='geolocation':
                lat,lon=args['latitude'],args['longitude']
                if not (-90<=lat<=90 and -180<=lon<=180):raise ValueError('经纬度范围错误')
                context.set_geolocation({'latitude':lat,'longitude':lon})
            else:raise ValueError('未知环境操作')
            self.actions.append({'action':'_environment','args':copy.deepcopy(args)})
            return {'operation':op,'offline':self.offline,'failure':self.injected_failure}

        def session_tool(self,args):
            op=args['operation'];name=args.get('name','')
            if op=='list':return {'active':self.active_name,'sessions':list(self.pages)}
            if op in ('new','tab'):
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}',name) or name in self.pages:raise ValueError('会话名需唯一且为短英文数字')
                if len(self.pages)>=6:raise ValueError('最多6个会话或标签页')
                if op=='new':
                    ctx=self.page.context.browser.new_context(viewport={'width':1280,'height':900},
                        locale=args.get('locale','zh-CN'),timezone_id=args.get('timezone','Asia/Shanghai'),
                        has_touch=bool(args.get('touch',False)),service_workers='block')
                    self.contexts.append(ctx);new=ctx.new_page();self.setup_context(ctx)
                    new.on('dialog',lambda d:d.accept() if self.accept_dialog else d.dismiss())
                    new.on('pageerror',lambda e:self.errors.append(str(e)[:1000]))
                else:new=self.page.context.new_page();new.on('dialog',lambda d:d.accept() if self.accept_dialog else d.dismiss())
                new.set_default_timeout(4500);self.pages[name]=new;self._watch(new)
                self.page=new;self.active_name=name;new.goto(self.url,wait_until='domcontentloaded',timeout=20000)
            elif op=='switch':
                self.page=self.pages[name];self.active_name=name
            elif op=='close':
                if name=='main':raise ValueError('不能关闭主会话')
                self.pages.pop(name).close();self.page=self.pages['main'];self.active_name='main'
            else:raise ValueError('未知会话操作')
            self.actions.append({'action':'_session','args':copy.deepcopy(args)})
            return self.observe()

        def assertion(self,spec):
            if spec['op']!='inspection':return super().assertion(spec)
            try:
                if 'result' not in self.last_inspection:
                    raise ValueError('先调用 inspect(kind="elements", selector="目标")，再检查 path=["0","disabled"]，expected 使用布尔值。')
                value=self.last_inspection['result']
                for key in spec.get('path',[]):value=value[key] if isinstance(value,dict) else value[int(key)]
                expected=spec['expected'];comparator=spec.get('comparator','equal')
                if comparator=='equal':passed=value==expected
                elif comparator=='contains':passed=expected in value
                elif comparator=='less_equal':passed=Decimal(str(value))<=Decimal(str(expected))
                elif comparator=='greater_equal':passed=Decimal(str(value))>=Decimal(str(expected))
                else:raise ValueError('未知比较方式')
                return {'evaluated':True,'passed':bool(passed),'actual':value,'expected':expected,
                        'assertion':spec,'after_action':len(self.actions)}
            except Exception as e:return {'evaluated':False,'passed':False,'error':str(e),
                                         'assertion':spec,'after_action':len(self.actions)}

        def set_stage(self,args):
            index=STAGES.index(args['name'])
            if index<self.stage_index or (self.stage_index==0 and index>1):raise ValueError('阶段不能倒退，也不能跳过核心任务')
            self.stage_index=index;self.stage_events.append(copy.deepcopy(args))
            result={'stage':args['name']}
            if self.initial_understanding is None:
                self.initial_understanding=args['note']
                result['product_requirements']=self.config['requirements'];result['tasks']=self.config['tasks']
            return result

        def set_coverage(self,args):
            pending={}
            for row in args['items']:
                d=row['dimension']
                if not 1<=d<=42:raise ValueError('维度编号需在1至42之间')
                if row['applicability'] not in ('适用','功能不存在','不适用','尚未确认'):raise ValueError('适用性错误')
                if row['result'] not in ('通过','发现问题','待确认','未测试'):raise ValueError('结果错误')
                if not row['reason'].strip():raise ValueError('必须说明依据或未测原因')
                cids=row.get('check_ids',[]);iids=row.get('issue_ids',[])
                if any(not isinstance(i,int) or not 1<=i<=len(self.checks) for i in cids):raise ValueError('检查点编号不存在')
                if any(not isinstance(i,int) or not 1<=i<=len(self.issues) for i in iids):raise ValueError('问题编号不存在')
                if row['result']=='通过' and (row['applicability']!='适用' or not cids or not all(self.checks[i-1]['passed'] for i in cids)):
                    raise ValueError('通过需关联实际通过的检查点，且只限已检查范围')
                if row['result']=='发现问题' and not iids:raise ValueError('发现问题需关联已记录的问题编号')
                if row['applicability'] in ('不适用','功能不存在') and row['result']!='未测试':raise ValueError('不存在/不适用的功能不能记为通过')
                pending[d]=copy.deepcopy(row)
            self.coverage_rows.update(pending)
            return {'recorded':list(pending)}

        def coverage_result(self):
            return [self.coverage_rows.get(d,{'dimension':d,'applicability':'尚未确认',
                'result':'未测试','reason':'本轮未记录检查结论（可能受预算或工具限制）','check_ids':[],'issue_ids':[]}) for d in range(1,43)]

        def restore_environment(self):
            for context in self.contexts:
                try:context.set_offline(False);context.clear_permissions();context.set_geolocation(None)
                except Exception:pass
            self.injected_failure=None;self.offline=False
    return ExtendedSession
