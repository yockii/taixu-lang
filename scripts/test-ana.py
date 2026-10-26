#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析节（SEB1 ANA1）的门（第 6 轮 6d-3a1；设计 docs/design/v052-round6-lsp-mcp-v0.1-20261019.md §四-2）。

经隐藏开发命令 `taixu dev-check --analysis`、`--analysis-check` 调用原生编译器，三组检查：

1. golden：tests/ana/*.tx 各入口自检通过（见 2），输出的出现逐行等于同名 .ana（期望结果逐行人工核对过）。
   位于标准库、prelude 的出现不比；键指向它们时只比模块与锚类（其行列随标准库变动）。
2. corpus：tests/cases、tests/nrt-cases、examples、tests/lsp、tests/ana 的全部 .tx 与编译器自身（bootstrap/taixu.tx，
   工具链），按目标与构建参数分组放进一个进程连查（--analysis-check）：每个入口先不带、再带分析节各查一次，诊断须
   相同、带分析节的 SEB1 须是不带时的 SEB1 后接 ANA1（分析节不改其余任何字节）。
3. keys：同样的入口按 --analysis 连查，每个有身份的出现，其键须在同一程序里有解析得到的声明（悬空即锚的扫法与
   源码对不上）。

用法（仓根执行）：
  python scripts/test-ana.py --compiler <原生 taixu> [--target windows|linux] [--only golden,corpus,keys]
任何 FAIL 时退出码 1。
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIRS = ('tests/cases', 'tests/nrt-cases', 'examples', 'tests/lsp', 'tests/ana')
SELF = 'bootstrap/taixu.tx'


def run(argv, timeout=3600):
    p = subprocess.run([str(x) for x in argv], cwd=ROOT, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')


def blocks_of(stdout):
    """dev-check output → list of {entry, diags, text, ana, anacheck}."""
    out = []
    for ln in stdout.replace('\r\n', '\n').split('\n'):
        if not ln:
            continue
        obj = json.loads(ln)
        if 'entry' in obj:
            out.append({'entry': obj['entry'], 'diags': [], 'text': None, 'ana': [], 'anacheck': None})
        elif 'text' in obj:
            out[-1]['text'] = obj['text']
        elif 'ana' in obj:
            out[-1]['ana'].append(obj)
        elif 'anacheck' in obj:
            out[-1]['anacheck'] = obj['anacheck']
        else:
            out[-1]['diags'].append(obj)
    return out


def external(module):
    return module.startswith('std/') or module.startswith('<prelude>')


def normalize(rows):
    """Golden form: drop rows located in std/prelude; keys into them keep only module and anchor kind."""
    out = []
    for r in rows:
        if external(r['ana'].rsplit(':', 2)[0]):
            continue
        r = dict(r)
        parts = r['key'].split(':')
        if r['key'] and external(parts[0]):
            r['key'] = ':'.join([parts[0], '*', '*'] + parts[3:])
        out.append(json.dumps(r, ensure_ascii=False, separators=(',', ':')))
    return out


def corpus_entries():
    """[(flags, name)] for the corpus and the compiler itself."""
    out = []
    for d in CORPUS_DIRS:
        for tx in sorted((ROOT / d).glob('*.tx')):
            flags = ('--toolchain',) if tx.with_suffix('.toolchain').exists() else ()
            out.append((flags, str(tx.relative_to(ROOT)).replace('\\', '/')))
    out.append((('--toolchain',), SELF))
    return out


def batches(entries):
    groups = {}
    for flags, name in entries:
        groups.setdefault(flags, []).append(name)
    return groups


def golden(compiler, target):
    bad = 0
    for tx in sorted((ROOT / 'tests/ana').glob('*.tx')):
        name = str(tx.relative_to(ROOT)).replace('\\', '/')
        want_file = tx.with_suffix('.ana')
        rc, out, err = run([compiler, 'dev-check', '--analysis-check', '--target=' + target, name])
        b = blocks_of(out) if rc in (0, 1, 3) else []
        if len(b) != 1 or b[0]['anacheck'] != 'ok':
            bad += 1
            print('FAIL golden %s %s: self-check %s (exit %d) %s' % (target, name, b[0]['anacheck'] if b else None, rc, err[-300:]), flush=True)
            continue
        rc, out, err = run([compiler, 'dev-check', '--analysis', '--target=' + target, name])
        b = blocks_of(out) if rc in (0, 1) else []
        got = normalize(b[0]['ana']) if len(b) == 1 else None
        want = want_file.read_text(encoding='utf-8').splitlines() if want_file.exists() else None
        if got != want:
            bad += 1
            print('FAIL golden %s %s differs from %s' % (target, name, want_file.name), flush=True)
            for i, (g, w) in enumerate(zip(got or [], want or [])):
                if g != w:
                    print('  line %d\n    got  %s\n    want %s' % (i + 1, g, w), flush=True)
                    break
            else:
                print('  got %d lines, want %d' % (len(got or []), len(want or [])), flush=True)
    print('golden %s: %s' % (target, 'PASS' if bad == 0 else 'FAIL=%d' % bad), flush=True)
    return bad


def corpus(compiler, target):
    bad = 0
    n = 0
    for flags, names in batches(corpus_entries()).items():
        t0 = time.time()
        rc, out, err = run([compiler, 'dev-check', '--analysis-check', *flags, '--target=' + target, *names])
        b = blocks_of(out) if rc in (0, 1, 3) else []
        if len(b) != len(names):
            bad += 1
            print('FAIL corpus %s %s: %d of %d entries (exit %d) %s' % (target, ' '.join(flags), len(b), len(names), rc, err[-300:]), flush=True)
            continue
        for name, blk in zip(names, b):
            n += 1
            if blk['anacheck'] != 'ok':
                bad += 1
                print('FAIL corpus %s %s: %s' % (target, name, blk['anacheck']), flush=True)
        print('corpus %s %s: %d entries in %.1f s' % (target, ' '.join(flags) or '-', len(names), time.time() - t0), flush=True)
    print('corpus %s: %d entries, %s' % (target, n, 'PASS' if bad == 0 else 'FAIL=%d' % bad), flush=True)
    return bad


def keys(compiler, target):
    bad = 0
    n = 0
    occ = 0
    for flags, names in batches(corpus_entries()).items():
        rc, out, err = run([compiler, 'dev-check', '--analysis', *flags, '--target=' + target, *names])
        b = blocks_of(out) if rc in (0, 1) else []
        if len(b) != len(names):
            bad += 1
            print('FAIL keys %s %s: %d of %d entries (exit %d) %s' % (target, ' '.join(flags), len(b), len(names), rc, err[-300:]), flush=True)
            continue
        for name, blk in zip(names, b):
            n += 1
            decls = set(r['key'] for r in blk['ana'] if r['decl'])
            dangling = sorted(set(r['key'] for r in blk['ana'] if r['key'] and r['key'] not in decls))
            occ += len(blk['ana'])
            if dangling:
                bad += 1
                print('FAIL keys %s %s: %d dangling, e.g. %s' % (target, name, len(dangling), dangling[:3]), flush=True)
    print('keys %s: %d entries, %d occurrences, %s' % (target, n, occ, 'PASS' if bad == 0 else 'FAIL=%d' % bad), flush=True)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, help='原生 taixu')
    ap.add_argument('--target', choices=('windows', 'linux'), action='append')
    ap.add_argument('--only', default='golden,corpus,keys')
    a = ap.parse_args()
    compiler = str(Path(a.compiler).resolve())
    groups = set(a.only.split(','))
    bad = 0
    for target in a.target or ['windows', 'linux']:
        if 'golden' in groups:
            bad += golden(compiler, target)
        if 'corpus' in groups:
            bad += corpus(compiler, target)
        if 'keys' in groups:
            bad += keys(compiler, target)
    print('SUMMARY %s' % ('PASS' if bad == 0 else 'FAIL=%d' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
