"""LSP 聚合资源预算与输出背压（安全审查攻关第 5 项，docs/design/v052-lsp-resources-v0.1-20261025.md）。

对真实的 `taixu lsp` 进程核对：initialize 宣告的资源预算（客户端只能收紧）；打开文档的个数、合计字节、单文档字节与一条
didChange 的编辑工作量超出时文档转为超预算（说明诊断、请求回 -32803 与 reason、全文变更恢复、导入方按磁盘内容分析）；
上千个独立文档一轮分析的时限；连续编辑时去抖的最长等待；$/cancelRequest（排队中与正在同步分析的请求回 -32800）；分析
预算与热缓存预算；单文件诊断截断；回复过大；客户端停读时服务阻塞而内存、CPU 不涨，恢复读后消息完整，阻塞中关读端即退出。

  python scripts/test-lsp-resources.py --compiler <taixu> --work <dir>
"""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('mcp_e2e', ROOT / 'scripts' / 'mcp-debug-e2e.py')
mcp_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_e2e)

WINDOWS = os.name == 'nt'
MIB = 1048576
DEFAULTS = {'max_message_bytes': 32 * MIB, 'max_response_bytes': 32 * MIB, 'max_document_bytes': 32 * MIB, 'max_open_bytes': 256 * MIB,
            'max_open_documents': 1024, 'max_change_work_bytes': 64 * MIB, 'max_analysis_source_bytes': 32 * MIB,
            'max_hot_cache_source_bytes': 4 * MIB, 'max_diagnostics_per_file': 1000, 'max_diagnostics_bytes_per_file': MIB,
            'debounce_ms': 300, 'max_debounce_ms': 2000, 'cancellation': 'between_roots', 'client_stdout_write_nonblocking': False,
            'client_stdout_write_deadline_ms': None}


def good(i):
    return 'export function main(args: []string): int {\n    let v%d = %d;\n    return v%d;\n}\n' % (i, i, i)


def bad(name):
    return 'export function main(args: []string): int {\n    return %s;\n}\n' % name


def errors(n):
    body = ''.join('    let e%d: int = missing%d;\n' % (i, i) for i in range(n))
    return 'export function main(args: []string): int {\n' + body + '    return 0;\n}\n'


class Lsp:
    """一个 `taixu lsp` 进程：读线程收帧（可推迟启动以模拟停读），主线程按 id 等回复、按 URI 取最后一次诊断。"""

    def __init__(self, compiler, ws, read=True):
        self.proc = subprocess.Popen([str(compiler), 'lsp', '--stdio', '--workspace', str(ws)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ws)
        self.msgs, self.notes, self.bad, self.err = {}, [], [], b''
        self.lock = threading.Lock()
        self.next_id = 0
        self.reader = None
        threading.Thread(target=lambda: setattr(self, 'err', self.proc.stderr.read()), daemon=True).start()
        if read:
            self.start_reader()

    def start_reader(self):
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        f = self.proc.stdout
        while True:
            length = None
            while True:
                try:
                    line = f.readline()
                except (OSError, ValueError):
                    return
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
                if 'id' in m and 'method' not in m:
                    self.msgs[json.dumps(m['id'])] = m
                else:
                    self.notes.append((time.monotonic(), m))

    @staticmethod
    def frame(obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        return b'Content-Length: %d\r\n\r\n' % len(b) + b

    def write(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, obj):
        self.write(self.frame(obj))

    def new_id(self):
        self.next_id += 1
        return self.next_id

    def req_frame(self, method, params, rid=None):
        return self.frame({'jsonrpc': '2.0', 'id': rid if rid is not None else self.new_id(), 'method': method, 'params': params})

    def wait(self, rid, timeout=120):
        key = json.dumps(rid)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if key in self.msgs:
                    return self.msgs[key]
            if self.proc.poll() is not None:
                return None
            time.sleep(0.005)
        return None

    def request(self, method, params, timeout=120):
        rid = self.new_id()
        self.send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        return self.wait(rid, timeout)

    def init(self, res=None):
        params = {'capabilities': {'general': {'positionEncodings': ['utf-8']}}}
        if res is not None:
            params['initializationOptions'] = {'taixuResources': res}
        r = self.request('initialize', params)
        self.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
        return r

    def open(self, path, text, version=1):
        self.send({'jsonrpc': '2.0', 'method': 'textDocument/didOpen',
                   'params': {'textDocument': {'uri': path.as_uri(), 'languageId': 'taixu', 'version': version, 'text': text}}})

    def change(self, path, version, changes):
        self.send({'jsonrpc': '2.0', 'method': 'textDocument/didChange',
                   'params': {'textDocument': {'uri': path.as_uri(), 'version': version}, 'contentChanges': changes}})

    def close_doc(self, path):
        self.send({'jsonrpc': '2.0', 'method': 'textDocument/didClose', 'params': {'textDocument': {'uri': path.as_uri()}}})

    def note_count(self):
        with self.lock:
            return len(self.notes)

    def publishes(self, path=None):
        uri = path.as_uri() if path is not None else None
        with self.lock:
            return [(t, m['params']) for t, m in self.notes
                    if m.get('method') == 'textDocument/publishDiagnostics' and (uri is None or m['params']['uri'] == uri)]

    def last(self, path):
        """最后一次发布的诊断；从未发布为 None。"""
        p = self.publishes(path)
        return p[-1][1]['diagnostics'] if p else None

    def logs(self):
        with self.lock:
            return [m['params']['message'] for _, m in self.notes if m.get('method') == 'window/logMessage']

    def settle(self, quiet=1.5, timeout=300):
        """等到 quiet 秒内没有新通知（每轮用一个廉价请求确认服务在处理输入）。"""
        start = time.monotonic()
        end = start + timeout
        while time.monotonic() < end:
            if self.request('taixu/none', {}, timeout=timeout) is None:
                return False
            with self.lock:
                last = max(start, self.notes[-1][0] if self.notes else 0)
            if time.monotonic() - last >= quiet:
                return True
            time.sleep(0.1)
        return False

    def wait_for(self, pred, timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            if self.proc.poll() is not None:
                return pred()
            time.sleep(0.02)
        return False

    def shutdown(self, timeout=60):
        try:
            r = self.request('shutdown', None, timeout=timeout)
            self.send({'jsonrpc': '2.0', 'method': 'exit'})
            code = self.proc.wait(timeout)
            return code if r is not None else 'no-shutdown-reply:%r' % code
        except (OSError, subprocess.TimeoutExpired) as e:
            self.proc.kill()
            self.proc.wait()
            return 'killed (%s)' % e.__class__.__name__

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    # —— 进程的工作集与 CPU 时间 ——
    def rss(self):
        if WINDOWS:
            class Counters(ctypes.Structure):
                _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in (
                    'peak', 'working', 'pp', 'p', 'pnp', 'np', 'pagefile', 'peak_pagefile')]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            get = ctypes.WinDLL('psapi').GetProcessMemoryInfo
            get.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
            get(int(self.proc._handle), ctypes.byref(c), c.cb)
            return c.working
        status = Path('/proc') / str(self.proc.pid) / 'status'
        if status.exists():
            for line in status.read_text().splitlines():
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024
        out = subprocess.run(['ps', '-o', 'rss=', '-p', str(self.proc.pid)], capture_output=True, text=True).stdout.strip()
        return int(out) * 1024 if out else 0

    def cpu(self):
        if WINDOWS:
            k = ctypes.windll.kernel32
            a, b, c, d = (ctypes.c_ulonglong() for _ in range(4))
            k.GetProcessTimes(int(self.proc._handle), ctypes.byref(a), ctypes.byref(b), ctypes.byref(c), ctypes.byref(d))
            return (c.value + d.value) / 1e7
        stat = Path('/proc') / str(self.proc.pid) / 'stat'
        if stat.exists():
            f = stat.read_text().rsplit(')', 1)[1].split()
            return (int(f[11]) + int(f[12])) / os.sysconf('SC_CLK_TCK')
        out = subprocess.run(['ps', '-o', 'time=', '-p', str(self.proc.pid)], capture_output=True, text=True).stdout.strip()
        parts = [float(x) for x in out.replace('-', ':').split(':')] if out else [0]
        sec = 0.0
        for x in parts:
            sec = sec * 60 + x
        return sec


def failed(reply, reason, code=-32803):
    return bool(reply) and reply.get('error', {}).get('code') == code and (reason is None or reply['error'].get('data', {}).get('reason') == reason)


def note_codes(diags):
    return [d.get('code') for d in diags or []]


def hover(lsp, path, line=0, ch=16):
    return lsp.request('textDocument/hover', {'textDocument': {'uri': path.as_uri()}, 'position': {'line': line, 'character': ch}})


def run(compiler, work, check):
    ws = work / 'ws'
    ws.mkdir(parents=True)

    # 1. 资源宣告：缺省值；收紧被采用、放宽被忽略、低于下限取下限。
    lsp = Lsp(compiler, ws)
    r = lsp.init()
    got = (r or {}).get('result', {}).get('capabilities', {}).get('experimental', {}).get('taixuResources')
    check('resources: defaults advertised', got == DEFAULTS, got)
    check('resources: default server exits cleanly', lsp.shutdown() == 0)
    lsp = Lsp(compiler, ws)
    r = lsp.init({'max_open_documents': 7, 'max_open_bytes': 10 * 1024 ** 3, 'max_diagnostics_bytes_per_file': 10, 'max_debounce_ms': 1,
                  'max_hot_cache_source_bytes': -5, 'max_document_bytes': 'x'})
    got = (r or {}).get('result', {}).get('capabilities', {}).get('experimental', {}).get('taixuResources', {})
    want = dict(DEFAULTS, max_open_documents=7, max_diagnostics_bytes_per_file=4096, max_debounce_ms=300, max_hot_cache_source_bytes=0)
    check('resources: client can only tighten (min, floors, wrong types ignored)', got == want, got)
    check('resources: tightened server exits cleanly', lsp.shutdown() == 0)

    # 2. 文档个数（缺省 1024）：上千个独立文档一轮分析在时限内完成；第 1025 个超预算，关掉一个后全文变更恢复。
    many = ws / 'many'
    many.mkdir()
    lsp = Lsp(compiler, ws)
    lsp.init()
    t0 = time.monotonic()
    for i in range(1024):
        f = many / ('m%d.tx' % i)
        f.write_text(bad('missing%d' % i), encoding='utf-8', newline='\n')
        lsp.open(f, bad('missing%d' % i))
    done = lsp.wait_for(lambda: len({p['uri'] for _, p in lsp.publishes() if p['diagnostics']}) >= 1024, timeout=240)
    seconds = time.monotonic() - t0
    check('documents: 1024 independent documents analysed within 120 s', done and seconds < 120, 'done=%s seconds=%.1f' % (done, seconds))
    extra = many / 'extra.tx'
    extra.write_text(good(1), encoding='utf-8', newline='\n')
    lsp.open(extra, good(1))
    lsp.wait_for(lambda: lsp.last(extra) is not None, timeout=60)
    check('documents: document 1025 is over budget with a document_budget note', note_codes(lsp.last(extra)) == ['document_budget'], lsp.last(extra))
    check('documents: requests on it fail with reason document_budget', failed(hover(lsp, extra), 'document_budget'))
    lsp.close_doc(many / 'm0.tx')
    lsp.change(extra, 2, [{'text': good(2)}])
    ok = lsp.wait_for(lambda: lsp.last(extra) == [], timeout=60)
    sym = lsp.request('textDocument/documentSymbol', {'textDocument': {'uri': extra.as_uri()}})
    check('documents: after closing one, a full-text change readmits it', ok and sym is not None and 'result' in sym and sym['result'], (lsp.last(extra), sym))
    check('documents: server exits cleanly', lsp.shutdown() == 0)

    # 3. 合计字节（收紧到 1 MiB）与单文档字节（收紧到 64 KiB，经 range 增长越界）。
    tb = ws / 'bytes'
    tb.mkdir()
    lsp = Lsp(compiler, ws)
    lsp.init({'max_open_bytes': MIB, 'max_document_bytes': 400 * 1024})
    pad = '// ' + 'p' * 1000 + '\n'
    docs = []
    for i in range(3):
        f = tb / ('b%d.tx' % i)
        text = good(i) + pad * 350
        f.write_text(text, encoding='utf-8', newline='\n')
        lsp.open(f, text)
        docs.append(f)
    lsp.wait_for(lambda: lsp.last(docs[2]) is not None, timeout=60)
    check('open bytes: the third 350 KiB document is over the 1 MiB total', note_codes(lsp.last(docs[2])) == ['document_budget'], lsp.last(docs[2]))
    grow = docs[1]
    lsp.change(grow, 2, [{'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}}, 'text': pad * 60}])
    lsp.wait_for(lambda: lsp.last(grow) is not None, timeout=60)
    check('document bytes: a range edit growing a document past 400 KiB puts it over budget',
          note_codes(lsp.last(grow)) == ['document_budget'] and failed(hover(lsp, grow), 'document_budget'), lsp.last(grow))
    lsp.change(grow, 3, [{'text': bad('regrown')}])
    ok = lsp.wait_for(lambda: len(lsp.last(grow) or []) == 1 and lsp.last(grow)[0].get('source') == 'taixu', timeout=60)
    check('document bytes: a small full-text change readmits it and analyses the new text', ok, lsp.last(grow))
    check('bytes: server exits cleanly', lsp.shutdown() == 0)

    # 4. 编辑工作量（缺省 64 MiB）：4 MiB 文档上 100 个 range 编辑被拒且很快；1 MiB 文档上一个 range 编辑照常、诊断位置正确。
    cw = ws / 'work'
    cw.mkdir()
    lsp = Lsp(compiler, ws)
    lsp.init()
    big = cw / 'big.tx'
    text = good(0) + ('// ' + 'w' * 60 + '\n') * (4 * MIB // 64)
    big.write_text(text, encoding='utf-8', newline='\n')
    lsp.open(big, text)
    lsp.settle()
    edit = {'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}}, 'text': ' '}
    t0 = time.monotonic()
    lsp.change(big, 2, [edit] * 100)
    r = lsp.request('taixu/none', {})
    seconds = time.monotonic() - t0
    lsp.wait_for(lambda: lsp.last(big) is not None, timeout=60)
    check('change work: 100 range edits on a 4 MiB document are refused quickly (change_budget)',
          r is not None and seconds < 10 and note_codes(lsp.last(big)) == ['change_budget'], 'seconds=%.1f diags=%r' % (seconds, lsp.last(big)))
    mid = cw / 'mid.tx'
    text = good(0) + ('// ' + 'm' * 60 + '\n') * (MIB // 64)
    mid.write_text(text, encoding='utf-8', newline='\n')
    lsp.open(mid, text)
    lsp.settle()
    lsp.change(mid, 2, [{'range': {'start': {'line': 2, 'character': 11}, 'end': {'line': 2, 'character': 13}}, 'text': 'nowhere'}])
    ok = lsp.wait_for(lambda: bool(lsp.last(mid)), timeout=60)
    d = (lsp.last(mid) or [{}])[0]
    check('change work: one range edit applies and is analysed at the right place', ok and d.get('range', {}).get('start', {}).get('line') == 2, lsp.last(mid))
    check('change work: server exits cleanly', lsp.shutdown() == 0)

    # 5. 超预算文档按未打开处理：导入方改按磁盘内容分析。
    imp = ws / 'imp'
    imp.mkdir()
    a, b = imp / 'a.tx', imp / 'b.tx'
    b.write_text('export function f(): int { return 1; }\n', encoding='utf-8', newline='\n')
    a.write_text('import { f } from "./b";\nexport function main(args: []string): int { return f(); }\n', encoding='utf-8', newline='\n')
    lsp = Lsp(compiler, ws)
    lsp.init({'max_document_bytes': 4096})
    lsp.open(a, a.read_text(encoding='utf-8'))
    lsp.open(b, 'export function g(): int { return 1; }\n')
    ok = lsp.wait_for(lambda: bool(lsp.last(a)), timeout=60)
    check('held document: the importer sees the unsaved overlay first (f missing)', ok, lsp.last(a))
    lsp.change(b, 2, [{'text': 'export function g(): int { return 1; }\n' + '// ' + 'h' * 5000 + '\n'}])
    ok = lsp.wait_for(lambda: lsp.last(a) == [] and note_codes(lsp.last(b)) == ['document_budget'], timeout=60)
    check('held document: the importer is re-analysed against the disk content', ok, (lsp.last(a), lsp.last(b)))
    check('held document: server exits cleanly', lsp.shutdown() == 0)

    # 6. 连续编辑：每 100 ms 一次、持续 6 s，期间至少 2 次发布（最长等待 2 s）。
    st = ws / 'starve.tx'
    st.write_text(bad('missingStart'), encoding='utf-8', newline='\n')
    lsp = Lsp(compiler, ws)
    lsp.init()
    lsp.open(st, bad('missingStart'))
    lsp.settle()
    n0 = len(lsp.publishes(st))
    end = time.monotonic() + 6
    v = 2
    while time.monotonic() < end:
        v += 1
        lsp.change(st, v, [{'text': bad('missing%d' % v)}])
        time.sleep(0.1)
    during = len(lsp.publishes(st)) - n0
    check('debounce: continuous edits still publish (max wait)', during >= 2, 'publishes during 6 s of edits: %d' % during)
    check('debounce: server exits cleanly', lsp.shutdown() == 0)

    # 7. 取消：正在同步分析的请求在入口之间回 -32800；排队中被取消的请求回 -32800；取消未知 id 无副作用。
    cancel_case(compiler, ws, check)

    # 8. 分析预算（收紧）与热缓存预算（收紧到 0：只留最近一个）。
    budget_case(compiler, ws, check)

    # 9. 单文件诊断截断与回复过大。
    out = ws / 'out'
    out.mkdir()
    many_err = out / 'errs.tx'
    many_err.write_text(errors(1500), encoding='utf-8', newline='\n')
    lsp = Lsp(compiler, ws)
    lsp.init()
    lsp.open(many_err, errors(1500))
    lsp.wait_for(lambda: lsp.last(many_err) is not None, timeout=120)
    ds = lsp.last(many_err) or []
    check('diagnostics: 1500 errors are cut to 1000 entries ending with diagnostics_truncated',
          len(ds) == 1000 and ds[-1].get('code') == 'diagnostics_truncated' and '501' in ds[-1].get('message', ''), (len(ds), ds[-1:] if ds else ds))
    check('diagnostics: server exits cleanly', lsp.shutdown() == 0)
    lsp = Lsp(compiler, ws)
    lsp.init({'max_diagnostics_per_file': 10, 'max_diagnostics_bytes_per_file': 4096, 'max_response_bytes': 8192})
    lsp.open(many_err, errors(1500))
    lsp.wait_for(lambda: lsp.last(many_err) is not None, timeout=120)
    ds = lsp.last(many_err) or []
    size = len(json.dumps(ds, ensure_ascii=False).encode())
    check('diagnostics: tightened caps hold (<= 10 entries, about 4 KiB)', 0 < len(ds) <= 10 and ds[-1].get('code') == 'diagnostics_truncated' and size <= 4096,
          (len(ds), size))
    r = lsp.request('textDocument/semanticTokens/full', {'textDocument': {'uri': many_err.as_uri()}})
    check('responses: a reply over max_response_bytes becomes RequestFailed response_too_large', failed(r, 'response_too_large'), r)
    check('responses: server exits cleanly', lsp.shutdown() == 0)

    # 10. 停读矩阵。
    stop_read_case(compiler, ws, check)


def cancel_case(compiler, ws, check):
    cc = ws / 'cancel'
    cc.mkdir()
    k, big, a, b = cc / 'k.tx', cc / 'big.tx', cc / 'a.tx', cc / 'b.tx'
    k.write_text('export function kv(): int { return 1; }\n', encoding='utf-8', newline='\n')
    body = ''.join('export function f%d(x: int): int {\n    let a = x + kv();\n    let b = a * 2 + %d;\n    if (b > 10) { return b - a; }\n    return a + b;\n}\n'
                   % (i, i) for i in range(4000))
    big.write_text('import { kv } from "./k";\n' + body, encoding='utf-8', newline='\n')
    for f in (a, b):
        f.write_text('import { f0 } from "./big";\nexport function main(args: []string): int { return f0(1); }\n', encoding='utf-8', newline='\n')
    lsp = Lsp(compiler, ws)
    lsp.init()
    for f in (a, b, k):
        lsp.open(f, f.read_text(encoding='utf-8'))
    lsp.settle(quiet=2.0)
    # 校准：k 改动后同步分析一个入口要多久（两个入口各重查 big）。
    lsp.change(k, 2, [{'text': 'export function kv(): int { return 2; }\n'}])
    t0 = time.monotonic()
    r = hover(lsp, k, 0, 16)
    per_root = (time.monotonic() - t0) / 2
    check('cancel: calibration request answered', r is not None and 'result' in r, r)
    # 正在处理的请求：请求与它的取消在同一次写入里，第一个取消点即生效。
    lsp.change(k, 3, [{'text': 'export function kv(): int { return 3; }\n'}])
    rid = lsp.new_id()
    lsp.write(lsp.req_frame('textDocument/hover', {'textDocument': {'uri': k.as_uri()}, 'position': {'line': 0, 'character': 16}}, rid)
              + lsp.frame({'jsonrpc': '2.0', 'method': '$/cancelRequest', 'params': {'id': rid}}))
    r = lsp.wait(rid)
    check('cancel: the request being handled answers RequestCancelled (-32800)', failed(r, None, code=-32800), r)
    lsp.settle(quiet=2.0)
    # 排队中的请求：R1 在第一个入口里分析时送来 R2 与它的取消；R1 照常答，R2 不做分析回 -32800。
    lsp.change(k, 4, [{'text': 'export function kv(): int { return 4; }\n'}])
    r1 = lsp.new_id()
    lsp.write(lsp.req_frame('textDocument/hover', {'textDocument': {'uri': k.as_uri()}, 'position': {'line': 0, 'character': 16}}, r1))
    time.sleep(min(0.3, per_root / 3))
    r2 = lsp.new_id()
    lsp.write(lsp.req_frame('textDocument/hover', {'textDocument': {'uri': k.as_uri()}, 'position': {'line': 0, 'character': 16}}, r2)
              + lsp.frame({'jsonrpc': '2.0', 'method': '$/cancelRequest', 'params': {'id': r2}}))
    a1, a2 = lsp.wait(r1), lsp.wait(r2)
    check('cancel: a queued request cancelled at a cancellation point answers -32800, the running one answers normally',
          a1 is not None and 'result' in a1 and failed(a2, None, code=-32800), {'per_root_seconds': round(per_root, 2), 'r1': a1, 'r2': a2})
    lsp.send({'jsonrpc': '2.0', 'method': '$/cancelRequest', 'params': {'id': 987654}})
    r = lsp.request('textDocument/documentSymbol', {'textDocument': {'uri': k.as_uri()}})
    with lsp.lock:
        stray = json.dumps(987654) in lsp.msgs
    check('cancel: cancelling an unknown id has no effect', r is not None and 'result' in r and not stray, r)
    check('cancel: server exits cleanly', lsp.shutdown() == 0)


def budget_case(compiler, ws, check):
    bc = ws / 'budget'
    bc.mkdir()
    p1, p2 = bc / 'p1.tx', bc / 'p2.tx'
    t1 = good(1) + '// ' + 'q' * 3000 + '\n'
    t2 = 'export function helper(): int { return 7; }\nexport function main(args: []string): int { return helper(); }\n' + '// ' + 'r' * 3000 + '\n'
    p1.write_text(t1, encoding='utf-8', newline='\n')
    p2.write_text(t2, encoding='utf-8', newline='\n')
    lsp = Lsp(compiler, ws)
    lsp.init({'max_analysis_source_bytes': 5000, 'max_hot_cache_source_bytes': 0})
    lsp.open(p1, t1)
    lsp.settle()
    lsp.open(p2, t2)
    ok = lsp.wait_for(lambda: note_codes(lsp.last(p2)) == ['analysis_budget'], timeout=60)
    check('analysis budget: the second root over the budget gets an analysis_budget note', ok, lsp.last(p2))
    check('analysis budget: requests on it fail with reason analysis_budget', failed(hover(lsp, p2, 1, 51), 'analysis_budget'))
    r = lsp.request('textDocument/rename', {'textDocument': {'uri': p1.as_uri()}, 'position': {'line': 1, 'character': 8}, 'newName': 'w1'})
    check('analysis budget: rename refuses while a root is unanalysed', failed(r, 'analysis_budget'), r)
    n = len(lsp.logs())
    r = lsp.request('textDocument/references', {'textDocument': {'uri': p1.as_uri()}, 'position': {'line': 1, 'character': 8}, 'context': {'includeDeclaration': True}})
    check('analysis budget: references answer with a logMessage about the unanalysed root',
          r is not None and 'result' in r and any('references' in m for m in lsp.logs()[n:]), (r, lsp.logs()[n:]))
    lsp.close_doc(p1)
    ok = lsp.wait_for(lambda: lsp.last(p2) == [], timeout=60)
    h = hover(lsp, p2, 1, 51)
    check('analysis budget: closing the other root re-admits it (note cleared, hover works)',
          ok and h is not None and 'result' in h and h['result'] is not None, (lsp.last(p2), h))
    check('budget: server exits cleanly', lsp.shutdown() == 0)
    # 热缓存预算 0：只有最近分析的入口留检查缓存。p1 先分析、p2 后分析，p1 的缓存被丢；p1 改坏、改好，诊断与 hover 同热时。
    lsp = Lsp(compiler, ws)
    lsp.init({'max_hot_cache_source_bytes': 0})
    lsp.open(p1, t1)
    lsp.settle()
    h = hover(lsp, p1, 1, 8)
    lsp.open(p2, t2)
    lsp.settle()
    lsp.change(p1, 2, [{'text': t1.replace('return v1;', 'return v1x;')}])
    bad_seen = lsp.wait_for(lambda: bool(lsp.last(p1)), timeout=60)
    lsp.change(p1, 3, [{'text': t1}])
    fixed = lsp.wait_for(lambda: lsp.last(p1) == [], timeout=60)
    h2 = hover(lsp, p1, 1, 8)
    check('hot cache budget: a cold root re-checks with the same results',
          bad_seen and fixed and h is not None and h2 is not None and h.get('result') is not None and h.get('result') == h2.get('result'),
          (lsp.last(p1), h, h2))
    check('hot cache: server exits cleanly', lsp.shutdown() == 0)


def stop_read_case(compiler, ws, check):
    sr = ws / 'stop'
    sr.mkdir()
    files = []
    for i in range(600):
        f = sr / ('s%d.tx' % i)
        text = bad('missingStop%d' % i) + '// ' + 's' * 200 + '\n'
        f.write_text(text, encoding='utf-8', newline='\n')
        files.append((f, text))

    def blocked_server():
        lsp = Lsp(compiler, ws, read=False)
        lsp.send({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'capabilities': {}}})
        lsp.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
        for f, text in files:
            lsp.open(f, text)
        # 等它阻塞：CPU 时间 2 s 内不再增长。
        last, since, end = lsp.cpu(), time.monotonic(), time.monotonic() + 120
        while time.monotonic() < end:
            time.sleep(0.5)
            now = lsp.cpu()
            if now - last > 0.05:
                last, since = now, time.monotonic()
            elif time.monotonic() - since >= 2:
                return lsp, True
        return lsp, False

    lsp, blocked = blocked_server()
    check('stop reading: the server blocks on its output (CPU time stops growing)', blocked and lsp.proc.poll() is None)
    stop, written = threading.Event(), [0]

    def flood():
        f, text = files[0]
        v = 2
        while not stop.is_set():
            v += 1
            try:
                lsp.change(f, v, [{'text': bad('missingFlood%d' % v) + '// ' + 'f' * 65536 + '\n'}])
            except OSError:
                return
            written[0] += 1
    writer = threading.Thread(target=flood, daemon=True)
    writer.start()
    rss0, cpu0 = lsp.rss(), lsp.cpu()
    time.sleep(3)
    w1 = written[0]
    time.sleep(2)
    rss1, cpu1, w2 = lsp.rss(), lsp.cpu(), written[0]
    check('stop reading: while blocked, memory and CPU stay flat and the client is held back',
          rss1 - rss0 < 16 * MIB and cpu1 - cpu0 < 0.3 and w2 == w1 and w2 < 200,
          {'rss_delta': rss1 - rss0, 'cpu_delta': round(cpu1 - cpu0, 3), 'writes': (w1, w2)})
    stop.set()
    lsp.start_reader()
    writer.join(60)
    lsp.settle(quiet=2.0, timeout=300)
    last_flood = 'missingFlood%d' % (written[0] + 2)
    finals = [lsp.last(f) for f, _ in files]
    code = lsp.shutdown(timeout=300)
    check('stop reading: after resuming, every frame parses, all diagnostics arrive and the server exits 0',
          code == 0 and not lsp.bad and all(d and len(d) == 1 for d in finals) and last_flood in json.dumps(finals[0]),
          {'code': code, 'bad': lsp.bad[:2], 'missing': sum(1 for d in finals if not d), 'first': finals[0], 'want': last_flood})
    lsp, blocked = blocked_server()
    t0 = time.monotonic()
    lsp.proc.stdout.close()
    try:
        code = lsp.proc.wait(10)
    except subprocess.TimeoutExpired:
        code = None
    lsp.kill()
    check('stop reading: closing the read end while blocked ends the server with exit 3 within 5 s',
          blocked and code == 3 and time.monotonic() - t0 < 5, {'blocked': blocked, 'code': code, 'seconds': round(time.monotonic() - t0, 2)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    work = a.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rows, bad_count = [], [0]

    def check(label, ok, detail=''):
        rows.append({'check': label, 'ok': bool(ok), 'detail': detail if ok else str(detail)[:3000]})
        print('%s %s%s' % ('PASS' if ok else 'FAIL', label, '' if ok else ': ' + str(detail)[:600]), flush=True)
        if not ok:
            bad_count[0] += 1

    # 判据自检。
    check('oracle: RequestFailed with another reason fails', not failed({'error': {'code': -32803, 'data': {'reason': 'x'}}}, 'document_budget'))
    check('oracle: a result is not a failure', not failed({'result': None}, 'document_budget') and not failed(None, None))
    check('oracle: RequestCancelled is recognised', failed({'error': {'code': -32800, 'message': 'RequestCancelled'}}, None, code=-32800))
    check('oracle: note codes read the code field', note_codes([{'code': 'change_budget'}, {}]) == ['change_budget', None] and note_codes(None) == [])
    run(compiler, work, check)
    (work / 'lsp-resources.json').write_text(json.dumps({'rows': rows, 'failed': bad_count[0]}, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print('SUMMARY %s lsp-resources: %d checks, %d failed' % ('PASS' if bad_count[0] == 0 else 'FAIL', len(rows), bad_count[0]), flush=True)
    sys.exit(0 if bad_count[0] == 0 else 1)


if __name__ == '__main__':
    main()
