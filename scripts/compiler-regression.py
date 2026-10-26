"""同源 ABBA/BAAB 编译耗时回归；两臂共用当前内核，产物须逐字节相同。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def select_cpu(cpu):
    """仅限制本编排进程及其子进程；每个原生子进程另作实际核对。"""
    if cpu is None:
        return {'mode': 'inherited'}, None
    if os.name == 'nt':
        import ctypes as c
        from ctypes import wintypes as w
        k = c.WinDLL('kernel32', use_last_error=True)
        k.GetCurrentProcess.restype = w.HANDLE
        k.GetProcessAffinityMask.argtypes = [w.HANDLE, c.POINTER(c.c_size_t), c.POINTER(c.c_size_t)]
        k.GetProcessAffinityMask.restype = w.BOOL
        k.SetProcessAffinityMask.argtypes = [w.HANDLE, c.c_size_t]
        k.SetProcessAffinityMask.restype = w.BOOL

        def read(handle):
            current, system = c.c_size_t(), c.c_size_t()
            if not k.GetProcessAffinityMask(handle, c.byref(current), c.byref(system)):
                raise c.WinError(c.get_last_error())
            return [i for i in range(c.sizeof(current) * 8) if current.value & (1 << i)]

        own = k.GetCurrentProcess()
        before = read(own)
        if cpu not in before:
            raise ValueError('CPU 不在当前允许集合: ' + str(before))
        if not k.SetProcessAffinityMask(own, 1 << cpu):
            raise c.WinError(c.get_last_error())
        child = lambda p: read(w.HANDLE(int(p._handle)))
        after = read(own)
    else:
        before = sorted(os.sched_getaffinity(0))
        if cpu not in before:
            raise ValueError('CPU 不在当前允许集合: ' + str(before))
        os.sched_setaffinity(0, {cpu})
        after = sorted(os.sched_getaffinity(0))
        child = lambda p: sorted(os.sched_getaffinity(p.pid))
    if after != [cpu]:
        raise RuntimeError('CPU 亲和性未生效: ' + str(after))
    return {'mode': 'fixed', 'cpu': cpu, 'before': before, 'parent_actual': after}, child


def build(argv, env, read_affinity, cpu):
    if read_affinity is None:
        p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=120)
        return p.returncode, p.stderr, None
    p = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        actual = read_affinity(p)
        if actual != [cpu]:
            raise RuntimeError('子进程未继承指定 CPU: ' + str(actual))
        _, stderr = p.communicate(timeout=120)
    except BaseException:
        if p.poll() is None:
            p.kill()
        p.communicate()
        raise
    return p.returncode, stderr, actual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', type=Path, required=True)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--kernel', type=Path, required=True)
    ap.add_argument('--runs', type=int, default=7)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--only', choices=['lexer', 'all'], default='all')
    ap.add_argument('--cpu', type=int, help='可选：两臂固定在该逻辑 CPU，验证每个子进程的实际集合')
    a = ap.parse_args()
    assert a.runs >= 5
    cpu_policy, read_affinity = select_cpu(a.cpu)
    work = a.work.resolve()
    assert not work.exists(), 'Fresh output directory required'
    work.mkdir(parents=True)
    empty = work / 'empty-path'; empty.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_')}
    env['PATH'] = str(empty)
    arms = {'old': a.baseline.resolve(), 'new': a.compiler.resolve()}
    kernel = a.kernel.resolve()
    exe = '.exe' if os.name == 'nt' else ''
    doc = {'compilers': {k: sha(p) for k, p in arms.items()}, 'N': a.runs,
           'kernel_sha256': sha(kernel), 'host': 'windows' if os.name == 'nt' else 'linux',
           'order': 'ABBA, then BAAB; one excluded warmup per arm and load',
           'clock': 'perf_counter_ns spawn through exit', 'path': 'empty',
           'cpu_policy': cpu_policy, 'priority_policy': 'unchanged', 'loads': {}}
    loads = ('bootstrap/lexer.tx',) if a.only == 'lexer' else ('bootstrap/lexer.tx', 'bootstrap/parser.tx', 'bootstrap/semamirror.tx')
    for load in loads:
        values = {'old': [], 'new': []}
        rows = []
        expected = None
        for r in range(-1, a.runs):
            order = ['old', 'new'] if r == -1 else (['old', 'new', 'new', 'old'] if r % 2 == 0 else ['new', 'old', 'old', 'new'])
            for k, arm in enumerate(order):
                dest = work / Path(load).stem / ('%d-%d-%s' % (r, k, arm))
                dest.mkdir(parents=True, exist_ok=True)
                start = time.perf_counter_ns()
                code, stderr, affinity = build([str(arms[arm]), 'build', load, '--link-object', str(kernel), '--out', str(dest)],
                                               env, read_affinity, a.cpu)
                ns = time.perf_counter_ns() - start
                if code != 0:
                    raise RuntimeError(stderr.decode('utf-8', 'replace'))
                digest = sha(dest / (Path(load).stem + exe))
                if expected is None:
                    expected = digest
                assert digest == expected, (load, arm, digest, expected)
                row = {'round': r, 'arm': arm, 'ns': ns, 'sha': digest}
                if affinity is not None:
                    row['cpu_affinity'] = affinity
                rows.append(row)
                if r >= 0:
                    values[arm].append(ns)
        med = {arm: statistics.median(v) for arm, v in values.items()}
        ratio = med['new'] / med['old']
        doc['loads'][load] = {'input_sha': sha(ROOT / load), 'samples': rows, 'medians_ns': med,
                              'ratio': ratio, 'pass': ratio <= 1.03}
        (work / 'report.json').write_text(json.dumps(doc, indent=2), encoding='utf-8')
        print(load, 'ratio', ratio, 'delta', (ratio - 1) * 100, 'PASS' if ratio <= 1.03 else 'FAIL', flush=True)
    doc['status'] = 'PASS' if all(x['pass'] for x in doc['loads'].values()) else 'FAIL'
    (work / 'report.json').write_text(json.dumps(doc, indent=2), encoding='utf-8')
    print('STATUS', doc['status'], flush=True)
    return int(doc['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
