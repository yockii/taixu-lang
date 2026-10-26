#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LSP 检查入口 drvCheck 的门（第 6 轮 6d-1；设计 docs/design/v052-round6-lsp-mcp-v0.1-20261019.md §三-6）。

经隐藏开发命令 `taixu dev-check` 调用原生编译器的 drvCheck，三组检查：

1. corpus：tests/cases、tests/nrt-cases、examples 的全部 .tx，按 `--target`（缺省两个目标都跑）与用例的
   构建参数（`<用例>.toolchain` 加 --toolchain），对照同一编译器 `taixu build` 的诊断文本：
   - 相同为 PASS；
   - build 在语义分析之后才失败（入口缺 main 的 3010、代码生成与链接阶段）：检查须无诊断，build 的 3102 须
     出现在检查结果里（embedBytes 的存在与大小）；其余计 POSTSEMA；
   - build 因导入失败停下（3008、3009、无有效恢复 AST）：build 的文本须是检查文本的前缀（检查继续分析其余
     模块），计 CONTINUE；
   - 每个入口的结构化记录逐条回渲须等于其文本。
2. resident：同一目标、同一组参数的全部入口放进一个 dev-check 进程连查，每个入口的结果须与单独检查相同
   （常驻进程里的状态复位，如 semamirror 的 smStSize）。
3. scenario：覆盖层（打开文档未保存的内容、未保存的新文件）、导入失败继续、embedBytes（存在、目录、
   超过 64 MiB）的专用用例，断言诊断的码、模块路径与行列，embedBytes 各例另断言消息里的原因并与 build 的
   文本对照；另按主机查非普通文件（设计 §三-6 补定）：Linux 上在系统临时目录建 FIFO 与指向 /dev/zero 的
   符号链接，Windows 上查 data/NUL（设备），检查与 build 都须在 60 s 内结束并报 not a regular file。
4. target（D3 v0.35，macOS 适配 M0b）：按目标选文件的四档（x_<os>_<arch>、x_<os>、x_unix、x）在 windows、linux 与只用于
   分析的 macOS（x86_64、aarch64）上各选到期望的文件；同组兄弟的导出面比对覆盖新后缀与覆盖层；被调用的 from syscall
   在 windows、macOS 上于检查时即报 3033（文案按目标），linux 无诊断；导入写法与磁盘不一致（文件名、目录名）在任何
   宿主上报同一条 3008，只差大小写的目标文件不被选中；产物命令拒绝 macOS 目标（退出码 64）。

用法（仓根执行）：
  python scripts/test-native-check.py --compiler <原生 taixu> [--target windows|linux] [--work 目录] [--only corpus,resident,scenario,target]
任何 FAIL 时退出码 1。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIRS = ('tests/cases', 'tests/nrt-cases', 'examples')
MAIN_MISSING = 'driver: 入口模块缺少 function main(args: []string)（3010）'


def run(argv, cwd=ROOT, timeout=600):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')


def text_lines(s):
    s = s.replace('\r\n', '\n')
    return [l for l in s.split('\n') if l != '']


def render(d):
    if d['code'] == 0:
        return d['msg'] + '\n'
    out = '%s:%d:%d: error %d: %s\n' % (d['module'], d['line'], d['col'], d['code'], d['msg'])
    if d['hint']:
        out += '  └─ 处置详见: taixu explain %d\n' % d['code']
    return out


def parse_check(stdout):
    """dev-check output → list of {entry, count, diags, text}."""
    blocks = []
    for ln in stdout.replace('\r\n', '\n').split('\n'):
        if not ln:
            continue
        obj = json.loads(ln)
        if 'entry' in obj:
            blocks.append({'entry': obj['entry'], 'count': obj['count'], 'diags': [], 'text': None})
        elif 'text' in obj:
            blocks[-1]['text'] = obj['text']
        else:
            blocks[-1]['diags'].append(obj)
    return blocks


def consistent(block):
    """Structured records render back to the text, and the count matches."""
    joined = ''.join(render(d) for d in block['diags'])
    want = block['text'] + '\n' if block['text'] else ''
    return block['count'] == len(block['diags']) and joined == want


def corpus_files():
    out = []
    for d in CORPUS_DIRS:
        out += sorted((ROOT / d).glob('*.tx'))
    return out


def flags_of(tx):
    return ['--toolchain'] if tx.with_suffix('.toolchain').exists() else []


def judge(build_rc, build_err, check_text):
    """Compare build's diagnostic text with the check's. Returns (status, detail)."""
    b = text_lines(build_err) if build_rc != 0 else []
    c = text_lines(check_text)
    if b == c:
        return 'PASS', ''
    if c == b[:len(c)] and len(b) > len(c) and not any(': error ' in l and 'error 3102:' not in l for l in c):
        # build failed after semantic analysis: 3010, codegen or link. The check reports at most embedBytes.
        rest = b[len(c):]
        if rest == [MAIN_MISSING]:
            return 'PASS', ''
        if all('error 3102:' in l for l in rest):
            return ('PASS', '') if all(l in c for l in rest) else ('FAIL', '3102 not reported by check')
        return 'POSTSEMA', rest[0][:200]
    if any('error 3102:' in l for l in c) and b and all('error 3102:' in l for l in b) and all(l in c for l in b):
        # build stops at the first failing embedBytes, the check lists every one.
        return 'PASS', ''
    if len(b) < len(c) and c[:len(b)] == b and any(('error 3008:' in l) or ('error 3009:' in l) or ('无有效恢复 AST' in l) for l in b):
        return 'CONTINUE', ''
    return 'FAIL', 'build=%r check=%r' % (b[:6], c[:6])


def check_cmd(compiler, target, flags, entries, overlays=()):
    argv = [compiler, 'dev-check', *flags, '--target=' + target]
    for path, content_file in overlays:
        argv += ['--overlay', path, content_file]
    return argv + [str(e) for e in entries]


def corpus(compiler, targets, work, report):
    bad = 0
    counts = {}
    results = {}
    for target in targets:
        for tx in corpus_files():
            name = str(tx.relative_to(ROOT)).replace('\\', '/')
            flags = flags_of(tx)
            rc, out, err = run(check_cmd(compiler, target, flags, [name]))
            if rc not in (0, 1):
                status, detail = 'FAIL', 'dev-check exit %d: %s' % (rc, (err or out)[-300:])
            else:
                block = parse_check(out)[0]
                results[(target, name)] = (flags, block)
                outdir = work / ('b-' + target) / tx.stem
                outdir.parent.mkdir(parents=True, exist_ok=True)  # build --out creates one level only
                brc, _, berr = run([compiler, 'build', name, *flags, '--target=' + target, '--out', outdir])
                status, detail = judge(brc, berr, block['text'])
                if not consistent(block):
                    status, detail = 'FAIL', 'structured records do not render back to the text'
            counts[status] = counts.get(status, 0) + 1
            if status == 'FAIL':
                bad += 1
            if status != 'PASS':
                print('%-9s %-6s %s %s' % (status, target, name, detail), flush=True)
            report.append({'group': 'corpus', 'target': target, 'case': name, 'status': status, 'detail': detail})
        shutil.rmtree(work / ('b-' + target), ignore_errors=True)
    print('corpus: ' + ' '.join('%s=%d' % kv for kv in sorted(counts.items())), flush=True)
    return bad, results


def resident(compiler, targets, results, report):
    bad = 0
    n = 0
    for target in targets:
        groups = {}
        for (t, name), (flags, block) in sorted(results.items()):
            if t == target:
                groups.setdefault(tuple(flags), []).append((name, block))
        for flags, items in groups.items():
            rc, out, err = run(check_cmd(compiler, target, list(flags), [nm for nm, _ in items]), timeout=1800)
            blocks = parse_check(out) if rc in (0, 1) else []
            if len(blocks) != len(items):
                bad += 1
                print('FAIL      resident %s %s: %d of %d entries (exit %d) %s' % (target, ' '.join(flags), len(blocks), len(items), rc, err[-300:]), flush=True)
                continue
            for (name, alone), got in zip(items, blocks):
                n += 1
                if got != alone:
                    bad += 1
                    print('FAIL      resident %s %s differs from a standalone check' % (target, name), flush=True)
                    report.append({'group': 'resident', 'target': target, 'case': name, 'status': 'FAIL'})
    print('resident: %d entries, %s' % (n, 'PASS' if bad == 0 else 'FAIL=%d' % bad), flush=True)
    return bad


# ---------------------------------------------------------------- scenarios

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def diag_set(block, root):
    """(code, path relative to root with '/', line, col) of every record."""
    out = []
    for d in block['diags']:
        p = d['path'].replace('\\', '/')
        r = str(root).replace('\\', '/')
        if p.startswith(r + '/'):
            p = p[len(r) + 1:]
        out.append((d['code'], p, d['line'], d['col']))
    return out


def scenarios(compiler, targets, work, report):
    root = (work / 'scen').resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    write(root / 'a.tx', 'import { twice } from "./b";\n\nexport function main(args: []string): int {\n    const s: string = 1;\n    return twice(2);\n}\n')
    write(root / 'b.tx', 'export function twice(n: int): int {\n    return n * 2;\n}\n')
    # ov-a imports ov-b (on disk, with an error) and c (only in the overlay: an unsaved new file).
    write(root / 'ov-a.tx', 'import { twice } from "./ov-b";\nimport { fresh } from "./c";\n\nexport function main(args: []string): int {\n    return twice(fresh());\n}\n')
    write(root / 'ov-b.tx', 'export function twice(n: int): int {\n    const s: string = n;\n    return n * 2;\n}\n')
    write(root / 'imp.tx', 'import { gone } from "./nope";\nimport { half } from "./broken";\nimport { ping } from "./cyc1";\nimport { twice } from "./b";\n\n'
          'export function main(args: []string): int {\n    const x: string = twice(1);\n    return gone() + half(2) + ping();\n}\n')
    write(root / 'broken.tx', 'export function half(n: int): int {\n    return n / ;\n}\n')
    write(root / 'cyc1.tx', 'import { pong } from "./cyc2";\nexport function ping(): int {\n    return pong();\n}\n')
    write(root / 'cyc2.tx', 'import { ping } from "./cyc1";\nexport function pong(): int {\n    return 1;\n}\n')
    write(root / 'data/small.bin', 'abc')
    big = root / 'data/big.bin'
    with open(big, 'wb') as f:
        f.truncate(64 * 1024 * 1024 + 1)
    (root / 'data/dir.bin').mkdir(exist_ok=True)
    write(root / 'emb.tx', 'export function main(args: []string): int {\n    const a = embedBytes("data/small.bin");\n    return bytesLen(a);\n}\n')
    write(root / 'emb-big.tx', 'export function main(args: []string): int {\n    const a = embedBytes("data/big.bin");\n    return bytesLen(a);\n}\n')
    write(root / 'emb-dir.tx', 'export function main(args: []string): int {\n    const a = embedBytes("data/dir.bin");\n    return bytesLen(a);\n}\n')
    write(root / 'emb-none.tx', 'export function main(args: []string): int {\n    const a = embedBytes("data/none.bin");\n    return bytesLen(a);\n}\n')
    write(root / 'emb-sema.tx', 'export function main(args: []string): int {\n    const s: string = 1;\n    const a = embedBytes("data/none.bin");\n    return bytesLen(a);\n}\n')
    write(root / 'ov/fixed-a.txt', 'import { twice } from "./b";\n\nexport function main(args: []string): int {\n    return twice(2);\n}\n')
    write(root / 'ov/fixed-b.txt', 'export function twice(n: int): int {\n    return n * 2;\n}\n')
    write(root / 'ov/new-c.txt', 'export function fresh(): int {\n    const t: string = 3;\n    return 3;\n}\n')

    def p(name):
        return str(root / name)

    def only_codes(got):
        return [(c, pth) for c, pth, _, _ in got]

    # (name, entry, overlays, predicate on the (code, path, line, col) records)
    cases = [
        ('disk', 'a.tx', [], lambda g: g == [(3002, 'a.tx', 4, 5)]),
        ('overlay-entry', 'a.tx', [(p('a.tx'), root / 'ov/fixed-a.txt')], lambda g: g == []),
        ('no-overlay', 'ov-a.tx', [], lambda g: (3008, 'ov-a.tx', 2, 1) in g and (3002, 'ov-b.tx', 2, 5) in g
         and (3001, 'ov-a.tx') in only_codes(g)),
        ('overlay-dep', 'ov-a.tx', [(p('ov-b.tx'), root / 'ov/fixed-b.txt'), (p('c.tx'), root / 'ov/new-c.txt')],
         lambda g: g == [(3002, 'c.tx', 2, 5)]),
        ('import-continue', 'imp.tx', [], lambda g: all(x in only_codes(g) for x in
         [(3008, 'imp.tx'), (3009, 'cyc2.tx'), (2001, 'broken.tx'), (3001, 'imp.tx'), (3002, 'imp.tx')])),
        ('embed-small', 'emb.tx', [], lambda g: g == []),
        ('embed-big', 'emb-big.tx', [], lambda g: g == [(3102, 'emb-big.tx', 2, 15)]),
        ('embed-dir', 'emb-dir.tx', [], lambda g: g == [(3102, 'emb-dir.tx', 2, 15)]),
        ('embed-none', 'emb-none.tx', [], lambda g: g == [(3102, 'emb-none.tx', 2, 15)]),
        ('embed-after-sema-error', 'emb-sema.tx', [], lambda g: g == [(3002, 'emb-sema.tx', 2, 5), (3102, 'emb-sema.tx', 3, 15)]),
    ]
    # The reason inside the 3102 message (LSP 与 MCP 实施设计 §三-6 补定).
    reasons = {
        'embed-big': 'embedBytes 文件超过 64MB：data/big.bin',
        'embed-dir': '（not a regular file: ',
        'embed-none': '（no such file: ',
        'embed-after-sema-error': '（no such file: ',
    }
    bad = 0
    for target in targets:
        for name, entry, overlays, pred in cases:
            rc, out, err = run(check_cmd(compiler, target, [], [p(entry)], overlays), cwd=root)
            if rc not in (0, 1):
                bad += 1
                print('FAIL      scenario %s %s: exit %d %s' % (target, name, rc, err[-300:]), flush=True)
                report.append({'group': 'scenario', 'target': target, 'case': name, 'status': 'FAIL'})
                continue
            block = parse_check(out)[0]
            got = diag_set(block, root)
            ok = consistent(block) and pred(got) and reasons.get(name, '') in (block['text'] or '')
            detail = '' if ok else 'got %r %r' % (got, (block['text'] or '')[-200:])
            if ok and name.startswith('embed'):
                # Same entry form as the check, so the paths inside the 3102 text agree.
                (work / 'scen-out').mkdir(parents=True, exist_ok=True)
                brc, _, berr = run([compiler, 'build', p(entry), '--target=' + target, '--out', work / 'scen-out' / name], cwd=root)
                bl = text_lines(berr) if brc != 0 else []
                cl = text_lines(block['text'])
                if name == 'embed-after-sema-error':
                    # build stops at the semantic error; the check lists the embedBytes finding after it.
                    ok = len(bl) > 0 and cl[:len(bl)] == bl and len(cl) > len(bl)
                else:
                    ok = judge(brc, berr, block['text'])[0] == 'PASS'
                detail = '' if ok else 'vs build: build=%r check=%r' % (bl, cl)
            status = 'PASS' if ok else 'FAIL'
            if not ok:
                bad += 1
            print('%-9s scenario %s %s %s' % (status, target, name, detail), flush=True)
            report.append({'group': 'scenario', 'target': target, 'case': name, 'status': status, 'detail': detail})
    shutil.rmtree(work / 'scen-out', ignore_errors=True)
    big.unlink()
    return bad + host_embed(compiler, targets, work, report)


def target_scenarios(compiler, work, report):
    """按目标选文件、兄弟导出面与 FFI 目标检查（D3 v0.35）；macOS 产物（适配 M1）在本机交叉构建并过独立校验器，不论宿主都跑。"""
    root = (work / 'target').resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    bad_body = 'export function which(): int {\n    const s: string = 1;\n    return 1;\n}\n'
    # 每份都带同一个语义错误：报告错误的文件就是被选中的那份（未选中的兄弟只解析导出面，不做语义分析）。
    for name in ['pick', 'pick_unix', 'pick_macos', 'pick_windows_x86_64', 'pick_linux', 'pick2', 'pick2_unix']:
        write(root / 'lib' / (name + '.tx'), bad_body)
    write(root / 'sel.tx', 'import { which } from "./lib/pick";\n\nexport function main(args: []string): int {\n    return which();\n}\n')
    write(root / 'sel2.tx', 'import { which } from "./lib/pick2";\n\nexport function main(args: []string): int {\n    return which();\n}\n')
    good = 'export function f(): int {\n    return 1;\n}\n'
    write(root / 'lib/vs.tx', good)
    write(root / 'lib/vs_unix.tx', good)
    write(root / 'lib/vs2.tx', good)
    write(root / 'lib/vs2_macos_aarch64.tx', 'export function f(): string {\n    return "";\n}\n')
    write(root / 'ov/vs_unix.txt', good + 'export function g(): int {\n    return 2;\n}\n')
    write(root / 'sib.tx', 'import { f } from "./lib/vs";\n\nexport function main(args: []string): int {\n    return f();\n}\n')
    write(root / 'sib2.tx', 'import { f } from "./lib/vs2";\n\nexport function main(args: []string): int {\n    return f();\n}\n')
    # 导入写法须与磁盘逐字节一致（§6.1-4）：三个宿主报同一条 3008（大小写不敏感的系统上能打开，区分大小写的找不到）。
    write(root / 'lib2/Case.tx', good)
    write(root / 'case-ok.tx', 'import { f } from "./lib2/Case";\n\nexport function main(args: []string): int {\n    return f();\n}\n')
    write(root / 'case-file.tx', 'import { f } from "./lib2/case";\n\nexport function main(args: []string): int {\n    return f();\n}\n')
    write(root / 'case-dir.tx', 'import { f } from "./Lib2/Case";\n\nexport function main(args: []string): int {\n    return f();\n}\n')
    # 只差大小写的目标文件不算这个名字的目标文件：两个宿主都选基础文件（基础文件里的语义错误证明选中的是它）。
    write(root / 'lib3/v.tx', bad_body)
    write(root / 'lib3/V_windows.tx', 'export function which(): int {\n    return 1;\n}\n')
    write(root / 'case-variant.tx', 'import { which } from "./lib3/v";\n\nexport function main(args: []string): int {\n    return which();\n}\n')
    write(root / 'sys.tx', 'declare function getpid(): i32 from syscall 39;\ndeclare function unused(fd: i32): i32 from syscall 3;\n\n'
          'export function main(args: []string): int {\n    return getpid() as int;\n}\n')

    def p(name):
        return str(root / name)

    def picked(name):
        return lambda g: g == [(3002, 'lib/' + name + '.tx', 2, 5)]

    win_hint, mac_hint = '（Windows 实现请放在 _windows 文件里', '（macOS 不提供稳定的系统调用接口'
    # (target, name, entry, overlays, predicate, text that must appear, text that must not)
    cases = [
        ('windows', 'pick-os-arch', 'sel.tx', [], picked('pick_windows_x86_64'), '', ''),
        ('linux', 'pick-os', 'sel.tx', [], picked('pick_linux'), '', ''),
        ('x86_64-macos', 'pick-os-x86_64', 'sel.tx', [], picked('pick_macos'), '', ''),
        ('aarch64-macos', 'pick-os-aarch64', 'sel.tx', [], picked('pick_macos'), '', ''),
        ('windows', 'pick-base', 'sel2.tx', [], picked('pick2'), '', ''),
        ('linux', 'pick-unix', 'sel2.tx', [], picked('pick2_unix'), '', ''),
        ('macos', 'pick-unix-macos', 'sel2.tx', [], picked('pick2_unix'), '', ''),
        ('windows', 'siblings-same', 'sib.tx', [], lambda g: g == [], '', ''),
        ('windows', 'siblings-overlay', 'sib.tx', [(p('lib/vs_unix.tx'), root / 'ov/vs_unix.txt')],
         lambda g: g == [(3033, 'sib.tx', 1, 1)], "vs_unix.tx 导出 'g'，vs.tx 没有", ''),
        ('linux', 'siblings-new-suffix', 'sib2.tx', [], lambda g: g == [(3033, 'sib2.tx', 1, 1)], 'vs2_macos_aarch64.tx 中为', ''),
        ('windows', 'syscall-windows', 'sys.tx', [], lambda g: g == [(3033, 'sys.tx', 1, 18)], win_hint, 'unused'),
        ('macos', 'syscall-macos', 'sys.tx', [], lambda g: g == [(3033, 'sys.tx', 1, 18)], mac_hint, 'unused'),
        ('linux', 'syscall-linux', 'sys.tx', [], lambda g: g == [], '', ''),
        ('windows', 'case-exact', 'case-ok.tx', [], lambda g: g == [], '', ''),
        ('windows', 'case-file', 'case-file.tx', [], lambda g: g == [(3008, 'case-file.tx', 1, 1), (3001, 'case-file.tx', 4, 12)], "'case.tx' 在磁盘上为 'Case.tx'", ''),
        ('linux', 'case-dir', 'case-dir.tx', [], lambda g: g == [(3008, 'case-dir.tx', 1, 1), (3001, 'case-dir.tx', 4, 12)], "'Lib2' 在磁盘上为 'lib2'", ''),
        ('windows', 'case-variant', 'case-variant.tx', [], lambda g: g == [(3002, 'lib3/v.tx', 2, 5)], '', ''),
    ]
    bad = 0
    (work / 'target-out').mkdir(parents=True, exist_ok=True)  # 原生 build --out 只建一层目录
    for target, name, entry, overlays, pred, must, must_not in cases:
        rc, out, err = run(check_cmd(compiler, target, [], [p(entry)], overlays), cwd=root)
        ok, detail = False, 'exit %d %s' % (rc, err[-300:])
        if rc in (0, 1):
            block = parse_check(out)[0]
            got = diag_set(block, root)
            text = block['text'] or ''
            ok = consistent(block) and pred(got) and must in text and (not must_not or must_not not in text)
            detail = '' if ok else 'got %r %r' % (got, text[-300:])
        if ok and name.startswith('syscall'):
            # 构建报同一条（语义分析阶段即停，不再到构建尾部）。
            brc, _, berr = run([compiler, 'build', p(entry), '--target=' + target, '--out', work / 'target-out' / name], cwd=root)
            ok = judge(brc, berr, text)[0] == 'PASS' if target != 'linux' else brc == 0
            detail = '' if ok else 'vs build: exit %d %r' % (brc, berr[-300:])
        status = 'PASS' if ok else 'FAIL'
        bad += not ok
        print('%-9s target %s %s %s' % (status, target, name, detail), flush=True)
        report.append({'group': 'target', 'target': target, 'case': name, 'status': status, 'detail': detail})
    bad += macos_products(compiler, root, work, report)
    shutil.rmtree(work / 'target-out', ignore_errors=True)
    return bad


def macos_products(compiler, root, work, report):
    """x86_64-macos 产物（docs/design/v052-macos-m1-macho-v0.1-20261024.md、v052-macos-m2-runtime-v0.1-20261024.md）：交叉构建、
    独立校验器（含篡改负例；外部函数产物带第二个 dylib、按库序号绑定）、两次构建逐字节相同；工具链与插桩构建（M3）；aarch64-macos 构建得 Mach-O arm64（M4），
    尚无 A64 指令清单的命令、尚未支持的形态与写错的库名按文案拒绝。目标一律写三元组（不带架构的 macos 按宿主架构取）。在测试机上运行见 test-nrt-products.py --target macos。"""
    write(root / 'mac-ffi.tx', 'declare function getpid(): i32 from "libSystem";\ndeclare function zlibVersion(): string from "/usr/lib/libz.1.dylib";\n\n'
          'export function main(args: []string): int {\n    return getpid() as int + len(zlibVersion());\n}\n')
    write(root / 'mac-ffi-soname.tx', 'declare function getpid(): i32 from "libc.so.6";\n\nexport function main(args: []string): int {\n    return getpid() as int;\n}\n')
    write(root / 'mac-tool.tx', 'export function main(args: []string): int {\n    return localFileSize(".", "x") catch 0;\n}\n')
    out = work / 'target-out'
    bad = 0

    def row(case, ok, detail):
        nonlocal bad
        bad += not ok
        print('%-9s target macos %s %s' % ('PASS' if ok else 'FAIL', case, '' if ok else detail), flush=True)
        report.append({'group': 'target', 'target': 'macos', 'case': case, 'status': 'PASS' if ok else 'FAIL', 'detail': '' if ok else detail})

    images = []
    for k in (1, 2):
        rc, o, err = run([compiler, 'build', str(root / 'sib.tx'), '--target=x86_64-macos', '--out', out / ('mac%d' % k)], cwd=root)
        exe = out / ('mac%d' % k) / 'sib'
        ok = rc == 0 and exe.exists()
        row('build-%d' % k, ok, 'exit %d %r' % (rc, err[-300:]))
        if ok:
            images.append(exe)
    if len(images) == 2:
        row('deterministic', images[0].read_bytes() == images[1].read_bytes(), 'two builds differ')
        checker = [sys.executable, str(ROOT / 'scripts' / 'macho_check.py')]
        rc, o, err = run([*checker, str(images[0])], cwd=ROOT)
        row('macho-check', rc == 0 and 'PASS' in o, (o + err)[-300:])
        rc, o, err = run([*checker, '--self-test', str(images[0])], cwd=ROOT)
        row('macho-check-negatives', rc == 0 and o.count('PASS negative case detected') == 3, (o + err)[-300:])
    rc, o, err = run([compiler, 'build', str(root / 'mac-ffi.tx'), '--target=x86_64-macos', '--out', out / 'ffi'], cwd=root)
    exe = out / 'ffi' / 'mac-ffi'
    row('ffi-build', rc == 0 and exe.exists() and '/usr/lib/libz.1.dylib' in (o + err), 'exit %d %r' % (rc, (o + err)[-300:]))
    if exe.exists():
        rc, o, err = run([sys.executable, str(ROOT / 'scripts' / 'macho_check.py'), str(exe)], cwd=ROOT)
        row('ffi-macho-check', rc == 0 and 'PASS' in o, (o + err)[-300:])
    # 工具链例程（M3a）与插桩构建（M3c）：构建成功、过独立校验器；在测试机上的运行见 tool-security 夹具与调试门。
    for case, argv, exe in [('toolchain', ['build', root / 'mac-tool.tx', '--target=x86_64-macos', '--toolchain', '--out', out / 'tool'], out / 'tool' / 'mac-tool'),
                            ('debug', ['build', root / 'sib.tx', '--target=x86_64-macos', '--debug', '--out', out / 'dbg'], out / 'dbg' / 'sib')]:
        rc, o, err = run([compiler, *argv], cwd=root)
        row(case + '-build', rc == 0 and exe.exists(), 'exit %d %r' % (rc, (o + err)[-300:]))
        if exe.exists():
            rc, o, err = run([sys.executable, str(ROOT / 'scripts' / 'macho_check.py'), str(exe)], cwd=ROOT)
            row(case + '-macho-check', rc == 0 and 'PASS' in o, (o + err)[-300:])
    # aarch64-macos（M4）：构建得 Mach-O arm64（运行在测试机的语料门里）；原生指令清单尚无 A64 版，disasm 与 profile 说明原因。
    for case, flag in [('build-aarch64', '--target=aarch64-macos'), ('build-arm64-alias', '--target=arm64-macos')]:
        rc, o, err = run([compiler, 'build', root / 'sib.tx', flag, '--out', out / case], cwd=root)
        head = (out / case / 'sib').read_bytes()[:8] if (out / case / 'sib').exists() else b''
        row(case, rc == 0 and head == bytes.fromhex('cffaedfe0c000001'), 'exit %d head %s %r' % (rc, head.hex(), (o + err)[-300:]))
    # arm64 产物过独立校验器（含逐字解码的分支与 ADRP 核对）及其负例；外部函数、工具链与插桩三种形态同样。
    a64 = out / 'build-aarch64' / 'sib'
    if a64.exists():
        checker = [sys.executable, str(ROOT / 'scripts' / 'macho_check.py')]
        rc, o, err = run([*checker, str(a64)], cwd=ROOT)
        row('aarch64-macho-check', rc == 0 and 'PASS' in o and 'arch=arm64' in o, (o + err)[-300:])
        rc, o, err = run([*checker, '--self-test', str(a64)], cwd=ROOT)
        row('aarch64-macho-check-negatives', rc == 0 and o.count('PASS negative case detected') == 6, (o + err)[-300:])
    for case, argv, exe in [('ffi', [root / 'mac-ffi.tx'], 'mac-ffi'), ('toolchain', [root / 'mac-tool.tx', '--toolchain'], 'mac-tool'),
                            ('debug', [root / 'sib.tx', '--debug'], 'sib')]:
        dest = out / ('a64-' + case)
        rc, o, err = run([compiler, 'build', *argv, '--target=aarch64-macos', '--out', dest], cwd=root)
        ok = rc == 0 and (dest / exe).exists()
        if ok:
            rc, o, err = run([sys.executable, str(ROOT / 'scripts' / 'macho_check.py'), str(dest / exe)], cwd=ROOT)
            ok = rc == 0 and 'arch=arm64' in o
        row('aarch64-' + case + '-macho-check', ok, 'exit %d %r' % (rc, (o + err)[-300:]))
    # A64 的原生指令清单与编译画像（设计 A11；清单与代码逐字对照在 test-native-disasm）。
    rc, o, err = run([compiler, 'disasm', root / 'sib.tx', '--target=aarch64-macos'], cwd=root)
    row('disasm-aarch64', rc == 0 and 'function main' in o and 'stp x29, x30, [sp, #-16]!' in o and 'ret x30' in o
        and '.inst' not in o, 'exit %d %r' % (rc, (o + err)[-300:]))
    rc, o, err = run([compiler, 'profile', 'compile', root / 'sib.tx', '--target=aarch64-macos', '--format=json'], cwd=root)
    try:
        prof = json.loads(o)['profile']
    except (ValueError, KeyError, TypeError):
        prof = {}
    fns = prof.get('functions') or []
    row('profile-aarch64', rc == 0 and prof.get('target') == 'aarch64-macos' and int((prof.get('summary') or {}).get('native_instructions', 0)) > 0
        and fns and all(int(f.get('native_instructions', 0)) > 0 for f in fns), 'exit %d %r' % (rc, (o + err)[-300:]))
    # --lib 与其他目标相同：没有 main 的入口照常编译与链接（不带 --lib 报 3010）。
    for arch in ('x86_64', 'aarch64'):
        rc, o, err = run([compiler, 'build', str(root / 'lib/vs.tx'), '--target=%s-macos' % arch, '--lib', '--out', out / ('lib-' + arch)], cwd=root)
        row('lib-' + arch, rc == 0 and (out / ('lib-' + arch) / 'vs').exists(), 'exit %d %r' % (rc, (o + err)[-300:]))
    rc, o, err = run([compiler, 'build', str(root / 'lib/vs.tx'), '--target=aarch64-macos', '--out', out / 'nolib'], cwd=root)
    row('no-main-without-lib', rc == 1 and '3010' in (o + err), 'exit %d %r' % (rc, (o + err)[-300:]))
    # 尚未支持的形态与写错的库名：说明原因（退出码 64 为用法，1 为构建拒绝）。
    rejects = [
        ('static', ['build', root / 'sib.tx', '--target=x86_64-macos', '--static', '--out', out / 'st'], 64, '不支持 --static'),
        ('ffi-soname', ['build', root / 'mac-ffi-soname.tx', '--target=x86_64-macos', '--out', out / 'soname'], 1, 'macOS 上库名须写 dylib 的安装名'),
    ]
    for case, argv, code, needle in rejects:
        rc, o, err = run([compiler, *argv], cwd=root)
        row(case + '-rejected', rc == code and needle in (o + err), 'exit %d %r' % (rc, (o + err)[-300:]))
    return bad


def host_embed(compiler, targets, work, report):
    """embedBytes of a non-regular file: the check and build both finish and report not a regular file."""
    root = Path(tempfile.mkdtemp(prefix='taixu-check-dev-')).resolve()  # FIFOs need a Linux file system, not /mnt
    (root / 'data').mkdir()
    entries = []
    if sys.platform.startswith('linux'):
        os.mkfifo(root / 'data/pipe.bin')
        os.symlink('/dev/zero', root / 'data/zero.bin')
        entries = [('embed-fifo', 'emb-fifo.tx', 'data/pipe.bin'), ('embed-dev-zero', 'emb-zero.tx', 'data/zero.bin')]
    elif sys.platform == 'win32':
        entries = [('embed-nul-device', 'emb-nul.tx', 'data/NUL')]
    for _, entry, lit in entries:
        write(root / entry, 'export function main(args: []string): int {\n    const a = embedBytes("%s");\n    return bytesLen(a);\n}\n' % lit)
    bad = 0
    for target in targets:
        for name, entry, lit in entries:
            try:
                rc, out, err = run(check_cmd(compiler, target, [], [root / entry]), cwd=root, timeout=60)
                block = parse_check(out)[0] if rc in (0, 1) else None
                ok = block is not None and consistent(block) and diag_set(block, root) == [(3102, entry, 2, 15)] \
                    and '（not a regular file: ' in (block['text'] or '')
                detail = '' if ok else 'check: exit %d %r' % (rc, (out + err)[-300:])
                if ok:
                    outdir = work / 'host-out' / name
                    outdir.parent.mkdir(parents=True, exist_ok=True)
                    brc, _, berr = run([compiler, 'build', root / entry, '--target=' + target, '--out', outdir], cwd=root, timeout=60)
                    ok = judge(brc, berr, block['text'])[0] == 'PASS'
                    detail = '' if ok else 'vs build: build=%r check=%r' % (text_lines(berr), text_lines(block['text']))
            except subprocess.TimeoutExpired as e:
                ok, detail = False, 'did not finish within 60 s: %s' % ' '.join(str(x) for x in e.cmd[:3])
            status = 'PASS' if ok else 'FAIL'
            if not ok:
                bad += 1
            print('%-9s scenario %s %s %s' % (status, target, name, detail), flush=True)
            report.append({'group': 'scenario', 'target': target, 'case': name, 'status': status, 'detail': detail})
    shutil.rmtree(work / 'host-out', ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, help='原生 taixu')
    ap.add_argument('--target', choices=('windows', 'linux'), action='append')
    ap.add_argument('--work', default='')
    ap.add_argument('--only', default='corpus,resident,scenario,target')
    ap.add_argument('--report', default='')
    a = ap.parse_args()
    compiler = str(Path(a.compiler).resolve())
    targets = a.target or ['windows', 'linux']
    work = Path(a.work).resolve() if a.work else Path(tempfile.mkdtemp(prefix='taixu-check-'))
    work.mkdir(parents=True, exist_ok=True)
    groups = set(a.only.split(','))
    report = []
    bad = 0
    results = {}
    if 'corpus' in groups or 'resident' in groups:
        b, results = corpus(compiler, targets, work, report)
        bad += b
    if 'resident' in groups:
        bad += resident(compiler, targets, results, report)
    if 'scenario' in groups:
        bad += scenarios(compiler, targets, work, report)
    if 'target' in groups:
        bad += target_scenarios(compiler, work, report)
    if a.report:
        Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    print('SUMMARY %s' % ('PASS' if bad == 0 else 'FAIL=%d' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
