"""Demo Clinic: model calls, isolated runs, repairs, reports. No secrets on disk."""
from __future__ import annotations
import base64, copy, difflib, html, json, os, shutil, socket, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IGNORE = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.env',
          'auth.json', 'secrets.json', 'credentials.json',
          '.codex', '.aws', '.ssh', '.idea', 'runs', 'outputs'}
EXTENSIONS = {'.py', '.js', '.mjs', '.cjs', '.html', '.css', '.json', '.ts', '.tsx', '.jsx', '.vue', '.svelte'}

def dump(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def usage_summary(run_dir, config):
    """All logged calls in this run, including connection checks and repeated batches."""
    rows = {role: {'model': config.get(role + '_model', ''), 'models': [],
        'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
        'missing_usage_responses': 0, 'errors': 0}
        for role in ('analysis', 'test', 'fix')}
    for path in Path(run_dir).rglob('*usage.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try: record = json.loads(line)
            except ValueError: continue
            row = rows.get(record.get('role'))
            if row is None: continue
            model = record.get('model', '')
            if model and model not in row['models']: row['models'].append(model)
            if record.get('status') != 'response':
                row['errors'] += 1
                continue
            row['requests'] += 1
            usage = record.get('usage') or {}
            if not all(k in usage for k in ('prompt_tokens', 'completion_tokens')):
                row['missing_usage_responses'] += 1
            for k in ('prompt_tokens', 'completion_tokens'):
                row[k] += usage.get(k, 0) or 0
    for row in rows.values(): row['total_tokens'] = row['prompt_tokens'] + row['completion_tokens']
    dump(Path(run_dir) / 'api_usage_summary.json', rows)
    return rows


def show_usage(run_dir, config):
    rows = usage_summary(run_dir, config)
    print('本轮累计 API 用量（含连接检查和本目录内的重复运行）：')
    for role, label in [('analysis','分析'), ('test','体验'), ('fix','修复')]:
        row = rows[role]
        print(f"{label}：响应 {row['requests']} 次；输入 {row['prompt_tokens']}；输出 {row['completion_tokens']}；合计 {row['total_tokens']} tokens")
        if row['missing_usage_responses']: print('  部分响应未提供完整用量，合计可能偏低。')
    print('统计依据为 API 返回的用量；失败请求及重试的实际账单以平台为准。')
    return rows


def say(message):
    print(time.strftime('%H:%M:%S'), message, flush=True)

def redact(text):
    text = str(text)
    for name in ('DEMO_ANALYSIS_KEY', 'DEMO_TEST_KEY', 'DEMO_FIX_KEY'):
        key = os.environ.get(name, '')
        if key: text = text.replace(key, '[密钥已隐藏]')
    return text

class ModelClient:
    """Role-specific OpenAI-compatible endpoints and in-memory credentials."""
    def __init__(self, config, role):
        if role not in ('analysis', 'test', 'fix'): raise ValueError('模型角色必须为 analysis、test 或 fix')
        self.config, self.role = config, role
        self.provider = config.get(role + '_provider', config.get('provider', 'openai_compatible'))
        self.base_url = config.get(role + '_base_url', config.get('base_url', ''))
        self.model = config[role + '_model']
        if not self.base_url: raise ValueError(f'请配置 {role}_base_url')
        self.key = os.environ.get({'analysis':'DEMO_ANALYSIS_KEY','test':'DEMO_TEST_KEY','fix':'DEMO_FIX_KEY'}[role], '')
        if not self.key: raise RuntimeError('请先运行 Notebook 的密钥输入单元格。')
        self.usage = {'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0}

    def request(self, messages, tools=None):
        model, provider = self.model, self.provider
        started = time.monotonic()
        text_chars, image_count = 0, 0
        for message in messages:
            content = message.get('content', '')
            if isinstance(content, list):
                text_chars += sum(len(part.get('text','')) for part in content)
                image_count += sum(part.get('type') == 'image_url' for part in content)
            elif isinstance(content, str): text_chars += len(content)
            text_chars += len(message.get('reasoning_content') or '')
            text_chars += len(json.dumps(message.get('tool_calls', []), ensure_ascii=False))
        def audit(status, usage=None):
            path = self.config.get('usage_log_path')
            if not path: return
            record = {'role':self.role,'model':model,'status':status,
                'seconds':round(time.monotonic()-started,2),'message_count':len(messages),
                'text_characters':text_chars,'tool_schema_characters':len(json.dumps(tools or [],ensure_ascii=False)),
                'image_count':image_count,'usage':usage or {}}
            try:
                with open(path,'a',encoding='utf-8') as stream: stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            except OSError: pass  # Telemetry cannot turn a successful model call into a failure.
        # Preserve reasoning returned by thinking models across tool-call turns.
        clean = copy.deepcopy(messages)
        if provider not in ('deepseek', 'kimi', 'glm'):
            for m in clean: m.pop('reasoning_content', None)
        payload = {'model': model, 'messages': clean,
                   'max_tokens': self.config.get(self.role + '_max_output_tokens', 3000 if self.role == 'test' else 6500)}
        if tools: payload['tools'] = tools
        if provider == 'deepseek':
            payload['thinking'] = {'type': 'disabled' if self.role == 'test' else 'enabled'}
            if self.role != 'test': payload['reasoning_effort'] = 'high'
        # Kimi/GLM use provider defaults; do not forward DeepSeek-only options.
        url = self.base_url.rstrip('/') + '/chat/completions'
        request = urllib.request.Request(url, json.dumps(payload).encode(),
                  {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.config.get('api_timeout', 120)) as r:
                    result = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt); continue
                detail = redact(e.read().decode(errors='replace'))[:500]
                audit('http_error')
                raise RuntimeError(f'{model} 接口返回 HTTP {e.code}: {detail}') from None
            except (urllib.error.URLError, TimeoutError) as e:
                audit('connection_error')
                raise RuntimeError('模型连接失败：' + redact(e)) from None
        audit('response', result.get('usage'))
        self.usage['requests'] += 1
        for name in ('prompt_tokens', 'completion_tokens'):
            self.usage[name] += result.get('usage', {}).get(name, 0)
        choice = result['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise RuntimeError('模型输出被长度上限截断；本轮停止，避免执行不完整的工具调用。')
        m = choice['message']
        return {k: m[k] for k in ('role', 'content', 'tool_calls', 'reasoning_content') if k in m}

def tool(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(required)}}}

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]

def copy_project(source, dest):
    source, dest = Path(source).resolve(), Path(dest).resolve()
    if dest.exists(): raise FileExistsError('不会覆盖已有工作副本：' + str(dest))
    if source == dest or source in dest.parents:
        raise ValueError('工作目录不能放进源项目内部，请使用项目外的 RUNS 目录。')
    def ignore(folder, names):
        return [n for n in names if n in IGNORE or n.startswith('.env') or
                n.endswith(('.log', '.pem', '.key', '.ipynb', '.zip')) or
                (Path(folder) / n).is_symlink()]
    shutil.copytree(source, dest, ignore=ignore)

def target_config(config, source, folder):
    """Create a fresh app copy and a fresh data directory for every experiment."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=False)
    if config['mode'] == 'url':
        return {'folder': str(folder), 'url': config['target_url'], 'project': None,
                'command': None, 'env': {}}
    project = folder / 'project'
    copy_project(source, project)
    port = config.get('fixed_port') or free_port()
    if config.get('fixed_port'):
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1', int(port)))
            except OSError: raise RuntimeError(f'端口 {port} 已被占用，请关闭原 demo 后重试。不会连接其他进程冒充本次项目。')
    command = [str(x).replace('{python}', sys.executable).replace('{port}', str(port))
               for x in config['start_command']]
    setup = [str(x).replace('{python}', sys.executable).replace('{port}', str(port))
             for x in config.get('setup_command', [])]
    return {'folder': str(folder), 'project': str(project),
            'url': f'http://127.0.0.1:{port}' + urllib.parse.quote(config.get('entry_path', '/'), safe='/?=&'), 'command': command,
            'setup_command': setup,
                'env': {'PORT': str(port),
                    'DEMO_DATA_DIR': str(folder / 'test_data')}}

class RunningTarget:
    def __init__(self, target): self.target, self.process, self.log = target, None, None
    def __enter__(self):
        if not self.target['command']: return self.target['url']
        self.log = open(Path(self.target['folder']) / 'server.log', 'w', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in ('DEMO_ANALYSIS_KEY', 'DEMO_TEST_KEY', 'DEMO_FIX_KEY')}
        env.update(self.target['env'])
        try:
            if self.target.get('setup_command'):
                say('准备副本依赖：执行你配置的固定准备命令')
                subprocess.run(self.target['setup_command'], cwd=self.target['project'], env=env,
                               stdout=self.log, stderr=subprocess.STDOUT, check=True, timeout=300,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.process = subprocess.Popen(self.target['command'], cwd=self.target['project'],
                env=env, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            last_error = ''
            for _ in range(100):
                if self.process.poll() is not None: break
                try:
                    with urllib.request.urlopen(self.target['url'], timeout=.7) as r:
                        if r.status == 200: return self.target['url']
                except (OSError, urllib.error.URLError) as e: last_error = redact(e)
                time.sleep(.1)
            raise RuntimeError('Demo 启动失败，请查看本次运行的 server.log。' +
                               f'进程状态：{self.process.poll()}；连接信息：{last_error}')
        except BaseException:
            self.__exit__(None, None, None); raise
    def __exit__(self, *args):
        if self.process and self.process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
        if self.log: self.log.close()

def worker(job, folder, config, credentials=True):
    """Separate process avoids Playwright/Jupyter asyncio conflicts on Windows."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    dump(folder / 'job.json', {'config': config, **job})
    env = os.environ.copy(); env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    env.pop('DEMO_ANALYSIS_KEY', None)
    if not credentials:
        env.pop('DEMO_TEST_KEY', None); env.pop('DEMO_FIX_KEY', None)
    else:
        env.pop('DEMO_FIX_KEY', None)
    process = subprocess.Popen([sys.executable, str(ROOT / 'browser_worker.py'), str(folder / 'job.json')],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        for line in process.stdout: print(redact(line.rstrip()), flush=True)
        code = process.wait()
    except BaseException:
        process.terminate(); process.wait(timeout=10); raise
    finally:
        process.stdout.close()
    if code or not (folder / 'result.json').exists():
        raise RuntimeError('浏览器阶段未完成。请检查上面的错误信息和依赖安装。')
    result = load(folder / 'result.json')
    if result.get('fatal_error'): raise RuntimeError(result['fatal_error'])
    return result

def run_baseline(config, run_dir):
    if config.get('layered_tasks', True):
        import task_queue
        return task_queue.run(config, run_dir)
    run_dir = Path(run_dir); results = []
    # One rigorous examiner; phases and dimensions replace personality roles.
    config = dict(config)
    prompt = Path(config.get('prompt_path') or ROOT / '体验Agent检查维度.md')
    if not prompt.is_file(): raise FileNotFoundError('未找到体验提示词：' + str(prompt))
    snapshot = run_dir / '体验规范_本轮快照.md'
    snapshot.write_bytes(prompt.read_bytes())
    config['prompt_path'] = str(snapshot.resolve())
    for persona in [{'id':'comprehensive','name':'客观严谨的多维度体验'}]:
        folder = run_dir / ('baseline_' + persona['id'])
        target = target_config(config, config.get('source_path'), folder)
        say('开始体验：' + persona['name'])
        with RunningTarget(target) as url:
            result = worker({'operation': 'test', 'url': url, 'persona': persona,
                             'output': str(folder)}, folder, config)
        result['persona'] = persona
        dump(folder / 'result.json', result); results.append(result)
    dump(run_dir / 'baseline.json', results)
    return results

def replay(config, run_dir, source, actions, checks, label):
    folder = Path(run_dir) / f"{label}_{uuid.uuid4().hex}"
    target = target_config(config, source, folder)
    with RunningTarget(target) as url:
        return worker({'operation': 'replay', 'url': url, 'actions': actions,
                       'checks': checks, 'output': target['folder']},
                      target['folder'], config, credentials=False)

def reproduce_issues(config, run_dir, results):
    issues = []
    for r in results:
        for issue in r.get('issues', []):
            issue['persona'] = r['persona']['name']
            issue['id'] = 'ISSUE-' + str(len(issues) + 1).zfill(3)
            if issue['kind'] != 'bug':
                issue['status'] = '体验建议' if issue['kind']=='usability' else '待确认：缺少可自动复现的证据'
                issues.append(issue); continue
            if config['mode'] == 'url':
                issue['status'] = '待复现：仅网址模式不能重置数据'
            else:
                result = replay(config, run_dir, config['source_path'], issue['actions'],
                    [{'after_action': len(issue['actions']), 'assertion': issue['assertion']}],
                    'reproduce_' + issue['id'])
                outcome = result['checks'][-1] if result.get('checks') else {}
                reproduced = result.get('actions_ok') and outcome.get('evaluated') and not outcome.get('passed')
                issue['status'] = '已复现（按所填预期）' if reproduced else '未能复现'
                issue['reproduction'] = result
            issues.append(issue)
    dump(Path(run_dir) / 'issues.json', issues)
    return issues

def safe_file(root, name):
    root = Path(root).resolve(); p = (root / name).resolve()
    if root not in p.parents or any(x in IGNORE or x.startswith('.env') for x in p.relative_to(root).parts):
        raise ValueError('路径超出允许修改的源码范围。')
    if p.suffix not in EXTENSIONS or not p.is_file() or p.is_symlink():
        raise ValueError('只允许读取或修改已有的代码、样式和配置文件。')
    if p.stat().st_size > 200_000: raise ValueError('文件过大，请先拆分源码。')
    return p

def source_files(root):
    root = Path(root)
    return [str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*')
            if p.is_file() and p.suffix in EXTENSIONS and
            not any(x in IGNORE or x.startswith('.env') for x in p.relative_to(root).parts)
            and not p.is_symlink()][:150]

def syntax_check(root):
    results = []
    for name in source_files(root):
        suffix = Path(name).suffix
        command = [sys.executable, '-m', 'py_compile', name] if suffix == '.py' else (
                  ['node', '--check', name] if suffix in ('.js', '.mjs', '.cjs') and shutil.which('node') else None)
        if command:
            r = subprocess.run(command, cwd=root, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=30)
            results.append({'file': name, 'passed': r.returncode == 0,
                            'output': redact(r.stdout + r.stderr)[:3000]})
    return {'passed': all(r['passed'] for r in results), 'checks': results,
            'scope': 'Python/JavaScript 语法检查；不等同完整测试或 TypeScript 编译'}

def repairable(config, issue):
    return bool(config.get('repair_enabled', False) and config['mode'] == 'source'
                and issue.get('kind') == 'bug' and issue.get('status') == '已复现（按所填预期）')


def repair(config, run_dir, issues):
    run_dir = Path(run_dir)
    # Refresh canonical cards; never rely on stale model-generated eligibility flags.
    report_path = run_dir / '修复问题单.json'
    report = load(report_path) if report_path.exists() else {}
    report.update(requirements=config['requirements'], mode=config['mode'],
                  automatic_repair_enabled=bool(config.get('repair_enabled', False)),
                  issues=[dict(i, eligible_for_automatic_repair=repairable(config, i)) for i in issues])
    dump(report_path, report)
    if not any(repairable(config, i) for i in issues):
        return {'status': 'skipped', 'reason': '未启用修复、只有网址，或没有已复现的功能故障。'}
    cards = {i['id']: i for i in report['issues']}
    if len(cards) != len(issues): raise ValueError('问题编号重复。')
    prompt_path = Path(config.get('repair_prompt_path') or ROOT / '修复Agent执行规范.md')
    prompt = prompt_path.read_text(encoding='utf-8')
    (run_dir / '修复规范_本轮快照.md').write_text(prompt, encoding='utf-8')
    candidate = run_dir / 'candidate'
    copy_project(config['source_path'], candidate)
    originals = {n: safe_file(candidate, n).read_text(encoding='utf-8') for n in source_files(candidate)}
    client = ModelClient(dict(config, usage_log_path=str(run_dir/'model_usage.jsonl')), 'fix')
    tools = [
        tool('read_issue', '按编号读取一张完整问题卡片；当前问题结束后才能读取下一张。',
             {'issue_id': {'type': 'string'}}, ['issue_id']),
        tool('search_code', '在允许的源码中搜索字面文本，最多返回30个匹配行。',
             {'query': {'type': 'string'}}, ['query']),
        tool('read_file', '读取候选副本中的源码。', {'path': {'type': 'string'}}, ['path']),
        tool('assess_issue', '记录当前问题的修复/暂缓判断；修改前必须提交依据。',
             {'decision': {'type': 'string', 'enum': ['repair', 'defer']},
              'reason': {'type': 'string'}, 'root_cause_evidence': {'type': 'string'},
              'files': {'type': 'array', 'items': {'type': 'string'}}, 'plan': {'type': 'string'}},
             ['decision', 'reason', 'root_cause_evidence', 'files', 'plan']),
        tool('replace_text', '仅修改当前问题：精确替换一次已经读取的源码文本。',
             {k: {'type': 'string'} for k in ('path', 'old', 'new')}, ['path', 'old', 'new']),
        tool('check_syntax', '执行固定语法检查，不代表功能验证。', {}),
        tool('finish_issue', '保存当前问题结果；暂缓将撤回本问题修改。',
             {'status': {'type': 'string', 'enum': ['candidate', 'deferred']},
              'summary': {'type': 'string'}, 'uncertainties': {'type': 'string'}},
             ['status', 'summary', 'uncertainties']),
        tool('finish_repair', '结束本轮修复，成功与否由后续浏览器复测判断。',
             {'summary': {'type': 'string'}}, ['summary'])]
    limit = max(1, int(config.get('max_repairs', 1)))
    results, log, active, before_issue, read_files = [], [], None, {}, set()
    assessment, syntax, summary, finished, model_error = None, None, '', False, None

    def fresh_messages():
        return [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps({
            'report': report_path.name, 'requirements': report['requirements'],
            'issue_index': [{k: i.get(k) for k in ('id', 'title', 'kind', 'severity', 'status',
                             'eligible_for_automatic_repair')} for i in cards.values()],
            'source_files': list(originals), 'max_issues_to_process': limit,
            'completed': results}, ensure_ascii=False)}]

    def changes_from(before):
        return [{'path': n, 'diff': ''.join(difflib.unified_diff(
            text.splitlines(True), (candidate / n).read_text(encoding='utf-8').splitlines(True),
            fromfile='before/' + n, tofile='after/' + n))}
            for n, text in before.items() if (candidate / n).read_text(encoding='utf-8') != text]

    def save_result():
        result = {'status': 'candidate' if any(r['status'] == 'candidate' for r in results) else 'skipped',
                  'reason': '没有已提交的候选修改' if not any(r['status'] == 'candidate' for r in results) else '',
                  'selected_ids': [r['id'] for r in results if r['status'] == 'candidate'],
                  'candidate': str(candidate), 'finished': finished, 'summary': summary,
                  'issue_results': results, 'active_issue': active, 'changes': changes_from(originals),
                  'usage': client.usage, 'log': log, 'model_error': model_error}
        dump(run_dir / 'repair.json', result)
        return result

    messages = fresh_messages()
    try:
        for turn in range(config.get('max_fix_turns', 14)):
            say(f'修复 agent：第 {turn + 1} 轮；已处理 {len(results)}/{limit} 张卡片')
            message = client.request(messages, tools); messages.append(message)
            calls = message.get('tool_calls') or []
            if not calls:
                messages.append({'role': 'user', 'content': '请按规范调用工具；先读取问题卡片。'}); continue
            reset = False
            for call in calls:
                name = call['function']['name']
                try:
                    if reset or finished: raise ValueError('当前问题已结束，请下一轮再调用工具。')
                    args = json.loads(call['function']['arguments'])
                    if name == 'read_issue':
                        if active: raise ValueError('请先 finish_issue 结束当前问题。')
                        if len(results) >= limit: raise ValueError('已达到卡片处理上限，请 finish_repair。')
                        key = args['issue_id']
                        if key not in cards or any(r['id'] == key for r in results):
                            raise ValueError('问题不存在或已处理。')
                        active = key
                        before_issue = {n: (candidate / n).read_text(encoding='utf-8') for n in originals}
                        read_files, assessment, syntax = set(), None, None
                        answer = cards[key]
                    elif name == 'finish_repair':
                        if active: raise ValueError('请先结束当前问题。')
                        summary, finished = str(args['summary']), True
                        answer = {'accepted': True}
                    else:
                        if not active: raise ValueError('先用 read_issue 读取一张卡片。')
                        if name == 'search_code':
                            query = args['query']
                            if not isinstance(query, str) or not query.strip(): raise ValueError('搜索词不能为空。')
                            matches = []
                            for n in originals:
                                for number, line in enumerate((candidate / n).read_text(encoding='utf-8').splitlines(), 1):
                                    if query.casefold() in line.casefold():
                                        matches.append({'path': n, 'line': number, 'text': line[:500]})
                                        if len(matches) >= 30: break
                                if len(matches) >= 30: break
                            answer = {'matches': matches, 'limit': 30}
                        elif name == 'read_file':
                            p = safe_file(candidate, args['path']); n = p.relative_to(candidate.resolve()).as_posix()
                            if n not in originals: raise ValueError('文件不在本轮源码清单中。')
                            answer = p.read_text(encoding='utf-8'); read_files.add(n)
                        elif name == 'assess_issue':
                            if args['decision'] not in ('repair', 'defer'): raise ValueError('判断无效。')
                            if not str(args['reason']).strip(): raise ValueError('需要判断理由。')
                            if args['decision'] == 'repair':
                                if not cards[active]['eligible_for_automatic_repair']: raise ValueError('此问题不允许自动修改。')
                                if not args['files'] or not set(args['files']).issubset(read_files):
                                    raise ValueError('必须先读取列出的相关源码。')
                                if not str(args['root_cause_evidence']).strip() or not str(args['plan']).strip():
                                    raise ValueError('需要定位依据与最小修改方案。')
                            assessment = args; answer = {'accepted': True}
                        elif name == 'replace_text':
                            if not assessment or assessment['decision'] != 'repair': raise ValueError('先提交可修复判断。')
                            p = safe_file(candidate, args['path']); n = p.relative_to(candidate.resolve()).as_posix()
                            if n not in read_files or n not in assessment['files']: raise ValueError('先读取文件并纳入修改方案。')
                            before = p.read_text(encoding='utf-8')
                            if not args['old'] or before.count(args['old']) != 1: raise ValueError('旧文本必须精确出现一次。')
                            if len(args['new']) > 60000: raise ValueError('单次修改过大。')
                            p.write_text(before.replace(args['old'], args['new'], 1), encoding='utf-8')
                            syntax = None; answer = {'changed': n}
                        elif name == 'check_syntax':
                            syntax = syntax_check(candidate); answer = syntax
                        elif name == 'finish_issue':
                            state = args['status']
                            if state not in ('candidate', 'deferred') or not assessment: raise ValueError('先提交判断并使用合法状态。')
                            changes = changes_from(before_issue)
                            if state == 'candidate':
                                if assessment['decision'] != 'repair' or not changes or not syntax or not syntax['passed']:
                                    raise ValueError('候选修改需要修复判断、实际改动及改后语法检查通过。')
                            else:
                                for n, text in before_issue.items(): (candidate / n).write_text(text, encoding='utf-8')
                                changes = []
                            results.append({'id': active, 'status': state, 'assessment': assessment,
                                'summary': str(args['summary']), 'uncertainties': str(args['uncertainties']),
                                'changed_files': [c['path'] for c in changes], 'syntax': syntax})
                            active = None; reset = True; answer = {'saved': True, 'status': state}
                        else: raise ValueError('未知工具。')
                except Exception as e: answer = {'error': redact(e)}
                log.append({'turn': turn + 1, 'issue_id': active, 'tool': name,
                            'error': answer.get('error') if isinstance(answer, dict) else None})
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(answer, ensure_ascii=False)})
                save_result()
            if finished: break
            if reset:
                if len(results) >= limit:
                    finished = True
                    summary = '已完成本次指定卡片，交由浏览器复测。'
                    break
                messages = fresh_messages()
    except Exception as e:
        model_error = redact(e)
    finally:
        if active:
            for n, text in before_issue.items(): (candidate / n).write_text(text, encoding='utf-8')
            results.append({'id': active, 'status': 'deferred', 'assessment': assessment,
                            'summary': '本问题未完成，已撤回本问题修改。', 'uncertainties': model_error or '轮数耗尽或执行中断',
                            'changed_files': []})
            active = None
        result = save_result()
        result['syntax'] = syntax_check(candidate)
        dump(run_dir / 'repair.json', result)
        (run_dir / 'changes.diff').write_text('\n'.join(c['diff'] for c in result['changes']), encoding='utf-8')
    return result


def select_repair_issues(text, config, issues):
    """Explicit user selection only; reject unknown, duplicate or unconfirmed cards."""
    import re
    tokens = [t for t in re.split(r'[,，\s]+', text.strip()) if t]
    ids = ['ISSUE-' + str(int(t)).zfill(3) if t.isdigit() else t.upper() for t in tokens]
    if len(ids) != len(set(ids)): raise ValueError('问题编号重复，请每个编号只填写一次。')
    cards = {i['id']: i for i in issues}
    for key in ids:
        if key not in cards: raise ValueError('不存在的问题编号：' + key)
        if not repairable(config, cards[key]):
            raise ValueError(key + ' 尚未确认可修复，请先补充复现；体验建议不会自动修改。')
    return ids


def repair_selected(config, run_dir, baseline, issues, selected_ids):
    """One isolated candidate per card; promote only after cumulative verification."""
    run_dir = Path(run_dir)
    selected_ids = select_repair_issues(','.join(selected_ids), config, issues)
    if not selected_ids:
        return {'status': 'skipped', 'reason': '用户未选择修复问题。',
                'verification': {'status': 'skipped', 'reason': '用户未选择修复问题。'}}
    batch = run_dir / ('repair_batch_' + uuid.uuid4().hex)
    batch.mkdir()
    previous = batch/'previous_results'
    previous.mkdir()
    for name in ('repair.json', 'verification.json', 'changes.diff', 'summary.json', '体验与修复报告.html'):
        if (run_dir/name).exists(): shutil.copy2(run_dir/name, previous/name)
    accepted_source = Path(config['source_path'])
    cards = {i['id']: i for i in issues}
    accepted, rows, usage = [], [], {'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
    last_verification = None
    result = {}
    def save():
        changes = []
        for name in source_files(accepted_source):
            old = Path(config['source_path']) / name
            before = old.read_text(encoding='utf-8') if old.exists() else ''
            after = (accepted_source / name).read_text(encoding='utf-8')
            if before != after:
                changes.append({'path': name, 'diff': ''.join(difflib.unified_diff(
                    before.splitlines(True), after.splitlines(True), fromfile='before/'+name, tofile='after/'+name))})
        all_passed = len(accepted) == len(selected_ids)
        verification = dict(last_verification or {'issues': [], 'regressions': []})
        verification.update(status='verified' if all_passed else ('partial_verified' if accepted else 'not_verified'),
            reason=f'所选 {len(selected_ids)} 项中 {len(accepted)} 项逐条复测通过；未通过的修改未并入交付副本。',
            requested_ids=selected_ids, verified_ids=list(accepted), per_issue=rows)
        result.update(status='candidate' if changes else 'skipped', reason=verification['reason'],
            summary=verification['reason'], candidate=str(accepted_source), selected_ids=list(accepted),
            requested_ids=selected_ids, issue_results=rows, changes=changes, usage=usage,
            syntax=syntax_check(accepted_source), verification=verification, batch_dir=str(batch))
        dump(batch/'repair.json', result)
        dump(run_dir/'repair.json', result)
        dump(run_dir/'verification.json', verification)
        (run_dir/'changes.diff').write_text('\n'.join(c['diff'] for c in changes), encoding='utf-8')
    save()
    for position, key in enumerate(selected_ids, 1):
        say(f'逐条修复 {position}/{len(selected_ids)}：{key}；通过后才进入下一项')
        card_dir = batch / key
        card_dir.mkdir()
        migration = run_dir/'replay_migration.json'
        if migration.exists(): shutil.copy2(migration, card_dir/migration.name)
        scenarios = run_dir/'regression_scenarios.json'
        if scenarios.exists(): shutil.copy2(scenarios, card_dir/scenarios.name)
        card_config = dict(config, source_path=str(accepted_source), max_repairs=1)
        row = {'id': key, 'status': 'deferred', 'summary': '', 'folder': str(card_dir)}
        try:
            candidate = repair(card_config, card_dir, [cards[key]])
            for metric in usage: usage[metric] += candidate.get('usage', {}).get(metric, 0)
            if candidate['status'] != 'candidate':
                row['summary'] = candidate.get('reason', '没有候选修改')
            else:
                # Later changes must preserve ALL earlier verified fixes, not just baseline checks.
                cumulative = dict(candidate, selected_ids=accepted + [key])
                verdict = verify(card_config, card_dir, baseline, issues, cumulative)
                row.update(status=verdict['status'], summary=verdict['reason'], verification=verdict)
                if verdict['status'] == 'verified':
                    accepted_source = Path(candidate['candidate'])
                    accepted.append(key)
                    last_verification = verdict
                else:
                    row['summary'] += '；本条候选保留供检查，但未并入后续修复。'
        except Exception as e:
            row.update(status='error', summary=redact(e))
        rows.append(row)
        save()
        say(key + '：' + row['status'])
    return result


def verify(config, run_dir, baseline, issues, repair_result):
    if repair_result['status'] == 'skipped': return {'status': 'skipped', 'reason': repair_result['reason']}
    candidate, outcomes = repair_result['candidate'], []
    migration_path = Path(run_dir) / 'replay_migration.json'
    migration = load(migration_path).get('mapping', []) if migration_path.exists() else []
    def replayable(actions):
        # Only use an audited migration for the exact original action sequence.
        if migration and len(actions) <= len(migration) and all(
                action == migration[i]['old'] for i, action in enumerate(actions)):
            return [copy.deepcopy(row['migrated']) for row in migration[:len(actions)]]
        return actions
    for issue in issues:
        if issue['id'] not in repair_result['selected_ids']: continue
        r = replay(config, run_dir, candidate, replayable(issue['actions']),
                   [{'after_action': len(issue['actions']), 'assertion': issue['assertion']}],
                   'verify_' + issue['id'])
        outcomes.append({'id': issue['id'], 'passed': bool(r['actions_ok'] and r['checks'] and
            r['checks'][-1].get('passed')), 'evidence': r})
    regressions = []
    scenarios_path = Path(run_dir)/'regression_scenarios.json'
    regression_baseline = baseline
    if scenarios_path.exists():
        import hashlib
        scenarios = load(scenarios_path)
        signature = hashlib.sha256(json.dumps(baseline, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if scenarios.get('baseline_sha256') != signature:
            raise ValueError('回归场景与原始体验记录不匹配，请重新核对，不能沿用旧场景。')
        regression_baseline = scenarios['scenarios']
    for b in regression_baseline:
        checks = [c for c in b.get('checks', []) if c.get('passed')]
        if not checks: continue
        # Replay only through the last successful checkpoint: later exploratory actions are irrelevant.
        last = max(c['after_action'] for c in checks)
        r = replay(config, run_dir, candidate, replayable(b['actions'][:last]), checks,
                   'regression_' + b['persona']['id'])
        regressions.append({'persona': b['persona']['name'],
            'passed': bool(r['actions_ok'] and r['checks'] and all(c.get('passed') for c in r['checks'])),
            'evidence': r})
    passed = (bool(repair_result['changes']) and repair_result['syntax']['passed'] and bool(outcomes)
              and all(o['passed'] for o in outcomes) and bool(regressions) and
              all(r['passed'] for r in regressions))
    blocked = any(row['evidence'].get('errors') for row in outcomes + regressions)
    result = {'status': 'verified' if passed else ('verification_blocked' if blocked else 'not_verified'), 'issues': outcomes,
              'regressions': regressions,
              'reason': '修复问题复测、已有成功检查点和语法检查均通过' if passed else
                        ('复测操作执行受阻，尚不能判断产品修复结果；请查看具体定位或执行错误' if blocked else
                         '检查未全部通过或没有成功的回归检查点；保留原始副本，不宣称修复成功')}
    dump(Path(run_dir) / 'verification.json', result)
    return result

def image_tag(path, label='截图'):
    path = Path(path)
    if not path.is_file(): return '<p>截图文件缺失</p>'
    data = base64.b64encode(path.read_bytes()).decode()
    return f'<figure><img alt="{html.escape(label)}" src="data:image/png;base64,{data}"><figcaption>{html.escape(label)}</figcaption></figure>'

def render_report(run_dir, config, baseline, issues, repair_result=None, verification=None):
    """Human-facing summary; detailed machine evidence remains in JSON files."""
    import re
    run_dir = Path(run_dir)
    esc = lambda value: html.escape(str(value))
    def short(value, limit=230):
        text = str(value or '')
        text = re.sub(r'\[data-wf-id=.*?\]', '对应控件', text)
        text = re.sub(r'https?://[^\s）)]+', '', text)
        text = re.sub(r'`[^`]*`', '相关功能', text)
        text = text.replace('placeholder', '提示文字').replace('#modal', '弹窗').replace('localStorage', '浏览器本地存储')
        text = re.sub(r'#[A-Za-z_][\w-]*', '页面控件', text)
        text = re.sub(r'故为 P\d 而非 P\d[。.]?', '', text)
        text = re.split(r'依据[：:]', text, maxsplit=1)[0]
        text = re.sub(r'\s+', ' ', text).strip()
        return esc(text if len(text) <= limit else text[:limit].rstrip('，；、 ') + '…')
    status = (verification or {}).get('status', 'pending')
    labels = {'verified':'所选问题已修复并通过复测','partial_verified':'部分问题已修复，仍有待处理项',
        'not_verified':'修复尚未通过验证','verification_blocked':'复测受阻，暂无法确认修复结果',
        'skipped':'本次未执行修复','pending':'体验结果已生成，尚未修复'}
    confirmed = sum(i.get('status') == '已复现（按所填预期）' for i in issues)
    fixed = set((verification or {}).get('verified_ids', []))
    if status == 'verified': fixed.update((repair_result or {}).get('selected_ids', []))
    requested = set((repair_result or {}).get('requested_ids', (repair_result or {}).get('selected_ids', [])))
    title = '产品体验报告'
    requirements = str(config.get('requirements', ''))
    project = re.search(r'(?:项目|项目名称)[：:]\s*([^\n]+)', requirements)
    purpose = re.search(r'(?:用途|用途介绍)[：:]\s*([^\n]+)', requirements)
    name = project.group(1) if project else '本次提交的产品'
    content = [f'<header><p class="eyebrow">DEMO CLINIC · 产品体验</p><h1>{short(name,80)}</h1>',
        '<p class="subtitle">'+short(purpose.group(1),150)+'</p>' if purpose else '',
        '<p class="badge">'+esc(labels.get(status,'结果待确认'))+'</p></header>',
        '<div class="metrics">'+''.join(f'<div><b>{number}</b><span>{label}</span></div>' for number,label in
        [(len(issues),'记录的问题'),(confirmed,'已确认的问题'),(len(fixed),'已验证修复')])+'</div>']
    content.append('<section><h2>本次重点</h2><ul>')
    tasks = config.get('tasks', [])
    if isinstance(tasks,str): tasks=[tasks]
    for task in tasks[:6]:
        if isinstance(task,dict): task=task.get('task',task.get('name',task.get('description','')))
        task=re.split(r'[：:；;]',str(task),maxsplit=1)[0]
        if task:content.append('<li>'+short(task,90)+'</li>')
    content.append('</ul><p class="muted">这些是计划检查的目标，不代表所有任务均已完成。实际结论以以下问题和检查证据为准。</p></section>')
    task_results=[b for b in baseline if b.get('task_id')]
    if task_results:
        task_labels={'passed':'通过','failed':'发现问题','blocked':'前置条件受阻','incomplete':'尚未完成',
                     'needs_review':'需要确认','error':'执行中断','not_run':'预算不足，未执行'}
        content.append('<section><h2>分层任务进度</h2><ul>')
        for task in task_results:
            card=task.get('task_card',{})
            text=f"第{card.get('layer',0)+1}层 · {task['task_id']} · {card.get('title','')}：{task_labels.get(task.get('task_status'),'未完成')}"
            if task.get('blocked_by'):text+='（前置任务 '+', '.join(task['blocked_by'])+' 未通过，未重复检查）'
            content.append('<li>'+esc(text)+'</li>')
        content.append('</ul><p class="muted">受阻不等于发现新缺陷，也不计为测试通过。前置修复后仍需重新验证依赖流程。</p></section>')
    content.append('<h2>发现了什么，需要怎么处理</h2>')
    if not issues:content.append('<section>本轮没有记录到问题，但不能据此判断所有功能正常。</section>')
    outcomes = {x['id']:x for x in (verification or {}).get('issues',[])}
    for item in issues:
        key=item['id']; outcome=outcomes.get(key,{})
        if key in fixed: state='已修复 · 复测通过'
        elif key in requested: state='已尝试修复 · 尚未验证通过'
        elif item.get('kind')=='usability': state='改进建议 · 尚未修改'
        elif item.get('status')=='已复现（按所填预期）': state='已确认 · 待修复'
        else: state='待进一步确认'
        severity={'P1':'优先处理','P2':'影响正常使用','P3':'体验优化'}.get(item.get('severity'),'待评估')
        content.append('<section class="issue"><div class="issue-meta">'+esc(key)+' · '+esc(severity)+'</div><h3>'+short(item.get('title'),150)+'</h3><p class="state">'+esc(state)+'</p>')
        for field,label in [('expected','应该怎样'),('impact','对用户的影响')]:
            if item.get(field):content.append('<p><strong>'+label+'：</strong>'+short(item[field],130)+'</p>')
        if key in fixed:content.append('<p><strong>处理结果：</strong>对应问题已通过复测；已记录的正常流程也完成了回归检查。</p>')
        if item.get('blocked_tasks'):content.append('<p><strong>连带影响：</strong>后续任务 '+esc(', '.join(item['blocked_tasks']))+' 暂未执行，统一关联到本问题。</p>')
        elif item.get('initial_recommendation'):content.append('<p><strong>建议：</strong>'+short(item['initial_recommendation'],130)+'</p>')
        screenshot=item.get('screenshot') or item.get('reproduction',{}).get('screenshot')
        after=outcome.get('evidence',{}).get('screenshot') if key in fixed else None
        if screenshot or after:
            content.append('<details><summary>查看现象与页面证据</summary><p>'+short(item.get('actual'),300)+'</p>')
            if screenshot:content.append(image_tag(screenshot,'发现问题时的页面'))
            if after:content.append(image_tag(after,'修复后复测的页面'))
            content.append('</details>')
        content.append('</section>')
    content.append('<section><h2>本次检查的范围</h2>')
    checkpoints=sum(sum(bool(x.get('evaluated')) for x in b.get('checks',[])) for b in baseline)
    content.append(f'<p>本轮记录了 {checkpoints} 个有效检查点。单个检查点通过，不代表整个功能已全面通过。</p>')
    if any('上限' in b.get('stop_reason','') for b in baseline):
        content.append('<p>本轮达到测试预算后停止，部分计划任务和边界情况尚未完成。</p>')
    if any(b.get('model_error') for b in baseline):
        content.append('<p>体验过程中服务连接中断，以上仅展示已保存的检查结果。</p>')
    if baseline and not any(any(x.get('result')!='未测试' for x in b.get('coverage',[])) for b in baseline):
        content.append('<p>尚未形成完整的功能覆盖清单，不能据此认定所有功能都已检查。</p>')
    if fixed:content.append('<p>“已修复”仅针对列出的具体问题及本轮检查条件，不代表产品不存在其他缺陷。</p>')
    if status=='verification_blocked':content.append('<p>自动复测执行受阻，需要处理测试流程后再确认修改结果。</p>')
    content.append('</section><footer>完整测试证据与技术记录已单独保存。原始项目文件未被覆盖。</footer>')
    css = """*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#25344a;font:16px/1.75 system-ui,'Microsoft YaHei',sans-serif}main{max-width:940px;margin:auto;padding:48px 24px}header{margin-bottom:28px}h1{font-size:32px;line-height:1.35;margin:12px 0}h2{font-size:22px;margin:28px 0 14px}h3{font-size:19px;margin:8px 0}p{margin:10px 0}.eyebrow,.issue-meta{font-size:13px;letter-spacing:.06em;color:#61748b}.subtitle,.muted,footer{color:#64748b}.badge{display:inline-block;background:#e8eef8;padding:6px 14px;border-radius:8px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metrics div,section{background:white;border:1px solid #e0e6ed;border-radius:14px;padding:22px}.metrics b{font-size:30px;display:block}.metrics span{color:#64748b;font-size:14px}section{margin:18px 0}section h2{margin-top:0}.state{font-weight:600;color:#245c7a}ul{padding-left:22px}details{border-top:1px solid #e4e9ef;margin-top:18px;padding-top:12px}summary{cursor:pointer;color:#365d85}img{max-width:100%;height:auto;border:1px solid #e0e6ed}figure{margin:14px 0}figcaption{font-size:13px;color:#64748b}footer{font-size:13px;margin-top:28px}@media(max-width:600px){main{padding:24px 16px}h1{font-size:25px}section,.metrics div{padding:16px}.metrics{gap:8px}.metrics b{font-size:25px}}"""
    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>'+css+'</style></head><body><main>'+''.join(content)+'</main></body></html>'
    path=run_dir/'体验与修复报告.html';path.write_text(document,encoding='utf-8')
    role_usage = usage_summary(run_dir, config)
    dump(run_dir / 'summary.json', {'run': run_dir.name, 'issue_count': len(issues),
        'confirmed': sum(i['status'] == '已复现（按所填预期）' for i in issues),
        'repair_status': status, 'baseline_usage': [b.get('usage') for b in baseline],
        'repair_usage': (repair_result or {}).get('usage'), 'usage_by_role': role_usage})
    dump(run_dir / '修复问题单.json', {
        'requirements':config['requirements'], 'mode':config['mode'],
        'automatic_repair_enabled':bool(config.get('repair_enabled',False)),
        'issues':[dict(i, eligible_for_automatic_repair=repairable(config, i)) for i in issues],
        'report_content':[b.get('report_content',{}) for b in baseline],
        'verification':verification, 'status_source':'复现与复测状态来自程序，模型内容仅作分析'} )
    return path

def make_run(root):
    path = Path(root) / (time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:5])
    path.mkdir(parents=True, exist_ok=False); return path

def export_delivery(run_dir, notebook_path=None):
    """Allowlist export: never zip working environments, credentials, or arbitrary files."""
    import zipfile
    run_dir = Path(run_dir); target = run_dir / '组委会交付包.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('体验与修复报告.html', 'summary.json', 'api_usage_summary.json', 'baseline.json', 'issues.json',
                     'repair.json', 'verification.json', 'changes.diff', 'config.json', '体验规范_本轮快照.md', '修复问题单.json', '修复规范_本轮快照.md', 'task_plan.json', 'task_state.json'):
            if (run_dir / name).exists(): z.write(run_dir / name, 'results/' + name)
        # Candidate contains only selected source files, never diaries or process logs.
        repair_path = run_dir / 'repair.json'
        candidate = Path(load(repair_path).get('candidate', run_dir / 'candidate')) if repair_path.exists() else run_dir / 'candidate'
        if candidate.is_dir():
            for name in source_files(candidate): z.write(candidate / name, 'candidate/' + name)
        for name in ('workflow_engine.py', 'browser_worker.py', 'browser_capabilities.py', 'experience_skill.py', 'task_queue.py', 'requirements.txt', '修复Agent执行规范.md'):
            z.write(ROOT / name, 'implementation/' + name)
        if (run_dir / '体验规范_本轮快照.md').is_file():
            z.write(run_dir / '体验规范_本轮快照.md', '体验Agent检查维度.md')
        elif (ROOT / '体验Agent检查维度.md').is_file():
            z.write(ROOT / '体验Agent检查维度.md', '体验Agent检查维度.md')
        if notebook_path and Path(notebook_path).is_file(): z.write(notebook_path, 'Demo体验与自动修复工作流.ipynb')
    return target
