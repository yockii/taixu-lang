"""可移植渲染器的对照（macOS 适配 M4 设计 A9 修订）：同一编译器把语料各编两遍——缺省渲染器（emitmirror）与
--codegen=portable（arm64 后端用的同一份渲染逻辑，此处落地为 x86_64）——各跑一遍（scripts/test-nrt-products.py），可选再跑
--leak-check 与 --debug 两种；每个用例的结果须相同（通过、失败、跳过、编译期拒绝一一对应）。

  python scripts/test-portable-codegen.py --compiler <taixu> --work <dir> [--leak-check] [--debug] [cases…]

门里用 --standard-normal／--standard-leak／--standard-debug 复用 products、leak、instrumented 三组已有的报告，标准语料不重跑。
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_rows(report):
    rows = json.loads(report.read_text(encoding='utf-8'))
    rows = rows.get('rows', rows) if isinstance(rows, dict) else rows
    return {r['case']: (r['status'], r.get('detail', '')) for r in rows}


def run_corpus(compiler, work, label, extra, mode, cases):
    report = work / ('%s-%s.json' % (label, (mode[0] if mode else '--normal').strip('-')))
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'test-nrt-products.py'), '--compiler', str(compiler),
                    '--work', str(work / ('run-%s-%s' % (label, (mode[0] if mode else 'normal').strip('-')))),
                    '--report', str(report), *extra, *mode, *cases], cwd=ROOT, capture_output=True)
    return load_rows(report)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--leak-check', action='store_true')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--standard-normal', type=Path, help='已有的标准渲染器报告（普通）')
    ap.add_argument('--standard-leak', type=Path, help='已有的标准渲染器报告（--leak-check）')
    ap.add_argument('--standard-debug', type=Path, help='已有的标准渲染器报告（--debug）')
    ap.add_argument('cases', nargs='*')
    a = ap.parse_args()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    compiler = a.compiler.resolve()
    modes = [[]] + ([['--leak-check']] if a.leak_check else []) + ([['--debug']] if a.debug else [])
    bad = 0
    for mode in modes:
        label = mode[0] if mode else 'normal'
        given = {'normal': a.standard_normal, '--leak-check': a.standard_leak, '--debug': a.standard_debug}[label]
        if given is not None and given.exists() and not a.cases:
            std = load_rows(given)
        else:
            std = run_corpus(compiler, work, 'standard', [], mode, a.cases)
        por = run_corpus(compiler, work, 'portable', ['--build-flag=--codegen=portable'], mode, a.cases)
        counts = {}
        for s, _ in por.values():
            counts[s] = counts.get(s, 0) + 1
        print('%s: %s' % (label, ' '.join('%s=%d' % kv for kv in sorted(counts.items()))), flush=True)
        diff = sorted(c for c in set(std) | set(por) if std.get(c, ('?', ''))[0] != por.get(c, ('?', ''))[0])
        for c in diff:
            print('  DIFF %s standard=%s portable=%s %s' % (c, std.get(c, ('?', ''))[0], por.get(c, ('?', ''))[0],
                                                            por.get(c, ('', ''))[1][:300]), flush=True)
        for c, (s, _) in sorted(std.items()):
            if s not in ('PASS', 'COMPILE_REJECT', 'SKIP'):
                print('  standard renderer itself: %s %s' % (c, s), flush=True)
        bad += len(diff)
    print('SUMMARY %s portable-codegen' % ('PASS' if bad == 0 else 'FAIL'), flush=True)
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
