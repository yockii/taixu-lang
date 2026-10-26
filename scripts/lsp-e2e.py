#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原生 `taixu lsp` 的无头会话门（第 6 轮 6d-2 起；设计 docs/design/v052-round6-lsp-mcp-v0.1-20261019.md §三-11、§四-1、§六）。

TOOL-47 时这个脚本对着 Zig CLI 的 LSP；6d 起改为原生版，按步骤扩项（6d-0 的 Zig 基准转录见 scripts/lsp-baseline.py）。
6d-2 的各组：
  lifecycle  initialize 之前、再次 initialize、坏 JSON、非对象、缺 method、带 id 的 $/、未知方法、串 id、shutdown 之后、退出码 0/1/3、
             --stdio 重复与 --toolchain；
  encoding   缺省 utf-16、宣告 utf-8 时选 utf-8，中文行的列；
  uri        Windows 的 %3A 形与小写盘符、百分号编码的中文路径，POSIX 路径；回复与诊断用客户端给的 URI；
  sync       全量与带 range 的变更；
  diag       分文件发布、依赖改动后重查、修好后清空、覆盖层、未保存的新文件、导入失败、关闭后、防抖、范围终点；
  format     只替换首尾相同行之间的一段（无尾换行、只改中间一行，编辑应用后与格式化结果相同）、无变化、词法错误、未打开；
  symbols    各种声明的种类、名字位置与范围，utf-16 下的中文列；
  refs       （6d-3a2，§四-3）definition（捕获、跨文件、字段、方法糖、内建、名字末尾的光标、中文列、for-in、泛型体）、
             references（struct 的导入名、类型标注补面、字面量、声明，includeDeclaration）、rename（局部、跨文件、方法糖
             的前缀改写与拒绝、struct 连类型标注、模板插值、标准库、遮蔽、坏新名与缺 newName），变更后立即请求按新文本作答；
  tokens     （6d-3a3，§四-4）legend、记号逐个对照（补面、中文串之后的 utf-16 列）、变更后立即请求、未打开的文档、
             refreshSupport 时的 refresh 与对其响应的静默收下；
  hover      （6d-3b1，§四-5）各类声明的说明、泛型照写与随实例不同的注明、导入名、prelude、内建与遮蔽内建的用户函数、
             名字末尾、关键字与注释为 null、变更后立即请求、plaintext 偏好。
  sig        （6d-3b2，§四-5）宣告的触发字符、各个实参位置、嵌套与 ) 之后、函数名上为 null、方法糖不含接收者、变体构造、
             函数类型的绑定、串里的逗号、隔着型参的内建（utf-16 偏移）、导入的函数、未闭合的 (、分组括号；
  complete   （6d-3b2，§四-5）局部按位置与块、形参、顶层声明与导入名、遮蔽内建、内建与 --toolchain、prelude 的导出、
             类型名、None 与关键字，注释、串与 . 之后为空表。
用法（仓根执行）：python scripts/lsp-e2e.py --compiler <原生 taixu> [--only 组,组]
"""

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from frame_cases import framing_cases

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == 'nt'
WAIT = 30.0

failures = []
passes = 0


def check(name, ok, detail=''):
    global passes
    if ok:
        passes += 1
        print('PASS  ' + name, flush=True)
    else:
        failures.append(name)
        print('FAIL  %s %s' % (name, str(detail)[:400]), flush=True)


def frame(body):
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    b = body.encode('utf-8')
    return b'Content-Length: %d\r\n\r\n' % len(b) + b


AUTHORIZED_TEST_ROOTS = (ROOT,)


class Session:
    def __init__(self, compiler, args=('lsp', '--stdio')):
        if '--workspace' not in args:
            args = (*args, *(v for root in AUTHORIZED_TEST_ROOTS for v in ('--workspace', str(root))))
            # 此语料需要读取仓库外的标准库；测试启动者显式授权，协议不扩权。
            if '--library-root' not in args:
                # 公开仓 taixu-lang 的标准库在仓内 stdlib/（已在工作区之内）；开发仓是同级的 taixu-stdlib。
                lib = ROOT / 'stdlib/src' if (ROOT / 'stdlib/src').is_dir() else ROOT.parent / 'taixu-stdlib/src'
                args = (*args, '--library-root', str(lib))
        self.p = subprocess.Popen([compiler, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=ROOT)
        self.q = queue.Queue()
        self.notes = []
        self.next_id = 0
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()
        self.err = []
        self.err_thread = threading.Thread(target=lambda: self.err.append(self.p.stderr.read()), daemon=True)
        self.err_thread.start()

    def _pump(self):
        f = self.p.stdout
        try:
            while True:
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
                self.q.put(json.loads(f.read(length or 0).decode('utf-8')))
        except Exception as e:  # noqa: BLE001
            self.q.put({'__reader_error__': repr(e)})
            self.q.put(None)

    def raw(self, data):
        try:
            self.p.stdin.write(data)
            self.p.stdin.flush()
        except OSError:
            pass

    def send(self, obj):
        self.raw(frame(obj))

    def get(self, timeout=WAIT):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return '__timeout__'

    def wait_id(self, rid, timeout=WAIT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self.get(deadline - time.time())
            if m is None or m == '__timeout__':
                return None
            if isinstance(m, dict) and 'method' not in m and m.get('id') == rid:
                return m
            self.notes.append(m)
        return None

    def request(self, method, params=None, rid=None):
        if rid is None:
            self.next_id += 1
            rid = self.next_id
        msg = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            msg['params'] = params
        self.send(msg)
        return self.wait_id(rid)

    def notify(self, method, params=None):
        msg = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            msg['params'] = params
        self.send(msg)

    def quiet(self, secs):
        """Collect notifications until `secs` of silence."""
        while True:
            m = self.get(secs)
            if m is None or m == '__timeout__':
                return
            self.notes.append(m)

    def diags(self, uri, timeout=WAIT):
        """The next publishDiagnostics for `uri` (searching collected notes first)."""
        deadline = time.time() + timeout
        while True:
            for i, m in enumerate(self.notes):
                if m.get('method') == 'textDocument/publishDiagnostics' and m['params']['uri'] == uri:
                    del self.notes[i]
                    return m['params']['diagnostics']
            left = deadline - time.time()
            if left <= 0:
                return None
            m = self.get(left)
            if m is None or m == '__timeout__':
                return None
            self.notes.append(m)

    def pubs_for(self, uri):
        n = [m for m in self.notes if m.get('method') == 'textDocument/publishDiagnostics' and m['params']['uri'] == uri]
        self.notes = [m for m in self.notes if m not in n]
        return [m['params']['diagnostics'] for m in n]

    def init(self, encodings=None):
        caps = {} if encodings is None else {'general': {'positionEncodings': encodings}}
        r = self.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': caps})
        self.notify('initialized', {})
        return r

    def open(self, uri, text, version=1):
        self.notify('textDocument/didOpen', {'textDocument': {'uri': uri, 'languageId': 'taixu', 'version': version, 'text': text}})

    def change(self, uri, version, changes):
        self.notify('textDocument/didChange', {'textDocument': {'uri': uri, 'version': version}, 'contentChanges': changes})

    def close(self, uri):
        self.notify('textDocument/didClose', {'textDocument': {'uri': uri}})

    def finish(self, shutdown=True):
        if shutdown:
            self.request('shutdown')
        self.notify('exit')
        try:
            code = self.p.wait(timeout=WAIT)
        except subprocess.TimeoutExpired:
            self.p.kill()
            code = 'killed'
        self.reader.join(WAIT)
        self.err_thread.join(WAIT)
        return code

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()
        self.reader.join(WAIT)
        self.err_thread.join(WAIT)


def uri_of(path):
    return Path(path).resolve().as_uri()


def vscode_uri(path):
    """VS Code's form on Windows: lowercase drive and an encoded colon (file:///e%3A/...)."""
    u = Path(path).resolve().as_uri()
    if WINDOWS and len(u) > 10 and u[9] == ':':
        u = u[:8] + u[8].lower() + '%3A' + u[10:]
    return u


def units(s, enc):
    if enc == 'utf-8':
        return len(s.encode('utf-8'))
    return len(s.encode('utf-16-le')) // 2


def pos_of(text, needle, enc, delta=0):
    for ln, line in enumerate(text.split('\n')):
        i = line.find(needle)
        if i >= 0:
            return {'line': ln, 'character': units(line[:i + delta], enc)}
    raise SystemExit('needle %r not found' % needle)


# ---------------------------------------------------------------- groups

def g_lifecycle(compiler, work):
    s = Session(compiler, ('lsp', '--stdio', '--stdio'))
    try:
        r = s.request('textDocument/documentSymbol', {'textDocument': {'uri': 'file:///x.tx'}}, rid=1)
        check('lifecycle: initialize 之前的请求 -32002', r and r.get('error', {}).get('code') == -32002 and r.get('id') == 1, r)
        r = s.init()
        caps = (r or {}).get('result', {}).get('capabilities', {})
        info = (r or {}).get('result', {}).get('serverInfo', {})
        check('lifecycle: initialize 能力（缺省 utf-16、格式化、documentSymbol、全量同步）',
              caps.get('positionEncoding') == 'utf-16' and caps.get('documentFormattingProvider') is True and caps.get('documentSymbolProvider') is True
              and caps.get('textDocumentSync') == {'openClose': True, 'change': 1}, caps)
        check('lifecycle: serverInfo 增 version', info.get('name') == 'taixu' and len(info.get('version', '')) > 0, info)
        r = s.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {}})
        check('lifecycle: 再次 initialize 回 -32600（LSP 3.17：只能发一次）', r and r.get('error', {}).get('code') == -32600, r)
        s.raw(frame('not json'))
        m = s.get()
        check('lifecycle: 坏 JSON 回 -32700 且带 "id":null', isinstance(m, dict) and m.get('error', {}).get('code') == -32700 and 'id' in m and m['id'] is None, m)
        s.raw(frame('[1,2]'))
        m = s.get()
        check('lifecycle: 非对象回 -32600 且带 "id":null', isinstance(m, dict) and m.get('error', {}).get('code') == -32600 and 'id' in m and m['id'] is None, m)
        s.raw(frame('{"jsonrpc":"2.0","id":77}'))
        m = s.get()
        check('lifecycle: 带 id 缺 method 回 -32600', isinstance(m, dict) and m.get('error', {}).get('code') == -32600 and m.get('id') == 77, m)
        r = s.request('$/frobnicate', {})
        check('lifecycle: 带 id 的 $/ 请求 -32601', r and r.get('error', {}).get('code') == -32601, r)
        r = s.request('workspace/symbol', {'query': 'x'}, rid='s-1')
        check('lifecycle: 未知请求 -32601，串 id 原样回显', r and r.get('error', {}).get('code') == -32601 and r.get('id') == 's-1', r)
        s.notify('$/cancelRequest', {'id': 1})
        s.notify('workspace/didChangeConfiguration', {'settings': {}})
        r = s.request('shutdown')
        check('lifecycle: shutdown 回 null', r is not None and 'result' in r and r['result'] is None, r)
        r = s.request('textDocument/documentSymbol', {'textDocument': {'uri': 'file:///x.tx'}})
        check('lifecycle: shutdown 之后的请求 -32600', r and r.get('error', {}).get('code') == -32600, r)
        code = s.finish(shutdown=False)
        check('lifecycle: shutdown 后 exit 退出 0', code == 0, code)
    finally:
        s.kill()
    s = Session(compiler, ('lsp',))
    try:
        s.init()
        code = s.finish(shutdown=False)
        check('lifecycle: 未 shutdown 就 exit 退出 1', code == 1, code)
    finally:
        s.kill()
    s = Session(compiler, ('lsp',))
    try:
        s.raw(b'Content-Length: abc\r\n\r\n{}')
        code = s.p.wait(timeout=WAIT)
        check('lifecycle: 坏帧头退出 3', code == 3, code)
    finally:
        s.kill()
    s = Session(compiler, ('lsp',))
    try:
        s.raw(b'Content-Length: 40000000\r\n\r\n')
        code = s.p.wait(timeout=WAIT)
        check('lifecycle: 超过 32 MiB 的消息退出 3', code == 3, code)
    finally:
        s.kill()
    s = Session(compiler, ('lsp', '--toolchain', '--stdio'))
    try:
        r = s.init()
        check('lifecycle: 接受 --toolchain', r is not None and 'result' in r, r)
        s.finish()
    finally:
        s.kill()
    p = subprocess.run([compiler, 'lsp', '--bogus'], capture_output=True, timeout=WAIT)
    check('lifecycle: 不认识的选项退出 64', p.returncode == 64, p.returncode)


ZH = ('// 中文注释：位置换算\n'
      'export struct Point {\n    x: int,\n    y: int,\n}\n\n'
      'export function dist(p: Ref< Point>): int {\n    return p.x + p.y;\n}\n\n'
      'export function main(args: []string): int {\n    const p: Ref< Point> = new Point { x: 1, y: 2 };\n'
      '    print("距离：" + toStr(dist(p)) + "𝄞" + toStr(nope));\n    return 0;\n}\n')


def g_encoding(compiler, work):
    path = work / 'enc' / 'zh.tx'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ZH, encoding='utf-8', newline='\n')
    for encs, want in ((['utf-8', 'utf-16'], 'utf-8'), (['utf-16'], 'utf-16'), (None, 'utf-16')):
        s = Session(compiler)
        try:
            r = s.init(encs)
            got = (r or {}).get('result', {}).get('capabilities', {}).get('positionEncoding')
            check('encoding: 宣告 %r 时选 %s' % (encs, want), got == want, got)
            u = uri_of(path)
            s.open(u, ZH)
            ds = s.diags(u)
            d = [x for x in (ds or []) if x.get('code') == 3001]
            want_start = pos_of(ZH, 'nope));', want)
            want_end = dict(want_start, character=want_start['character'] + 4)
            check('encoding: %s 下中文串与四字节字符之后的诊断列与终点' % want,
                  len(d) == 1 and d[0]['range'] == {'start': want_start, 'end': want_end}, ds)
            s.finish()
        finally:
            s.kill()


def g_uri(compiler, work):
    d = work / 'uri' / '目录 甲'
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'm.tx'
    text = 'export function main(args: []string): int {\n    return gone();\n}\n'
    f.write_text(text, encoding='utf-8', newline='\n')
    forms = [('百分号编码', uri_of(f))]
    if WINDOWS:
        forms.append(('VS Code 形（小写盘符、%3A）', vscode_uri(f)))
        forms.append(('未编码的中文', 'file:///' + str(f.resolve()).replace('\\', '/')))
    for name, u in forms:
        s = Session(compiler)
        try:
            s.init()
            s.open(u, text)
            ds = s.diags(u)
            check('uri: %s 的诊断以客户端的 URI 发布' % name, ds is not None and any(x.get('code') == 3001 for x in ds), ds)
            r = s.request('textDocument/documentSymbol', {'textDocument': {'uri': u}})
            check('uri: %s 的 documentSymbol' % name, r and [x['name'] for x in r.get('result', [])] == ['main'], r)
            s.finish()
        finally:
            s.kill()


def proj(work):
    d = work / 'proj'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    files = {
        'a.tx': 'import { twice } from "./b";\nimport { fresh } from "./c";\n\nexport function main(args: []string): int {\n    return twice(fresh());\n}\n',
        'b.tx': 'export function twice(n: int): int {\n    const s: string = n;\n    return n * 2;\n}\n',
    }
    for k, v in files.items():
        (d / k).write_text(v, encoding='utf-8', newline='\n')
    return d, files


def codes(ds):
    return sorted(x.get('code', 0) for x in (ds or []))


def g_diag(compiler, work):
    d, files = proj(work)
    ua, ub, uc = uri_of(d / 'a.tx'), uri_of(d / 'b.tx'), uri_of(d / 'c.tx')
    s = Session(compiler)
    try:
        s.init()
        s.open(ua, files['a.tx'])
        da = s.diags(ua)
        db = s.diags(ub)
        check('diag: 入口的诊断发在入口（缺导入 3008、未定义 3001）', codes(da) == [3001, 3008], da)
        check('diag: 被导入模块的诊断发在它自己的文件（未打开也发）', codes(db) == [3002], db)
        imp = [x for x in (da or []) if x.get('code') == 3008]
        check('diag: 缺导入落在 import 语句上', imp and imp[0]['range']['start'] == {'line': 1, 'character': 0}, imp)
        undef = [x for x in (da or []) if x.get('code') == 3001]
        st = pos_of(files['a.tx'], 'fresh());', 'utf-16')
        check('diag: 范围终点为记号末尾', undef and undef[0]['range'] == {'start': st, 'end': dict(st, character=st['character'] + 5)}, undef)
        # 未保存的新文件：打开 c.tx（磁盘上没有）后缺导入消失。
        s.open(uc, 'export function fresh(): int {\n    return 3;\n}\n')
        da = s.diags(ua)
        check('diag: 打开未保存的新文件后缺导入消失', codes(da) == [], da)
        # 依赖改动后重查：b 打开并改掉 twice，a 的诊断随之出现。
        s.open(ub, files['b.tx'])
        s.quiet(1.0)
        s.pubs_for(ub)
        s.change(ub, 2, [{'text': 'export function thrice(n: int): int {\n    return n * 3;\n}\n'}])
        da = s.diags(ua)
        db = s.diags(ub)
        check('diag: 依赖改动后导入方重查（twice 不再导出）', da is not None and (3001 in codes(da) or 3008 in codes(da)), da)
        check('diag: 修好的文件清空诊断', db == [], db)
        # 防抖：连续五次变更只分析一次（300 ms 内）。
        s.quiet(1.0)
        s.pubs_for(ua)
        s.pubs_for(ub)
        for v in range(3, 8):
            body = 'export function twice(n: int): int {\n    const s: string = %d;\n    return n * 2;\n}\n' % v
            s.change(ub, v, [{'text': body}])
            time.sleep(0.03)
        s.quiet(1.5)
        s.pubs_for(ua)  # twice 恢复后入口的诊断清空，这条与本项无关
        pb = s.pubs_for(ub)
        check('diag: 连续变更防抖后只发布一次', len(pb) == 1 and codes(pb[0]) == [3002], pb)
        # 带 range 的变更按协商编码应用（§五）。
        # 第 1 行为 `    const s: string = 7;`，把第 22 列的 7 换成 "x"。
        s.change(ub, 9, [{'range': {'start': {'line': 1, 'character': 22}, 'end': {'line': 1, 'character': 23}}, 'text': '"x"'}])
        s.quiet(1.5)
        s.pubs_for(ua)
        pb = s.pubs_for(ub)
        check('diag: 带 range 的变更生效（修好后清空）', pb == [[]], pb)
        r = s.request('textDocument/documentSymbol', {'textDocument': {'uri': ub}})
        check('diag: 带 range 的变更后文本正确', r and [x['name'] for x in r['result']] == ['twice'], r)
        # 关闭未保存的新文件：缺导入回来。
        s.close(uc)
        da = s.diags(ua)
        check('diag: 关闭未保存的新文件后缺导入回来', da is not None and 3008 in codes(da), da)
        # 覆盖层：入口未保存的修改。
        s.change(ua, 2, [{'text': 'export function main(args: []string): int {\n    return 0;\n}\n'}])
        da = s.diags(ua)
        check('diag: 入口未保存的修改生效（覆盖层）', da == [], da)
        code = s.finish()
        check('diag: 正常退出', code == 0, code)
    finally:
        s.kill()


def apply_edits(text, edits, enc):
    """Apply LSP TextEdits (non-overlapping) to text; positions in `enc` units."""
    lines = text.split('\n')

    def off(p):
        before = sum(len(l.encode('utf-8')) + 1 for l in lines[:p['line']])
        line = lines[p['line']] if p['line'] < len(lines) else ''
        b = line.encode('utf-8')
        if enc == 'utf-16':
            k = len(line.encode('utf-16-le')[:p['character'] * 2].decode('utf-16-le', 'ignore').encode('utf-8'))
        else:
            k = p['character']
        return before + min(k, len(b))

    raw = text.encode('utf-8')
    for e in sorted(edits, key=lambda e: (e['range']['start']['line'], e['range']['start']['character']), reverse=True):
        a, z = off(e['range']['start']), off(e['range']['end'])
        raw = raw[:a] + e['newText'].encode('utf-8') + raw[z:]
    return raw.decode('utf-8')


def g_format(compiler, work):
    path = work / 'fmt' / 'f.tx'
    path.parent.mkdir(parents=True, exist_ok=True)
    messy = 'export function f(n: int): int {\n  return n;\n}'
    clean = 'export function f(n: int): int {\n    return n;\n}\n'
    path.write_text(messy, encoding='utf-8', newline='\n')
    u = uri_of(path)
    s = Session(compiler)
    try:
        s.init()
        s.open(u, messy)
        r = s.request('textDocument/formatting', {'textDocument': {'uri': u}, 'options': {'tabSize': 4, 'insertSpaces': True}})
        edits = (r or {}).get('result')
        # 首行相同、最后一行无尾换行而不同：替换第 1 行起到文末（改正 Zig 版重复最后一行）。
        ok = isinstance(edits, list) and len(edits) == 1 and edits[0]['range'] == {'start': {'line': 1, 'character': 0}, 'end': {'line': 2, 'character': 1}}
        check('format: 无尾换行时只替换变化的行段、终点为文末', ok, edits)
        check('format: 应用编辑后与格式化结果相同', isinstance(edits, list) and apply_edits(messy, edits, 'utf-16') == clean, edits)
        mid = 'export function f(n: int): int {\n  return n;\n}\n'
        s.change(u, 2, [{'text': mid}])
        r = s.request('textDocument/formatting', {'textDocument': {'uri': u}, 'options': {}})
        edits = (r or {}).get('result')
        check('format: 只改中间一行时编辑只覆盖那一行',
              isinstance(edits, list) and len(edits) == 1 and edits[0]['range'] == {'start': {'line': 1, 'character': 0}, 'end': {'line': 2, 'character': 0}}
              and edits[0]['newText'] == '    return n;\n', edits)
        check('format: 应用后与格式化结果相同（中间一行）', isinstance(edits, list) and apply_edits(mid, edits, 'utf-16') == clean, edits)
        s.change(u, 3, [{'text': clean}])
        r = s.request('textDocument/formatting', {'textDocument': {'uri': u}, 'options': {}})
        check('format: 已规范时回 []', r and r.get('result') == [], r)
        s.change(u, 4, [{'text': 'export function f(): int {\n    return "unterminated;\n}\n'}])
        r = s.request('textDocument/formatting', {'textDocument': {'uri': u}, 'options': {}})
        check('format: 词法错误时回 null', r and 'result' in r and r['result'] is None, r)
        r = s.request('textDocument/formatting', {'textDocument': {'uri': uri_of(work / 'fmt' / 'none.tx')}, 'options': {}})
        check('format: 未打开的文档回 null', r and 'result' in r and r['result'] is None, r)
        s.finish()
    finally:
        s.kill()


SYMS = ('import { x } from "./other";\n'
        'export const UNIT: int = 1;\n'
        'let counter = 0;\n'
        'const (left, right) = pair();\n'
        'export struct Box<T> {\n    v: T,\n}\n'
        'enum Color {\n    Red,\n    Green,\n}\n'
        'export interface Shape {\n    function area(): int;\n}\n'
        'declare type Handle;\n'
        'declare function lstrlenW(s: wstr): i32 from "kernel32";\n'
        'export async function load(): int {\n    return 1;\n}\n'
        'function* gen(): Gen<int> {\n    yield 1;\n}\n'
        'test "加法：\\"一\\"" {\n    assert(1 + 1 == 2, "x");\n}\n'
        'export function 坏(\n'
        'export function after(): int {\n    return 2;\n}\n')


def g_symbols(compiler, work):
    path = work / 'sym' / 's.tx'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SYMS, encoding='utf-8', newline='\n')
    u = uri_of(path)
    s = Session(compiler)
    try:
        s.init()
        s.open(u, SYMS)
        r = s.request('textDocument/documentSymbol', {'textDocument': {'uri': u}})
        res = (r or {}).get('result') or []
        got = [(x['name'], x['kind']) for x in res]
        want = [('UNIT', 14), ('counter', 13), ('left', 14), ('right', 14), ('Box', 23), ('Color', 10), ('Shape', 11),
                ('Handle', 23), ('lstrlenW', 12), ('load', 12), ('gen', 12), ('加法："一"', 12), ('after', 12)]
        check('symbols: 种类与名字（import 不列，test 为 12 且名字解码，解构逐个列）', got == want, got)
        by = {x['name']: x for x in res}
        unit = by.get('UNIT')
        check('symbols: 绑定的名字位置在名字上（不在关键字上）', unit and unit['selectionRange'] == {'start': {'line': 1, 'character': 13}, 'end': {'line': 1, 'character': 17}}, unit)
        check('symbols: 范围从 export 起到 ; 止', unit and unit['range'] == {'start': {'line': 1, 'character': 0}, 'end': {'line': 1, 'character': 27}}, unit)
        box = by.get('Box')
        check('symbols: 块形声明的范围到匹配的 }', box and box['range']['end'] == {'line': 6, 'character': 1}, box)
        t = by.get('加法："一"')
        ln = SYMS.split('\n').index('test "加法：\\"一\\"" {')
        check('symbols: test 的名字位置为串字面量（utf-16 列）', t and t['selectionRange'] == {'start': {'line': ln, 'character': 5}, 'end': {'line': ln, 'character': 5 + units('"加法：\\"一\\""', 'utf-16')}}, t)
        check('symbols: 写到一半的声明不吞掉后面的声明', 'after' in by, got)
        r = s.request('textDocument/documentSymbol', {'textDocument': {'uri': uri_of(work / 'sym' / 'none.tx')}})
        check('symbols: 未打开的文档回 []', r and r.get('result') == [], r)
        s.finish()
    finally:
        s.kill()


REFS_A = '''import { Point, Pair, LIMIT, makePoint, pick } from "./b";
import { mapNew } from "std/map_set";

function pointSum(p: Point): int {
    return p.x + p.y;
}

function sumPair(q: Pair): int {
    return q.a + q.b;
}

function apply(f: (int) => int, v: int): int {
    return f(v);
}

function main(args: []string): int {
    const base = 10;
    const outer = (a: int): int => {
        const inner = (b: int): int => a + b + base;
        return inner(a);
    };
    const p: Point = makePoint(1, 2);
    let total = p.sum() + apply(outer, base) + LIMIT + sumPair(Pair { a: 1, b: 2 });
    const count = pick(1, 2) + pick(3, 4);
    print(`n=${count}`);
    const m = mapNew();
    for (k in args) {
        total = total + len(k);
    }
    print("中文" + toStr(base));
    return total;
}
'''

REFS_B = '''export struct Point {
    x: int,
    y: int,
}

export struct Pair {
    a: int,
    b: int,
}

export const LIMIT: int = 3;

export function makePoint(x: int, y: int): Point {
    return Point { x: x, y: y };
}

export function pick<T>(a: T, b: T): T {
    const c = a;
    return c;
}
'''


def rng(text, needle, n, enc='utf-16', delta=0):
    """Range of `n` characters starting at needle+delta (first occurrence)."""
    p = pos_of(text, needle, enc, delta)
    line = text.split('\n')[p['line']]
    i = line.find(needle) + delta
    return {'start': p, 'end': {'line': p['line'], 'character': units(line[:i + n], enc)}}


def rng_line(text, line_no, needle, n, enc='utf-16'):
    line = text.split('\n')[line_no]
    i = line.find(needle)
    return {'start': {'line': line_no, 'character': units(line[:i], enc)}, 'end': {'line': line_no, 'character': units(line[:i + n], enc)}}


def g_refs(compiler, work):
    d = work / 'refs'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    a, b = d / 'a.tx', d / 'b.tx'
    a.write_text(REFS_A, encoding='utf-8', newline='\n')
    b.write_text(REFS_B, encoding='utf-8', newline='\n')
    ua, ub = uri_of(a), uri_of(b)
    s = Session(compiler)
    try:
        r = s.init()
        caps = (r or {}).get('result', {}).get('capabilities', {})
        check('refs: 宣告 definition、references、rename', caps.get('definitionProvider') is True and caps.get('referencesProvider') is True and caps.get('renameProvider') is True, caps)
        s.open(ua, REFS_A)

        def req(method, uri, pos, **extra):
            params = {'textDocument': {'uri': uri}, 'position': pos}
            params.update(extra)
            return s.request(method, params)

        # 未经防抖就请求：入口先同步分析。
        r = req('textDocument/definition', ua, pos_of(REFS_A, '+ base;', 'utf-16', 2))
        want = {'uri': ua, 'range': rng(REFS_A, 'const base', 4, delta=6)}
        check('refs: definition 嵌套闭包里的捕获解到源（打开后立即请求）', r and r.get('result') == want, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, '+ base;', 'utf-16', 6))
        check('refs: definition 光标在名字末尾', r and r.get('result') == want, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, 'makePoint(1', 'utf-16', 2))
        check('refs: definition 跨文件到未打开的模块', r and r.get('result') == {'uri': ub, 'range': rng(REFS_B, 'makePoint', 9)}, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, 'p.x', 'utf-16', 2))
        check('refs: definition 字段到被导入模块的字段声明', r and r.get('result') == {'uri': ub, 'range': rng_line(REFS_B, 1, 'x', 1)}, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, 'p.sum()', 'utf-16', 2))
        check('refs: definition 方法糖到函数', r and r.get('result') == {'uri': ua, 'range': rng(REFS_A, 'pointSum', 8)}, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, 'len(k)', 'utf-16'))
        check('refs: definition 内建回 null', r and r.get('result') is None and 'error' not in r, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, 'toStr(base)', 'utf-16', 6))
        check('refs: definition utf-16 下中文串之后的列', r and r.get('result') == want, r)
        r = req('textDocument/definition', ua, pos_of(REFS_A, 'len(k)', 'utf-16', 4))
        check('refs: definition for-in 变量', r and r.get('result') == {'uri': ua, 'range': rng(REFS_A, 'for (k', 1, delta=5)}, r)

        r = req('textDocument/references', ua, pos_of(REFS_A, 'Pair {', 'utf-16'), context={'includeDeclaration': True})
        got = (r or {}).get('result') or []
        want_pair = [{'uri': ua, 'range': rng_line(REFS_A, 0, 'Pair', 4)}, {'uri': ua, 'range': rng(REFS_A, 'q: Pair', 4, delta=3)},
                     {'uri': ua, 'range': rng(REFS_A, 'Pair {', 4)}, {'uri': ub, 'range': rng(REFS_B, 'Pair', 4)}]
        key = lambda x: (x['uri'], x['range']['start']['line'], x['range']['start']['character'])
        check('refs: references struct（导入名、类型标注、字面量、声明）', sorted(got, key=key) == sorted(want_pair, key=key), got)
        r = req('textDocument/references', ua, pos_of(REFS_A, 'Pair {', 'utf-16'), context={'includeDeclaration': False})
        got2 = (r or {}).get('result') or []
        check('refs: references 不含声明', sorted(got2, key=key) == sorted(want_pair[:3], key=key), got2)

        s.open(ub, REFS_B)
        r = req('textDocument/definition', ub, pos_of(REFS_B, 'return c', 'utf-16', 7))
        check('refs: definition 泛型函数体里的局部（经实例）', r and r.get('result') == {'uri': ub, 'range': rng(REFS_B, 'const c', 1, delta=6)}, r)

        # —— rename ——
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'const base', 'utf-16', 6), newName='start')
        res = (r or {}).get('result') or {}
        edits = res.get('changes', {}).get(ua, [])
        check('refs: rename 局部（含捕获处）', len(edits) == 4 and apply_edits(REFS_A, edits, 'utf-16') == REFS_A.replace('base', 'start'), r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'makePoint(1', 'utf-16'), newName='newPoint')
        ch = (r or {}).get('result', {}).get('changes', {})
        check('refs: rename 跨文件（打开的与未打开的都改）',
              set(ch) == {ua, ub} and apply_edits(REFS_A, ch[ua], 'utf-16') == REFS_A.replace('makePoint', 'newPoint')
              and apply_edits(REFS_B, ch[ub], 'utf-16') == REFS_B.replace('makePoint', 'newPoint'), r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'function pointSum', 'utf-16', 9), newName='pointTotal')
        ch = (r or {}).get('result', {}).get('changes', {})
        check('refs: rename 方法糖调用点按前缀改写', apply_edits(REFS_A, ch.get(ua, []), 'utf-16') == REFS_A.replace('pointSum', 'pointTotal').replace('p.sum()', 'p.total()'), r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'function pointSum', 'utf-16', 9), newName='totalOf')
        check('refs: rename 方法糖新名不带前缀即拒绝（-32803）', r and r.get('error', {}).get('code') == -32803, r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'Pair {', 'utf-16'), newName='Couple')
        ch = (r or {}).get('result', {}).get('changes', {})
        check('refs: rename struct 连类型标注一起改（补面）',
              apply_edits(REFS_A, ch.get(ua, []), 'utf-16') == re.sub(r'\bPair\b', 'Couple', REFS_A)
              and apply_edits(REFS_B, ch.get(ub, []), 'utf-16') == REFS_B.replace('Pair', 'Couple'), r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'const count', 'utf-16', 6), newName='amount')
        err = (r or {}).get('error', {})
        check('refs: rename 遇同名模板插值即拒绝', err.get('code') == -32803 and '模板' in err.get('message', ''), r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, 'mapNew()', 'utf-16'), newName='makeMap')
        err = (r or {}).get('error', {})
        check('refs: rename 标准库里的声明即拒绝', err.get('code') == -32803 and '标准库' in err.get('message', ''), r)
        r = req('textDocument/rename', ua, pos_of(REFS_A, '(a: int)', 'utf-16', 1), newName='b')
        err = (r or {}).get('error', {})
        check('refs: rename 造成遮蔽即拒绝（重查）', err.get('code') == -32803 and '遮蔽' in err.get('message', ''), r)
        for bad in ('let', '1x', 'a b', ''):
            r = req('textDocument/rename', ua, pos_of(REFS_A, 'const base', 'utf-16', 6), newName=bad)
            check('refs: rename 新名 %r 拒绝（-32803）' % bad, r and r.get('error', {}).get('code') == -32803, r)
        r = s.request('textDocument/rename', {'textDocument': {'uri': ua}, 'position': pos_of(REFS_A, 'const base', 'utf-16', 6)})
        check('refs: rename 缺 newName 回 -32602', r and r.get('error', {}).get('code') == -32602, r)

        # 改动后立即请求：位置随新文本。
        new_a = '// 新加一行\n' + REFS_A
        s.change(ua, 2, [{'text': new_a}])
        r = req('textDocument/definition', ua, pos_of(new_a, '+ base;', 'utf-16', 2))
        check('refs: 变更后立即请求按新文本作答', r and r.get('result') == {'uri': ua, 'range': rng(new_a, 'const base', 4, delta=6)}, r)
        r = req('textDocument/definition', uri_of(d / 'none.tx'), {'line': 0, 'character': 0})
        check('refs: 未打开的文档回 null', r and r.get('result') is None, r)
        s.finish()
    finally:
        s.kill()


TOKS = '''struct Point {
    x: int,
}

enum Color {
    Red,
}

function area(p: Point, c: Color): int {
    const s = "中文" + toStr(p.x);
    return switch (c) {
        case Red => p.x,
    };
}
'''

# (行, utf-16 列, 长, 类)：类为 legend 下标（function struct enum enumMember variable parameter property）。
TOKS_WANT = [(0, 7, 5, 1), (1, 4, 1, 6), (4, 5, 5, 2), (5, 4, 3, 3), (8, 9, 4, 0), (8, 14, 1, 5), (8, 17, 5, 1), (8, 24, 1, 5),
             (8, 27, 5, 2), (9, 10, 1, 4), (9, 27, 1, 5), (9, 29, 1, 6), (10, 19, 1, 5), (11, 13, 3, 3), (11, 20, 1, 5), (11, 22, 1, 6)]

LEGEND = {'tokenTypes': ['function', 'struct', 'enum', 'enumMember', 'variable', 'parameter', 'property'], 'tokenModifiers': []}


def decode(data):
    out = []
    line = col = 0
    for i in range(0, len(data), 5):
        dl, dc, n, tt, mod = data[i:i + 5]
        if dl:
            line += dl
            col = dc
        else:
            col += dc
        out.append((line, col, n, tt) if mod == 0 else (line, col, n, tt, mod))
    return out


def g_tokens(compiler, work):
    d = work / 'toks'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    f = d / 't.tx'
    f.write_text(TOKS, encoding='utf-8', newline='\n')
    u = uri_of(f)
    s = Session(compiler)
    try:
        r = s.init()
        caps = (r or {}).get('result', {}).get('capabilities', {})
        check('tokens: legend 逐字同 Zig 版、只有 full', caps.get('semanticTokensProvider') == {'legend': LEGEND, 'full': True}, caps.get('semanticTokensProvider'))
        s.open(u, TOKS)
        r = s.request('textDocument/semanticTokens/full', {'textDocument': {'uri': u}})
        got = decode(((r or {}).get('result') or {}).get('data', []))
        check('tokens: 打开后立即请求，记号逐个相同（补面、中文串之后的 utf-16 列、模式、字段）', got == TOKS_WANT, got)
        new = '// 加一行\n' + TOKS
        s.change(u, 2, [{'text': new}])
        r = s.request('textDocument/semanticTokens/full', {'textDocument': {'uri': u}})
        got = decode(((r or {}).get('result') or {}).get('data', []))
        check('tokens: 变更后立即请求按新文本', got == [(l + 1, c, n, t) for l, c, n, t in TOKS_WANT], got)
        r = s.request('textDocument/semanticTokens/full', {'textDocument': {'uri': uri_of(d / 'none.tx')}})
        check('tokens: 未打开的文档回空 data', r and r.get('result') == {'data': []}, r)
        s.quiet(1.0)
        refreshes = [m for m in s.notes if m.get('method') == 'workspace/semanticTokens/refresh']
        check('tokens: 客户端未宣告 refreshSupport 时不发 refresh', not refreshes, refreshes)
        s.finish()
    finally:
        s.kill()
    s = Session(compiler)
    try:
        s.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {'workspace': {'semanticTokens': {'refreshSupport': True}}}})
        s.notify('initialized', {})
        s.open(u, TOKS)
        deadline = time.time() + WAIT
        ref = None
        while time.time() < deadline and ref is None:
            m = s.get(deadline - time.time())
            if m is None or m == '__timeout__':
                break
            if isinstance(m, dict) and m.get('method') == 'workspace/semanticTokens/refresh':
                ref = m
            else:
                s.notes.append(m)
        check('tokens: 宣告 refreshSupport 时防抖分析后发 refresh（带 id 的请求）', ref is not None and 'id' in ref, ref)
        if ref is not None:
            s.send({'jsonrpc': '2.0', 'id': ref['id'], 'result': None})
        s.quiet(1.0)
        errs = [m for m in s.notes if isinstance(m, dict) and 'error' in m]
        check('tokens: 对 refresh 的响应静默收下（不回错误）', not errs, errs)
        r = s.request('textDocument/semanticTokens/full', {'textDocument': {'uri': u}})
        check('tokens: 之后的请求照常', decode(((r or {}).get('result') or {}).get('data', [])) == TOKS_WANT, r)
        s.finish()
    finally:
        s.kill()


HOV_LIB = '''export function scale(v: int, k: int): int {
    return v * k;
}
'''

HOV = '''import { scale } from "./hlib";

struct Point {
    x: int,
}

enum Shape {
    Dot,
    Circle(r: int),
}

function wrap<T>(v: T): List< T> {
    const out: List< T> = listNew();
    listPush(out, v);
    return out;
}

function risky(n: int): int | Error {
    if (n < 0) { return err("neg"); }
    return n;
}

function assert(ok: bool): void {
    if (!ok) { print("bad"); }
}

function main(args: []string): int {
    const p = Point { x: scale(2, 3) };
    const m = mapNew< string, int>();
    let s = "中文" + toStr(p.x);
    print(s);
    assert(true);
    const a = wrap(1);
    const b = wrap("x");
    const v = risky(p.x) catch (e) {
        return 1;
    };
    return switch (Shape.Circle(v)) {
        case Circle(r) => r + listLen(a) + listLen(b),
        case Dot => 0,
    };
}
// print(s)
'''


def md(code, note=''):
    v = '```taixu\n' + code + '\n```'
    if note:
        v += '\n\n' + note
    return {'kind': 'markdown', 'value': v}


def g_hover(compiler, work):
    d = work / 'hover'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    f = d / 'h.tx'
    f.write_text(HOV, encoding='utf-8', newline='\n')
    (d / 'hlib.tx').write_text(HOV_LIB, encoding='utf-8', newline='\n')
    u = uri_of(f)
    s = Session(compiler)
    try:
        r = s.init()
        caps = (r or {}).get('result', {}).get('capabilities', {})
        check('hover: 宣告 hoverProvider', caps.get('hoverProvider') is True, caps)
        s.open(u, HOV)

        def hov(needle, delta=0, text=HOV):
            return s.request('textDocument/hover', {'textDocument': {'uri': u}, 'position': pos_of(text, needle, 'utf-16', delta)})

        def res(r):
            return (r or {}).get('result')

        # 未经防抖就请求：入口先同步分析。
        r = hov('struct Point', 7)
        check('hover: struct 声明（打开后立即请求），range 为名字', res(r) == {'contents': md('struct Point { x: int }'), 'range': rng(HOV, 'struct Point', 5, delta=7)}, r)
        r = hov('x: int,')
        check('hover: 字段声明', (res(r) or {}).get('contents') == md('Point.x: int'), r)
        r = hov('p.x)', 2)
        check('hover: 字段访问', (res(r) or {}).get('contents') == md('Point.x: int'), r)
        r = hov('Circle(r: int)')
        check('hover: 变体声明', (res(r) or {}).get('contents') == md('Shape.Circle(r: int)'), r)
        r = hov('function wrap', 9)
        check('hover: 泛型函数照写型参', (res(r) or {}).get('contents') == md('function wrap<T>(v: T): List<T>'), r)
        r = hov('const out', 6)
        check('hover: 泛型体里随实例不同的绑定只写名字并注明', (res(r) or {}).get('contents') == md('const out', '类型随泛型实例而定'), r)
        r = hov('scale(2', 1)
        check('hover: 导入名指到别的文件的声明', (res(r) or {}).get('contents') == md('function scale(v: int, k: int): int'), r)
        r = hov('const m', 6)
        check('hover: 推断的泛型实例类型', (res(r) or {}).get('contents') == md('const m: Map<string, int>'), r)
        r = hov('mapNew', 2)
        check('hover: prelude 的声明', (res(r) or {}).get('contents') == md('function mapNew<K extends Hashable, V>(): Map<K, V>'), r)
        r = hov('let s', 4)
        check('hover: let 绑定附推断的类型', (res(r) or {}).get('contents') == md('let s: string'), r)
        r = hov('toStr(p.x)', 2)
        check('hover: 中文串之后的内建（utf-16 的 range）', res(r) == {'contents': md('function toStr(int | float | bool | string | Error): string', '转字符串（Error 得 "Error: " 加消息）'), 'range': rng(HOV, 'toStr(p.x)', 5)}, r)
        r = hov('print(s)', 1)
        check('hover: 内建 print', (res(r) or {}).get('contents') == md('function print(string): void', '标准输出一行'), r)
        r = hov('assert(true)', 1)
        check('hover: 遮蔽内建的用户函数取用户的声明', (res(r) or {}).get('contents') == md('function assert(ok: bool): void'), r)
        r = hov('catch (e)', 7)
        check('hover: catch 绑定', (res(r) or {}).get('contents') == md('e: Error'), r)
        r = hov('Circle(r) =>', 7)
        check('hover: 模式绑定', (res(r) or {}).get('contents') == md('r: int'), r)
        r = hov('r + listLen', 1)
        check('hover: 光标在名字末尾', (res(r) or {}).get('contents') == md('r: int'), r)
        r = hov('return switch')
        check('hover: 关键字为 null', r is not None and 'result' in r and res(r) is None, r)
        r = hov('// print(s)', 4)
        check('hover: 注释里为 null', r is not None and 'result' in r and res(r) is None, r)
        r = s.request('textDocument/hover', {'textDocument': {'uri': uri_of(d / 'none.tx')}, 'position': {'line': 0, 'character': 0}})
        check('hover: 未打开的文档为 null', r is not None and 'result' in r and res(r) is None, r)
        new = HOV.replace('mapNew< string, int>()', 'mapNew< int, bool>()')
        s.change(u, 2, [{'text': new}])
        r = hov('const m', 6, new)
        check('hover: 变更后立即请求按新文本', (res(r) or {}).get('contents') == md('const m: Map<int, bool>'), r)
        s.finish()
    finally:
        s.kill()
    s = Session(compiler)
    try:
        s.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {'textDocument': {'hover': {'contentFormat': ['plaintext', 'markdown']}}}})
        s.notify('initialized', {})
        s.open(u, HOV)
        r = s.request('textDocument/hover', {'textDocument': {'uri': u}, 'position': pos_of(HOV, 'print(s)', 'utf-16', 1)})
        check('hover: 客户端首选 plaintext 时给纯文本', (r or {}).get('result', {}).get('contents') == {'kind': 'plaintext', 'value': 'function print(string): void\n\n标准输出一行'}, r)
        s.finish()
    finally:
        s.kill()


SIG_LIB = '''export function scale(v: int, k: int): int {
    return v * k;
}
'''

SIG = '''import { scale } from "./slib";

struct P {
    x: int,
}

enum Shape {
    Dot,
    Circle(r: int),
}

function pArea(p: P, k: int): int {
    return p.x * k;
}

function add(a: int, b: int): int {
    return a + b;
}

function wrap<T>(v: T, n: int): List< T> {
    const out: List< T> = listNew();
    listPush(out, v);
    return out;
}

function main(args: []string): int {
    const p = P { x: 1 };
    const g = (q: int): int => q * 2;
    const s = add(1, add(2, 3));
    const t = p.area(4);
    const u = Shape.Circle(5);
    const w = g(6);
    const z = wrap("a,b", 7);
    const c = wrapTo< u8>(300);
    print(toStr(scale(s, t)));
    return 0;
}
'''

SIG_CAPS = {'triggerCharacters': ['(', ','], 'retriggerCharacters': [')']}
L_ADD = 'function add(a: int, b: int): int'
L_SCALE = 'function scale(v: int, k: int): int'


def sig(label, spans, active):
    return {'signatures': [{'label': label, 'parameters': [{'label': list(x)} for x in spans], 'activeParameter': active}], 'activeSignature': 0, 'activeParameter': active}


def g_sig(compiler, work):
    d = work / 'sig'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    f = d / 's.tx'
    f.write_text(SIG, encoding='utf-8', newline='\n')
    (d / 'slib.tx').write_text(SIG_LIB, encoding='utf-8', newline='\n')
    u = uri_of(f)
    s = Session(compiler)
    try:
        r = s.init()
        caps = (r or {}).get('result', {}).get('capabilities', {})
        check('sig: 宣告 signatureHelpProvider（触发 ( 与 ,，重触发 )）', caps.get('signatureHelpProvider') == SIG_CAPS, caps.get('signatureHelpProvider'))
        s.open(u, SIG)

        def at(needle, delta, text=SIG):
            return (s.request('textDocument/signatureHelp', {'textDocument': {'uri': u}, 'position': pos_of(text, needle, 'utf-16', delta)}) or {})

        r = at('add(1', 4)
        check('sig: 第一个实参（打开后立即请求）', r.get('result') == sig(L_ADD, [(13, 19), (21, 27)], 0), r)
        r = at('add(1, add', 7)
        check('sig: 第二个实参', r.get('result') == sig(L_ADD, [(13, 19), (21, 27)], 1), r)
        r = at('add(2, 3)', 7)
        check('sig: 嵌套调用取最内层', r.get('result') == sig(L_ADD, [(13, 19), (21, 27)], 1), r)
        r = at('add(2, 3))', 9)
        check('sig: 内层 ) 之后回到外层', r.get('result') == sig(L_ADD, [(13, 19), (21, 27)], 1), r)
        r = at('add(2, 3));', 10)
        check('sig: 外层 ) 之后为 null', 'result' in r and r.get('result') is None, r)
        r = at('add(1', 1)
        check('sig: 光标在函数名上为 null', 'result' in r and r.get('result') is None, r)
        r = at('area(4', 5)
        check('sig: 方法糖不含接收者', r.get('result') == sig('function pArea(p: P, k: int): int', [(21, 27)], 0), r)
        r = at('Circle(5', 7)
        check('sig: 变体构造', r.get('result') == sig('Shape.Circle(r: int)', [(13, 19)], 0), r)
        r = at('g(6', 2)
        check('sig: 函数类型的绑定', r.get('result') == sig('const g: (int) => int', [(10, 13)], 0), r)
        r = at('wrap("a,b", 7', 11)
        check('sig: 串里的逗号不计，泛型照写', r.get('result') == sig('function wrap<T>(v: T, n: int): List<T>', [(17, 21), (23, 29)], 1), r)
        r = at('wrapTo< u8>(', 12)
        check('sig: 隔着型参的内建，偏移按 utf-16（D3 的中文占位）', r.get('result') == sig('function wrapTo<T>(整数): T', [(19, 21)], 0), r)
        r = at('toStr(scale', 6)
        check('sig: 内建', r.get('result') == sig('function toStr(int | float | bool | string | Error): string', [(15, 50)], 0), r)
        r = at('scale(s, t', 9)
        check('sig: 导入的函数', r.get('result') == sig(L_SCALE, [(15, 21), (23, 29)], 1), r)
        new = SIG.replace('    return 0;\n}', '    const y = add(9, 8, 7\n    return 0;\n}')
        s.change(u, 2, [{'text': new}])
        r = at('add(9, 8, 7', 11, new)
        check('sig: 未闭合的 ( 按名字找到被调者，越界不高亮', r.get('result') == sig(L_ADD, [(13, 19), (21, 27)], 2), r)
        new2 = new.replace('    const y = add(9, 8, 7\n', '    const y = (1 + \n')
        s.change(u, 3, [{'text': new2}])
        r = at('(1 + ', 5, new2)
        check('sig: 分组括号不是调用', 'result' in r and r.get('result') is None, r)
        s.finish()
    finally:
        s.kill()


COMP = '''import { scale } from "./clib";

struct Box {
    v: int,
}

function helper(n: int): int {
    return n;
}

function assert(ok: bool): void {
    if (!ok) { print("bad"); }
}

let counter: int = 0;

function main(args: []string): int {
    const outer = 1;
    if (outer > 0) {
        const inner = 2;
        print(toStr(inner + scale(1, 2)));
    }
    // here comment
    const msg = "text here";
    const b = Box { v: 1 };
    const later = b.v;
    return outer;
}
'''


COMP2 = '''enum Kind {
    A,
    B(n: int),
}

function pick(k: Kind): int {
    return switch (k) {
        case B(n) => n + 0,
        else => 0,
    };
}

export function main(args: []string): int {
    const outer = 1;
    if (outer > 0) {
        const outer = "s";
        print(outer);
    }
    const data = readFile("nope.txt") catch (e) {
        print(e.message);
        return pick(Kind.A);
    };
    const tpl = `v=${outer} d=${data}`;
    print(tpl);
    return 0;
}
'''


def g_complete(compiler, work):
    d = work / 'comp'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    f = d / 'c.tx'
    f.write_text(COMP, encoding='utf-8', newline='\n')
    (d / 'clib.tx').write_text(SIG_LIB, encoding='utf-8', newline='\n')
    u = uri_of(f)
    for tool in (False, True):
        args = ('lsp', '--stdio', '--toolchain') if tool else ('lsp', '--stdio')
        s = Session(compiler, args)
        try:
            r = s.init()
            caps = (r or {}).get('result', {}).get('capabilities', {})
            if not tool:
                check('completion: 宣告 completionProvider（不宣告触发字符）', caps.get('completionProvider') == {}, caps.get('completionProvider'))
            s.open(u, COMP)

            def at(needle, delta=0):
                r = s.request('textDocument/completion', {'textDocument': {'uri': u}, 'position': pos_of(COMP, needle, 'utf-16', delta)}) or {}
                res = r.get('result')
                return r, ({x['label']: x for x in res} if isinstance(res, list) else None)

            if tool:
                r, items = at('if (outer')
                check('completion: 带 --toolchain 时给工具链内建', items is not None and items.get('fileSize', {}).get('kind') == 3, r)
                s.finish()
                continue
            r, items = at('if (outer')
            ok = items is not None
            check('completion: 局部按位置（之前声明的有、之后的与块里的没有；打开后立即请求）',
                  ok and items.get('outer') == {'label': 'outer', 'kind': 21, 'detail': 'const outer: int', 'sortText': '0outer'}
                  and 'inner' not in items and 'later' not in items and 'msg' not in items, r)
            check('completion: 形参', ok and items.get('args') == {'label': 'args', 'kind': 6, 'detail': 'args: []string', 'sortText': '0args'}, r)
            check('completion: 顶层声明与种类', ok and items.get('helper', {}).get('kind') == 3 and items.get('helper', {}).get('detail') == 'function helper(n: int): int'
                  and items.get('Box', {}).get('kind') == 22 and items.get('counter') == {'label': 'counter', 'kind': 6, 'detail': 'let counter: int', 'sortText': '1counter'}, r)
            check('completion: 导入名', ok and items.get('scale') == {'label': 'scale', 'kind': 3, 'detail': 'function scale(v: int, k: int): int', 'sortText': '1scale'}, r)
            check('completion: 遮蔽内建的用户函数取用户的声明', ok and items.get('assert') == {'label': 'assert', 'kind': 3, 'detail': 'function assert(ok: bool): void', 'sortText': '1assert'}, r)
            check('completion: 内建', ok and items.get('print') == {'label': 'print', 'kind': 3, 'detail': 'function print(string): void', 'sortText': '2print'}, r)
            check('completion: 未带 --toolchain 时不给工具链内建', ok and 'fileSize' not in items and 'procSpawn' not in items, r)
            check('completion: prelude 的导出（程序未用到 prelude 时也给）', ok and items.get('mapNew', {}).get('detail') == 'function mapNew<K extends Hashable, V>(): Map<K, V>'
                  and items.get('Map', {}).get('kind') == 22 and 'mixHash' not in items, r)
            check('completion: 预声明类型名、None 与关键字', ok and items.get('List', {}).get('kind') == 7 and items.get('int', {}).get('kind') == 7
                  and items.get('None', {}).get('kind') == 21 and items.get('return') == {'label': 'return', 'kind': 14, 'sortText': '3return'}, r)
            r, items = at('print(toStr(inner')
            check('completion: 块里的局部', items is not None and 'inner' in items and 'outer' in items, r)
            r, items = at('// here comment')
            check('completion: 块结束后块里的局部不在', items is not None and 'inner' not in items and 'outer' in items, r)
            r, items = at('here comment')
            check('completion: 注释里为空表', r.get('result') == [], r)
            r, items = at('text here')
            check('completion: 串里为空表', r.get('result') == [], r)
            r, items = at('b.v', 2)
            check('completion: . 之后为空表', r.get('result') == [], r)
            r, items = at('b.v', 3)
            check('completion: . 之后正在输入的名字为空表', r.get('result') == [], r)
            r, items = at('return outer', 7)
            check('completion: 其后才声明的不在、之前的在', items is not None and 'later' in items and 'b' in items and 'msg' in items, r)
            f2 = d / 'c2.tx'
            f2.write_text(COMP2, encoding='utf-8', newline='\n')
            u2 = uri_of(f2)
            s.open(u2, COMP2)

            def at2(needle, delta=0):
                r = s.request('textDocument/completion', {'textDocument': {'uri': u2}, 'position': pos_of(COMP2, needle, 'utf-16', delta)}) or {}
                res = r.get('result')
                return r, ({x['label']: x for x in res} if isinstance(res, list) else None)

            r, items = at2('print(outer')
            check('completion: 遮蔽取内层', items is not None and items.get('outer', {}).get('detail') == 'const outer: string', r)
            r, items = at2('const data')
            check('completion: 块结束后回到外层的声明', items is not None and items.get('outer', {}).get('detail') == 'const outer: int', r)
            r, items = at2('print(e.message')
            check('completion: catch 绑定在其块里', items is not None and items.get('e') == {'label': 'e', 'kind': 6, 'detail': 'e: Error', 'sortText': '0e'}, r)
            r, items = at2('const tpl')
            check('completion: catch 绑定在块外不在', items is not None and 'e' not in items and 'data' in items and 'tpl' not in items, r)
            r, items = at2('n + 0')
            check('completion: 模式绑定在其臂里，enum 的种类', items is not None and items.get('n') == {'label': 'n', 'kind': 6, 'detail': 'n: int', 'sortText': '0n'}
                  and items.get('Kind', {}).get('kind') == 13, r)
            r, items = at2('else => 0', 8)
            check('completion: 模式绑定在别的臂里不在', items is not None and 'n' not in items and 'k' in items, r)
            r, items = at2('${outer', 2)
            check('completion: 模板串的插值段里按代码补全', items is not None and 'outer' in items and 'data' in items, r)
            r, items = at2('v=$', 1)
            check('completion: 模板串的字面部分为空表', r.get('result') == [], r)
            s.finish()
        finally:
            s.kill()


def g_framing(compiler, work):
    framing_cases(compiler, 'lsp', ROOT, check)


GROUPS = [('lifecycle', g_lifecycle), ('framing', g_framing), ('encoding', g_encoding), ('uri', g_uri), ('diag', g_diag), ('format', g_format), ('symbols', g_symbols),
          ('refs', g_refs), ('tokens', g_tokens), ('hover', g_hover), ('sig', g_sig),
          ('complete', g_complete)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True)
    ap.add_argument('--only', default='')
    ap.add_argument('--work', default='')
    a = ap.parse_args()
    compiler = str(Path(a.compiler).resolve())
    # 工作目录也作授权根：信任根的路径里不许有符号链接（macOS 的临时目录在 /var 下，/var 是链接），取真实路径。
    work = Path(a.work).resolve() if a.work else Path(tempfile.mkdtemp(prefix='taixu-lsp-')).resolve()
    work.mkdir(parents=True, exist_ok=True)
    global AUTHORIZED_TEST_ROOTS
    AUTHORIZED_TEST_ROOTS = (ROOT, work)
    only = {x for x in a.only.split(',') if x}
    for name, fn in GROUPS:
        if only and name not in only:
            continue
        fn(compiler, work)
    print('SUMMARY %s (%d checks)' % ('PASS' if not failures else 'FAIL=%d' % len(failures), passes + len(failures)))
    # 自建的临时根：通过即删，失败留给排查（同 pytest 的 failed 保留策略）。
    if not a.work:
        if failures:
            print('NOTE 工作目录保留以便排查：', work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
