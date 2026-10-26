"""新旧编译器的编译耗时对照（ABBA 交替）：判定指标 Linux 用用户态退休指令数 instructions:u，macOS 用 proc_pid_rusage 的
退休指令数，Windows 用子进程 CPU 周期。

每个负载是 `<编译器> build <源> --out <目录>`；两臂按 ABBA 交替运行，各取中位数，按负载单独判定
（候选/基线 − 1 不超过 --threshold 百分比）。cycles:u、墙钟与峰值内存只记录不判定。--aa 让两臂都用基线，
测同一二进制的噪声，用来校准门限。结果写 --report（JSON），不进 Git。协议见
docs/design/v052-perf-measurement-v0.1-20261023.md。
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
LOADS = {'lexer': ['bootstrap/lexer.tx'], 'parser': ['bootstrap/parser.tx'], 'semamirror': ['bootstrap/semamirror.tx'],
         'self': ['bootstrap/taixu.tx', '--toolchain']}
LINUX = sys.platform.startswith('linux')


class LinuxCounters:
    """perf_event_open（x86_64 系统调用 298）：用户态 instructions 与 cycles，随 exec 启用、继承到子线程。
    读数带启用与实际计数的时长；两者不等说明计数器被多路复用、读数是内核按比例估的，该样本作废。"""
    SYS_PERF_EVENT_OPEN = 298
    FLAGS = (1 << 0) | (1 << 1) | (1 << 5) | (1 << 6) | (1 << 12)  # disabled inherit exclude_kernel exclude_hv enable_on_exec
    READ_FORMAT = 1 | 2  # TOTAL_TIME_ENABLED TOTAL_TIME_RUNNING

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)

    def open(self, pid, config):
        buf = bytearray(128)
        struct.pack_into('<IIQQQQQ', buf, 0, 0, 128, config, 0, 0, self.READ_FORMAT, self.FLAGS)
        attr = (ctypes.c_char * 128).from_buffer(buf)
        fd = self.libc.syscall(self.SYS_PERF_EVENT_OPEN, ctypes.byref(attr), pid, -1, -1, 0)
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, 'perf_event_open: ' + os.strerror(err))
        return fd

    def run(self, argv, cwd):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(w)
                os.read(r, 1)
                os.chdir(cwd)
                devnull = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1):
                    os.dup2(devnull, fd)
                os.execv(argv[0], argv)
            finally:
                os._exit(127)
        os.close(r)
        fds = [self.open(pid, 1), self.open(pid, 0)]  # PERF_COUNT_HW_INSTRUCTIONS, PERF_COUNT_HW_CPU_CYCLES
        t0 = time.perf_counter()
        os.write(w, b'x')
        os.close(w)
        _, status, usage = os.wait4(pid, 0)
        wall = time.perf_counter() - t0
        (ins, ins_on, ins_run), (cyc, cyc_on, cyc_run) = (struct.unpack('<QQQ', os.read(fd, 24)) for fd in fds)
        for fd in fds:
            os.close(fd)
        if ins_on != ins_run or cyc_on != cyc_run:
            raise SystemExit('PMU 计数器被多路复用（instructions %d/%d，cycles %d/%d ns），读数不是精确计数；'
                             '先停掉占用计数器的进程（如 perf、NMI watchdog）' % (ins_run, ins_on, cyc_run, cyc_on))
        return {'exit': os.waitstatus_to_exitcode(status), 'instructions': ins, 'cycles': cyc, 'wall_s': wall,
                'cpu_s': usage.ru_utime + usage.ru_stime, 'peak_kib': usage.ru_maxrss}


class WindowsCounters:
    """子进程的 CPU 周期（QueryProcessCycleTime：各线程在 CPU 上时按 TSC 计的周期，含内核态）、
    CPU 时间（GetProcessTimes，按 15.625 ms 的时钟节拍量化，只记录）与峰值工作集。"""

    def __init__(self):
        import ctypes.wintypes as w
        self.w = w
        self.k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.psapi = ctypes.WinDLL('psapi', use_last_error=True)

    def run(self, argv, cwd):
        w = self.w
        t0 = time.perf_counter()
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        p.wait()
        wall = time.perf_counter() - t0
        times = [w.FILETIME() for _ in range(4)]
        if not self.k32.GetProcessTimes(int(p._handle), *[ctypes.byref(t) for t in times]):
            raise OSError(ctypes.get_last_error(), 'GetProcessTimes')
        ticks = lambda t: (t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7
        cycles = ctypes.c_ulonglong()
        if not self.k32.QueryProcessCycleTime(int(p._handle), ctypes.byref(cycles)):
            raise OSError(ctypes.get_last_error(), 'QueryProcessCycleTime')
        # PROCESS_MEMORY_COUNTERS（x64，72 字节）：cb 与 PageFaultCount 两个 DWORD 合占第 0 个字，第 1 个字是 PeakWorkingSetSize。
        counters = (ctypes.c_size_t * 9)()
        counters[0] = ctypes.sizeof(counters)
        if not self.psapi.GetProcessMemoryInfo(int(p._handle), counters, ctypes.sizeof(counters)):
            raise OSError(ctypes.get_last_error(), 'GetProcessMemoryInfo')
        return {'exit': p.returncode, 'cycle_time': cycles.value, 'cpu_s': ticks(times[2]) + ticks(times[3]),
                'wall_s': wall, 'peak_kib': counters[1] // 1024}


class DarwinCounters:
    """proc_pid_rusage(RUSAGE_INFO_V4)：子进程退出后、回收之前（waitid 带 WNOWAIT）取 ri_instructions 与 ri_cycles（Apple 芯片
    由性能计数器给出，不需要 root；Rosetta 下数的是翻译后的 arm64 指令，跨架构对照改用 cycles）。CPU 时间与峰值常驻取 wait4 的
    rusage（macOS 的 ru_maxrss 以字节计）。偏移按 Apple 的 sys/resource.h：rusage_info_v4 共 296 字节，instructions@248、
    cycles@256（测试机上以 clang 的 offsetof 核对）。同一二进制两次运行的 instructions 相差约 0.01%。
    macOS 的 Python 没有 os.waitid，经 libSystem 调用：P_PID 1、WEXITED 4、WNOWAIT 32、siginfo_t 104 字节（同样以 clang 核对）。"""
    SIZE, INSTRUCTIONS = 296, 248
    P_PID, WEXITED, WNOWAIT, SIGINFO = 1, 4, 32, 104

    def __init__(self):
        self.libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)

    def run(self, argv, cwd):
        t0 = time.perf_counter()
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        info = ctypes.create_string_buffer(self.SIGINFO)
        while self.libc.waitid(self.P_PID, p.pid, info, self.WEXITED | self.WNOWAIT) != 0:
            if ctypes.get_errno() != 4:  # EINTR 重试
                raise OSError(ctypes.get_errno(), 'waitid')
        wall = time.perf_counter() - t0
        buf = ctypes.create_string_buffer(self.SIZE)
        if self.libc.proc_pid_rusage(p.pid, 4, buf) != 0:
            raise OSError(ctypes.get_errno(), 'proc_pid_rusage')
        ins, cyc = struct.unpack_from('<QQ', buf, self.INSTRUCTIONS)
        _, status, usage = os.wait4(p.pid, 0)
        p.returncode = os.waitstatus_to_exitcode(status)
        return {'exit': p.returncode, 'instructions': ins, 'cycles': cyc, 'wall_s': wall,
                'cpu_s': usage.ru_utime + usage.ru_stime, 'peak_kib': usage.ru_maxrss // 1024}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--base', type=Path, required=True, help='基线编译器（如 main 的不动点）')
    ap.add_argument('--cand', type=Path, help='候选编译器；--aa 时不用')
    ap.add_argument('--aa', action='store_true', help='两臂都用基线，测噪声')
    ap.add_argument('--loads', default='lexer,parser,semamirror', help='逗号分隔：' + ','.join(LOADS))
    ap.add_argument('--runs', type=int, default=None, help='每臂样本数（缺省 Linux 3、Windows 11）')
    ap.add_argument('--warmup', type=int, default=1, help='每负载每臂先跑几次不计')
    ap.add_argument('--threshold', type=float, default=3.0, help='判定门限（百分比）')
    ap.add_argument('--target', help='两臂都加 --target=<写法>（如两种架构的编译器编同一目标，产物相同）')
    ap.add_argument('--metric', help='判定指标（缺省 Linux、macOS 为 instructions，Windows 为 cycle_time）；跨架构对照用 cycles')
    ap.add_argument('--report', type=Path)
    ap.add_argument('--src-root', type=Path, default=ROOT,
                    help='负载源码所在的仓库根（缺省本仓）。两臂编同一份源码；基线编译器编不了新源码时（如用了它没有的内建）'
                         '指向基线的源码树，例如 git archive 展开的目录')
    a = ap.parse_args()
    src_root = a.src_root.resolve()
    if not a.aa and a.cand is None:
        ap.error('需要 --cand，或用 --aa 测噪声')
    arms = {'A': a.base.resolve(), 'B': (a.base if a.aa else a.cand).resolve()}
    darwin = sys.platform == 'darwin'
    meter = LinuxCounters() if LINUX else (DarwinCounters() if darwin else WindowsCounters())
    metric = a.metric or ('instructions' if LINUX or darwin else 'cycle_time')
    runs = a.runs or (3 if LINUX or darwin else 11)
    target = ['--target=' + a.target] if a.target else []
    work = Path(tempfile.mkdtemp(prefix='taixu-perf-'))
    report = {'metric': metric, 'threshold_pct': a.threshold, 'runs': runs, 'aa': a.aa, 'src_root': str(src_root),
              'arms': {k: str(v) for k, v in arms.items()}, 'loads': {}}
    failed = False
    try:
        for name in a.loads.split(','):
            src = LOADS[name]
            samples = {'A': [], 'B': []}

            def once(arm):
                out = work / ('%s-%s' % (name, arm))
                shutil.rmtree(out, ignore_errors=True)
                row = meter.run([str(arms[arm]), 'build', *src, *target, '--out', str(out)], src_root)
                if row['exit'] != 0:
                    raise SystemExit('%s 臂 %s 编译 %s 失败（退出码 %d）' % (arm, arms[arm], name, row['exit']))
                return row

            for _ in range(a.warmup):
                once('A'); once('B')
            for i in range(runs):
                for arm in ('AB' if i % 2 == 0 else 'BA'):
                    samples[arm].append(once(arm))
            med = {arm: statistics.median(r[metric] for r in rows) for arm, rows in samples.items()}
            spread = {arm: (max(r[metric] for r in rows) - min(r[metric] for r in rows)) / med[arm] * 100
                      for arm, rows in samples.items()}
            delta = (med['B'] / med['A'] - 1) * 100
            ok = delta <= a.threshold
            failed = failed or not ok
            extra = {k: {arm: statistics.median(r[k] for r in rows) for arm, rows in samples.items()}
                     for k in samples['A'][0] if k not in ('exit', metric)}
            report['loads'][name] = {'median': med, 'delta_pct': delta, 'spread_pct': spread, 'pass': ok,
                                     'secondary_median': extra, 'samples': samples}
            print('%s %-10s %s %+.3f%%（基线 %.6g，候选 %.6g；臂内极差 A %.3f%% B %.3f%%）%s' % (
                'PASS' if ok else 'FAIL', name, metric, delta, med['A'], med['B'], spread['A'], spread['B'],
                '；' + '，'.join('%s %+.2f%%' % (k, (v['B'] / v['A'] - 1) * 100) for k, v in extra.items() if v['A'])),
                flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report['pass'] = not failed
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print('SUMMARY', 'PASS' if not failed else 'FAIL')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
