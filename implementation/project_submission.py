"""Local ZIP intake, launch discovery, guided notebook form and complete project export."""
import io, json, os, re, shutil, stat, sys, zipfile
from pathlib import Path, PurePosixPath
import workflow_engine as wf

MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXTRACTED = 200 * 1024 * 1024
EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '__macosx',
            '.aws', '.ssh', '.codex', 'auth.json', 'credentials.json', 'secrets.json'}

def excluded(parts):
    return any(p.lower() in EXCLUDED or p.lower().startswith('.env') or
               p.lower().endswith(('.pem', '.key', '.pyc')) for p in parts)

def extract_zip(content, destination):
    """Validate the entire archive before extraction; reject ambiguous Windows paths."""
    if len(content) > MAX_UPLOAD: raise ValueError('ZIP 超过 50 MB，请去掉依赖目录后重新压缩。')
    destination = Path(destination).resolve()
    if destination.exists(): raise ValueError('解压目录已存在，不能覆盖。')
    seen, entries, total, skipped = set(), [], 0, []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if len(archive.infolist()) > 10000: raise ValueError('ZIP 文件数量超过 10000。')
        for item in archive.infolist():
            name = item.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            if not parts or name.startswith('/') or any(p in ('.','..') or ':' in p or
                p.endswith((' ', '.')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',p,re.I)
                for p in parts): raise ValueError('ZIP 含不安全路径：' + name)
            if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                raise ValueError('不支持链接文件或加密 ZIP。')
            key = '/'.join(parts).casefold()
            if key in seen: raise ValueError('ZIP 含重复或大小写冲突路径：' + name)
            seen.add(key)
            total += item.file_size
            if total > MAX_EXTRACTED: raise ValueError('解压后超过 200 MB。')
            if excluded(parts): skipped.append(name); continue
            if not item.is_dir(): entries.append((item, parts))
        if not entries: raise ValueError('压缩包中没有可用项目文件。')
        destination.mkdir(parents=True)
        written = 0
        for item, parts in entries:
            target = destination.joinpath(*parts)
            if destination not in target.resolve().parents: raise ValueError('非法解压路径。')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as src, target.open('wb') as dst:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_EXTRACTED: raise ValueError('实际解压大小超过限制。')
                    dst.write(chunk)
    # GitHub downloads normally wrap the whole project in one directory.
    root = destination
    while True:
        children = list(root.iterdir())
        if len(children) == 1 and children[0].is_dir(): root = children[0]
        else: break
    return root, skipped

def detect_launch(root):
    root = Path(root)
    launch = {'setup_command': [], 'entry_path': '/', 'launch_kind': 'custom'}
    manifest = root / 'package.json'
    if manifest.is_file():
        pkg = json.loads(manifest.read_text(encoding='utf-8-sig'))
        scripts = pkg.get('scripts', {})
        if pkg.get('dependencies') or pkg.get('devDependencies'):
            raise ValueError('该项目声明了 npm 依赖。请在高级启动设置中填写准备命令与启动命令。')
        # Only recognize a direct node entry, never blindly run install/start scripts.
        for name in ('start','dev'):
            m = re.fullmatch(r'node\s+([\w./-]+\.(?:js|mjs|cjs))', scripts.get(name,''))
            if m:
                entry = root / m[1]
                if entry.is_file() and 'process.env.PORT' in entry.read_text(encoding='utf-8'):
                    if not shutil.which('node'): raise ValueError('此项目需要 Node.js，本机尚未安装。')
                    return dict(launch, start_command=['node',m[1]], launch_kind='Node（读取 PORT）')
    servers = [root/n for n in ('server.mjs','server.js','server.cjs','app.py','server.py','main.py') if (root/n).is_file()]
    if len(servers) == 1:
        entry = servers[0]; text = entry.read_text(encoding='utf-8-sig')
        if entry.suffix == '.py':
            if re.search(r'(?:getenv|environ.get)\([\s]*[\'\"]PORT[\'\"]|environ\[[\'\"]PORT[\'\"]\]', text):
                return dict(launch, start_command=['{python}',entry.name], launch_kind='Python（读取 PORT）')
        elif 'process.env.PORT' in text:
            if not shutil.which('node'): raise ValueError('此项目需要 Node.js，本机尚未安装。')
            return dict(launch, start_command=['node',entry.name], launch_kind='Node（读取 PORT）')
        raise ValueError('发现后端入口，但无法确认端口设置。请在高级设置填启动命令；若使用固定端口，也填写该端口。')
    if servers: raise ValueError('发现多个后端入口，请在高级设置指定启动命令。')
    if manifest.is_file(): raise ValueError('无法确认 package.json 的启动方式，请填写高级启动设置。')
    htmls = list(root.glob('*.html'))
    entry = root/'index.html' if (root/'index.html').is_file() else (htmls[0] if len(htmls)==1 else None)
    if entry:
        # Uses Python standard library only. Assets and subdirectories remain intact.
        return dict(launch, start_command=['{python}','-m','http.server','{port}','--bind','127.0.0.1'],
                    entry_path='/' if entry.name=='index.html' else '/'+entry.name,
                    launch_kind='静态 HTML/CSS/JavaScript（无需安装项目依赖）')
    raise ValueError('没有识别到网站入口，请填写高级启动命令和首页路径。')

def prepare_submission(content, filename, runtime, fields, output_root, command=None, setup=None,
                       fixed_port=None, entry_path='/'):
    if not filename.lower().endswith('.zip'): raise ValueError('请上传 ZIP 文件。')
    for key in ('project_name','description','target_users'):
        if not fields.get(key,'').strip(): raise ValueError('请填写项目名称、用途和目标用户。')
    tasks = fields.get('core_tasks', [])
    if not tasks or any(not t['task'].strip() or not t['success'].strip() for t in tasks):
        raise ValueError('至少填写一项核心任务和对应成功标准。')
    run = wf.make_run(output_root)
    (run/'uploaded.zip').write_bytes(content)
    try:
        source, skipped = extract_zip(content, run/'original')
        if command:
            if not isinstance(command,list) or not all(isinstance(x,str) for x in command):
                raise ValueError('启动命令必须是字符串列表。')
            launch = {'start_command':command,'setup_command':setup or [], 'entry_path':entry_path,
                      'launch_kind':'用户指定启动方式'}
        else:
            launch = detect_launch(source)
            if setup: launch['setup_command'] = setup
            if entry_path != '/': launch['entry_path'] = entry_path
        if not entry_path.startswith('/') or '://' in entry_path: raise ValueError('首页路径需要以 / 开头。')
        if fixed_port is not None and not 1024 <= fixed_port <= 65535: raise ValueError('固定端口需为 1024～65535。')
        if setup and (not isinstance(setup,list) or not all(isinstance(x,str) for x in setup)):
            raise ValueError('准备命令必须是字符串列表。')
        lines = ['项目：'+fields['project_name'],'用途：'+fields['description'],'目标用户：'+fields['target_users']]
        lines += [f'任务 {i+1}：{t["task"]}；成功标准：{t["success"]}' for i,t in enumerate(tasks)]
        for key,label in [('known_limitations','已知问题'),('constraints','产品约束'),('test_scope','测试范围'),
                          ('access_notes','测试账号与数据说明'),('validation_notes','验收补充')]:
            lines.append(label+'：'+fields.get(key,'无补充'))
        lines.append('已知未完成功能应单独标注，不当成新发现；以本次产品约束和成功标准为准。')
        config = {**runtime, **launch, 'mode':'source','source_path':str(source.resolve()),
                  'target_url':'','submission':fields,'requirements':'\n'.join(lines),
                  'tasks':[t['task']+'；成功标准：'+t['success'] for t in tasks],
                  'repair_enabled':fields.get('service')=='repair','fixed_port':fixed_port,
                  'submission_run':str(run.resolve()),'skipped_upload_files':skipped}
        wf.dump(run/'config.json',config)
        return config,run
    except Exception as e:
        wf.dump(run/'提交未完成.json',{'reason':str(e)})
        raise

def show_submission(runtime, output_root, namespace):
    import ipywidgets as w
    from IPython.display import display
    def clear_previous():
        for name in ('CONFIG','RUN','PREVIEW','BASELINE','ISSUES','REPAIR','VERIFICATION','REPORT','RESULTS'):
            namespace.pop(name,None)
    clear_previous()
    upload = w.FileUpload(accept='.zip',multiple=False,description='选择项目 ZIP')
    style={'description_width':'150px'}; layout=w.Layout(width='95%')
    def field(label,value='',area=False):
        return (w.Textarea if area else w.Text)(description=label,value=value,style=style,layout=layout)
    boxes={
        'project_name':field('项目名称 *'), 'description':field('用途介绍 *',area=True),
        'target_users':field('目标用户 *'),
        'known_limitations':field('已知问题/未完成功能','无补充',True),
        'constraints':field('设计与操作限制','只操作测试数据；保留既有设计',True),
        'test_scope':field('重点测试范围','桌面核心流程，兼顾新用户与边界情况',True),
        'access_notes':field('账号与数据说明','无需登录，无额外测试数据',True),
        'validation_notes':field('额外验收要求','无补充',True)}
    task_rows=[(field(f'任务 {i+1}'+(' *' if i==0 else '')),field('对应成功标准'+(' *' if i==0 else ''))) for i in range(5)]
    service=w.Dropdown(options=[('完整流程：体验、修复、复测','repair'),('仅体验并出报告','report')],
                       description='服务方式',style=style,layout=layout)
    trusted=w.Checkbox(value=False,description='我确认这是获准测试的项目，允许在本机启动其代码并操作测试数据。',indent=False)
    command=field('启动命令（选填）',''); setup=field('准备命令（选填）','')
    port=field('固定端口（选填）',''); entry=field('首页路径','/')
    advanced=w.Accordion(children=[w.VBox([w.HTML('自动识别失败时再填写。命令格式如 <code>["python", "app.py"]</code>；支持 {python}、{port} 占位符。'),command,setup,port,entry])],selected_index=None)
    advanced.set_title(0,'高级启动设置：普通静态网页无需填写')
    output=w.Output();button=w.Button(description='提交 ZIP 并准备项目',button_style='primary')
    def submit(_):
        clear_previous()
        with output:
            output.clear_output()
            try:
                if not trusted.value: raise ValueError('请先确认项目允许在本机运行。')
                if not upload.value: raise ValueError('请先点击“选择项目 ZIP”上传文件。')
                item=next(iter(upload.value.values())) if isinstance(upload.value,dict) else upload.value[0]
                fields={k:v.value.strip() for k,v in boxes.items()}
                fields['core_tasks']=[{'task':t.value.strip(),'success':s.value.strip()} for t,s in task_rows if t.value.strip() or s.value.strip()]
                fields['service']=service.value
                config,run=prepare_submission(bytes(item['content']),item.get('name',item.get('metadata',{}).get('name','project.zip')),
                    runtime,fields,output_root,json.loads(command.value) if command.value.strip() else None,
                    json.loads(setup.value) if setup.value.strip() else None,
                    int(port.value) if port.value.strip() else None,entry.value.strip())
                namespace['CONFIG'],namespace['RUN']=config,run
                print('提交成功！识别结果：'+config['launch_kind'])
                print('原始源码：'+config['source_path'])
                print('本轮输出：'+str(run))
                print('跳过依赖/凭证等文件：'+str(len(config['skipped_upload_files'])))
                print('请继续第 5 步。第 7 步将自动启动网站并显示截图。')
            except Exception as e: print('尚未提交成功：'+str(e)+'\n修改后可以再次点击提交；请勿继续后面的步骤。')
    button.on_click(submit)
    display(w.VBox([w.HTML('<h3>上传 demo 与填写项目信息</h3><p>ZIP 最大 50 MB。不要上传真实密码和私人数据。提交后等“提交成功”再运行下一格。</p>'),upload,
        *list(boxes.values())[:3],w.HTML('<b>核心任务：至少一项，最多五项</b>'),
        *[w.VBox(list(pair)) for pair in task_rows],*list(boxes.values())[3:],service,advanced,trusted,button,output]))
    return upload  # keep widget reference alive

def export_project(run, config, repair, verification):
    run=Path(run);dest=run/'交付结果';dest.mkdir(exist_ok=True)
    changed=repair.get('status')=='candidate' and bool(repair.get('changes'))
    verified=changed and verification.get('status')=='verified'
    name='修复后的项目.zip' if verified else ('待验证的修改候选.zip' if changed else '原始项目_未修改.zip')
    for previous in ('修复后的项目.zip','待验证的修改候选.zip','原始项目_未修改.zip'):
        if previous != name: (dest/previous).unlink(missing_ok=True)
    source=Path(repair['candidate']) if changed else Path(config['source_path'])
    # Build archive from original manifest, overlay changed source, retain images/fonts/build assets.
    original=Path(config['source_path'])
    with zipfile.ZipFile(dest/name,'w',zipfile.ZIP_DEFLATED) as z:
        for p in original.rglob('*'):
            if not p.is_file() or p.is_symlink(): continue
            rel=p.relative_to(original)
            if excluded(rel.parts):continue
            actual=source/rel if (source/rel).is_file() else p
            z.write(actual,rel.as_posix())
    for filename in ('体验与修复报告.html','修复问题单.json','changes.diff','verification.json','summary.json','api_usage_summary.json'):
        if (run/filename).is_file():shutil.copy2(run/filename,dest/filename)
    notes=['项目：'+config['submission']['project_name'],
           '状态：'+('所选修复通过已有检查，不代表整个产品无缺陷' if verified else
                    '存在待验证修改，请查看报告' if changed else '未产生修复修改，交付原始项目'),
           '启动识别：'+config['launch_kind'],'启动命令：'+json.dumps(config['start_command'],ensure_ascii=False),
           '准备命令：'+json.dumps(config.get('setup_command',[]),ensure_ascii=False),
           '命令中的 {python} 表示本机 Python；{port} 可替换为可用端口。',
           '服务读取 PORT 时，请设置 PORT；数据目录请使用测试环境。',
           '修改文件：'+', '.join(c['path'] for c in repair.get('changes',[])),
           '复测说明：'+verification.get('reason','未执行'),
           '上传时跳过的文件：'+json.dumps(config.get('skipped_upload_files',[]),ensure_ascii=False)]
    (dest/'修改与运行说明.txt').write_text('\n'.join(notes),encoding='utf-8')
    return dest
