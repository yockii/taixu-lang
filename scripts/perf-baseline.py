"""性能基线（docs/design/v052-perf-baseline-v0.1-20261026.md）：用一个编译器测编译耗时套件与生成代码套件，结果追加到
bench/baseline/<宿主>.jsonl（每次一行，不改写旧行）。计数沿用 scripts/perf-compare.py 的测量：Linux instructions:u（perf_event_open），
macOS proc_pid_rusage 的退休指令数，Windows 子进程 CPU 周期（QueryProcessCycleTime，只记录）。

编译套件（`<编译器> build <源> --out <目录>`）：
  主基准  self（bootstrap/taixu.tx --toolchain）、lexer、parser、semamirror、hello（examples/hello.tx）
  次基准  运行时现生成的压力输入（生成规则带版本号 STRESS_VERSION，改了规则须加一）：
          big-function（一个函数里 3000 条语句，压活跃与逃逸分析及其预算）、many-modules（200 个模块链式导入，压模块发现与链接）、
          deep-nesting（嵌套 64 层的 if 与循环，压控制流）
生成代码套件：bench/algo 的九个负载（D50 的同构移植，校验和与 C 版逐字节一致才计入）；同机的参考实现——C（cc -O2 -ffp-contract=off，
bench/algo/cmp/cmp.c）必测，Go（cmp.go）、Rust（cmp.rs）在 PATH 上找得到才测，找不到记“未测”。参考只作定位，不作门。

  python scripts/perf-baseline.py --compiler <taixu> [--runs N] [--suite compile,runtime] [--stdlib <标准库>] [--no-record]
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('perf_compare', ROOT / 'scripts' / 'perf-compare.py')
perf_compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf_compare)

LINUX = sys.platform.startswith('linux')
DARWIN = sys.platform == 'darwin'
WINDOWS = os.name == 'nt'
EXE = '.exe' if WINDOWS else ''
STRESS_VERSION = 1
PRIMARY = {'self': ['bootstrap/taixu.tx', '--toolchain'], 'lexer': ['bootstrap/lexer.tx'], 'parser': ['bootstrap/parser.tx'],
           'semamirror': ['bootstrap/semamirror.tx'], 'hello': ['examples/hello.tx']}
LOADS = ['fib-rec', 'fib-iter', 'qsort', 'sieve', 'mandelbrot', 'matmul', 'nbody', 'string-build-find', 'json-roundtrip']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def big_function(d):
    lines = ['export function main(args: []string): int {', '    let a = len(args);', '    let b = 1;', '    let s = 0;']
    for i in range(3000):
        k = i % 4
        if k == 0:
            lines.append('    a = a + %d;' % (i % 97))
        elif k == 1:
            lines.append('    if (a > %d) { b = b + a; } else { b = b - 1; }' % (i % 89))
        elif k == 2:
            lines.append('    s = s + (a ^ b) %% %d;' % (i % 83 + 1))
        else:
            lines.append('    const t%d = s + b;' % i)
            lines.append('    s = t%d %% 1000003;' % i)
    lines += ['    print(toStr(s));', '    return 0;', '}']
    (d / 'big-function.tx').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    return [str(d / 'big-function.tx')]


def many_modules(d):
    n = 200
    (d / 'm0.tx').write_text('export function f0(x: int): int {\n    return x + 1;\n}\n', encoding='utf-8', newline='\n')
    for i in range(1, n):
        (d / ('m%d.tx' % i)).write_text('import { f%d } from "./m%d";\n\nexport function f%d(x: int): int {\n    return f%d(x) + %d;\n}\n'
                                         % (i - 1, i - 1, i, i - 1, i), encoding='utf-8', newline='\n')
    (d / 'many-modules.tx').write_text('import { f%d } from "./m%d";\n\nexport function main(args: []string): int {\n'
                                        '    print(toStr(f%d(len(args))));\n    return 0;\n}\n' % (n - 1, n - 1, n - 1),
                                        encoding='utf-8', newline='\n')
    return [str(d / 'many-modules.tx')]


def deep_nesting(d):
    depth = 64
    body = ['export function main(args: []string): int {', '    let s = 0;', '    let i = 0;']
    for k in range(depth):
        pad = '    ' * (k + 1)
        body.append(pad + ('if (i < %d) {' % (1000 + k) if k % 2 == 0 else 'while (s < %d) {' % (k * 3 + 1)))
        body.append(pad + '    s = s + %d;' % (k + 1))
    for k in reversed(range(depth)):
        body.append('    ' * (k + 1) + '}')
    body += ['    print(toStr(s));', '    return 0;', '}']
    (d / 'deep-nesting.tx').write_text('\n'.join(body) + '\n', encoding='utf-8', newline='\n')
    return [str(d / 'deep-nesting.tx')]


SECONDARY = {'big-function': big_function, 'many-modules': many_modules, 'deep-nesting': deep_nesting}


def summary(rows):
    keys = [k for k in rows[0] if k != 'exit']
    med = {k: statistics.median(r[k] for r in rows) for k in keys}
    spread = {k: ((max(r[k] for r in rows) - min(r[k] for r in rows)) / med[k] * 100 if med[k] else 0.0) for k in keys}
    return {'median': med, 'spread_pct': spread, 'samples': rows}


def measure(meter, argv, cwd, runs, warmup, label):
    for _ in range(warmup):
        meter.run(argv, cwd)
    rows = []
    for _ in range(runs):
        row = meter.run(argv, cwd)
        if row['exit'] != 0:
            raise SystemExit('%s 失败（退出码 %d）：%s' % (label, row['exit'], ' '.join(map(str, argv))))
        rows.append(row)
    return summary(rows)


def profile_facts(compiler, src):
    """不计时的一次 profile compile：分析预算命中与文本中转（A′、BOOT-50′ 的度量）及阶段墙钟；旧编译器没有的字段记 None。"""
    p = subprocess.run([str(compiler), 'profile', 'compile', *src, '--format=json', '--top', '0'], cwd=ROOT, capture_output=True)
    try:
        x = json.loads(p.stdout)['profile']
    except (ValueError, KeyError, TypeError):
        return None
    if x is None:
        return None
    s, hops = x['summary'], x.get('text_hops', {})
    num = lambda v: int(v) if v is not None else None
    return {'functions': num(s.get('functions_total')), 'analysis_skipped_functions': num(s.get('analysis_skipped_functions')),
            'analysis_max_cells': num(s.get('analysis_max_cells')), 'analysis_budget_cells': num(s.get('analysis_budget_cells')),
            'seb1_utf8_bytes': num(hops.get('seb1_utf8_bytes')), 'fab1_utf8_bytes': num(hops.get('fab1_utf8_bytes')),
            'record_utf8_bytes': num(s.get('record_utf8_bytes')),
            'phases_ns': {q['name']: int(q['wall_ns']) for q in x.get('phases', [])},
            'codegen_details_ns': {q['name']: int(q['wall_ns']) for q in x.get('codegen_details', [])}}


def git_commit():
    p = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ''


def references(work):
    """同机的参考实现：C 必备，Go、Rust 找得到才编。返回 {语言: 可执行文件或 None}。"""
    src = ROOT / 'bench' / 'algo' / 'cmp'
    refs = {}
    cc = os.environ.get('TAIXU_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    refs['c'] = None
    if cc:
        out = work / ('cmp-c' + EXE)
        # -ffp-contract=off：不把乘加融合成 FMA，按 IEEE 逐步舍入（同太虚）；Apple clang 默认融合，arm64 上 nbody 的结果会变。
        if subprocess.run([cc, '-O2', '-ffp-contract=off', '-o', str(out), str(src / 'cmp.c')], capture_output=True).returncode == 0:
            refs['c'] = out
    go = shutil.which('go')
    refs['go'] = None
    if go:
        out = work / ('cmp-go' + EXE)
        if subprocess.run([go, 'build', '-o', str(out), str(src / 'cmp.go')], capture_output=True, cwd=work).returncode == 0:
            refs['go'] = out
    rustc = shutil.which('rustc')
    refs['rust'] = None
    if rustc:
        out = work / ('cmp-rs' + EXE)
        if subprocess.run([rustc, '-O', '-o', str(out), str(src / 'cmp.rs')], capture_output=True).returncode == 0:
            refs['rust'] = out
    return refs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--runs', type=int, default=None, help='每项样本数（缺省 Linux、macOS 3，Windows 11）')
    ap.add_argument('--warmup', type=int, default=1)
    ap.add_argument('--suite', default='compile,runtime')
    ap.add_argument('--stdlib', type=Path, default=ROOT / 'stdlib' if (ROOT / 'stdlib/src').is_dir() else ROOT.parent / 'taixu-stdlib',
                    help='标准库仓（json-roundtrip 用到 std/；公开仓为仓内 stdlib/）')
    ap.add_argument('--record', type=Path, help='追加到的基线记录（缺省 bench/baseline/<宿主>.jsonl）')
    ap.add_argument('--no-record', action='store_true', help='只打印，不追加记录')
    ap.add_argument('--only', help='逗号分隔的用例名，只测这些（试跑用；记录照样标明测了哪些）')
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    meter = perf_compare.LinuxCounters() if LINUX else (perf_compare.DarwinCounters() if DARWIN else perf_compare.WindowsCounters())
    metric = 'instructions' if LINUX or DARWIN else 'cycle_time'
    runs = a.runs or (3 if LINUX or DARWIN else 11)
    host = ('linux' if LINUX else ('macos' if DARWIN else 'windows')) + '-' + platform.machine().lower()
    os.environ['TAIXU_STDLIB'] = str((a.stdlib / 'src').resolve())
    work = Path(tempfile.mkdtemp(prefix='taixu-baseline-'))
    record = {'schema': 'perf-baseline/1', 'date': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
              'host': host, 'commit': git_commit(), 'compiler_sha256': sha(compiler), 'metric': metric, 'runs': runs,
              'stress_version': STRESS_VERSION, 'only': a.only or '', 'compile': {}, 'runtime': {}, 'references': {}}
    suites = a.suite.split(',')
    try:
        if 'compile' in suites:
            stress = work / 'stress'
            stress.mkdir()
            cases = [(name, 'primary', src) for name, src in PRIMARY.items()]
            cases += [(name, 'secondary', make(stress)) for name, make in SECONDARY.items()]
            for name, tier, src in cases:
                if a.only and name not in a.only.split(','):
                    continue
                out = work / ('out-' + name)
                row = measure(meter, [str(compiler), 'build', *src, '--out', str(out)], ROOT, runs, a.warmup, name)
                row['tier'] = tier
                row['profile'] = profile_facts(compiler, src)
                record['compile'][name] = row
                print('compile %-14s %-9s %s %.6g（极差 %.3f%%）' % (name, tier, metric, row['median'][metric], row['spread_pct'][metric]), flush=True)
        if 'runtime' in suites:
            refs = references(work)
            record['references'] = {k: (sha(v) if v else None) for k, v in refs.items()}
            for load in LOADS:
                if a.only and load not in a.only.split(','):
                    continue
                out = work / ('rt-' + load)
                b = subprocess.run([str(compiler), 'build', str(ROOT / 'bench' / 'algo' / (load + '.tx')), '--out', str(out)],
                                   cwd=ROOT, capture_output=True)
                exe = out / (load + EXE)
                if b.returncode != 0 or not exe.exists():
                    raise SystemExit('编不了 %s：%s' % (load, b.stderr.decode('utf-8', 'replace')[-400:]))
                got = subprocess.run([str(exe)], capture_output=True, cwd=work).stdout
                entry = {'product_bytes': exe.stat().st_size, 'product_sha256': sha(exe), 'refs': {}}
                expect = subprocess.run([str(refs['c']), load], capture_output=True).stdout if refs['c'] else None
                entry['checksum_matches_c'] = expect is not None and got.replace(b'\r\n', b'\n') == expect.replace(b'\r\n', b'\n')
                entry.update(measure(meter, [str(exe)], work, runs, a.warmup, load))
                for lang, ref in refs.items():
                    entry['refs'][lang] = measure(meter, [str(ref), load], work, runs, a.warmup, lang + ' ' + load) if ref else None
                record['runtime'][load] = entry
                ratio = ''
                if entry['refs'].get('c'):
                    ratio = '，对 C %.2f×' % (entry['median'][metric] / entry['refs']['c']['median'][metric])
                print('runtime %-18s %s %.6g%s%s' % (load, metric, entry['median'][metric], ratio,
                                                     '' if entry['checksum_matches_c'] else '（校验和与 C 不同）'), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not a.no_record:
        path = a.record or (ROOT / 'bench' / 'baseline' / (host + '.jsonl'))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
        print('追加到', path)
    bad = [k for k, v in record['runtime'].items() if not v['checksum_matches_c']]
    print('SUMMARY', 'PASS' if not bad else 'FAIL 校验和与 C 不同：' + ','.join(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
