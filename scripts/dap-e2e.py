#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native `taixu dap` end-to-end check (round 6 step 6c-5b, docs/design/v052-round6-debug-v0.1-20261018.md §四-8, §六).

Starts `taixu dap` on stdio (Content-Length frames) and drives it like VS Code does:
- main: configurationDone before launch (remembered), breakpoints before launch (pending, one on a line without
  statements), launch (changed events), stop on the breakpoint, stack top first, the "局部变量" and "槽位" scopes,
  a struct local expanded one level (u8 field), the module state slot expanded to the global, evaluate of a
  variable name and of an expression (refused), next, setBreakpoints replacing the file's breakpoints, program
  output as stdout output events, exited with the exit code and terminated, continue after the end (refused),
  disconnect (adapter exits 0);
- live: an endless loop; a breakpoint set while it runs stops it, clearing it and pausing stops it again
  (reason pause), terminate, launch again in the same adapter, pause, disconnect;
- trap: stderr output with the trap line, exited 3 and terminated;
- compile-fail: launch fails with the diagnostics on stderr output and terminated; stdin EOF ends the adapter (0);
- framing: a bad Content-Length ends the adapter with 3 and `taixu dap: BadFrame` on stderr;
- misc: attach and unknown commands are refused, a string seq answers request_seq 0 with valid JSON.

Usage: python scripts/dap-e2e.py --compiler <taixu>   (exit 0 when every check passes)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TIMEOUT_S = 60

MAIN_TX = """struct P {
    x: u8,
    y: int,
}

let g: int = 7;

export function main(args: []string): int {
    let a: int = 1;
    const p = P { x: 200, y: 5 };
    let b: int = 2;
    print("sum " + toStr(a + b + g + p.y));
    return a + b + g;
}
"""

LOOP_TX = """export function main(args: []string): int {
    let i: int = 0;
    while (true) {
        i = i + 1;
    }
    return i;
}
"""

TRAP_TX = """export function main(args: []string): int {
    let z: int = 0;
    print("before");
    return 10 / z;
}
"""

BAD_TX = """export function main(args: []string): int {
    return nope;
}
"""


class Dap:
    """A `taixu dap` process: a reader thread collects messages; the main thread waits for them."""

    def __init__(self, exe, cwd):
        self.proc = subprocess.Popen([exe, 'dap'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=cwd)
        self.msgs = []
        self.bad = []
        self.lock = threading.Lock()
        self.seq = 0
        self.err = b''
        # finish() 等进程退出后还要等这两个线程读完（输出与 stderr 收齐）。
        self.readers = [threading.Thread(target=self._read, daemon=True), threading.Thread(target=self._err, daemon=True)]
        for t in self.readers:
            t.start()

    def _err(self):
        self.err = self.proc.stderr.read()

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
                k, _, v = line.partition(b':')
                if k.strip().lower() == b'content-length':
                    length = int(v.strip())
            body = f.read(length or 0)
            try:
                m = json.loads(body.decode('utf-8'))
            except ValueError:
                with self.lock:
                    self.bad.append(body)
                continue
            with self.lock:
                self.msgs.append(m)

    def raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, command, args=None, seq=None):
        self.seq += 1
        msg = {'seq': self.seq if seq is None else seq, 'type': 'request', 'command': command}
        if args is not None:
            msg['arguments'] = args
        body = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        self.raw(b'Content-Length: %d\r\n\r\n' % len(body) + body)
        return self.seq

    def wait(self, pred, timeout=TIMEOUT_S):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for i, m in enumerate(self.msgs):
                    if pred(m):
                        return self.msgs.pop(i)
            if self.proc.poll() is not None and not self.msgs:
                time.sleep(0.05)
                with self.lock:
                    for i, m in enumerate(self.msgs):
                        if pred(m):
                            return self.msgs.pop(i)
                return None
            time.sleep(0.01)
        return None

    def response(self, seq, timeout=TIMEOUT_S):
        return self.wait(lambda m: m.get('type') == 'response' and m.get('request_seq') == seq, timeout)

    def event(self, name, timeout=TIMEOUT_S, pred=None):
        return self.wait(lambda m: m.get('type') == 'event' and m.get('event') == name and (pred is None or pred(m)), timeout)

    def request(self, command, args=None):
        return self.response(self.send(command, args))

    def outputs(self, category):
        with self.lock:
            got = [m['body']['output'] for m in self.msgs if m.get('event') == 'output' and m['body'].get('category') == category]
        return ''.join(got)

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


def body(r, key, default=None):
    return (r or {}).get('body', {}).get(key, default)


def start(exe, tmp, check, label):
    d = Dap(exe, tmp)
    r = d.request('initialize', {'adapterID': 'taixu'})
    caps = (r or {}).get('body', {})
    check(label + ':initialize', 'capabilities' not in caps and caps.get('supportsConfigurationDoneRequest') is True
          and caps.get('supportsTerminateRequest') is True and caps.get('supportsEvaluateForHovers') is True
          and caps.get('supportsLoadedSourcesRequest') is True, caps)
    check(label + ':initialized', d.event('initialized') is not None)
    return d


def sc_main(exe, tmp, check):
    path = os.path.join(tmp, 'main.tx')
    d = start(exe, tmp, check, 'main')
    r = d.request('configurationDone')
    check('main:configurationDone-first', r is not None and r.get('success') is True, r)
    r = d.request('setBreakpoints', {'source': {'name': 'main.tx', 'path': path}, 'breakpoints': [{'line': 11}, {'line': 2}]})
    bp = body(r, 'breakpoints', [])
    check('main:pending', len(bp) == 2 and not bp[0]['verified'] and not bp[1]['verified'] and bp[0]['id'] != bp[1]['id'], bp)
    r = d.request('launch', {'program': 'main.tx'})
    check('main:launch', r is not None and r.get('success') is True, r)
    e1 = d.event('breakpoint', pred=lambda m: m['body']['breakpoint']['id'] == bp[0]['id'])
    e2 = d.event('breakpoint', pred=lambda m: m['body']['breakpoint']['id'] == bp[1]['id'])
    check('main:changed', e1 is not None and e1['body']['breakpoint']['verified'] is True
          and e2 is not None and e2['body']['breakpoint']['verified'] is False and '该行没有可停的语句' in e2['body']['breakpoint'].get('message', ''),
          (e1, e2))
    ev = d.event('stopped')
    check('main:stopped', ev is not None and ev['body']['reason'] == 'breakpoint' and ev['body']['description'].startswith('main@11:'), ev)
    r = d.request('stackTrace', {'threadId': 1})
    frames = body(r, 'stackFrames', [])
    check('main:stack', len(frames) == 1 and frames[0]['id'] == 1000 and frames[0]['name'] == 'main' and frames[0]['line'] == 11
          and frames[0]['source']['path'].lower().endswith('main.tx'), frames)
    r = d.request('scopes', {'frameId': 1000})
    scopes = body(r, 'scopes', [])
    check('main:scopes', [s['name'] for s in scopes] == ['局部变量', '槽位'] and scopes[0]['variablesReference'] == 1000000
          and scopes[1]['variablesReference'] == 2000000, scopes)
    r = d.request('variables', {'variablesReference': 1000000})
    vs = {v['name']: v for v in body(r, 'variables', [])}
    check('main:locals', 'a' in vs and vs['a']['value'] == '1' and vs['a']['type'] == 'int' and 'p' in vs
          and vs['p']['value'] == 'P（2 字段）' and vs['p']['variablesReference'] > 0 and 'b' not in vs, vs)
    r = d.request('variables', {'variablesReference': vs.get('p', {}).get('variablesReference', 0)})
    kids = {v['name']: v for v in body(r, 'variables', [])}
    check('main:expand', kids.get('x', {}).get('value') == '200' and kids.get('x', {}).get('type') == 'u8'
          and kids.get('y', {}).get('value') == '5', kids)
    r = d.request('variables', {'variablesReference': 2000000})
    slots = body(r, 'variables', [])
    s0 = next((v for v in slots if v['name'] == 's0'), None)
    check('main:slots', s0 is not None and s0['type'] == 'ref' and s0['variablesReference'] > 0, slots)
    r = d.request('variables', {'variablesReference': s0['variablesReference'] if s0 else 0})
    check('main:global', any(v['name'] == 'g' and v['value'] == '7' for v in body(r, 'variables', [])), r)
    r = d.request('evaluate', {'expression': 'a', 'frameId': 1000, 'context': 'hover'})
    check('main:evaluate', body(r, 'result') == '1' and body(r, 'type') == 'int', r)
    r = d.request('evaluate', {'expression': 'a + 1', 'frameId': 1000})
    check('main:evaluate-expr', r is not None and r.get('success') is False and '只支持变量名' in r.get('message', ''), r)
    r = d.request('next', {'threadId': 1})
    ev = d.event('stopped')
    check('main:next', ev is not None and ev['body']['reason'] == 'step' and ev['body']['description'].startswith('main@12:'), ev)
    r = d.request('setBreakpoints', {'source': {'name': 'main.tx', 'path': path}, 'breakpoints': [{'line': 13}]})
    bp = body(r, 'breakpoints', [])
    check('main:replace', len(bp) == 1 and bp[0]['verified'] is True and bp[0]['line'] == 13, bp)
    d.request('continue', {'threadId': 1})
    ev = d.event('stopped')
    check('main:replaced-stop', ev is not None and ev['body']['description'].startswith('main@13:'), ev)
    check('main:stdout', d.outputs('stdout') == 'sum 15\n', d.outputs('stdout'))
    d.request('continue', {'threadId': 1})
    ex = d.event('exited')
    check('main:exited', ex is not None and ex['body']['exitCode'] == 10, ex)
    check('main:terminated', d.event('terminated') is not None)
    r = d.request('continue', {'threadId': 1})
    check('main:after-end', r is not None and r.get('success') is False and '调试目标已结束' in r.get('message', ''), r)
    r = d.request('disconnect')
    check('main:disconnect', r is not None and r.get('success') is True, r)
    code = d.finish()
    check('main:exit0', code == 0 and not d.bad, (code, d.bad[:1]))


def sc_live(exe, tmp, check):
    path = os.path.join(tmp, 'loop.tx')
    d = start(exe, tmp, check, 'live')
    for round_ in (1, 2):
        r = d.request('launch', {'program': path})
        check('live:launch%d' % round_, r is not None and r.get('success') is True, r)
        d.request('configurationDone')
        time.sleep(0.4)
        if round_ == 1:
            r = d.request('setBreakpoints', {'source': {'path': path}, 'breakpoints': [{'line': 4}]})
            check('live:set-running', body(r, 'breakpoints', [{}])[0].get('verified') is True, r)
            ev = d.event('stopped')
            check('live:hit', ev is not None and ev['body']['reason'] == 'breakpoint' and ev['body']['description'].startswith('main@4:'), ev)
            d.request('setBreakpoints', {'source': {'path': path}, 'breakpoints': []})
            d.request('continue', {'threadId': 1})
            time.sleep(0.4)
        r = d.request('pause', {'threadId': 1})
        ev = d.event('stopped')
        check('live:pause%d' % round_, r is not None and r.get('success') is True and ev is not None and ev['body']['reason'] == 'pause', (r, ev))
        r = d.request('stackTrace', {'threadId': 1})
        check('live:stack%d' % round_, [f['name'] for f in body(r, 'stackFrames', [])] == ['main'], r)
        if round_ == 1:
            r = d.request('terminate')
            check('live:terminate', r is not None and r.get('success') is True and d.event('terminated') is not None, r)
    d.request('disconnect')
    code = d.finish()
    check('live:exit0', code == 0, code)


def sc_trap(exe, tmp, check):
    d = start(exe, tmp, check, 'trap')
    d.request('launch', {'program': os.path.join(tmp, 'trap.tx')})
    d.request('configurationDone')
    ex = d.event('exited')
    term = d.event('terminated')
    err = d.outputs('stderr')
    check('trap:events', ex is not None and ex['body']['exitCode'] == 3 and term is not None, (ex, term))
    check('trap:stderr', 'trap div_by_zero' in err and 'trap.tx:4:' in err, err)
    check('trap:stdout', d.outputs('stdout') == 'before\n', d.outputs('stdout'))
    d.request('disconnect')
    check('trap:exit0', d.finish() == 0)


def sc_compile_fail(exe, tmp, check):
    d = start(exe, tmp, check, 'compile')
    r = d.request('launch', {'program': os.path.join(tmp, 'bad.tx')})
    check('compile:launch-fails', r is not None and r.get('success') is False and '编译失败或早退' in r.get('message', ''), r)
    err = d.outputs('stderr')
    check('compile:stderr', '编译失败（调试目标未启动）' in err and 'bad.tx:2:' in err, err)
    check('compile:terminated', d.event('terminated') is not None)
    check('compile:eof-exit0', d.finish() == 0)


def sc_framing(exe, tmp, check):
    from frame_cases import framing_cases
    framing_cases(exe, 'dap', tmp, check)
    d = Dap(exe, tmp)
    d.raw(b'Content-Length: abc\r\n\r\n{}')
    code = d.finish()
    time.sleep(0.2)
    check('framing:bad-length', code == 3 and b'taixu dap: BadFrame' in d.err, (code, d.err[-200:]))


def sc_misc(exe, tmp, check):
    d = start(exe, tmp, check, 'misc')
    r = d.request('attach', {})
    check('misc:attach', r is not None and r.get('success') is False and 'attach' in r.get('message', ''), r)
    r = d.request('frobnicate', {})
    check('misc:unknown', r is not None and r.get('success') is False and r.get('message') == 'command not supported', r)
    d.send('threads', None, seq='x7')
    r = d.wait(lambda m: m.get('type') == 'response' and m.get('command') == 'threads')
    check('misc:string-seq', r is not None and r.get('request_seq') == 0 and not d.bad, (r, d.bad[:1]))
    d.request('disconnect')
    check('misc:exit0', d.finish() == 0)


def sc_link(exe, tmp, check):
    """Breakpoint path and module path that differ only through a symbolic link (a junction on Windows) name the same file:
    the adapter compares real paths, so the breakpoint hits in both directions (跟进第 4 条)."""
    real = os.path.join(tmp, 'real')
    link = os.path.join(tmp, 'link')
    os.makedirs(real, exist_ok=True)
    Path(real, 'main.tx').write_text(MAIN_TX, encoding='utf-8', newline='\n')
    if not os.path.lexists(link):
        if os.name == 'nt':
            from _winapi import CreateJunction
            CreateJunction(real, link)
        else:
            os.symlink(real, link, target_is_directory=True)
    for label, program, bp_path in (('link:bp-via-link', os.path.join(real, 'main.tx'), os.path.join(link, 'main.tx')),
                                    ('link:launch-via-link', os.path.join(link, 'main.tx'), os.path.join(real, 'main.tx'))):
        d = start(exe, tmp, check, label)
        d.request('configurationDone')
        d.request('setBreakpoints', {'source': {'path': bp_path}, 'breakpoints': [{'line': 11}]})
        r = d.request('launch', {'program': program})
        ev = d.event('stopped')
        check(label + ':stopped', r is not None and r.get('success') is True and ev is not None and ev['body']['reason'] == 'breakpoint'
              and ev['body']['description'].startswith('main@11:'), (r, ev))
        d.request('disconnect')
        code = d.finish()
        check(label + ':exit0', code == 0, code)


SCENARIOS = {'main': sc_main, 'live': sc_live, 'trap': sc_trap, 'compile-fail': sc_compile_fail, 'framing': sc_framing, 'misc': sc_misc,
             'link': sc_link}


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, type=Path, help='native taixu compiler')
    ap.add_argument('scenarios', nargs='*')
    a = ap.parse_args()
    exe = str(a.compiler.resolve())
    # 取真实路径：相对的 program 按 getcwd（物理路径）解析，断点路径须与之同形（macOS 的临时目录在符号链接 /var 下）。
    tmp = os.path.realpath(tempfile.mkdtemp(prefix='taixu-dap-e2e-'))
    for name, text in (('main.tx', MAIN_TX), ('loop.tx', LOOP_TX), ('trap.tx', TRAP_TX), ('bad.tx', BAD_TX)):
        Path(tmp, name).write_text(text, encoding='utf-8', newline='\n')
    check = Checks()
    try:
        for name in a.scenarios or list(SCENARIOS):
            try:
                SCENARIOS[name](exe, tmp, check)
            except Exception as e:  # a crash in one scenario is a failure, the rest still run
                check(name + ':crash', False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('SUMMARY %s (%d checks)' % ('PASS' if check.fails == 0 else 'FAIL=%d' % check.fails, check.total))
    return 1 if check.fails else 0


if __name__ == '__main__':
    sys.exit(main())
