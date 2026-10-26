#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LSP 与 MCP 核心工具的基准转录（第 6 轮 6d-0；设计 docs/design/v052-round6-lsp-mcp-v0.1-20261019.md §六）。

趁 Zig 版还在，用 Zig CLI 录下两条链路的转录，作为原生 `taixu lsp` 与 MCP `compile`、`run`、`test`（6d-2 起）的对照基准：
  - lsp：`taixu lsp` 会话（Content-Length 帧）；
  - mcp：`taixu mcp` 的核心工具（JSON-RPC 行协议）。

用例清单是 tests/lsp/cases.json，语料在 tests/lsp/ 下，转录写到 tests/lsp/zig/<用例>.<种类>。
转录格式同 scripts/debug-baseline.py：`> ` 为发出的消息，`< ` 为收到的消息，`= ` 为本脚本的记录（退出码、超时）。

LSP 用例的脚本项：
  - `open`、`close`：didOpen、didClose（文本取自磁盘）；
  - `change`：didChange，`text` 为全文、`replace` 为 [旧, 新]（须恰好出现一次，发全文）、`ranged` 为带 range 的一项；
  - `req`：请求，`file` 给出 textDocument，`at`＋`delta` 给出位置（`at` 在该文档非 `//` 行里的首次出现，
    再右移 `delta` 个字符，按协商的位置编码换算），`params` 并入参数，`id` 缺省为递增整数，`wait` 为等回复的秒数；
  - `notify`：通知（`exit` 之后读到 EOF 并记退出码）；`raw`：一帧任意正文；`frame`：任意字节（不加帧头）。
  `pre_init` 里的项在 initialize 之前发出。initialize 按 `encodings` 宣告 general.positionEncodings（null 为不宣告）。

每一步的收尾：请求等到它的回复；open 与 change 等到该文档的 publishDiagnostics；之后再等 150 ms 无新消息
（raw 与 notify 等 1 s）。等不到时记 `= {"harness":...}` 并继续。

规范化（只为跨机器一致，不改协议内容）：
  - tests/lsp 目录的路径换成 `<dir>`、其 file URI（原样或百分号编码、盘符大小写、`%3A` 各形）换成 `<uri>`，其后的分隔符统一为 `/`；
  - 对象的键同样规范化（rename 的 changes 以 URI 为键）；
  - `duration_us`、`at_ms` 置 0；MCP 回执的 stdout、stderr 里能解析为 JSON 的行先规范化再写回紧凑形；
  - MCP 的 tools/call 回复只记 structuredContent（与 isError）；
  - 进程崩溃（标准错误里有 Zig 的 panic 或段错误报告、Windows 异常状态码、被信号终止）时退出码记为 `abnormal`
    （Debug 版 Zig 的 panic 在 Windows 上退出码为 3，与 LSP 的坏帧退出码相同；ReleaseFast 版是 0xC0000005；Linux 上是信号）。

用法（仓根执行）：
  python scripts/lsp-baseline.py --taixu zig-out/bin/taixu.exe record   # 重写全部转录
  python scripts/lsp-baseline.py --taixu zig-out/bin/taixu.exe check    # 重录并与已存转录逐字节比较
可加 `--only 名字[,名字]`、`--out 目录`（缺省 tests/lsp/zig）。check 有任何不一致时退出码 1。
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LSP_DIR = ROOT / 'tests' / 'lsp'
STEP_TIMEOUT = 60.0
DIAG_TIMEOUT = 30.0
QUIET_S = 0.15
LOOSE_QUIET_S = 1.0

_spec = importlib.util.spec_from_file_location('debug_baseline', ROOT / 'scripts' / 'debug-baseline.py')
_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db)
dump, parse, LineReader, Transcript = _db.dump, _db.parse, _db.LineReader, _db.Transcript

STOP = set(' "\'\n\t:),;）（：，。')


def uri_of(path):
    return Path(path).resolve().as_uri()


class Normalizer:
    def __init__(self, base):
        base = Path(base).resolve()
        native = str(base)
        fwd = native.replace('\\', '/')
        paths = {native, fwd}
        if len(fwd) > 1 and fwd[1] == ':':
            for p in list(paths):
                paths.add(p[0].lower() + p[1:])
                paths.add(p[0].upper() + p[1:])
        uris = set()
        for p in {x.replace('\\', '/') for x in paths}:
            lead = 'file://' if p.startswith('/') else 'file:///'
            for safe in ('/:', '/'):
                q = urllib.parse.quote(p, safe=safe)
                uris.add(lead + q)
                uris.add(lead + _lower_hex(q))
            uris.add(lead + p)
        pairs = [(u, '<uri>') for u in uris] + [(p, '<dir>') for p in paths]
        self.pairs = sorted(pairs, key=lambda x: len(x[0]), reverse=True)

    def text(self, s):
        if not any(n in s for n, _ in self.pairs):
            return s
        out = []
        i = 0
        while i < len(s):
            hit = None
            for n, tok in self.pairs:
                if s.startswith(n, i):
                    hit = (n, tok)
                    break
            if hit is None:
                out.append(s[i])
                i += 1
                continue
            i += len(hit[0])
            j = i
            while j < len(s) and s[j] not in STOP:
                j += 1
            out.append(hit[1] + s[i:j].replace('\\', '/'))
            i = j
        return ''.join(out)

    def value(self, v, key=None):
        if isinstance(v, dict):
            return {self.text(k): (0 if k in ('duration_us', 'at_ms') else self.value(x, k)) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, str):
            if key in ('stdout', 'stderr'):
                return '\n'.join(self.json_line(x) for x in v.split('\n'))
            return self.text(v)
        return v

    def json_line(self, line):
        if line[:1] in ('{', '['):
            try:
                return dump(self.value(json.loads(line)))
            except ValueError:
                pass
        return self.text(line)


def _lower_hex(q):
    out = []
    i = 0
    while i < len(q):
        if q[i] == '%' and i + 2 < len(q):
            out.append(q[i:i + 3].lower())
            i += 3
        else:
            out.append(q[i])
            i += 1
    return ''.join(out)


def units(s, enc):
    if enc == 'utf-8':
        return len(s.encode('utf-8'))
    if enc == 'utf-32':
        return len(s)
    return len(s.encode('utf-16-le')) // 2


def index_of(line, ch, enc):
    """Code-point index in `line` of the position `ch` counted in `enc` units."""
    n = 0
    for i, c in enumerate(line):
        if n >= ch:
            return i
        n += units(c, enc)
    return len(line)


CRASH = re.compile(rb'Segmentation fault|panic:|General protection exception|Illegal instruction|Bus error')


def spawn(taixu, args):
    proc = subprocess.Popen([taixu, *args], cwd=LSP_DIR, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.err_chunks = []

    def drain():
        while True:
            b = proc.stderr.read(4096)
            if not b:
                return
            proc.err_chunks.append(b)
    proc.err_thread = threading.Thread(target=drain, daemon=True)
    proc.err_thread.start()
    return proc


def finish(proc, tr, reader, record):
    """Read to EOF and note the exit code; a crash (Zig panic or fault report, a Windows exception status, or a
    signal) is noted as `abnormal` because its code differs between builds and platforms."""
    try:
        proc.stdin.close()
    except OSError:
        pass
    while True:
        m = reader.get(STEP_TIMEOUT)
        if m is None:
            break
        if m == '__timeout__':
            tr.note({'harness': 'timeout', 'at': 'eof'})
            proc.kill()
            break
        record(m)
    try:
        code = proc.wait(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = 'killed'
    proc.err_thread.join(STEP_TIMEOUT)
    if isinstance(code, int) and (code < 0 or code >= 0xC0000000 or (code != 0 and CRASH.search(b''.join(proc.err_chunks)))):
        code = 'abnormal'
    tr.note({'exit': code})


# ---------------------------------------------------------------- lsp

class LspSession:
    def __init__(self, taixu, norm):
        self.norm = norm
        self.tr = Transcript(norm)
        self.proc = spawn(taixu, ['lsp'])
        self.reader = LineReader(self.proc.stdout, framed=True)
        self.docs = {}
        self.versions = {}
        self.enc = 'utf-16'
        self.next_id = 0
        self.eof = False
        self.dead = False

    def record(self, raw):
        self.tr.recv(self.norm.value(parse(raw)))

    def write(self, data):
        if self.dead:
            return False
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
            return True
        except OSError:
            self.dead = True
            self.tr.note({'harness': 'stdin closed'})
            return False

    def send(self, obj, shown):
        self.tr.send(dump(self.norm.value(shown)))
        body = dump(obj).encode('utf-8')
        return self.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)

    def settle(self, until=None, wait=STEP_TIMEOUT, quiet=QUIET_S, what=None):
        """Record messages until `until(msg)` holds (or `wait` runs out), then until `quiet` seconds of silence."""
        if self.eof:
            return None
        hit = None
        if until is not None:
            deadline = time.time() + wait
            while hit is None:
                left = deadline - time.time()
                raw = self.reader.get(max(left, 0.001)) if left > 0 else '__timeout__'
                if raw is None:
                    self.eof = True
                    self.tr.note({'eof': True})
                    return None
                if raw == '__timeout__':
                    self.tr.note({'harness': 'no ' + what})
                    break
                msg = parse(raw)
                self.record(raw)
                if until(msg):
                    hit = msg
        while True:
            raw = self.reader.get(quiet)
            if raw is None:
                self.eof = True
                self.tr.note({'eof': True})
                return hit
            if raw == '__timeout__':
                return hit
            self.record(raw)

    def uri(self, name):
        return uri_of(LSP_DIR / name)

    def diag_for(self, name):
        want = self.norm.text(self.uri(name))

        def ok(m):
            return (isinstance(m, dict) and m.get('method') == 'textDocument/publishDiagnostics'
                    and self.norm.text(str((m.get('params') or {}).get('uri'))) == want)
        return ok

    def position(self, name, at, delta):
        lines = self.docs[name].split('\n')
        for ln, line in enumerate(lines):
            if line.lstrip().startswith('//'):
                continue
            i = line.find(at)
            if i >= 0:
                return {'line': ln, 'character': units(line[:i + delta], self.enc)}
        raise SystemExit('marker %r not found in %s' % (at, name))

    def request(self, step):
        if 'id' in step:
            rid = step['id']
        else:
            self.next_id += 1
            rid = self.next_id
        params = {}
        if 'file' in step:
            params['textDocument'] = {'uri': self.uri(step['file'])}
        if 'at' in step:
            params['position'] = self.position(step['file'], step['at'], step.get('delta', 0))
        params.update(step.get('params', {}))
        msg = {'jsonrpc': '2.0', 'id': rid, 'method': step['req']}
        if params or step['req'] != 'shutdown':
            msg['params'] = params
        shown = {'id': rid, 'method': step['req']}
        if 'params' in msg:
            shown['params'] = params
        if not self.send(msg, shown):
            return None

        def ok(m):
            return isinstance(m, dict) and 'method' not in m and m.get('id') == rid
        return self.settle(ok, step.get('wait', STEP_TIMEOUT), what='response')

    def change(self, step):
        name = step['change']
        text = self.docs[name]
        self.versions[name] += 1
        shown = {'change': name, 'version': self.versions[name]}
        if 'text' in step:
            change = {'text': step['text']}
            self.docs[name] = step['text']
            shown['text'] = step['text']
        elif 'replace' in step:
            old, new = step['replace']
            if text.count(old) != 1:
                raise SystemExit('replace %r: expected once in %s' % (old, name))
            self.docs[name] = text.replace(old, new)
            change = {'text': self.docs[name]}
            shown['replace'] = step['replace']
        else:
            change = step['ranged']
            lines = text.split('\n')
            starts = [0]
            for line in lines:
                starts.append(starts[-1] + len(line) + 1)
            r = change['range']
            a = starts[r['start']['line']] + index_of(lines[r['start']['line']], r['start']['character'], self.enc)
            b = starts[r['end']['line']] + index_of(lines[r['end']['line']], r['end']['character'], self.enc)
            self.docs[name] = text[:a] + change['text'] + text[b:]
            shown['ranged'] = change
        msg = {'jsonrpc': '2.0', 'method': 'textDocument/didChange',
               'params': {'textDocument': {'uri': self.uri(name), 'version': self.versions[name]},
                          'contentChanges': [change]}}
        if self.send(msg, shown):
            self.settle(self.diag_for(name), step.get('wait', 3.0 if 'ranged' in step else DIAG_TIMEOUT),
                        what='diagnostics')

    def step(self, step):
        """Run one script item; returns False once the session is over."""
        if 'open' in step:
            name = step['open']
            self.docs[name] = (LSP_DIR / name).read_bytes().decode('utf-8')
            self.versions[name] = 1
            msg = {'jsonrpc': '2.0', 'method': 'textDocument/didOpen',
                   'params': {'textDocument': {'uri': self.uri(name), 'languageId': 'taixu', 'version': 1,
                                               'text': self.docs[name]}}}
            if self.send(msg, {'open': name, 'version': 1}):
                self.settle(self.diag_for(name), step.get('wait', DIAG_TIMEOUT), what='diagnostics')
        elif 'close' in step:
            name = step['close']
            msg = {'jsonrpc': '2.0', 'method': 'textDocument/didClose',
                   'params': {'textDocument': {'uri': self.uri(name)}}}
            if self.send(msg, {'close': name}):
                self.settle(quiet=LOOSE_QUIET_S)
        elif 'change' in step:
            self.change(step)
        elif 'req' in step:
            self.request(step)
        elif 'notify' in step:
            msg = {'jsonrpc': '2.0', 'method': step['notify']}
            shown = {'notify': step['notify']}
            if 'params' in step:
                msg['params'] = step['params']
                shown['params'] = step['params']
            self.send(msg, shown)
            if step['notify'] == 'exit':
                return False
            self.settle(quiet=LOOSE_QUIET_S)
        elif 'raw' in step:
            body = step['raw'].encode('utf-8')
            self.tr.send(dump({'raw': step['raw']}))
            if self.write(b'Content-Length: %d\r\n\r\n' % len(body) + body):
                self.settle(quiet=LOOSE_QUIET_S)
        elif 'frame' in step:
            self.tr.send(dump({'frame': step['frame']}))
            self.write(step['frame'].encode('utf-8'))
            return False
        else:
            raise SystemExit('unknown step %r' % (step,))
        return not self.eof

    def initialize(self, encodings):
        caps = {}
        if encodings is not None:
            caps = {'general': {'positionEncodings': encodings}}
        self.next_id += 1
        rid = self.next_id
        params = {'processId': None, 'rootUri': None, 'capabilities': caps}
        msg = {'jsonrpc': '2.0', 'id': rid, 'method': 'initialize', 'params': params}
        self.send(msg, {'id': rid, 'method': 'initialize', 'params': params})
        resp = self.settle(lambda m: isinstance(m, dict) and 'method' not in m and m.get('id') == rid,
                           what='response')
        caps = ((resp or {}).get('result') or {}).get('capabilities') or {}
        self.enc = caps.get('positionEncoding') or 'utf-16'
        self.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}}, {'notify': 'initialized'})
        self.settle(quiet=QUIET_S)


def run_lsp(taixu, case, norm, cases):
    script = case.get('script')
    if 'same_as' in case:
        script = next(c for c in cases if c['name'] == case['same_as'])['script']
    s = LspSession(taixu, norm)
    for item in case.get('pre_init', []):
        s.step(item)
    s.initialize(case.get('encodings'))
    for item in script:
        if s.eof:
            s.tr.note({'skipped': item})
            continue
        if not s.step(item):
            break
    finish(s.proc, s.tr, s.reader, s.record)
    return s.tr.text()


# ---------------------------------------------------------------- mcp

def resolve_args(item):
    if isinstance(item, dict):
        return {k: resolve_args(v) for k, v in item.items()}
    if isinstance(item, list):
        return [resolve_args(v) for v in item]
    if isinstance(item, str) and item.startswith('<dir>/'):
        return str(LSP_DIR / item[len('<dir>/'):])
    return item


def run_mcp(taixu, case, norm, cases):
    tr = Transcript(norm)
    proc = spawn(taixu, ['mcp', '--trust-workspace'])
    reader = LineReader(proc.stdout)
    rid = 0
    for step in case['script']:
        rid += 1
        if 'tool' in step:
            req = {'jsonrpc': '2.0', 'id': rid, 'method': 'tools/call',
                   'params': {'name': step['tool'], 'arguments': resolve_args(step.get('arguments', {}))}}
            shown = {'tool': step['tool'], 'arguments': step.get('arguments', {})}
        else:
            req = {'jsonrpc': '2.0', 'id': rid, 'method': step['method'], 'params': step.get('params', {})}
            shown = {'method': step['method']}
        tr.send(dump(shown))
        try:
            proc.stdin.write(dump(req).encode('utf-8') + b'\n')
            proc.stdin.flush()
        except OSError:
            tr.note({'harness': 'stdin closed'})
            break
        while True:
            raw = reader.get(STEP_TIMEOUT + 30)
            if raw is None or raw == '__timeout__':
                tr.note({'harness': 'no response' if raw is None else 'timeout'})
                break
            m = parse(raw)
            if m.get('id') != rid:
                tr.recv(norm.value(m))
                continue
            result = m.get('result')
            if isinstance(result, dict) and 'structuredContent' in result:
                out = {'structured': result['structuredContent']}
                if result.get('isError'):
                    out['isError'] = True
            else:
                out = {k: v for k, v in m.items() if k not in ('jsonrpc', 'id')}
            tr.recv(norm.value(out))
            break
    finish(proc, tr, reader, lambda raw: tr.recv(norm.value(parse(raw))))
    return tr.text()


RUNNERS = {'lsp': run_lsp, 'mcp': run_mcp}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--taixu', required=True, help='Zig CLI（zig-out/bin/taixu(.exe)）')
    ap.add_argument('--only', default='', help='逗号分隔的用例名')
    ap.add_argument('--out', default=str(LSP_DIR / 'zig'), help='转录目录')
    ap.add_argument('mode', choices=('record', 'check'))
    a = ap.parse_args()
    taixu = str(Path(a.taixu).resolve())
    cases = json.loads((LSP_DIR / 'cases.json').read_text(encoding='utf-8'))
    only = {x for x in a.only.split(',') if x}
    norm = Normalizer(LSP_DIR)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    bad = 0
    for case in cases:
        if only and case['name'] not in only:
            continue
        t0 = time.time()
        text = RUNNERS[case['kind']](taixu, case, norm, cases)
        path = out_dir / ('%s.%s' % (case['name'], case['kind']))
        took = time.time() - t0
        if a.mode == 'record':
            path.write_text(text, encoding='utf-8', newline='\n')
            print('REC   %-20s %4d lines  %.1fs' % (case['name'], text.count('\n'), took), flush=True)
            continue
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        if old == text:
            print('PASS  %-20s %.1fs' % (case['name'], took), flush=True)
            continue
        bad += 1
        ol, nl = old.splitlines(), text.splitlines()
        i = next((k for k in range(max(len(ol), len(nl)))
                  if k >= len(ol) or k >= len(nl) or ol[k] != nl[k]), 0)
        print('FAIL  %-20s line %d' % (case['name'], i + 1), flush=True)
        print('   stored: %s' % (ol[i][:300] if i < len(ol) else '<none>'))
        print('   now   : %s' % (nl[i][:300] if i < len(nl) else '<none>'))
    if a.mode == 'check':
        print('SUMMARY %s' % ('PASS' if bad == 0 else 'FAIL=%d' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
