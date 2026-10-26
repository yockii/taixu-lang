"""可移植版内核的对照（macOS 适配 M4 设计 A8）：内核生成器的 --generic=模块,… 让所列模块用 rtfn 写的可移植版代替手写的
x86 版。本脚本由给定编译器编出生成器，生成本宿主目标的两个内核（标准、可移植），各跑一遍语料（scripts/test-nrt-products.py
经 --kernel），可选再跑 --leak-check 与 --debug 两种；可移植内核的每个用例结果须与标准内核相同（通过、失败、跳过一一对应）。

  python scripts/test-generic-kernel.py --compiler <taixu> --generic intern --work <dir> [--target linux|macos] [--leak-check] [--debug] [cases…]

--target 缺省为宿主目标；linux 在 Windows 宿主上经 wsl.exe 运行产物，macos 经 ssh 在测试机上运行（x86_64，Rosetta）。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from importlib import import_module

products = import_module('test-nrt-products')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--generic', required=True, help='逗号分隔的模块名，或 all')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--leak-check', action='store_true')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--target', choices=['windows', 'linux', 'macos'], help='产物目标（缺省为宿主）')
    ap.add_argument('cases', nargs='*')
    a = ap.parse_args()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    exe = '.exe' if os.name == 'nt' else ''
    os_target = a.target or ('windows' if os.name == 'nt' else 'linux')
    target = {'windows': 'x86_64-windows', 'linux': 'x86_64-linux', 'macos': 'x86_64-macos'}[os_target]
    name = {'windows': 'kernel.obj', 'linux': 'kernel-linux.o', 'macos': 'kernel-macos.o'}[os_target]
    # 生成器在宿主上运行。
    tflag = [] if os.name == 'nt' else ['--target=linux']
    gen = work / 'gen'
    p = subprocess.run([str(a.compiler.resolve()), 'build', 'bootstrap/runtime_memory_gen.tx', '--runtime=native', *tflag, '--out', str(gen)],
                       cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if p.returncode != 0:
        raise SystemExit('generator build failed:\n' + p.stdout + p.stderr)
    kernels = {}
    for kind, extra in (('standard', []), ('generic', ['--generic=' + a.generic])):
        out = work / kind
        out.mkdir(exist_ok=True)
        k = out / name
        p = subprocess.run([str(gen / ('runtime_memory_gen' + exe)), '--target', target, str(k), *products.KERNEL_FLAGS, *extra],
                           cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if p.returncode != 0:
            raise SystemExit('%s kernel failed: %s' % (kind, (p.stdout + p.stderr).strip()))
        kernels[kind] = k
    modes = [[]] + ([['--leak-check']] if a.leak_check else []) + ([['--debug']] if a.debug else [])
    bad = 0
    for mode in modes:
        label = mode[0] if mode else 'normal'
        results = {}
        for kind, k in kernels.items():
            report = work / ('%s-%s.json' % (kind, label.strip('-')))
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'test-nrt-products.py'), '--compiler', str(a.compiler.resolve()),
                            '--kernel', str(k), '--work', str(work / ('run-%s-%s' % (kind, label.strip('-')))), '--report', str(report),
                            '--target', os_target, *mode, *a.cases], cwd=ROOT, capture_output=True)
            rows = json.loads(report.read_text(encoding='utf-8'))
            rows = rows.get('rows', rows) if isinstance(rows, dict) else rows
            results[kind] = {r['case']: r['status'] for r in rows}
        std, gen_ = results['standard'], results['generic']
        diff = sorted(c for c in set(std) | set(gen_) if std.get(c) != gen_.get(c))
        counts = {}
        for s in gen_.values():
            counts[s] = counts.get(s, 0) + 1
        print('%s: %s' % (label, ' '.join('%s=%d' % kv for kv in sorted(counts.items()))), flush=True)
        for c in diff:
            print('  DIFF %s standard=%s generic=%s' % (c, std.get(c), gen_.get(c)), flush=True)
        fails = sorted(c for c, s in std.items() if s not in ('PASS', 'COMPILE_REJECT', 'SKIP'))
        for c in fails:
            print('  standard kernel itself: %s %s' % (c, std[c]), flush=True)
        bad += len(diff)
    print('SUMMARY %s generic=%s' % ('PASS' if bad == 0 else 'FAIL', a.generic), flush=True)
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
