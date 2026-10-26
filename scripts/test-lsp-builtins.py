#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LSP 内建表的门（第 6 轮 6d-3b1；设计 docs/design/v052-round6-lsp-mcp-v0.1-20261019.md §四-5）。

bootstrap/selfhost/lsp_builtins.tx 是 hover、signatureHelp、completion 用的内建表。本门是纯源码检查：
1. 表里每一条的签名等于 D3（docs/design/ 下版本号最大的 D3-s1-language-spec-v*.md）§7 内建表同名行的签名，
   按固定规则转写成 `function 名<型参>(参数): 返回`（D3 的写法有 `(参数) 返回` 与 `名<型参>(参数) 返回` 两种）；
2. 表的名字集合等于 D3 §7 的名字集合，也等于 semamirror 的 builtinOf 认的、用户能写的名字（不含 `#` 开头与
   `__rt` 开头的内部内建）——三者任何一处增删都须同步；
3. 表里“仅 --toolchain”的标记与 builtinOf 的号段一致（35 writeExecutable、50..56、79 hostArch、86 setEnv、3584..3599，同 semamirror 的
   checkSysBuiltin、checkToolBuiltin 与 35 号的检查）。

用法（仓根执行）：python scripts/test-lsp-builtins.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / 'bootstrap' / 'selfhost' / 'lsp_builtins.tx'
SEMA = ROOT / 'bootstrap' / 'semamirror.tx'


def latest_d3():
    best = None
    for p in (ROOT / 'docs' / 'design').glob('D3-s1-language-spec-v*.md'):
        m = re.search(r'-v(\d+)\.(\d+)\.md$', p.name)
        if m:
            v = (int(m.group(1)), int(m.group(2)))
            if best is None or v > best[0]:
                best = (v, p)
    return best[1]


def d3_rows(text):
    """[(name, sig)] from the §7 builtin table (`\\|` unescaped)."""
    sec = text[text.index('## 7. 内建函数与程序入口'):]
    sec = sec[:sec.index('\n## ', 10)]
    out = []
    for line in sec.splitlines():
        m = re.match(r'^\| `([A-Za-z_][A-Za-z0-9_]*)` \| `((?:[^`\\]|\\.)*)` \|', line)
        if m:
            out.append((m.group(1), m.group(2).replace('\\|', '|')))
    return out


def convert(name, sig):
    """D3 的签名 → `function name<tps>(params): ret`."""
    s = sig.strip()
    if s.startswith(name):
        s = s[len(name):]
    tps = ''
    if s.startswith('<'):
        depth = 0
        for i, c in enumerate(s):
            depth += {'<': 1, '>': -1}.get(c, 0)
            if depth == 0:
                tps, s = s[:i + 1], s[i + 1:]
                break
    if not s.startswith('('):
        raise ValueError('signature of %s has no parameter list: %r' % (name, sig))
    depth = 0
    for i, c in enumerate(s):
        depth += {'(': 1, ')': -1}.get(c, 0)
        if depth == 0:
            params, rest = s[:i + 1], s[i + 1:]
            break
    ret = rest.strip()
    if ret.startswith(':'):
        ret = ret[1:].strip()
    return 'function %s%s%s: %s' % (name, tps, params, ret)


def builtin_ids(text):
    """{name: id} from semamirror's builtinOf."""
    body = text[text.index('function builtinOf(name: string): int {'):]
    body = body[:body.index('\n}\n')]
    out = {}
    for m in re.finditer(r'if \(([^{]*)\) \{ return (\d+); \}', body):
        for n in re.findall(r'name == "([^"]+)"', m.group(1)):
            out[n] = int(m.group(2))
    return out


def is_toolchain(bid):
    return bid == 35 or 50 <= bid <= 56 or bid == 79 or bid == 86 or 3584 <= bid <= 3615


def table_rows(text):
    """[(name, sig, doc, toolchain)] from lsp_builtins.tx."""
    out = []
    for m in re.finditer(r'^    ([bt])\("([^"]*)", "([^"]*)", "((?:[^"\\]|\\.)*)"\);$', text, re.M):
        out.append((m.group(2), m.group(3), m.group(4), m.group(1) == 't'))
    return out


def main():
    bad = 0
    d3_path = latest_d3()
    d3 = d3_rows(d3_path.read_text(encoding='utf-8'))
    ids = builtin_ids(SEMA.read_text(encoding='utf-8'))
    table = table_rows(TABLE.read_text(encoding='utf-8'))
    d3_sig = dict(d3)
    names = [r[0] for r in table]
    if len(names) != len(set(names)):
        bad += 1
        print('FAIL  表里有重名：%s' % sorted({n for n in names if names.count(n) > 1}))
    user = {n for n in ids if not n.startswith('#') and not n.startswith('__rt')}
    for n in sorted(set(d3_sig) - set(names)):
        bad += 1
        print('FAIL  D3 §7 有、表里没有：%s' % n)
    for n in sorted(set(names) - set(d3_sig)):
        bad += 1
        print('FAIL  表里有、D3 §7 没有：%s' % n)
    for n in sorted(user - set(names)):
        bad += 1
        print('FAIL  builtinOf 认、表里没有：%s' % n)
    for n in sorted(set(names) - user):
        bad += 1
        print('FAIL  表里有、builtinOf 不认：%s' % n)
    for name, sig, doc, tool in table:
        if name in d3_sig:
            want = convert(name, d3_sig[name])
            if sig != want:
                bad += 1
                print('FAIL  %s 的签名 %r 不等于 D3 §7 转写的 %r' % (name, sig, want))
        if name in ids and tool != is_toolchain(ids[name]):
            bad += 1
            print('FAIL  %s 的仅 --toolchain 标记与 builtinOf 的号段不符' % name)
        if not doc:
            bad += 1
            print('FAIL  %s 没有说明' % name)
    print('d3=%s rows=%d table=%d builtinOf-user=%d toolchain=%d' % (d3_path.name, len(d3), len(table), len(user), sum(1 for r in table if r[3])))
    print('SUMMARY %s' % ('PASS' if bad == 0 else 'FAIL=%d' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
