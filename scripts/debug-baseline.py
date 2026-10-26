#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试协议基准转录（第 6 轮 6c-0；设计 docs/design/v052-round6-debug-v0.1-20261018.md §五）。

趁 Zig VM 还在，用 Zig CLI 录下三条调试链路的转录，作为原生调试构建（6c-2 起）的对照基准：
  - proto：`taixu run --debug <程序>` 的 NDJSON 协议（D15）；
  - dap：`taixu dap` 的 DAP 会话（Content-Length 帧）；
  - mcp：`taixu mcp` 的 `debug_*` 工具（JSON-RPC 行协议）。

用例清单是 tests/debug/cases.json，程序在 tests/debug/ 下，转录写到 tests/debug/vm/<用例>.<种类>。
转录一行一条：`> ` 为发出的命令或请求，`< ` 为收到的事件或应答，`= ` 为本脚本的记录（退出码、超时）。
proto 进程没发终结事件（exited、trapped、compile_failed）就以非零码退出时，退出码记为 `abnormal`
（Zig 的 panic 在 Windows 上退出码为 3，在 Linux 上是 SIGABRT，两边不同）。

规范化（只为让转录可重放、跨机器一致，不改协议内容）：
  - 字符串里的 tests/debug 绝对路径换成 `<dir>`，其后的路径分隔符统一为 `/`；
  - `at_ms` 置 0；DAP 消息去掉 `seq`（保留 `request_seq`）；
  - 连续 9 条以上的 stdout 事件（proto）或 DAP `output` 事件折叠为一行计数加首尾两条；
    MCP 回执里超过 16 条的 `events` 数组保留首尾各 3 条，中间记省略条数；
  - DAP 每一步先记应答、再按到达顺序记事件；步骤结束前等 150 ms 无新消息。

用法（仓根执行）：
  python scripts/debug-baseline.py --taixu zig-out/bin/taixu.exe record   # 重写全部转录
  python scripts/debug-baseline.py --taixu zig-out/bin/taixu.exe check    # 重录并与已存转录逐字节比较
可加 `--only 名字[,名字]`。check 有任何不一致时退出码 1。
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBUG_DIR = ROOT / 'tests' / 'debug'
VM_DIR = DEBUG_DIR / 'vm'
STEP_TIMEOUT = 60.0
QUIET_S = 0.15
FOLD_MIN = 9
EVENTS_KEEP = 3


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class Normalizer:
    def __init__(self, base):
        self.bases = sorted({str(base), str(base).replace('\\', '/')}, key=len, reverse=True)

    def text(self, s):
        for b in self.bases:
            if b in s:
                out = []
                rest = s
                while b in rest:
                    i = rest.index(b)
                    out.append(rest[:i])
                    rest = rest[i + len(b):]
                    j = 0
                    while j < len(rest) and rest[j] not in ' "\'\n\t:),;':
                        j += 1
                    out.append('<dir>' + rest[:j].replace('\\', '/'))
                    rest = rest[j:]
                out.append(rest)
                return ''.join(out)
        return s

    def value(self, v, key=None):
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if k == 'at_ms':
                    out[k] = 0
                else:
                    out[k] = self.value(x, k)
            if key == '__dap__':
                out.pop('seq', None)
            return out
        if isinstance(v, list):
            items = [self.value(x) for x in v]
            if key == 'events' and len(items) > 2 * EVENTS_KEEP + 10:
                return items[:EVENTS_KEEP] + [{'elided': len(items) - 2 * EVENTS_KEEP}] + items[-EVENTS_KEEP:]
            return items
        if isinstance(v, str):
            return self.text(v)
        return v


class LineReader:
    """Background reader feeding decoded messages into a queue (None marks EOF)."""

    def __init__(self, stream, framed=False):
        self.q = queue.Queue()
        self.stream = stream
        self.framed = framed
        self.closed = False
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        f = self.stream
        try:
            while True:
                if self.framed:
                    length = None
                    while True:
                        line = f.readline()
                        if not line:
                            self.q.put(None)
                            return
                        line = line.strip()
                        if not line:
                            break
                        k, _, v = line.partition(b':')
                        if k.strip().lower() == b'content-length':
                            length = int(v.strip())
                    body = f.read(length or 0)
                    self.q.put(body.decode('utf-8', 'replace'))
                else:
                    line = f.readline()
                    if not line:
                        self.q.put(None)
                        return
                    self.q.put(line.decode('utf-8', 'replace').rstrip('\r\n'))
        except Exception as e:  # noqa: BLE001 - report as EOF with reason
            self.q.put('__reader_error__ %r' % (e,))
            self.q.put(None)

    def get(self, timeout):
        if self.closed and self.q.empty():
            return None
        try:
            m = self.q.get(timeout=timeout)
        except queue.Empty:
            return '__timeout__'
        if m is None:
            self.closed = True
        return m


def parse(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return {'__raw__': raw}


class Transcript:
    def __init__(self, norm):
        self.norm = norm
        self.lines = []
        self.fold = []

    def flush(self):
        if not self.fold:
            return
        if len(self.fold) >= FOLD_MIN:
            first, last = self.fold[0], self.fold[-1]
            self.lines.append('< ' + dump({'folded': len(self.fold), 'first': first, 'last': last}))
        else:
            self.lines += ['< ' + dump(x) for x in self.fold]
        self.fold = []

    def recv(self, msg, foldable=False):
        if foldable:
            self.fold.append(msg)
            return
        self.flush()
        self.lines.append('< ' + dump(msg))

    def send(self, text):
        self.flush()
        self.lines.append('> ' + text)

    def note(self, value):
        self.flush()
        self.lines.append('= ' + dump(value))

    def text(self):
        self.flush()
        return '\n'.join(self.lines) + '\n'


def spawn(taixu, args):
    return subprocess.Popen([taixu, *args], cwd=DEBUG_DIR, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def finish(proc, tr, reader=None, crashed=None):
    try:
        proc.stdin.close()
    except OSError:
        pass
    if reader is not None:
        while True:
            m = reader.get(STEP_TIMEOUT)
            if m is None:
                break
            if m == '__timeout__':
                tr.note({'harness': 'timeout', 'at': 'eof'})
                proc.kill()
                break
            yield m
    try:
        code = proc.wait(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = 'killed'
    if crashed is not None and crashed() and code != 0:
        code = 'abnormal'
    tr.note({'exit': code})


# ---------------------------------------------------------------- proto

REPLY = {
    'breakpoint_set': {'breakpoint_set'}, 'breakpoint_fn': {'breakpoint_fn'},
    'frames': {'frames'}, 'inspect': {'inspect'}, 'stops': {'stops'},
    'continue': {'stopped', 'exited', 'trapped'}, 'step': {'stopped', 'exited', 'trapped'},
    'kill': {'exited'},
}
TERMINAL = {'exited', 'trapped', 'compile_failed'}


def run_proto(taixu, case, norm):
    tr = Transcript(norm)
    proc = spawn(taixu, ['run', '--debug', case['program']])
    reader = LineReader(proc.stdout)
    state = {'done': False, 'frames': None, 'terminal': False}

    def read_until(want):
        while True:
            raw = reader.get(STEP_TIMEOUT)
            if raw is None:
                state['done'] = True
                tr.note({'eof': True})
                return None
            if raw == '__timeout__':
                tr.note({'harness': 'timeout'})
                proc.kill()
                state['done'] = True
                return None
            msg = norm.value(parse(raw))
            ev = msg.get('event') if isinstance(msg, dict) else None
            tr.recv(msg, foldable=(ev == 'stdout'))
            if ev in TERMINAL:
                state['done'] = True
                state['terminal'] = True
            if ev == 'frames':
                state['frames'] = msg.get('frames')
            if ev in want or ev == 'error' or ev in TERMINAL:
                return msg

    def send(cmd):
        text = cmd if isinstance(cmd, str) else dump(cmd)
        tr.send(norm.text(text))
        proc.stdin.write(text.encode('utf-8') + b'\n')
        proc.stdin.flush()
        want = REPLY.get(cmd.get('cmd'), set()) if isinstance(cmd, dict) else set()
        return read_until(want)

    read_until({'ready'})
    for item in case.get('script', []):
        if state['done']:
            tr.note({'skipped': item})
            continue
        op = item.get('@') if isinstance(item, dict) else None
        if op is None:
            send(norm_args(item, norm))
        elif op == 'inspect_all':
            frames = state['frames'] or []
            idx = item['frame']
            n = frames[idx]['reg_count'] if 0 <= idx < len(frames) else 0
            for slot in range(n):
                if state['done']:
                    break
                send({'cmd': 'inspect', 'frame': idx, 'slot': slot})
        elif op == 'inspect_named':
            # Native sessions only (round 6 step 6c-4c): the slots of the frame that carry a variable name.
            frames = state['frames'] or []
            idx = item['frame']
            slots = frames[idx]['slots'] if 0 <= idx < len(frames) else []
            for s in slots:
                if state['done']:
                    break
                if 'name' in s:
                    send({'cmd': 'inspect', 'frame': idx, 'slot': s['idx']})
        elif op in ('continue_until_exit', 'step_until_exit'):
            cmd = {'cmd': 'continue'} if op == 'continue_until_exit' else {'cmd': 'step', 'mode': item.get('mode', 'into')}
            for _ in range(item.get('max', 50)):
                if state['done']:
                    break
                r = send(cmd)
                if item.get('frames') and r is not None and r.get('event') == 'stopped':
                    send({'cmd': 'frames'})
            if not state['done']:
                tr.note({'harness': 'max reached', 'op': op})
        elif op == 'eof':
            break
        else:
            raise SystemExit('unknown directive %r in %s' % (op, case['name']))
    for raw in finish(proc, tr, reader, crashed=lambda: not state['terminal']):
        msg = norm.value(parse(raw))
        ev = msg.get('event') if isinstance(msg, dict) else None
        if ev in TERMINAL:
            state['terminal'] = True
        tr.recv(msg, foldable=(ev == 'stdout'))
    return tr.text()


def norm_args(item, norm):
    """Replace the `<dir>` placeholder in script values with the real tests/debug path."""
    if isinstance(item, dict):
        return {k: norm_args(v, norm) for k, v in item.items()}
    if isinstance(item, list):
        return [norm_args(v, norm) for v in item]
    if isinstance(item, str) and item.startswith('<dir>/'):
        return str(DEBUG_DIR / item[len('<dir>/'):])
    return item


# ---------------------------------------------------------------- dap

def run_dap(taixu, case, norm):
    tr = Transcript(norm)
    proc = spawn(taixu, ['dap'])
    reader = LineReader(proc.stdout, framed=True)
    seq = 0
    pending = []
    eof = False

    def take(timeout):
        nonlocal eof
        if pending:
            return pending.pop(0)
        if eof:
            return None
        raw = reader.get(timeout)
        if raw is None:
            eof = True
            return None
        if raw == '__timeout__':
            return '__timeout__'
        return norm.value(parse(raw), '__dap__')

    for step in case['script']:
        seq += 1
        msg = {'seq': seq, 'type': 'request', 'command': step['request']}
        if 'arguments' in step:
            msg['arguments'] = norm_args(step['arguments'], norm)
        body = dump(msg).encode('utf-8')
        tr.send(dump({'command': step['request'], 'arguments': norm.value(msg.get('arguments'))}
                     if 'arguments' in msg else {'command': step['request']}))
        try:
            proc.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
            proc.stdin.flush()
        except OSError:
            tr.note({'harness': 'stdin closed'})
            break
        want = list(step.get('until', []))
        response = None
        events = []
        deadline = time.time() + STEP_TIMEOUT
        while (response is None or want) and time.time() < deadline:
            m = take(deadline - time.time())
            if m is None or m == '__timeout__':
                break
            if m.get('type') == 'response' and m.get('request_seq') == seq:
                response = m
            else:
                events.append(m)
                if m.get('type') == 'event' and m.get('event') in want:
                    want.remove(m.get('event'))
        while True:
            m = take(QUIET_S)
            if m is None or m == '__timeout__':
                break
            if m.get('type') == 'response' and m.get('request_seq') == seq and response is None:
                response = m
            else:
                events.append(m)
        tr.recv(response if response is not None else {'harness': 'no response'})
        for e in events:
            tr.recv(e, foldable=(e.get('event') == 'output'))
        if want:
            tr.note({'harness': 'missing events', 'events': want})
    for raw in finish(proc, tr, reader):
        m = norm.value(parse(raw), '__dap__')
        tr.recv(m, foldable=(m.get('event') == 'output'))
    return tr.text()


# ---------------------------------------------------------------- mcp

def run_mcp(taixu, case, norm):
    tr = Transcript(norm)
    proc = spawn(taixu, ['mcp', '--trust-workspace'])
    sessions = {}
    reader = LineReader(proc.stdout)
    rid = 0
    for step in case['script']:
        rid += 1
        if 'tool' in step:
            req = {'jsonrpc': '2.0', 'id': rid, 'method': 'tools/call',
                   'params': {'name': step['tool'], 'arguments': norm_args(step.get('arguments', {}), norm)}}
            shown = {'tool': step['tool'], 'arguments': step.get('arguments', {})}
        else:
            req = {'jsonrpc': '2.0', 'id': rid, 'method': step['method'], 'params': step.get('params', {})}
            shown = {'method': step['method']}
        tr.send(dump(shown))
        if 'tool' in step and 'session' in req['params']['arguments']:
            logical = req['params']['arguments']['session']
            req['params']['arguments']['session'] = sessions.get(logical, logical)
        proc.stdin.write(dump(req).encode('utf-8') + b'\n')
        proc.stdin.flush()
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
                structured = result['structuredContent']
                if step.get('tool') == 'debug_start' and isinstance(structured, dict) and 'session' in structured:
                    sessions[str(len(sessions) + 1)] = structured['session']
                if isinstance(structured, dict) and 'session' in structured:
                    structured = dict(structured)
                    structured['session'] = next((k for k, v in sessions.items() if v == structured['session']), structured['session'])
                out = {'structured': structured}
                if result.get('isError'):
                    out['isError'] = True
            else:
                out = {k: v for k, v in m.items() if k not in ('jsonrpc', 'id')}
            tr.recv(norm.value(out))
            break
    for raw in finish(proc, tr, reader):
        tr.recv(norm.value(parse(raw)))
    return tr.text()


RUNNERS = {'proto': run_proto, 'dap': run_dap, 'mcp': run_mcp}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--taixu', required=True, help='Zig CLI（zig-out/bin/taixu(.exe)）')
    ap.add_argument('--only', default='', help='逗号分隔的用例名')
    ap.add_argument('mode', choices=('record', 'check'))
    a = ap.parse_args()
    taixu = str(Path(a.taixu).resolve())
    cases = json.loads((DEBUG_DIR / 'cases.json').read_text(encoding='utf-8'))
    only = {x for x in a.only.split(',') if x}
    norm = Normalizer(DEBUG_DIR)
    VM_DIR.mkdir(exist_ok=True)
    bad = 0
    for case in cases:
        if only and case['name'] not in only:
            continue
        t0 = time.time()
        text = RUNNERS[case['kind']](taixu, case, norm)
        path = VM_DIR / ('%s.%s' % (case['name'], case['kind']))
        took = time.time() - t0
        if a.mode == 'record':
            path.write_text(text, encoding='utf-8', newline='\n')
            print('REC   %-22s %4d lines  %.1fs' % (case['name'], text.count('\n'), took), flush=True)
            continue
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        if old == text:
            print('PASS  %-22s %.1fs' % (case['name'], took), flush=True)
            continue
        bad += 1
        ol, nl = old.splitlines(), text.splitlines()
        i = next((k for k in range(max(len(ol), len(nl)))
                  if k >= len(ol) or k >= len(nl) or ol[k] != nl[k]), 0)
        print('FAIL  %-22s line %d' % (case['name'], i + 1), flush=True)
        print('   stored: %s' % (ol[i][:300] if i < len(ol) else '<none>'))
        print('   now   : %s' % (nl[i][:300] if i < len(nl) else '<none>'))
    if a.mode == 'check':
        print('SUMMARY %s' % ('PASS' if bad == 0 else 'FAIL=%d' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
