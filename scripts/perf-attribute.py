"""编译耗时退化的逐提交归因（Linux，instructions:u）。协议见 docs/design/v052-perf-measurement-v0.1-20261023.md §六。

对每个提交：git archive 展开 bootstrap、prelude 与 scripts/nrt-bootstrap.py；按该提交自己的 KERNEL_FLAGS，用起始编译器
编 runtime_memory_gen 并生成两端内核；起始编译器编出 B，B 自编得 C（该提交自己的代码生成）；C 编负载源码树里的负载，
记用户态退休指令数。指令数对同一二进制与输入是确定的，每个提交测一次；构建单线程，提交间并行不影响计数。

起始编译器须能编这些提交的源码（通常用较新的不动点）；负载源码树须能被所有 C 编（通常取最早那个提交的源码树，
例如 git archive 展开并按 build.py 生成两个内核对象）。第一个提交的 C 应等于它的已知不动点，可作自检。

用法：python3 scripts/perf-attribute.py --start <编译器> --src-root <负载源码树> --work <目录> <提交>...
      [--loads parser,semamirror] [--jobs 6]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('perf_compare', ROOT / 'scripts/perf-compare.py')
perf_compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf_compare)


def sh(argv, cwd, timeout=1800):
    p = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError('%s 退出 %d：%s' % ([str(a) for a in argv[:3]], p.returncode,
                                              (p.stdout + p.stderr).decode('utf-8', 'replace')[-600:]))


def measure(commit, a, meter):
    d = a.work / commit
    shutil.rmtree(d, ignore_errors=True)
    src = d / 'src'
    src.mkdir(parents=True)
    paths = ['bootstrap', 'scripts/nrt-bootstrap.py']
    if subprocess.run(['git', 'cat-file', '-e', commit + ':prelude'], cwd=ROOT, capture_output=True).returncode == 0:
        paths.append('prelude')
    data = subprocess.check_output(['git', 'archive', '--format=tar', commit, *paths], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(src, filter='data')
    text = (src / 'scripts/nrt-bootstrap.py').read_text(encoding='utf-8')
    flags = re.findall(r"'(--with-[a-z-]+)'", text.split('KERNEL_FLAGS', 1)[1].split(']', 1)[0])
    (src / 'bootstrap/nrt-kernel').mkdir(exist_ok=True)
    sh([a.start, 'build', 'bootstrap/runtime_memory_gen.tx', '--runtime=native', '--out', d / 'gen'], src)
    for target, name in [('x86_64-windows', 'kernel.obj'), ('x86_64-linux', 'kernel-linux.o')]:
        sh([d / 'gen/runtime_memory_gen', '--target', target, src / 'bootstrap/nrt-kernel' / name, *flags], src)
    sh([a.start, 'build', 'bootstrap/taixu.tx', '--toolchain', '--out', d / 'b'], src)
    sh([d / 'b/taixu', 'build', 'bootstrap/taixu.tx', '--toolchain', '--out', d / 'c'], src)
    row = {'commit': commit, 'c_sha256': hashlib.sha256((d / 'c/taixu').read_bytes()).hexdigest(), 'loads': {}}
    for load in a.loads.split(','):
        r = meter.run([str(d / 'c/taixu'), 'build', 'bootstrap/%s.tx' % load, '--out', str(d / ('out-' + load))], a.src_root)
        if r['exit'] != 0:
            raise RuntimeError('%s 的 C 编 %s 失败（退出码 %d）' % (commit, load, r['exit']))
        row['loads'][load] = {k: r[k] for k in ('instructions', 'cycles', 'peak_kib')}
    shutil.rmtree(src, ignore_errors=True)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--start', type=Path, required=True, help='能编这些提交源码的编译器（Linux）')
    ap.add_argument('--src-root', type=Path, required=True, help='负载源码树（所有 C 都能编）')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--loads', default='parser,semamirror')
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('commits', nargs='+', help='按时间顺序；第一个作基准')
    a = ap.parse_args()
    if not sys.platform.startswith('linux'):
        ap.error('instructions:u 只在 Linux 上可得')
    a.start, a.src_root, a.work = a.start.resolve(), a.src_root.resolve(), a.work.resolve()
    a.work.mkdir(parents=True, exist_ok=True)
    meter = perf_compare.LinuxCounters()

    def safe(commit):
        try:
            return measure(commit, a, meter)
        except Exception as e:  # 记下失败，其余提交照测
            return {'commit': commit, 'error': str(e)[-800:]}
    with ThreadPoolExecutor(a.jobs) as pool:
        rows = list(pool.map(safe, a.commits))
    (a.work / 'attribution.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    first = prev = None
    for row in rows:
        if 'error' in row:
            print(row['commit'], 'ERROR', row['error'][-300:], flush=True)
            continue
        ins = {k: v['instructions'] for k, v in row['loads'].items()}
        first = first or ins
        prev = prev or ins
        print(row['commit'], row['c_sha256'][:12], '  '.join(
            '%s %.4e（对首个 %+.2f%%，对上一个 %+.2f%%）' % (k, v, (v / first[k] - 1) * 100, (v / prev[k] - 1) * 100)
            for k, v in ins.items()), flush=True)
        prev = ins
    return 1 if any('error' in r for r in rows) else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
