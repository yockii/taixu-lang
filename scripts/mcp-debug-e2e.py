#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native `taixu mcp` end-to-end check (round 6 step 6d-4, LSP/MCP design §四-6; debug design §四-9, §六).

Starts `taixu mcp` on stdio (one JSON-RPC message per line) with a private temporary directory (TMP/TEMP/TMPDIR, so
the debug products it writes can be watched) and checks:
- port of the five debug_* tests of src/tools/mcp.zig: tools/list names the eight debug tools with JSON Schema
  2020-12 and server/discover lists debug_kill; a full session (session "1", ready, breakpoint_set, stopped in main
  with reason breakpoint, frames, the module state slot inspected as a ref, stops, kill, then an unknown session);
  a 5,000-line flood (exited still arrives, events_truncated, valid JSON); idle_timeout_s 1 (after 1.2 s the
  session is unknown). The 301 s idle default and the 1800 s age cap are not run (they would take 30 minutes;
  both go through the same expiry check as the 1 s idle path);
- every reply line is valid JSON (strict: no NaN), including tools/list whose twelve inputSchema objects carry
  $schema;
- JSON-RPC: parse error and non-object requests answer -32600/-32700 with id null, unknown methods -32601,
  notifications and blank lines get no reply, string / integer / fractional / boolean / null ids are echoed;
  initialize carries serverInfo.version;
- tools/call dispatch: malformed requests and unknown tool names are JSON-RPC errors; tool argument validation
  failures are text-only isError results (no structuredContent); unknown names are checked before tool arguments;
- compile: native bytecode size and SHA-256 match build --bytecode; no main required; diagnostics, missing files,
  import failures and success/failure/success on a resident server; compiling never writes a product;
- run/test: CLI parity, arguments and nonzero exits, compile failures, timeout including compilation, empty stdin,
  per-test isolation and timeout name, bounded UTF-8 output retaining both ends, and mixed resident build modes;
- breakpoints: debug_start answers one receipt per entry (unknown module and malformed entries as error events),
  debug_breakpoint on an unknown module answers the error event and the session keeps going;
- debug_step with timeout_s 1 over an endless call ends with exited code 3 reason timeout, then the session is gone;
- events_truncated is reported on the flooded reply only;
- failures carry isError, successes do not;
- the idle sweep runs while the server waits for input (the debug product is deleted without any request);
- EOF with a live session exits 0 and deletes the product; two sessions side by side;
- a line over 16 MiB ends the server with 3 and `taixu mcp: MessageTooLong` on stderr.

Usage: python scripts/mcp-debug-e2e.py --compiler <taixu>   (exit 0 when every check passes)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 60
TOOLS = ['compile', 'run', 'test', 'disasm', 'debug_start', 'debug_breakpoint', 'debug_step', 'debug_continue', 'debug_frames', 'debug_inspect', 'debug_stops', 'debug_kill']
UNKNOWN_SESSION = '未知会话（已退出、被 kill 或超时清扫）'
DIED = '调试子进程已退出'

BASIC_TX = """let g: int = 7;

function twice(x: int): int {
    const y = x * 2;
    return y;
}

export function main(args: []string): int {
    let a: int = 1;
    let b: int = twice(a);
    print("b=" + toStr(b));
    a = a + b;
    return a + g;
}
"""

FLOOD_TX = """function mark(): int {
    return 1;
}

export function main(args: []string): int {
    let i: int = 0;
    while (i < 5000) {
        print("line " + toStr(i));
        i = i + 1;
    }
    const m = mark();
    print("after");
    return m;
}
"""

SPIN_TX = """function spin(): int {
    let i: int = 0;
    while (true) {
        i = i + 1;
    }
    return i;
}

export function main(args: []string): int {
    const x = spin();
    return x;
}
"""


def hard_timeout(s):
    return isinstance(s, dict) and s.get('schema') == 'tool-error/1' and s.get('reason') == 'request_timeout' and s.get('termination_requested') is True and s.get('reaped') is True and s.get('artifacts_cleaned') is True


def unique_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate keys %s' % sorted({k for k in keys if keys.count(k) > 1}))
    return dict(pairs)


def strict_json(text):
    def bad(c):
        raise ValueError('non-standard constant %s' % c)
    return json.loads(text, parse_constant=bad, object_pairs_hook=unique_keys)


class Mcp:
    """A `taixu mcp` process: a reader thread collects reply lines; the main thread waits for them."""

    def __init__(self, exe, cwd, tmpdir, server_args=('--trust-workspace', '--request-timeout-ms', '60000')):
        env = dict(os.environ)
        env['TMP'] = env['TEMP'] = env['TMPDIR'] = tmpdir
        self.proc = subprocess.Popen([exe, 'mcp', *server_args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=cwd, env=env)
        self.msgs = []
        self.bad = []
        self.lock = threading.Lock()
        self.err = b''
        self.next_id = 0
        self.session_ids = []
        # finish() 等进程退出后还要等这两个线程读完（输出与 stderr 收齐）。
        self.readers = [threading.Thread(target=self._read, daemon=True), threading.Thread(target=self._err, daemon=True)]
        for t in self.readers:
            t.start()

    def _err(self):
        self.err = self.proc.stderr.read()

    def _read(self):
        for raw in self.proc.stdout:
            text = raw.decode('utf-8', 'replace').rstrip('\n')
            try:
                m = strict_json(text)
            except ValueError:
                with self.lock:
                    self.bad.append(text)
                continue
            with self.lock:
                self.msgs.append(m)

    def raw(self, data):
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
            return True
        except OSError:
            return False

    def send(self, msg):
        self.raw(json.dumps(msg, ensure_ascii=False).encode('utf-8') + b'\n')

    def wait_id(self, rid, timeout=TIMEOUT_S):
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
                        if m.get('id') == rid:
                            return self.msgs.pop(i)
                return None
            time.sleep(0.01)
        return None

    def call(self, method, params=None, rid=None):
        if rid is None:
            self.next_id += 1
            rid = self.next_id
        msg = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            msg['params'] = params
        self.send(msg)
        return self.wait_id(rid)

    def tool(self, name, args, timeout=TIMEOUT_S):
        """(structuredContent, isError, error) of one tools/call."""
        self.next_id += 1
        self.send({'jsonrpc': '2.0', 'id': self.next_id, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
        r = self.wait_id(self.next_id, timeout)
        if r is None:
            return None, None, 'no reply'
        if 'error' in r:
            return None, None, r['error']
        res = r.get('result', {})
        if 'structuredContent' not in res:
            content = res.get('content')
            if res.get('isError') is True and isinstance(content, list) and len(content) == 1 \
                    and content[0].get('type') == 'text' and isinstance(content[0].get('text'), str):
                return None, True, content[0]['text']
            return None, None, 'invalid text-only error result: %r' % res
        text_ok = res.get('content', [{}])[0].get('text') is not None and strict_json(res['content'][0]['text']) == res.get('structuredContent')
        if not text_ok:
            return None, None, 'content text does not match structuredContent: %r' % res
        content = res.get('structuredContent')
        if name == 'debug_start' and isinstance(content, dict) and isinstance(content.get('session'), str):
            self.session_ids.append(content['session'])
        return content, res.get('isError', False), None

    def session(self, number):
        return self.session_ids[number - 1] if len(self.session_ids) >= number else '<missing-session-%d>' % number

    def ids_valid(self):
        """会话 ID 形如 <代>.<n>、在本进程内两两不同，同一代内 n 递增（回复里的 ID 不能只和自己比）。"""
        parsed = [re.fullmatch(r'([1-9]\d*)\.([1-9]\d*)', sid) for sid in self.session_ids]
        if not all(parsed) or len(set(self.session_ids)) != len(self.session_ids):
            return False
        last = {}
        for p in parsed:
            gen, n = int(p.group(1)), int(p.group(2))
            if n <= last.get(gen, 0):
                return False
            last[gen] = n
        return True

    def pending(self):
        with self.lock:
            return list(self.msgs)

    def finish(self, timeout=30):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return None
        for t in self.readers:
            t.join(timeout)
        return code


class Checks:
    def __init__(self):
        self.fails = 0
        self.total = 0

    def __call__(self, label, ok, detail=''):
        self.total += 1
        if ok:
            print('PASS  %s' % label)
        else:
            self.fails += 1
            print('FAIL  %-28s %s' % (label, str(detail)[:400]))
        return ok


def products(tmpdir, stem):
    return sorted(str(p.relative_to(tmpdir)) for p in Path(tmpdir).glob('taixu-mcp-worker-*/p-*') if p.is_file())


def wait_until(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def sc_list(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    r = m.call('tools/list')
    tools = (r or {}).get('result', {}).get('tools', [])
    check('list:names', [t.get('name') for t in tools] == TOOLS, [t.get('name') for t in tools])
    check('list:schema', all(isinstance(t.get('inputSchema'), dict) and '2020-12' in t['inputSchema'].get('$schema', '')
                             and t['inputSchema'].get('type') == 'object' for t in tools) and not m.bad, m.bad[:1] or tools[:1])
    r = m.call('server/discover')
    names = (r or {}).get('result', {}).get('tools', [])
    check('list:discover', 'debug_kill' in names and names == TOOLS, r)
    check('list:exit0', m.finish() == 0)


def sc_session(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    reference, _, _ = m.tool('compile', {'path': 'basic.tx'})
    s, e, err = m.tool('debug_start', {'path': 'basic.tx', 'breakpoints': [{'module': 'basic.tx', 'line': 4}, {'fn': 'main'}]})
    check('session:start', s is not None and s.get('session') == m.session(1) and m.ids_valid() and s.get('start', {}).get('event') == 'ready'
          and [b.get('event') for b in s.get('breakpoints', [])] == ['breakpoint_set', 'breakpoint_fn'] and not e, (s, err))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('session:stopped', s is not None and s.get('event') == 'stopped' and s.get('fn') == 'main' and s.get('reason') == 'breakpoint', (s, err))
    s, e, err = m.tool('compile', {'path': 'basic.tx'})
    check('session:compile-while-paused', s is not None and s.get('ok') is True and not e and err is None
          and s.get('txc_sha256') == (reference or {}).get('txc_sha256'), (s, e, err))
    s, e, err = m.tool('debug_frames', {'session': m.session(1)})
    check('session:frames', s is not None and s.get('event') == 'frames' and s['frames'][0]['fn'] == 'main', (s, err))
    s, e, err = m.tool('debug_inspect', {'session': m.session(1), 'frame': 0, 'slot': 0})
    check('session:inspect', s is not None and s.get('event') == 'inspect' and s['value'].get('tag') == 'ref', (s, err))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('session:stopped-twice', s is not None and s.get('event') == 'stopped' and s.get('fn') == 'twice', (s, err))
    s, e, err = m.tool('debug_stops', {'session': m.session(1)})
    check('session:stops', s is not None and len(s.get('stops', [])) == 2, (s, err))
    s, e, err = m.tool('debug_nope', {'session': m.session(1)})
    check('session:unknown-tool', err == {'code': -32602, 'message': 'Unknown tool: debug_nope'}, err)
    s, e, err = m.tool('debug_kill', {'session': m.session(1)})
    check('session:kill', s is not None and s.get('killed') is True and s.get('exited', {}).get('reason') == 'kill' and not e, (s, err))
    s, e, err = m.tool('debug_frames', {'session': m.session(1)})
    check('session:gone', s is not None and s.get('error') == UNKNOWN_SESSION and e is True, (s, e, err))
    check('session:products-deleted', wait_until(lambda: not products(prod, 'basic'), 10), products(prod, 'basic'))
    check('session:valid-json', not m.bad, m.bad[:1])
    check('session:exit0', m.finish() == 0)


def sc_flood(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('debug_start', {'path': 'flood.tx', 'breakpoints': [{'fn': 'mark'}]})
    check('flood:start', s is not None and s.get('start', {}).get('event') == 'ready', (s, err))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    evs = (s or {}).get('events', [])
    check('flood:truncated', s is not None and s.get('event') == 'stopped' and s.get('fn') == 'mark' and s.get('events_truncated') is True
          and len(evs) == 4095 and evs[-1].get('text') == 'line 4999', (len(evs), s and {k: v for k, v in s.items() if k != 'events'}))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('flood:reset', s is not None and s.get('event') == 'exited' and s.get('code') == 1 and 'events_truncated' not in s
          and s.get('events') == [{'event': 'stdout', 'text': 'after'}], (s, err))
    check('flood:valid-json', not m.bad, m.bad[:1])
    check('flood:exit0', m.finish() == 0)


def sc_idle(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('debug_start', {'path': 'basic.tx', 'idle_timeout_s': 1})
    check('idle:start', s is not None and s.get('session') == m.session(1), (s, err))
    time.sleep(1.2)
    s, e, err = m.tool('debug_stops', {'session': m.session(1)})
    check('idle:swept', s is not None and s.get('error') == UNKNOWN_SESSION, (s, err))
    # 不发请求：等输入时到点清扫，产物随之删除。
    s, e, err = m.tool('debug_start', {'path': 'idle.tx', 'idle_timeout_s': 1})
    check('idle:start2', s is not None and s.get('session') == m.session(2) and m.ids_valid() and products(prod, 'idle'), (s, err, products(prod, 'idle')))
    check('idle:sweep-while-waiting', wait_until(lambda: not products(prod, 'idle'), 5), products(prod, 'idle'))
    s, e, err = m.tool('debug_stops', {'session': m.session(2)})
    check('idle:swept2', s is not None and s.get('error') == UNKNOWN_SESSION, (s, err))
    s, e, err = m.tool('debug_start', {'path': 'basic.tx', 'idle_timeout_s': 0})
    check('idle:bad-param', s is None and e is True and err == 'Invalid params: idle_timeout_s 需要正整数（秒）', err)
    check('idle:exit0', m.finish() == 0)


def sc_jsonrpc(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    r = m.call('initialize', {'protocolVersion': '2025-11-25'})
    info = (r or {}).get('result', {}).get('serverInfo', {})
    ver = subprocess.run([exe, 'version'], capture_output=True, timeout=60).stdout.decode('utf-8', 'replace')
    check('rpc:initialize', (r or {}).get('result', {}).get('protocolVersion') == '2025-11-25' and info.get('name') == 'taixu'
          and isinstance(info.get('version'), str) and info['version'] and info['version'] in ver, (r, ver))
    m.raw(b'garbage\n')
    m.raw(b'[1]\n')
    m.raw(b'  \t\r\n')
    m.send({'jsonrpc': '2.0', 'method': 'tools/list'})
    m.send({'jsonrpc': '2.0', 'id': 99, 'method': 'notifications/initialized'})
    m.send({'jsonrpc': '2.0', 'method': 'nope'})
    r = m.call('ping', rid=5)
    with m.lock:
        early = list(m.msgs)
        m.msgs.clear()
    errs = [x.get('error', {}).get('code') for x in early]
    check('rpc:parse-and-invalid', errs == [-32700, -32600] and all(x.get('id') is None for x in early), early)
    check('rpc:no-reply', r is not None and r.get('result') == {} and len(early) == 2, (r, early))
    r = m.call('nope')
    check('rpc:method-not-found', (r or {}).get('error') == {'code': -32601, 'message': 'Method not found'}, r)
    for label, rid in (('string', 'a"b\\c'), ('int', 123456789012), ('fraction', 7.25), ('null', None)):
        m.send({'jsonrpc': '2.0', 'id': rid, 'method': 'ping'})
        got = m.wait_id(rid) if rid is not None else None
        if rid is None:
            got = m.wait_id(None)
        check('rpc:id-' + label, got is not None and got.get('id') == rid and got.get('result') == {}, got)
    m.send({'jsonrpc': '2.0', 'id': True, 'method': 'ping'})
    got = m.wait_id(None)
    check('rpc:id-bool', got is not None and got.get('id') is None and got.get('result') == {}, got)
    for label, params, want in (
            ('missing-params', None, 'params 须为对象'), ('params-not-object', 'x', 'params 须为对象'),
            ('missing-name', {}, 'name 须为字符串'), ('name-not-string', {'name': 3}, 'name 须为字符串'),
            ('arguments-not-object', {'name': 'compile', 'arguments': []}, 'arguments 须为对象'),
            ('arguments-null', {'name': 'compile', 'arguments': None}, 'arguments 须为对象')):
        got = m.call('tools/call', params)
        check('rpc:' + label, (got or {}).get('error') == {'code': -32602, 'message': 'Invalid params: ' + want}, got)
    for label, args, want in (('compile', {}, 'Invalid params: 缺少 path'), ('compile', {'path': 3}, 'Invalid params: path 须为字符串'),
                              ('compile', {'path': ''}, 'Invalid params: path 为空')):
        s, e, err = m.tool(label, args)
        check('rpc:tool-%s-%s' % (label, want.split(':')[-1].strip().replace(' ', '-')), s is None and e is True and err == want, err)
    got = m.call('tools/call', {'name': 'compile'})
    check('rpc:arguments-omitted', (got or {}).get('result') == {
        'content': [{'type': 'text', 'text': 'Invalid params: 缺少 path'}], 'isError': True}, got)
    for name in ('nope', 'debug_nope', '', 'debug_', 'compile"', 'debug_start,debug_kill'):
        s, e, err = m.tool(name, {})
        check('rpc:unknown-' + name, err == {'code': -32602, 'message': 'Unknown tool: ' + name}, err)
    s, e, err = m.tool('debug_frames', {})
    check('rpc:no-session', s is None and e is True and err == 'Invalid params: 缺少 session', err)
    s, e, err = m.tool('debug_frames', {'session': 1})
    check('rpc:session-type', s is None and e is True and err == 'Invalid params: session 须为字符串', err)
    check('rpc:valid-json', not m.bad, m.bad[:1])
    check('rpc:exit0', m.finish() == 0)


def sc_compile(exe, tmp, prod, check):
    fixtures = {
        'library.tx': 'export function answer(): int { return 42; }\n',
        '中文.tx': 'export function main(args: []string): int { print("太虚"); return 0; }\n',
        'bad.tx': 'export function answer(): int { return true; }\n',
        'missing-import.tx': 'import { answer } from "./missing";\nexport function main(args: []string): int { return answer(); }\n',
        'tool-only.tx': 'export function answer(): int { const b = bytesNew(1); return stdinRead(b, 0); }\n',
    }
    for name, src in fixtures.items():
        Path(tmp, name).write_text(src, encoding='utf-8', newline='\n')
    expected = {}
    # The reference belongs to the harness, outside the server's product area.
    # Clean it before checking the server's compile/disasm read-only behavior.
    with tempfile.TemporaryDirectory(prefix='taixu-mcp-compile-ref-') as temp:
        reference_dir = Path(temp).resolve()
        assert reference_dir.parent == Path(tempfile.gettempdir()).resolve()
        p = subprocess.run([exe, 'build', '--toolchain', 'bootstrap/selfhost/mcp-compile-probe.tx', '--out', str(reference_dir)],
                           cwd=ROOT, capture_output=True, timeout=1200)
        reference = reference_dir / ('mcp-compile-probe.exe' if os.name == 'nt' else 'mcp-compile-probe')
        check('compile:reference-probe', p.returncode == 0 and reference.exists(), p.stderr.decode('utf-8', 'replace'))
        if not reference.exists():
            return
        for name in ('basic.tx', 'library.tx', '中文.tx'):
            out = reference_dir / (name + '.ir')
            p = subprocess.run([str(reference), name, str(out)],
                               cwd=tmp, capture_output=True, timeout=TIMEOUT_S)
            check('compile:reference-' + name, p.returncode == 0 and out.exists(), p.stderr.decode('utf-8', 'replace'))
            if out.exists():
                b = out.read_bytes()
                expected[name] = (hashlib.sha256(b).hexdigest(), len(b))
    before = {p.relative_to(tmp): p.read_bytes() for p in Path(tmp).rglob('*') if p.is_file()}
    m = Mcp(exe, tmp, prod)
    for name, want_ok in (('basic.tx', True), ('bad.tx', False), ('library.tx', True), ('missing.tx', False),
                          ('missing-import.tx', False), ('中文.tx', True), ('tool-only.tx', False), ('basic.tx', True)):
        s, e, err = m.tool('compile', {'path': name})
        ok = isinstance(s, dict) and s.get('ok') is want_ok and e is (not want_ok) and err is None
        check('compile:status-' + name, ok, (s, e, err))
        if not isinstance(s, dict):
            continue
        check('compile:duration-' + name, type(s.get('duration_us')) is int and s['duration_us'] >= 0, s)
        diags = s.get('diags')
        diag_ok = isinstance(diags, list) and (not diags if want_ok else bool(diags)) \
            and all(set(d) == {'severity', 'code', 'msg', 'module', 'line', 'col'}
                    and type(d['code']) is int and isinstance(d['msg'], str) for d in diags)
        check('compile:diags-' + name, diag_ok, diags)
        if want_ok:
            check('compile:bytecode-' + name, (s.get('txc_sha256'), s.get('txc_bytes')) == expected.get(name), s)
        else:
            check('compile:no-product-' + name, 'txc_sha256' not in s and 'txc_bytes' not in s, s)
    check('compile:valid-json', not m.bad, m.bad[:1])
    check('compile:exit0', m.finish() == 0)
    after = {p.relative_to(tmp): p.read_bytes() for p in Path(tmp).rglob('*') if p.is_file()}
    check('compile:read-only', before == after, sorted(str(p) for p in set(after) - set(before)))


def sc_run(exe, tmp, prod, check):
    lib = 'msvcrt.dll' if os.name == 'nt' else ('libSystem' if sys.platform == 'darwin' else 'libc.so.6')
    fixtures = {
        'run-args.tx': 'function main(args: []string): int { for (a in args) { print(a); } eprint("错误✓"); return 7; }\n',
        'run-trap.tx': 'function main(args: []string): int { eprint("before trap"); assert(false, "故意失败"); return 0; }\n',
        'run-library.tx': 'export function answer(): int { return 42; }\n',
        'run-stdin.tx': 'declare function getchar(): i32 from "%s";\nfunction main(args: []string): int { print(toStr(getchar())); return 0; }\n' % lib,
    }
    for name, src in fixtures.items():
        Path(tmp, name).write_text(src, encoding='utf-8', newline='\n')
    m = Mcp(exe, tmp, prod)
    argv = ['hello world', '引号"\\', '', '--flag']
    for name, args in (('basic.tx', []), ('run-args.tx', argv), ('run-trap.tx', [])):
        p = subprocess.run([exe, 'run', '--format=json', name, *args], cwd=tmp, capture_output=True, timeout=TIMEOUT_S)
        s, e, err = m.tool('run', {'path': name, 'args': args})
        want = (p.returncode, p.stdout.decode('utf-8'), p.stderr.decode('utf-8'))
        got = (s.get('exit_code'), s.get('stdout'), s.get('stderr')) if s else None
        check('run:cli-' + name, got == want and not e and err is None, (got, want, e, err))
        check('run:flags-' + name, s is not None and s.get('timed_out') is False and s.get('truncated') is False
              and type(s.get('duration_us')) is int and s['duration_us'] >= 0, s)
    for name in ('missing-run.tx', 'run-library.tx'):
        s, e, err = m.tool('run', {'path': name})
        check('run:compile-error-' + name, s is not None and s.get('exit_code') == 1 and e is True
              and s.get('stdout') == '' and bool(json.loads(s.get('stderr', '{}')).get('diags')), (s, e, err))
    s, e, err = m.tool('run', {'path': 'run-stdin.tx', 'timeout_ms': 3000})
    check('run:empty-stdin', s is not None and s.get('exit_code') == 0 and s.get('stdout') == '-1\n' and not e, (s, e, err))
    invalid = 'declare function putchar(c: i32): i32 from "%s";\ndeclare function fflush(stream: usize): i32 from "%s";\n' % (lib, lib)
    invalid += 'function main(args: []string): int { '
    invalid += ''.join('putchar(%d); ' % b for b in (65, 240, 159, 146, 161, 255, 226, 130))
    invalid += 'fflush(0); return 0; }\n'
    Path(tmp, 'run-invalid.tx').write_text(invalid, encoding='utf-8', newline='\n')
    s, e, err = m.tool('run', {'path': 'run-invalid.tx'})
    check('run:invalid-utf8', s is not None and s.get('exit_code') == 0 and s.get('stdout') == 'A💡' + '\ufffd' * 3
          and not e and not s.get('truncated'), (s, e, err))
    s, e, err = m.tool('run', {'path': 'basic.tx', 'args': ['\0']})
    check('run:spawn-error', s is not None and s.get('exit_code') == 3 and e is True
          and 'invalid argument' in s.get('stderr', ''), (s, e, err))
    for label, params in (('compile', {'path': 'basic.tx', 'timeout_ms': 1}),
                          ('runtime', {'path': 'spin.tx', 'timeout_ms': 500})):
        s, e, err = m.tool('run', params)
        check('run:timeout-' + label, s is not None and e is True and err is None and
              (hard_timeout(s) if label == 'compile' else s.get('exit_code') == -1 and s.get('timed_out') is True and s.get('stdout') == ''), (s, e, err))
    for value in (0, -1, True, 1.5, '100', None):
        for tool in ('run', 'test'):
            s, e, err = m.tool(tool, {'path': 'basic.tx', 'timeout_ms': value})
            check('run:timeout-param-%s-%r' % (tool, value), s is None and e is True
                  and err == 'timeout_ms 需要 1..60000 的整数（启动配置上限）', (s, e, err))
    for value in (None, 'x', [1], [True], ['ok', None]):
        s, e, err = m.tool('run', {'path': 'basic.tx', 'args': value})
        check('run:args-param-%r' % value, s is None and e is True and err == 'Invalid params: args 须为字符串数组', (s, e, err))
    s, e, err = m.tool('run', {'path': 'basic.tx', 'timeout_ms': 60001})
    check('run:timeout-upper-bound', s is None and e and err == 'timeout_ms 需要 1..60000 的整数（启动配置上限）', (s, e, err))
    if os.name != 'nt':
        libc = 'libSystem' if sys.platform == 'darwin' else 'libc.so.6'
        Path(tmp, 'run-signal.tx').write_text('declare function raise(sig: i32): i32 from "' + libc + '";\nfunction main(args: []string): int { return raise(15) as int; }\n', encoding='utf-8')
        s, e, err = m.tool('run', {'path': 'run-signal.tx'})
        check('run:signal', s is not None and s.get('exit_code') == -2 and s.get('signal') == 15 and not e, (s, e, err))
    check('run:valid-json', not m.bad, m.bad[:1])
    check('run:exit0', m.finish() == 0)
    check('run:cleanup', not list(Path(prod).glob('taixu-mcp-*')), list(Path(prod).iterdir()))


def sc_run_output(exe, tmp, prod, check):
    line = '界' * 683
    source = '''function main(args: []string): int {
    print("stdout-head✓"); eprint("stderr-head✓");
    let i = 0;
    while (i < 650) { print("%s"); eprint("%s"); i = i + 1; }
    print("stdout-tail✓"); eprint("stderr-tail✓");
    return 0;
}
''' % (line, line)
    Path(tmp, 'run-output.tx').write_text(source, encoding='utf-8', newline='\n')
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('run', {'path': 'run-output.tx', 'timeout_ms': 60000})
    check('run-output:status', s is not None and s.get('exit_code') == 0 and s.get('truncated') is True and not e, (s, e, err))
    for stream in ('stdout', 'stderr'):
        text = s.get(stream, '') if s else ''
        check('run-output:' + stream, text.startswith(stream + '-head✓\n') and text.endswith(stream + '-tail✓\n')
              and '截去 ' in text and '\ufffd' not in text and len(text.encode('utf-8')) <= 1048576, (len(text.encode('utf-8')), text[:30], text[-30:]))
    tests = source.replace('function main(args: []string): int {', 'test "output" {').replace('    return 0;\n', '')
    tests += 'test "last" { print("last test✓"); }\n'
    Path(tmp, 'test-output.tx').write_text(tests, encoding='utf-8', newline='\n')
    s, e, err = m.tool('test', {'path': 'test-output.tx', 'timeout_ms': 60000})
    check('test-output:status', s is not None and s.get('exit_code') == 0 and s.get('truncated') is True and not e
          and s.get('summary') == {'total': 2, 'pass': 2, 'fail': 0}, (s, e, err))
    for stream in ('stdout', 'stderr'):
        text = s.get(stream, '') if s else ''
        check('test-output:' + stream, text.startswith(stream + '-head✓\n') and stream + '-tail✓\n' in text
              and '截去 ' in text and '\ufffd' not in text and len(text.encode('utf-8')) <= 1048576, (len(text.encode('utf-8')), text[:30], text[-200:]))
    check('test-output:summary-tail', s is not None and 'last test✓\n' in s.get('stdout', '')
          and json.loads(s['stdout'].splitlines()[-1]).get('summary') == s.get('summary'), s)
    check('run-output:valid-json', not m.bad, m.bad[:1])
    check('run-output:exit0', m.finish() == 0)


def without_durations(doc):
    return {'summary': doc.get('summary'),
            'tests': [{k: v for k, v in t.items() if k != 'duration_us'} for t in doc.get('tests', [])]}


def sc_test(exe, tmp, prod, check):
    fixtures = {
        'tests-pass.tx': 'test "first" { print("测试输出✓"); eprint("测试错误✓"); assert(true, "first"); }\ntest "second" { assert(2 == 2, "second"); }\n',
        'tests-fail.tx': 'test "bad" { assert(false, "故意失败"); }\ntest "after" { print("after failure"); assert(true, "after"); }\n',
        'tests-none.tx': 'export function answer(): int { return 42; }\n',
        'tests-timeout.tx': 'test "first" { print("first done"); }\ntest "loop" { let i = 0; while (true) { i = i + 1; } }\ntest "never" { print("never reached"); }\n',
    }
    for name, src in fixtures.items():
        Path(tmp, name).write_text(src, encoding='utf-8', newline='\n')
    m = Mcp(exe, tmp, prod)
    for name, want_code in (('tests-pass.tx', 0), ('tests-fail.tx', 2), ('tests-none.tx', 0)):
        p = subprocess.run([exe, 'test', '--format=json', name], cwd=tmp, capture_output=True, timeout=TIMEOUT_S)
        text = p.stdout.decode('utf-8').splitlines()
        expected = json.loads(text[-1]) if text else {}
        s, e, err = m.tool('test', {'path': name})
        check('test:status-' + name, s is not None and s.get('exit_code') == want_code == p.returncode and not e and err is None, (s, e, err))
        check('test:structured-' + name, s is not None and without_durations(s) == without_durations(expected), (s, expected))
        out = s.get('stdout', '').splitlines() if s else []
        check('test:stdout-' + name, out[:-1] == text[:-1] and bool(out)
              and without_durations(json.loads(out[-1])) == without_durations(expected), (out, text))
        check('test:stderr-' + name, s is not None and s.get('stderr') == p.stderr.decode('utf-8'), (s, p.stderr))
    s, e, err = m.tool('test', {'path': 'tests-timeout.tx', 'timeout_ms': 1000})
    check('test:timeout', s is not None and s.get('exit_code') == -1 and s.get('timed_out') is True and e is True
          and s.get('timeout_test') == 'loop' and s.get('summary') == {'total': 2, 'pass': 1, 'fail': 1}
          and [t['name'] for t in s.get('tests', [])] == ['first', 'loop'] and 'never reached' not in s.get('stdout', ''), (s, e, err))
    s, e, err = m.tool('test', {'path': 'missing-test.tx'})
    check('test:compile-error', s is not None and s.get('exit_code') == 1 and e is True
          and s.get('tests') == [] and bool(json.loads(s.get('stderr', '{}')).get('diags')), (s, e, err))
    s, e, err = m.tool('test', {'path': 'tests-pass.tx', 'timeout_ms': 1})
    check('test:compile-timeout', hard_timeout(s) and e is True and err is None, (s, e, err))
    check('test:valid-json', not m.bad, m.bad[:1])
    check('test:exit0', m.finish() == 0)
    check('test:cleanup', not list(Path(prod).glob('taixu-mcp-*')), list(Path(prod).iterdir()))


def sc_disasm(exe, tmp, prod, check):
    Path(tmp, 'disasm-bad.tx').write_text('function invalid(): int { return true; }\n', encoding='utf-8')
    m = Mcp(exe, tmp, prod)
    reference, _, _ = m.tool('compile', {'path': 'basic.tx'})
    def snapshot():
        # The persistent worker already owns an empty scratch directory. No disasm writes are allowed.
        return {p.relative_to(tmp).as_posix(): ('dir' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest(),
                                               p.stat().st_mtime_ns) for p in Path(tmp).rglob('*')}
    before = snapshot()
    cli = subprocess.run([exe, 'disasm', '--format=json', 'basic.tx'], cwd=tmp, capture_output=True, timeout=60)
    cli_doc = strict_json(cli.stdout.decode('utf-8')) if cli.returncode == 0 else {}
    check('disasm:cli-json', cli.returncode == 0 and cli_doc.get('functions'), (cli.returncode, cli.stderr))
    for fmt in (None, 'text', 'json'):
        args = {'path': 'basic.tx'}
        if fmt is not None:
            args['format'] = fmt
        s, e, err = m.tool('disasm', args)
        label = fmt or 'default'
        check('disasm:' + label, s is not None and s.get('exit_code') == 0 and s.get('timed_out') is False
              and s.get('truncated') is False and not e and err is None and s.get('duration_us', -1) >= 0
              and s.get('format') == (fmt or 'text') and s.get('functions') == cli_doc.get('functions'), (s, e, err))
        if s is not None:
            if fmt == 'json':
                check('disasm:json-parity', s.get('stdout', '').encode() == cli.stdout and strict_json(s['stdout']) == cli_doc, s)
            else:
                plain = subprocess.run([exe, 'disasm', 'basic.tx'], cwd=tmp, capture_output=True, timeout=60)
                check('disasm:' + label + '-parity', s.get('stdout', '').encode() == plain.stdout and plain.returncode == 0, s)
    check('disasm:no-writes', snapshot() == before, {'before': before, 'after': snapshot()})
    for path in ('no-such.tx', 'disasm-bad.tx'):
        s, e, err = m.tool('disasm', {'path': path})
        check('disasm:failure-' + path, s is not None and s.get('exit_code') == 1 and e and err is None
              and s.get('functions') == [] and strict_json(s.get('stderr', '{}')).get('diags'), (s, e, err))
    for fmt in (None, 4, True, [], 'yaml', ''):
        s, e, err = m.tool('disasm', {'path': 'basic.tx', 'format': fmt})
        check('disasm:bad-format-' + repr(fmt), s is None and e and err == 'Invalid params: format 须为 text 或 json', (s, e, err))
    large = Path(tmp, 'large-listing.tx')
    large.write_text(''.join('function f%d(): int { return %d; }\n' % (i, i) for i in range(1500)), encoding='utf-8')
    s, e, err = m.tool('disasm', {'path': large.name, 'format': 'json'})
    check('disasm:bounded', s is not None and s.get('exit_code') == 0 and not e and err is None
          and s.get('truncated') is True and len(s.get('stdout', '').encode()) <= 1048576
          and len(json.dumps(s.get('functions'), ensure_ascii=False, separators=(',', ':')).encode()) <= 1048576
          and strict_json(s.get('stdout', '{}')).get('functions') == s.get('functions')
          and strict_json(s.get('stdout', '{}')).get('truncated') is True, (s, e, err))
    s, e, err = m.tool('disasm', {'path': large.name, 'timeout_ms': 1})
    check('disasm:compile-budget', hard_timeout(s) and e and err is None, (s, e, err))
    for timeout in (None, 0, -1, 1.5, True, '1'):
        s, e, err = m.tool('disasm', {'path': 'basic.tx', 'timeout_ms': timeout})
        check('disasm:bad-timeout-' + repr(timeout), s is None and e
              and err == 'timeout_ms 需要 1..60000 的整数（启动配置上限）', (s, e, err))
    s, e, err = m.tool('disasm', {'path': 'basic.tx', 'timeout_ms': 60001})
    check('disasm:timeout-upper-bound', s is None and e and err == 'timeout_ms 需要 1..60000 的整数（启动配置上限）', (s, e, err))
    after, e, err = m.tool('compile', {'path': 'basic.tx'})
    check('disasm:compile-after', after is not None and after.get('txc_sha256') == reference.get('txc_sha256') and not e, (after, err))
    check('disasm:valid-json', not m.bad, m.bad[:1])
    check('disasm:exit0', m.finish() == 0)


def sc_modes(exe, tmp, prod, check):
    Path(tmp, 'modes-test.tx').write_text('test "mode" { assert(true, "mode"); }\n', encoding='utf-8')
    m = Mcp(exe, tmp, prod)
    reference, _, _ = m.tool('compile', {'path': 'basic.tx'})
    for name, path in (('test', 'modes-test.tx'), ('disasm', 'basic.tx'), ('run', 'basic.tx'), ('compile', 'basic.tx')):
        s, e, err = m.tool(name, {'path': path})
        check('modes:before-debug-' + name, s is not None and not e and err is None
              and (name != 'compile' or s.get('txc_sha256') == reference.get('txc_sha256')), (s, e, err))
    s, e, err = m.tool('debug_start', {'path': 'basic.tx', 'breakpoints': [{'fn': 'main'}]})
    check('modes:debug-start', s is not None and s.get('start', {}).get('event') == 'ready' and not e, (s, e, err))
    m.tool('debug_continue', {'session': m.session(1)})
    for name, path in (('test', 'modes-test.tx'), ('disasm', 'basic.tx'), ('run', 'basic.tx'), ('compile', 'basic.tx')):
        s, e, err = m.tool(name, {'path': path})
        check('modes:paused-' + name, s is not None and not e and err is None
              and (name != 'compile' or s.get('txc_sha256') == reference.get('txc_sha256')), (s, e, err))
    s, e, err = m.tool('debug_frames', {'session': m.session(1)})
    check('modes:frames', s is not None and s.get('frames', [{}])[0].get('fn') == 'main' and not e, (s, e, err))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('modes:continue', s is not None and s.get('event') == 'exited' and not e, (s, e, err))
    check('modes:exit0', m.finish() == 0)


def sc_breaks(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('debug_start', {'path': 'basic.tx', 'breakpoints': [{'module': 'nope.tx', 'line': 1}, {'line': 3}, 5, {'fn': 7}, {'fn': 'twice'}]})
    bps = (s or {}).get('breakpoints', [])
    check('break:receipts', [b.get('event') for b in bps] == ['error', 'error', 'error', 'error', 'breakpoint_fn']
          and bps[0].get('message') == '未知模块（见 ready 事件的 modules 清单）'
          and bps[1].get('message') == 'breakpoints[1] 需要 fn（字符串）或 module（字符串）+line（整数）'
          and bps[2].get('message', '').startswith('breakpoints[2] ') and bps[3].get('message', '').startswith('breakpoints[3] '), bps)
    s, e, err = m.tool('debug_breakpoint', {'session': m.session(1), 'module': 'nope.tx', 'line': 1})
    check('break:unknown-module', s is not None and s.get('event') == 'error' and e is True, (s, e, err))
    s, e, err = m.tool('debug_breakpoint', {'session': m.session(1), 'module': 'basic.tx', 'line': -1})
    check('break:negative-line', s is not None and s.get('event') == 'error' and 'line' in s.get('message', ''), (s, err))
    s, e, err = m.tool('debug_breakpoint', {'session': m.session(1)})
    check('break:no-target', s is None and e is True and err == 'Invalid params: 需要 fn 或 module+line', err)
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('break:alive', s is not None and s.get('event') == 'stopped' and s.get('fn') == 'twice' and not e, (s, err))
    s, e, err = m.tool('debug_inspect', {'session': m.session(1), 'frame': 9, 'slot': 0})
    check('break:inspect-error', s is not None and s.get('event') == 'error' and e is True, (s, err))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('break:exited', s is not None and s.get('event') == 'exited' and s.get('code') == 10, (s, err))
    s, e, err = m.tool('debug_continue', {'session': m.session(1)})
    check('break:died', s is not None and s.get('error') == DIED and s.get('stderr') == '' and e is True, (s, err))
    check('break:exit0', m.finish() == 0)


def sc_step(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('debug_start', {'path': 'spin.tx', 'breakpoints': [{'fn': 'main'}]})
    m.tool('debug_continue', {'session': m.session(1)})
    s, e, err = m.tool('debug_step', {'session': m.session(1), 'mode': 'over', 'timeout_s': 0})
    check('step:bad-timeout', s is None and e is True and err == 'Invalid params: timeout_s 需要正整数（秒）', err)
    t0 = time.time()
    s, e, err = m.tool('debug_step', {'session': m.session(1), 'mode': 'over', 'timeout_s': 1})
    took = time.time() - t0
    check('step:timeout', s is not None and s.get('event') == 'exited' and s.get('code') == 3 and s.get('reason') == 'timeout' and took < 10, (s, err, took))
    s, e, err = m.tool('debug_step', {'session': m.session(1)})
    check('step:died', s is not None and s.get('error') == DIED and e is True, (s, err))
    s, e, err = m.tool('debug_start', {'path': 'basic.tx'})
    s2, e2, err2 = m.tool('debug_start', {'path': 'basic.tx', 'breakpoints': [{'fn': 'twice'}]})
    check('step:two-sessions', (s or {}).get('session') == m.session(2) and (s2 or {}).get('session') == m.session(3) and m.ids_valid(), (s, s2, err, err2, m.session_ids))
    s2, e2, err2 = m.tool('debug_continue', {'session': m.session(3)})
    s, e, err = m.tool('debug_continue', {'session': m.session(2)})
    check('step:interleaved', (s2 or {}).get('fn') == 'twice' and (s or {}).get('event') == 'exited' and (s or {}).get('code') == 10, (s, s2))
    s2, e2, err2 = m.tool('debug_step', {'session': m.session(3), 'mode': 'out'})
    check('step:out', (s2 or {}).get('event') == 'stopped' and (s2 or {}).get('fn') == 'main' and s2.get('reason') == 'step', (s2, err2))
    check('step:exit0-live', m.finish() == 0)
    check('step:products-deleted', wait_until(lambda: not products(prod, 'basic') and not products(prod, 'spin'), 10), os.listdir(prod))


def sc_eof(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('debug_start', {'path': 'eof.tx'})
    check('eof:start', s is not None and products(prod, 'eof'), (s, err, products(prod, 'eof')))
    check('eof:exit0', m.finish() == 0)
    check('eof:product-deleted', wait_until(lambda: not products(prod, 'eof'), 10), products(prod, 'eof'))


def sc_golden(exe, tmp, prod, check):
    # tests/golden/hello.native.txc.sha256 锁定 examples/hello.tx 的内部 TXC 编码；MCP compile 回报它的 SHA-256。
    Path(tmp, 'hello.tx').write_bytes((ROOT / 'examples/hello.tx').read_bytes())
    want = (ROOT / 'tests/golden/hello.native.txc.sha256').read_text(encoding='utf-8').strip()
    m = Mcp(exe, tmp, prod)
    s, e, err = m.tool('compile', {'path': 'hello.tx'})
    check('golden:hello-txc', (s or {}).get('txc_sha256') == want and not e, (s, err, want))
    m.finish()


def sc_budget(exe, tmp, prod, check):
    # 请求时限短于程序运行：到点暂停（reason pause）并照常回复；该会话与同一 worker 里的其他会话都保留，
    # 不会因撞上父进程的请求墙钟而整个 worker 被杀。
    m = Mcp(exe, tmp, prod, ('--trust-workspace', '--request-timeout-ms', '3000'))
    m.tool('debug_start', {'path': 'basic.tx'})
    m.tool('debug_start', {'path': 'spin.tx', 'breakpoints': [{'fn': 'main'}]})
    s, e, err = m.tool('debug_continue', {'session': m.session(2)})
    check('budget:at-main', s is not None and s.get('event') == 'stopped' and s.get('fn') == 'main', (s, err))
    t0 = time.time()
    s, e, err = m.tool('debug_continue', {'session': m.session(2), 'timeout_s': 60})
    took = time.time() - t0
    check('budget:pause', s is not None and s.get('event') == 'stopped' and s.get('reason') == 'pause' and not e and took < 3.5,
          (s, err, took))
    s, e, err = m.tool('debug_frames', {'session': m.session(2)})
    check('budget:session-kept', s is not None and s.get('event') == 'frames' and not e, (s, err))
    s, e, err = m.tool('debug_stops', {'session': m.session(1)})
    check('budget:other-session-kept', s is not None and not e and 'error' not in s, (s, err))
    s, e, err = m.tool('debug_kill', {'session': m.session(2)})
    check('budget:kill', s is not None and s.get('killed') is True, (s, err))
    m.tool('debug_kill', {'session': m.session(1)})
    check('budget:valid-json', not m.bad, m.bad[:1])
    m.finish()


def sc_long(exe, tmp, prod, check):
    m = Mcp(exe, tmp, prod)
    m.raw(b'x' * (16 * 1024 * 1024 + 8) + b'\n')
    code = m.finish()
    err = m.err.decode('utf-8', 'replace')
    check('long:exit3', code == 3 and 'taixu mcp: MessageTooLong' in err, (code, err[:200]))


SCENARIOS = {'list': sc_list, 'session': sc_session, 'flood': sc_flood, 'idle': sc_idle, 'jsonrpc': sc_jsonrpc, 'compile': sc_compile,
             'run': sc_run, 'run-output': sc_run_output, 'test': sc_test, 'modes': sc_modes, 'disasm': sc_disasm,
             'breaks': sc_breaks, 'step': sc_step, 'golden': sc_golden, 'budget': sc_budget, 'eof': sc_eof, 'long': sc_long}


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, type=Path, help='native taixu compiler')
    ap.add_argument('scenarios', nargs='*')
    a = ap.parse_args()
    exe = str(a.compiler.resolve())
    tmp = tempfile.mkdtemp(prefix='taixu-mcp-e2e-')
    prod = os.path.join(tmp, 'tmp')
    os.mkdir(prod)
    for name, text in (('basic.tx', BASIC_TX), ('idle.tx', BASIC_TX), ('eof.tx', BASIC_TX), ('flood.tx', FLOOD_TX), ('spin.tx', SPIN_TX)):
        Path(tmp, name).write_text(text, encoding='utf-8', newline='\n')
    check = Checks()
    try:
        for name in a.scenarios or list(SCENARIOS):
            try:
                SCENARIOS[name](exe, tmp, prod, check)
            except Exception as e:  # a crash in one scenario is a failure, the rest still run
                check(name + ':crash', False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('SUMMARY %s (%d checks)' % ('PASS' if check.fails == 0 else 'FAIL=%d' % check.fails, check.total))
    return 1 if check.fails else 0


if __name__ == '__main__':
    sys.exit(main())
