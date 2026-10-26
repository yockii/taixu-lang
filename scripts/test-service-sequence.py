"""服务状态序列 fuzz（安全审查攻关第 4 项，docs/design/v052-service-sequence-v0.1-20261025.md）。

对真实的 `taixu mcp` 进程跑带种子的请求序列：每条请求随机切片写入（偶尔两条并进一次写入），失败与正常随机交替；每步对照
抽象模型核对回复（恰一条、id 对得上、严格 JSON、类别符合命令），并观察服务的后代进程（Windows 走 Toolhelp 快照，Unix 走
proc_probe）与临时目录里本服务的 worker 根（只允许当前一代，代数与模型一致）。序列末随机选关停时机（空闲、在途请求、刚触发
硬超时）关 stdin，服务须在时限内退出，之后无后代、无残留 worker 根。另有确定性的 cleanup_pending 正例：在 worker 根里放
陌生文件使其清不掉，两代之后新请求须以 cleanup_pending 拒绝，移走后经空闲清扫恢复。失败的序列按步缩小。

  python scripts/test-service-sequence.py --compiler <taixu> --work <dir> [--sequences N] [--steps N] [--seed S]
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
_spec = importlib.util.spec_from_file_location('mcp_e2e', ROOT / 'scripts' / 'mcp-debug-e2e.py')
mcp_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_e2e)

WINDOWS = os.name == 'nt'
SEED = 20261025
BUDGET = 32768
ROOT_RE = re.compile(r'^taixu-mcp-worker-\d+-(\d+)-\d+$')


def tokens(data):
    """json_envelope.tx 的 jsonEnvelopeWithin 计数规则（引号外的结构符、每个串与原子各一）。"""
    t = 0
    quoted = escaped = atom = False
    for c in data:
        if quoted:
            if escaped:
                escaped = False
            elif c == 92:
                escaped = True
            elif c == 34:
                quoted = False
            continue
        if c == 34:
            quoted, atom = True, False
            t += 1
        elif c in (123, 91, 125, 93, 58, 44):
            t += 1
            atom = False
        elif c in (32, 9, 10, 13):
            atom = False
        elif not atom:
            atom = True
            t += 1
    return t


def budget_line(rid, total):
    """结构记号恰为 total 的 tools/list 请求：params 里垫一个数组，每多一个 0 恰多 2 个记号（逗号与原子），末尾加不加 [] 改奇偶。"""
    def line(k, odd):
        pad = ['0'] * k + (['[]'] if odd else [])
        return ('{"jsonrpc":"2.0","id":%d,"method":"tools/list","params":{"pad":[%s]}}' % (rid, ','.join(pad))).encode()
    for odd in (False, True):
        gap = total - tokens(line(1, odd))
        if gap >= 0 and gap % 2 == 0:
            out = line(1 + gap // 2, odd)
            assert tokens(out) == total
            return out + b'\n'
    raise ValueError('no padding reaches %d tokens' % total)


def descendants(pid, hosts=False):
    """服务进程的后代 pid 集合。Windows 上 worker 的控制台宿主 conhost.exe 随 worker 生灭，计数时不算（hosts=True 时一并给出）。"""
    if not WINDOWS:
        import proc_probe
        return {p for p, _ in proc_probe.descendants(pid)}
    import ctypes
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD), ('heap', ctypes.c_void_p), ('module', w.DWORD),
                    ('threads', w.DWORD), ('ppid', w.DWORD), ('priority', ctypes.c_long), ('flags', w.DWORD), ('exe', ctypes.c_wchar * 260)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    snap = k.CreateToolhelp32Snapshot(2, 0)
    entry = Entry()
    entry.size = ctypes.sizeof(Entry)
    parent, names = {}, {}
    ok = k.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        parent.setdefault(entry.ppid, []).append(entry.pid)
        names[entry.pid] = entry.exe.lower()
        ok = k.Process32NextW(snap, ctypes.byref(entry))
    k.CloseHandle(snap)

    def created(p):
        """创建时间（FILETIME），打不开为 None。ppid 可能指向已退出、pid 被复用的进程，真正的子进程不早于父进程创建。"""
        h = k.OpenProcess(0x1000, False, p)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        times = [w.FILETIME() for _ in range(4)]
        ok = k.GetProcessTimes(h, *[ctypes.byref(t) for t in times])
        k.CloseHandle(h)
        return (times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) if ok else None
    out, todo = set(), [pid]
    born = {pid: created(pid)}
    while todo:
        p = todo.pop()
        for child in parent.get(p, []):
            if child in out or child == pid:
                continue
            c = created(child)
            if c is None or born[p] is None or c < born[p]:
                continue
            born[child] = c
            out.add(child)
            todo.append(child)
    return out if hosts else {p for p in out if names.get(p) != 'conhost.exe'}


def alive(pid):
    if WINDOWS:
        import ctypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Server:
    """一个 `taixu mcp` 进程：读线程收齐每一行（严格 JSON 或记为坏行），写入按片进行。"""

    def __init__(self, compiler, ws, tmp, extra=()):
        env = dict(os.environ)
        env['TMP'] = env['TEMP'] = env['TMPDIR'] = str(tmp)
        self.proc = subprocess.Popen([str(compiler), 'mcp', '--trust-workspace', '--workspace', str(ws), '--request-timeout-ms', '4000',
                                      '--worker-cpu-s', '20', '--worker-memory-mib', '1024', *extra],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ws, env=env)
        self.msgs, self.bad, self.err = [], [], b''
        self.lock = threading.Lock()
        self.readers = [threading.Thread(target=self._read, daemon=True), threading.Thread(target=self._stderr, daemon=True)]
        for t in self.readers:
            t.start()

    def _stderr(self):
        self.err = self.proc.stderr.read()

    def _read(self):
        for raw in self.proc.stdout:
            text = raw.decode('utf-8', 'replace').rstrip('\n')
            try:
                m = mcp_e2e.strict_json(text)
            except ValueError:
                with self.lock:
                    self.bad.append(text[:300])
                continue
            with self.lock:
                self.msgs.append(m)

    def write(self, data, rng):
        at = 0
        try:
            while at < len(data):
                n = rng.randint(1, 4096)
                self.proc.stdin.write(data[at:at + n])
                self.proc.stdin.flush()
                at += n
                if rng.random() < 0.3:
                    time.sleep(rng.random() * 0.003)
            return True
        except OSError:
            return False

    def take(self, rid, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for i, m in enumerate(self.msgs):
                    if m.get('id') == rid and type(m.get('id')) is type(rid):
                        return self.msgs.pop(i)
            if self.proc.poll() is not None:
                time.sleep(0.05)
                with self.lock:
                    for i, m in enumerate(self.msgs):
                        if m.get('id') == rid and type(m.get('id')) is type(rid):
                            return self.msgs.pop(i)
                return None
            time.sleep(0.005)
        return None

    def close(self, timeout=10):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None
        for t in self.readers:
            t.join(5)
        return code


def tool_result(r):
    """tools/call 回复 → (structuredContent, isError, error)，同 mcp-debug-e2e 的 Mcp.tool。"""
    if r is None:
        return None, None, 'no reply'
    if 'error' in r:
        return None, None, r['error']
    res = r.get('result', {})
    if 'structuredContent' not in res:
        content = res.get('content')
        if res.get('isError') is True and isinstance(content, list) and len(content) == 1 and content[0].get('type') == 'text':
            return None, True, content[0]['text']
        return None, None, 'invalid result %r' % res
    if mcp_e2e.strict_json(res['content'][0]['text']) != res['structuredContent']:
        return None, None, 'content text differs from structuredContent'
    return res['structuredContent'], res.get('isError', False), None


# ---------------------------------------------------------------------------------------------------------------------
# 命令：build(rid) → (请求字节, 回复的 id)；judge(回复, 模型) → 错误说明或 None，并按回复更新模型。

def call(rid, method, params=None):
    m = {'jsonrpc': '2.0', 'id': rid, 'method': method}
    if params is not None:
        m['params'] = params
    return json.dumps(m, ensure_ascii=False).encode() + b'\n'


def tool(rid, name, args):
    return call(rid, 'tools/call', {'name': name, 'arguments': args})


class Model:
    def __init__(self):
        self.generation = 0     # 最近一代 worker 的代数（0：还没起过）
        self.worker = False     # 当前是否有活着的 worker

    def needs_worker(self):
        if not self.worker:
            self.generation += 1
            self.worker = True


def judge_tool(kind):
    def judge(r, model):
        s, e, err = tool_result(r)
        if kind == 'bad-timeout':
            # 父进程按启动配置核对，回纯文本的 isError 结果，不起 worker。
            return None if s is None and e is True and isinstance(err, str) and 'timeout_ms' in err else 'bad-timeout: %r' % ((s, e, err),)
        if err is not None:
            return 'tool reply: %r' % (err,)
        if kind == 'compile-ok':
            model.needs_worker()
            return None if s.get('schema') == 'compile/1' and s.get('ok') is True and not e else 'compile-ok: %r' % s
        if kind in ('compile-error', 'compile-missing'):
            model.needs_worker()
            return None if s.get('schema') == 'compile/1' and s.get('ok') is False and e and s.get('diags') else '%s: %r' % (kind, s)
        if kind == 'run-ok':
            model.needs_worker()
            return None if s.get('schema') == 'run/1' and s.get('exit_code') == 0 and s.get('stdout') == 'safe\n' else 'run-ok: %r' % s
        if kind == 'run-spin':
            model.needs_worker()
            return None if s.get('schema') == 'run/1' and s.get('timed_out') is True else 'run-spin: %r' % s
        if kind == 'run-flood':
            model.needs_worker()
            return None if s.get('schema') == 'run/1' and s.get('exit_code') == 0 and s.get('truncated') is True else 'run-flood: %r' % {
                k: v for k, v in s.items() if k not in ('stdout', 'stderr')}
        if kind == 'hard-timeout':
            model.needs_worker()
            if s.get('schema') == 'compile/1' and s.get('ok') is True:
                return None  # 1 ms 内完成也合法
            if s.get('schema') != 'tool-error/1' or s.get('reason') != 'request_timeout' or s.get('termination_requested') is not True:
                return 'hard-timeout: %r' % s
            if s.get('worker_generation') != model.generation:
                return 'generation %r, model %d' % (s.get('worker_generation'), model.generation)
            model.worker = False
            if s.get('reaped') is not True or s.get('artifacts_cleaned') is not (not s.get('cleanup_pending_paths')):
                return 'cleanup fields inconsistent: %r' % s
            return None
        return 'unknown kind ' + kind
    return judge


def judge_rpc(code=None, message=None, result_key=None):
    def judge(r, model):
        if r is None:
            return 'no reply'
        if result_key is not None:
            return None if result_key in r.get('result', {}) else 'expected result.%s: %r' % (result_key, r)
        e = r.get('error') or {}
        if code is not None and e.get('code') != code:
            return 'expected error %d: %r' % (code, r)
        if message is not None and e.get('message') != message:
            return 'expected %s: %r' % (message, r)
        return None
    return judge


def commands():
    """(名字, 权重, build(rid) → (字节, 回复 id), judge)。回复 id 为 None 的是解析不了 id 的协议错。"""
    t = lambda name, args: (lambda rid: (tool(rid, name, args), rid))
    return [
        ('discover', 2, lambda rid: (call(rid, 'server/discover'), rid), judge_rpc(result_key='identity')),
        ('list', 2, lambda rid: (call(rid, 'tools/list'), rid), judge_rpc(result_key='tools')),
        ('compile-ok', 6, t('compile', {'path': 'main.tx'}), judge_tool('compile-ok')),
        ('compile-error', 3, t('compile', {'path': 'invalid.tx'}), judge_tool('compile-error')),
        ('compile-missing', 2, t('compile', {'path': 'nope.tx'}), judge_tool('compile-missing')),
        ('run-ok', 3, t('run', {'path': 'main.tx'}), judge_tool('run-ok')),
        ('run-spin', 2, t('run', {'path': 'spin.tx', 'timeout_ms': 300}), judge_tool('run-spin')),
        ('run-flood', 1, t('run', {'path': 'flood.tx'}), judge_tool('run-flood')),
        ('hard-timeout', 3, t('compile', {'path': 'main.tx', 'timeout_ms': 1}), judge_tool('hard-timeout')),
        ('bad-timeout', 2, lambda rid: (tool(rid, 'compile', {'path': 'main.tx', 'timeout_ms': random.Random(rid).choice([0, -1, True, 1.5, '1', None])}), rid),
         judge_tool('bad-timeout')),
        ('unknown-method', 2, lambda rid: (call(rid, 'no/such'), rid), judge_rpc(code=-32601)),
        ('bad-json', 2, lambda rid: (b'{"jsonrpc":"2.0","id":%d,"method":\n' % rid, None), judge_rpc(code=-32700)),
        ('not-object', 1, lambda rid: (b'[1,2,3]\n', None), judge_rpc(code=-32600)),
        ('literal-cr', 1, lambda rid: (b'{"jsonrpc":"2.0","id":%d,"method":"pi\rng"}\n' % rid, None), judge_rpc(code=-32700)),
        ('budget-at', 1, lambda rid: (budget_line(rid, BUDGET), rid), lambda r, m: None if r is not None and r.get('error', {}).get('message') != 'RequestTooComplex' else 'budget-at: %r' % (r,)),
        ('budget-over', 1, lambda rid: (budget_line(rid, BUDGET + 1), None), judge_rpc(message='RequestTooComplex')),
    ]


def workspace(work):
    ws = work / 'ws'
    ws.mkdir(parents=True, exist_ok=True)
    (ws / 'main.tx').write_text('export function main(args: []string): int { print("safe"); return 0; }\n', encoding='utf-8', newline='\n')
    (ws / 'invalid.tx').write_text('function main(args: []string): int { return missing; }\n', encoding='utf-8', newline='\n')
    (ws / 'spin.tx').write_text('function main(args: []string): int { let n = 0; while (true) { n = n + 1; } return n; }\n', encoding='utf-8', newline='\n')
    (ws / 'flood.tx').write_text('function main(args: []string): int { let i = 0; while (i < 40000) { print("0123456789012345678901234567890123456789012345678901234567890123"); i = i + 1; } return 0; }\n',
                                 encoding='utf-8', newline='\n')
    return ws


def roots(tmp):
    out = []
    for name in os.listdir(tmp):
        m = ROOT_RE.match(name)
        if m:
            out.append((int(m.group(1)), name))
    return sorted(out)


def settle(server, tmp, model, timeout=3.0):
    """空闲清扫（≤50 ms 一轮）之后：后代至多一个（当前 worker），worker 根只剩当前一代。随机序列里不该出现清不掉的根。"""
    end = time.time() + timeout
    while True:
        kids = descendants(server.proc.pid)
        have = roots(tmp)
        bad = []
        if len(kids) > (1 if model.worker else 0):
            bad.append('descendants %r' % sorted(kids))
        if [g for g, _ in have] != ([model.generation] if model.worker else []):
            bad.append('worker roots %r (model generation %d, worker %s)' % (have, model.generation, model.worker))
        if not bad or time.time() > end:
            return '; '.join(bad) or None
        time.sleep(0.05)


def run_sequence(compiler, work, seed, steps, label):
    """一个序列；返回 (失败说明或 None, 实际命令序列)。"""
    rng = random.Random(seed)
    cmds = commands()
    weights = [w for _, w, _, _ in cmds]
    plan = [rng.choices(range(len(cmds)), weights)[0] for _ in range(steps)]
    return replay(compiler, work, seed, plan, label)


def replay(compiler, work, seed, plan, label):
    rng = random.Random(seed * 7919 + len(plan))
    cmds = commands()
    seq = work / label
    if seq.exists():
        shutil.rmtree(seq)
    tmp = seq / 'tmp'
    tmp.mkdir(parents=True)
    ws = workspace(seq)
    server = Server(compiler, ws, tmp)
    model = Model()
    rid = 0
    failure = None
    remembered = set()
    names = [cmds[i][0] for i in plan]
    try:
        i = 0
        while i < len(plan) and failure is None:
            batch = [plan[i]]
            if i + 1 < len(plan) and rng.random() < 0.15:
                batch.append(plan[i + 1])  # 两条并进一次写入
            data, waits = b'', []
            for c in batch:
                rid += 1
                body, reply_id = cmds[c][2](rid)
                data += body
                waits.append((c, reply_id))
            if not server.write(data, rng):
                failure = 'write failed at step %d' % i
                break
            for c, reply_id in waits:
                r = server.take(reply_id)
                why = cmds[c][3](r, model)
                if why:
                    failure = 'step %d %s: %s' % (i, cmds[c][0], why)
                    break
            i += len(batch)
            if failure is None:
                remembered |= descendants(server.proc.pid, hosts=True)
                why = settle(server, tmp, model)
                if why:
                    failure = 'after step %d: %s' % (i - 1, why)
            with server.lock:
                if server.bad:
                    failure = failure or 'non-JSON output %r' % server.bad[:2]
                stray = [m for m in server.msgs if m.get('id') is not None]
                if stray and failure is None:
                    failure = 'unexpected replies %r' % stray[:2]
        # 关停时机：空闲、在途请求、刚触发硬超时。
        mode = rng.choice(['idle', 'inflight', 'after-timeout'])
        if failure is None and mode != 'idle':
            rid += 1
            body = tool(rid, 'run', {'path': 'spin.tx', 'timeout_ms': 2000}) if mode == 'inflight' else tool(rid, 'compile', {'path': 'main.tx', 'timeout_ms': 1})
            server.write(body, rng)
            time.sleep(rng.random() * 0.2)
        remembered |= descendants(server.proc.pid, hosts=True)
        code = server.close()
        if failure is None:
            if code is None:
                failure = 'shutdown (%s): did not exit within 10 s' % mode
            elif server.bad:
                failure = 'shutdown (%s): non-JSON output %r' % (mode, server.bad[:2])
        end = time.time() + 5
        while time.time() < end and (any(alive(p) for p in remembered) or roots(tmp)):
            time.sleep(0.05)
        if failure is None and any(alive(p) for p in remembered):
            failure = 'shutdown (%s): descendants left %r' % (mode, sorted(p for p in remembered if alive(p)))
        if failure is None and roots(tmp):
            failure = 'shutdown (%s): worker roots left %r' % (mode, roots(tmp))
    finally:
        if server.proc.poll() is None:
            server.proc.kill()
    return failure, names


def shrink(compiler, work, seed, plan, label):
    """按步删减：逐条去掉命令，仍失败则保留删减。"""
    cur = list(plan)
    i = 0
    while i < len(cur):
        trial = cur[:i] + cur[i + 1:]
        failure, _ = replay(compiler, work, seed, trial, label + '-shrink')
        if failure:
            cur = trial
        else:
            i += 1
    return cur


def pending_case(compiler, work, check):
    """cleanup_pending 正例：worker 根里放陌生文件使其清不掉（scratchClean 不删不认识的条目）。"""
    seq = work / 'pending'
    if seq.exists():
        shutil.rmtree(seq)
    tmp = seq / 'tmp'
    tmp.mkdir(parents=True)
    ws = workspace(seq)
    rng = random.Random(SEED)
    server = Server(compiler, ws, tmp)
    rid = [0]

    def do(name, args):
        rid[0] += 1
        server.write(tool(rid[0], name, args), rng)
        return tool_result(server.take(rid[0]))

    planted = []
    try:
        for gen in (1, 2):
            s, e, err = do('compile', {'path': 'main.tx'})
            have = [n for g, n in roots(tmp) if g == gen]
            check('pending: generation %d starts' % gen, err is None and (s or {}).get('ok') is True and len(have) == 1, (s, err, roots(tmp)))
            if not have:
                return
            marker = tmp / have[0] / 'unknown.txt'
            marker.write_text('not a product', encoding='utf-8')
            planted.append(marker)
            s, e, err = do('compile', {'path': 'main.tx', 'timeout_ms': 1})
            check('pending: generation %d terminated, root reported pending' % gen,
                  err is None and (s or {}).get('reason') == 'request_timeout' and s.get('artifacts_cleaned') is False
                  and any(have[0] in p for p in s.get('cleanup_pending_paths', [])), (s, err))
        s, e, err = do('compile', {'path': 'main.tx'})
        check('pending: at two uncleaned roots a new worker is refused with cleanup_pending',
              err is None and (s or {}).get('schema') == 'tool-error/1' and s.get('reason') == 'cleanup_pending' and e is True
              and len(s.get('cleanup_pending_paths', [])) == 2, (s, err))
        check('pending: refusal starts no worker', not descendants(server.proc.pid) and len(roots(tmp)) == 2, (descendants(server.proc.pid), roots(tmp)))
        for marker in planted:
            marker.unlink()
        end = time.time() + 5
        while time.time() < end and roots(tmp):
            time.sleep(0.05)
        check('pending: idle sweep cleans the roots once the strangers are gone', not roots(tmp), roots(tmp))
        s, e, err = do('compile', {'path': 'main.tx'})
        check('pending: recovery with the next generation', err is None and (s or {}).get('ok') is True
              and [g for g, _ in roots(tmp)] == [3], (s, err, roots(tmp)))
    finally:
        code = server.close()
        check('pending: clean shutdown', code == 0 and not server.bad, (code, server.bad[:2], server.err[-300:]))


# ---------------------------------------------------------------------------------------------------------------------
# LSP：Content-Length 分帧，同样的切片写入与粘包。

class Lsp:
    def __init__(self, compiler, ws):
        self.proc = subprocess.Popen([str(compiler), 'lsp', '--stdio', '--workspace', str(ws)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ws)
        self.msgs, self.notes, self.bad, self.err = [], [], [], b''
        self.lock = threading.Lock()
        self.readers = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=lambda: setattr(self, 'err', self.proc.stderr.read()), daemon=True)]
        for t in self.readers:
            t.start()

    def _read(self):
        f = self.proc.stdout
        while True:
            length = None
            while True:
                line = f.readline()
                if not line:
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b'content-length:'):
                    length = int(line.split(b':', 1)[1])
            if length is None:
                with self.lock:
                    self.bad.append('frame without Content-Length')
                return
            body = f.read(length)
            try:
                m = mcp_e2e.strict_json(body.decode('utf-8'))
            except (ValueError, UnicodeError):
                with self.lock:
                    self.bad.append(body[:200])
                continue
            with self.lock:
                (self.notes if 'id' not in m else self.msgs).append(m)

    def frame(self, body):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        return b'Content-Length: %d\r\n\r\n' % len(body) + body

    write = Server.write
    take = Server.take

    def close(self, timeout=10):
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None
        for t in self.readers:
            t.join(5)
        return code


GOOD_SRC = 'export function main(args: []string): int {\n    let x = 1;\n    return x;\n}\n'
BAD_SRC = 'export function main(args: []string): int {\n    return missing;\n}\n'


def diagnostics_mismatch(docs, notes):
    """仍打开的文档里，末次诊断与当前文本（有错／无错）不符的 URI。从未发布等同于空：去抖把“坏→好”合并成一次分析、
    结果为空且此前没发布过时，服务不发（LSP 只要求诊断变化时发布）；曾发布过错误、改好后却没发清空，仍判不符。"""
    last = {}
    for note in notes:
        if note.get('method') == 'textDocument/publishDiagnostics':
            last[note['params']['uri']] = note['params'].get('diagnostics', [])
    return [u for u, (_, bad_doc) in docs.items() if bool(last.get(u, [])) != bad_doc]


def lsp_sequence(compiler, work, seed, steps, label):
    """一个 LSP 序列；返回失败说明或 None。文档的预期诊断：当前文本有错即非空，无错即空。"""
    rng = random.Random(seed)
    seq = work / label
    if seq.exists():
        shutil.rmtree(seq)
    ws = seq / 'ws'
    ws.mkdir(parents=True)
    docs = {}  # uri → (版本, 是否有错)
    names = ['a.tx', 'b.tx']
    for n in names:
        (ws / n).write_text(GOOD_SRC, encoding='utf-8', newline='\n')
    uri_of = {n: (ws / n).resolve().as_uri() for n in names}
    server = Lsp(compiler, ws)
    rid = [0]
    failure = None

    def request(method, params, expect):
        rid[0] += 1
        server.write(server.frame({'jsonrpc': '2.0', 'id': rid[0], 'method': method, 'params': params}), rng)
        r = server.take(rid[0])
        return None if r is not None and expect(r) else '%s: %r' % (method, r)

    try:
        failure = request('initialize', {'processId': None, 'rootUri': ws.resolve().as_uri(), 'capabilities': {}},
                          lambda r: 'capabilities' in r.get('result', {}))
        server.write(server.frame({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}}), rng)
        kinds = ['open', 'change', 'close', 'symbols', 'hover', 'format', 'unknown', 'bad-json', 'budget-at', 'budget-over']
        weights = [3, 6, 1, 2, 2, 2, 1, 1, 1, 1]
        for step in range(steps):
            if failure:
                break
            kind = rng.choices(kinds, weights)[0]
            name = rng.choice(names)
            uri = uri_of[name]
            bad_text = rng.random() < 0.5
            text = BAD_SRC if bad_text else GOOD_SRC
            if kind == 'open' and uri not in docs:
                docs[uri] = (1, bad_text)
                server.write(server.frame({'jsonrpc': '2.0', 'method': 'textDocument/didOpen', 'params': {
                    'textDocument': {'uri': uri, 'languageId': 'taixu', 'version': 1, 'text': text}}}), rng)
            elif kind == 'change' and uri in docs:
                version = docs[uri][0] + 1
                docs[uri] = (version, bad_text)
                server.write(server.frame({'jsonrpc': '2.0', 'method': 'textDocument/didChange', 'params': {
                    'textDocument': {'uri': uri, 'version': version}, 'contentChanges': [{'text': text}]}}), rng)
            elif kind == 'close' and uri in docs:
                del docs[uri]
                server.write(server.frame({'jsonrpc': '2.0', 'method': 'textDocument/didClose', 'params': {'textDocument': {'uri': uri}}}), rng)
            elif kind == 'symbols' and uri in docs:
                failure = request('textDocument/documentSymbol', {'textDocument': {'uri': uri}},
                                  lambda r: 'result' in r and (r['result'] is None or isinstance(r['result'], list)))
            elif kind == 'hover' and uri in docs:
                failure = request('textDocument/hover', {'textDocument': {'uri': uri}, 'position': {'line': 1, 'character': 8}}, lambda r: 'result' in r)
            elif kind == 'format' and uri in docs:
                failure = request('textDocument/formatting', {'textDocument': {'uri': uri}, 'options': {'tabSize': 4, 'insertSpaces': True}},
                                  lambda r: 'result' in r and (r['result'] is None or isinstance(r['result'], list)))
            elif kind == 'unknown':
                failure = request('no/such', {}, lambda r: r.get('error', {}).get('code') == -32601)
            elif kind == 'bad-json':
                server.write(server.frame(b'{"jsonrpc":"2.0","id":'), rng)
                r = server.take(None)
                failure = None if r and r.get('error', {}).get('code') == -32700 else 'bad-json: %r' % (r,)
            elif kind in ('budget-at', 'budget-over'):
                rid[0] += 1
                body = budget_line(rid[0], BUDGET if kind == 'budget-at' else BUDGET + 1).rstrip(b'\n').replace(b'"tools/list"', b'"no/such"')
                server.write(server.frame(body), rng)
                if kind == 'budget-at':
                    r = server.take(rid[0])
                    failure = None if r and r.get('error', {}).get('code') == -32601 else 'budget-at: %r' % (r,)
                else:
                    r = server.take(None)
                    failure = None if r and r.get('error', {}).get('message') == 'RequestTooComplex' else 'budget-over: %r' % (r,)
            with server.lock:
                if server.bad and not failure:
                    failure = 'non-JSON frame %r' % server.bad[:2]
        # 每个仍打开的文档：最后一次诊断须与其当前文本一致（去抖之后）。
        end = time.time() + 5
        while failure is None and time.time() < end:
            with server.lock:
                mismatch = diagnostics_mismatch(docs, list(server.notes))
            if not mismatch:
                break
            time.sleep(0.05)
        else:
            if failure is None:
                failure = 'final diagnostics disagree for %r' % mismatch
        if failure is None:
            failure = request('shutdown', None, lambda r: 'result' in r and r['result'] is None)
        server.write(server.frame({'jsonrpc': '2.0', 'method': 'exit'}), rng)
        code = server.close()
        if failure is None and code != 0:
            failure = 'exit code %r after shutdown/exit' % code
        if failure is None and server.bad:
            failure = 'non-JSON frame %r' % server.bad[:2]
    finally:
        if server.proc.poll() is None:
            server.proc.kill()
    return failure


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--sequences', type=int, default=8)
    ap.add_argument('--steps', type=int, default=24)
    ap.add_argument('--seed', type=int, default=SEED)
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    work = a.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rows, bad = [], [0]

    def check(label, ok, detail=''):
        rows.append({'check': label, 'ok': bool(ok), 'detail': detail if ok else str(detail)[:3000]})
        print('%s %s%s' % ('PASS' if ok else 'FAIL', label, '' if ok else ': ' + str(detail)[:600]), flush=True)
        if not ok:
            bad[0] += 1

    # 判据自检：预算请求恰在边界上；各类坏结局须判失败。
    check('budget lines hit the boundary exactly', tokens(budget_line(1, BUDGET)) == BUDGET and tokens(budget_line(1, BUDGET + 1)) == BUDGET + 1)
    m = Model()
    check('oracle: missing reply fails', judge_tool('compile-ok')(None, m) is not None)
    check('oracle: generation jump fails', judge_tool('hard-timeout')({'result': {'structuredContent': {'schema': 'tool-error/1', 'reason': 'request_timeout',
          'termination_requested': True, 'worker_generation': 5, 'reaped': True, 'artifacts_cleaned': True, 'cleanup_pending_paths': []},
          'content': [{'type': 'text', 'text': json.dumps({'schema': 'tool-error/1', 'reason': 'request_timeout', 'termination_requested': True,
          'worker_generation': 5, 'reaped': True, 'artifacts_cleaned': True, 'cleanup_pending_paths': []})}], 'isError': True}}, Model()) is not None)
    check('oracle: wrong error code fails', judge_rpc(code=-32700)({'error': {'code': -32600}}, m) is not None)
    note = lambda n: {'method': 'textDocument/publishDiagnostics', 'params': {'uri': 'u', 'diagnostics': [{}] * n}}
    check('oracle: erroneous text without diagnostics fails', diagnostics_mismatch({'u': (1, True)}, []) == ['u'])
    check('oracle: fixed text whose error was never cleared fails', diagnostics_mismatch({'u': (2, False)}, [note(1)]) == ['u'])
    check('oracle: cleared or never-published fixed text passes', diagnostics_mismatch({'u': (2, False)}, [note(1), note(0)]) == []
          and diagnostics_mismatch({'u': (2, False)}, []) == [])

    pending_case(compiler, work, check)
    for n in range(a.sequences):
        seed = a.seed + n
        failure, names = run_sequence(compiler, work, seed, a.steps, 'seq-%d' % n)
        detail = {'seed': seed, 'commands': names, 'failure': failure}
        if failure:
            rng = random.Random(seed)
            cmds = commands()
            plan = [rng.choices(range(len(cmds)), [w for _, w, _, _ in cmds])[0] for _ in range(a.steps)]
            small = shrink(compiler, work, seed, plan, 'seq-%d' % n)
            detail['minimal'] = [cmds[i][0] for i in small]
        check('sequence seed %d (%d steps)' % (seed, a.steps), failure is None, detail)
    for n in range(a.sequences):
        seed = a.seed + 1000 + n
        failure = lsp_sequence(compiler, work, seed, a.steps, 'lsp-%d' % n)
        check('lsp sequence seed %d (%d steps)' % (seed, a.steps), failure is None, failure)
    (work / 'service-sequence.json').write_text(json.dumps({'seed': a.seed, 'rows': rows, 'failed': bad[0]}, ensure_ascii=False, indent=1, default=str),
                                                encoding='utf-8')
    print('SUMMARY %s service-sequence: %d checks, %d failed' % ('PASS' if bad[0] == 0 else 'FAIL', len(rows), bad[0]), flush=True)
    sys.exit(0 if bad[0] == 0 else 1)


if __name__ == '__main__':
    main()
