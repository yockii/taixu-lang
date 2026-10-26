#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原生 LSP 只读的源码层检查（第 6 轮 6d-2；设计 docs/design/v052-round6-lsp-mcp-v0.1-20261019.md §三-9、§四-1）。

LSP 的代码路径不运行程序、不起进程、不访问网络、不写文件（用户裁决 §十一-6）。从 bootstrap/selfhost/lsp.tx 的
lspServe 出发，沿 import 收集可达模块，在这些模块里按名字求闭包：函数体里出现的任何与某个函数同名的标识符都算
可达（不只是调用——函数可以当作值传递；同名即算，宁多勿漏），闭包里任何函数调用了下列内建即失败：procSpawn、
procWrite、procKill、runProcess、writeFile、writeFileBytes、writeExecutable、removeFile、makeDir。stdoutWrite
（协议输出本身）与 readFile、entryKind、fileSize（读）不在其列。按名字求闭包是完整的：S1 的内建不能当作值
（3001），import 没有改名的写法。
另检查：LSP 自己的模块（lsp*.tx、frame_io.tx、check_diag.tx、ana_index.tx）里一处也不出现这些名字；并列出可达模块里调用了
这些内建、但不在闭包里的函数（不在 drvCheck 的路径上，供核对）。

用法（仓根执行）：python scripts/test-lsp-readonly.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / 'bootstrap' / 'selfhost' / 'lsp.tx'
FORBIDDEN = ('procSpawnLimited', 'procSpawn', 'procWrite', 'procTryWrite', 'procKill', 'runProcess', 'writeFile', 'writeFileBytes',
             'writeExecutable', 'removeFile', 'makeDir', 'setEnv')
# LSP 自己的模块：lsp*.tx 按前缀全认（6d-3a3 起；此前的固定名单漏了后加的 lsp_refs.tx、lsp_tokens.tx），
# 另加分帧、检查诊断与分析索引。
OWN_EXTRA = ('frame_io.tx', 'check_diag.tx', 'check_model.tx', 'check_graph.tx', 'source_cache.tx', 'ana_index.tx')


def own(name):
    return name.startswith('lsp') or name in OWN_EXTRA

IMPORT = re.compile(r'^import\s*\{[^}]*\}\s*from\s*"([^"]+)"', re.M)
FUNC = re.compile(r'^(?:export\s+)?(?:async\s+)?function\*?\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)
IDENT = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\b')


def strip_comments(text):
    out = []
    for line in text.split('\n'):
        i = 0
        in_str = False
        cut = len(line)
        while i < len(line):
            c = line[i]
            if in_str:
                if c == '\\':
                    i += 2
                    continue
                if c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == '/' and line[i:i + 2] == '//':
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return '\n'.join(out)


def strip_strings(text):
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def modules():
    """Modules reachable from lsp.tx by import (with every target-file variant)."""
    seen = []
    todo = [ENTRY]
    while todo:
        p = todo.pop()
        if p in seen or not p.exists():
            continue
        seen.append(p)
        for spec in IMPORT.findall(p.read_text(encoding='utf-8')):
            base = (p.parent / spec)
            suffixes = ['windows', 'linux', 'macos', 'unix'] + [o + '_' + a for o in ['windows', 'linux', 'macos'] for a in ['x86_64', 'aarch64']]
            for cand in (base.with_suffix('.tx'), *(base.parent / (base.name + '_' + s + '.tx') for s in suffixes)):
                if cand.exists():
                    todo.append(cand.resolve())
    return seen


def functions(path):
    """name → body text (comments and string contents removed), for every function in the module."""
    text = strip_strings(strip_comments(path.read_text(encoding='utf-8')))
    out = {}
    marks = [(m.start(), m.group(1)) for m in FUNC.finditer(text)]
    for k, (at, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
        out.setdefault(name, []).append((path, text[at:end]))
    return out


def main():
    bad = 0
    mods = modules()
    table = {}
    for m in mods:
        for name, bodies in functions(m).items():
            table.setdefault(name, []).extend(bodies)
    reach = set()
    todo = ['lspServe']
    while todo:
        name = todo.pop()
        if name in reach or name not in table:
            continue
        reach.add(name)
        for _, body in table[name]:
            for callee in IDENT.findall(body):
                if callee in table and callee not in reach:
                    todo.append(callee)
    for name in sorted(reach):
        for path, body in table[name]:
            for f in FORBIDDEN:
                if re.search(r'\b' + f + r'\s*\(', body):
                    bad += 1
                    print('FAIL  %s 经 %s（%s）可达，调用了 %s' % (name, path.relative_to(ROOT), 'lspServe', f))
    owned = [m for m in mods if own(m.name)]
    for m in owned:
            text = strip_strings(strip_comments(m.read_text(encoding='utf-8')))
            for f in FORBIDDEN:
                if re.search(r'\b' + f + r'\b', text):
                    bad += 1
                    print('FAIL  %s 出现 %s' % (m.relative_to(ROOT), f))
    off = []
    for name in sorted(table):
        if name in reach:
            continue
        for path, body in table[name]:
            hits = [f for f in FORBIDDEN if re.search(r'\b' + f + r'\s*\(', body)]
            if hits:
                off.append('%s:%s（%s）' % (path.relative_to(ROOT).as_posix(), name, '、'.join(hits)))
    for x in off:
        print('OFFPATH  ' + x)
    print('modules=%d own=%d reachable-functions=%d offpath-writers=%d' % (len(mods), len(owned), len(reach), len(off)))
    print('SUMMARY %s' % ('PASS' if bad == 0 else 'FAIL=%d' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
