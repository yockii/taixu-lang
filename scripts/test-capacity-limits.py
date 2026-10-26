"""容量与编码上限门（docs/design/v052-capacity-limits-review-v0.1-20261016.md）：超出上限的程序必须得到诊断，
不得静默生成错误代码；上限之内（含越过旧上限）的程序须正确运行。源码现场生成（不入库，体积大）。

原生链（TXC v6；C3a 之后）：
- 越过旧 7 位、8 位、16 位编码上限的程序须编译成功且输出正确：300 个模块顶层绑定、200 个 struct 字段、
  200 个 enum 变体字段、130 个闭包捕获、7 万个常量（Bx 32 位）、4 万条指令的循环体（sBx 32 位）、
  第 300 个外部函数（callbi 编号越过 255）、200 层泛型实例化嵌套；单函数 300、5000、6 万个局部（寄存器越过
  旧上限 256，C3b）、300 个参数的函数与箭头函数（越过旧上限 254，C3b）；
- 负例须报诊断、不产出：单函数 7 万个局部即寄存器超过 65535 个（3101）、泛型实例化嵌套超过 256 层（3020）、
  泛型实例类型规模指数膨胀（3020，须在时限内给出，不得耗尽内存）。
- 编译时间（C3c）：大输入须在 120 秒内编完并正确运行——4 万语句循环体、1.6 万个函数、3.2 万个顶层绑定、
  8 万个字符串常量、6 万个变体的 enum（本机各约 7–15 秒；任一处退回平方级即在数分钟以上）。
- 运行时间（字符串原地追加）：三种逐段累加写法各 40 万轮须在 120 秒内跑完且长度正确（本机约 1 秒；
  退回整串复制即在数分钟以上）。
- 文件与输出（C4b）：128 MiB 的串写出后经 readFile、readFileBytes 读回须完整一致（原先 64 MiB 上限处得错误值）；
  2400 万字节（800 万个“太”）的一行输出到标准输出须逐字节正确（Windows 分块写出）。
- 异步任务（C4d）：一个任务先后启动并等待 200 万个任务（每个先 sleepMs(0) 再返回），结果须正确、峰值常驻内存
  不超过 64 MiB（任务记录回收；原先记录只增不减。本机 Windows 峰值约 227 MiB → 约 7 MiB，与 hello 相当；Linux
  约 221 MiB → 约 7 MiB）。
历史 Zig v4 上限检查已随对应实现退场；当前门只检查原生链。
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(argv, cwd, timeout=900):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).decode('utf-8', 'replace')


def run_peak(argv, cwd, timeout=120):
    """运行并取峰值常驻内存（字节）：Windows 为 PeakWorkingSetSize，POSIX 为 wait4 的 ru_maxrss。"""
    argv = [str(x) for x in argv]
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + \
                [(n, ctypes.c_size_t) for n in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                                                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                                                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate(timeout=timeout)
        c = Counters()
        c.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL('psapi')
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        ok = psapi.GetProcessMemoryInfo(wintypes.HANDLE(int(p._handle)), ctypes.byref(c), c.cb)
        return p.returncode, out.decode('utf-8', 'replace'), c.PeakWorkingSetSize if ok else -1
    # Linux 的 ru_maxrss 在 exec 时并入发起 fork 的那份地址空间的峰值：直接从本进程（跑过前面的大用例，
    # 常驻上百 MiB）fork 会把它算到子进程头上。改由一个新起的小 Python 进程 fork、exec 并 wait4（它本身不大：
    # 父进程压着 150 MiB 时，新版读数仍约 7 MiB）。结果经文件传回（子进程的标准输出、错误照常收集）。
    note = Path(cwd) / '.peak-rss'
    helper = ('import os, sys\npid = os.fork()\nif pid == 0:\n    os.execv(sys.argv[2], sys.argv[2:])\n'
              '_, st, ru = os.wait4(pid, 0)\nopen(sys.argv[1], "w").write("%d %d" % (os.waitstatus_to_exitcode(st), ru.ru_maxrss))\n')
    p = subprocess.run([sys.executable, '-c', helper, str(note)] + argv, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout)
    rc, maxrss = (int(x) for x in note.read_text().split())
    note.unlink()
    # ru_maxrss 的单位：Linux 为 KiB，macOS 为字节（man 2 getrusage）。
    return rc, p.stdout.decode('utf-8', 'replace'), maxrss if sys.platform == 'darwin' else maxrss * 1024


def main_wrap(body, prints):
    return '\n'.join(['export function main(args: []string): int {'] + ['    ' + x for x in body]
                     + ['    print(' + prints + ');', '    return 0;', '}']) + '\n'


def globals_src(n):
    lines = [f'let g{i}: int = {i * 10};' for i in range(n)]
    last = n - 1
    return '\n'.join(lines) + '\n\n' + main_wrap([f'g{last} = 7;'], f'toStr(g0) + " " + toStr(g1) + " " + toStr(g{last})')


def consts_src(n):
    items = ', '.join(str(i) for i in range(n))
    return main_wrap([f'const t = [{items}];'], f'toStr(t[0]) + " " + toStr(t[{n - 1}])')


def jumps_src(lines):
    body = '\n'.join('        x = x + 1;' for _ in range(lines))
    return ('export function main(args: []string): int {\n    let x = 0;\n    let i = 0;\n    while (i < 3) {\n'
            f'{body}\n        i = i + 1;\n    }}\n    print(toStr(x) + " " + toStr(i));\n    return 0;\n}}\n')


def enum_src(n):
    fs = ', '.join(f'f{i}: int' for i in range(n))
    args = ', '.join(str(i) for i in range(n))
    pat = ', '.join(f'f{i}' for i in range(n))
    lines = ['enum E {', f'    A({fs}),', '    B,', '}', '',
             'function pick(e: E): int {', '    return switch (e) {',
             f'        case A({pat}) => f0 + f{n - 1} * 1000,', '        else => 0,', '    };', '}', '']
    return '\n'.join(lines) + '\n' + main_wrap([], f'toStr(pick(E.A({args})))')


def struct_src(n):
    fs = '\n'.join(f'    f{i}: int,' for i in range(n))
    init = ', '.join(f'f{i}: {i}' for i in range(n))
    return (f'struct S {{\n{fs}\n}}\n\n'
            + main_wrap([f'let s = S {{ {init} }};', f's.f{n - 1} = s.f{n - 1} + 1;'],
                        f'toStr(s.f0) + " " + toStr(s.f{n - 1})'))


def captures_src(n):
    decls = [f'const c{i} = {i};' for i in range(n)]
    total = ' + '.join(f'c{i}' for i in range(n))
    return main_wrap(decls + [f'const f = (): int => {total};'], 'toStr(f())')


def locals_src(n):
    decls = [f'let v{i} = {i};' for i in range(n)]
    total = ' + '.join(f'v{i}' for i in range(n))
    return main_wrap(decls, f'toStr({total})')


def locals_shallow_src(n):
    # 局部多而表达式浅（深的连加链受编译器自身的递归深度限制，另属原生栈预算）。
    decls = [f'let v{i} = {i};' for i in range(n)]
    return main_wrap(decls, f'toStr(v0 + v{n - 1})')


def params_src(n):
    ps = ', '.join(f'p{i}: int' for i in range(n))
    total = ' + '.join(f'p{i}' for i in range(n))
    args = ', '.join(str(i) for i in range(n))
    return (f'function big({ps}): int {{\n    return {total};\n}}\n\n'
            + main_wrap([f'const f = ({ps}): int => p0 + p{n - 1};', f'const r = big({args}) + f({args});'], 'toStr(r)'))


def externs_src(n, host):
    if host == 'linux':
        decls = [f'declare function x{i}(): i32 from syscall 39;' for i in range(n)]
        call = f'x{n - 1}() > 0'
    elif host == 'macos':
        # macOS 不提供稳定的系统调用接口（D3 §6.8-7）：经 libSystem；只声明不调用的不链接（§6.8-9）。
        decls = [f'declare function x{i}(): i32 from "libSystem";' for i in range(n - 1)]
        decls.append('declare function getpid(): i32 from "libSystem";')
        call = 'getpid() > 0'
    else:
        decls = [f'declare function x{i}(): u32 from "kernel32";' for i in range(n - 1)]
        decls.append('declare function GetCurrentProcessId(): u32 from "kernel32";')
        call = 'GetCurrentProcessId() > (0 as u32)'
    return '\n'.join(decls) + '\n\n' + main_wrap([], f'toStr({call})')


def gen_chain_src(n):
    # g0<T> 调 g1<List<T>> …… 调 g{n-1}：实例化嵌套 n 层（每层在上一层的实例体检查中展开）。
    out = []
    for i in range(n):
        if i == n - 1:
            out.append(f'function g{i}<T>(x: T): int {{\n    return {i};\n}}\n')
        else:
            out.append(f'function g{i}<T>(x: T): int {{\n    const l: List< T> = listNew();\n'
                       f'    listPush(l, x);\n    return g{i + 1}(l);\n}}\n')
    return '\n'.join(out) + '\n' + main_wrap([], 'toStr(g0(1))')


def fns_src(n):
    out = [f'function f{i}(x: int): int {{\n    return x + {i};\n}}\n' for i in range(n)]
    return '\n'.join(out) + '\n' + main_wrap([f'const s = f{n - 1}(f0(1));'], 'toStr(s)')


def strs_src(n):
    items = ', '.join(f'"s{i}"' for i in range(n))
    return main_wrap([f'const t = [{items}];'], f't[{n - 1}]')


def variants_src(n):
    vs = ',\n'.join(f'    V{i}(x: int)' for i in range(n))
    return (f'enum E {{\n{vs},\n}}\n\nfunction pick(e: E): int {{\n    return switch (e) {{\n'
            f'        case V{n - 1}(x) => x,\n        else => 0,\n    }};\n}}\n\n'
            + main_wrap([], f'toStr(pick(E.V{n - 1}(5)))'))


def str_append_src(n):
    # 三种逐段累加写法（join 式每轮两处、右侧为局部变量、链式），唯一持有时须原地追加、线性。
    return main_wrap(['let a = "";', 'let b = "";', 'let c = "";', 'let i = 0;', f'while (i < {n}) {{',
                      '    if (i > 0) { a = a + ","; }', '    a = a + "ab";', '    const piece = "xy";',
                      '    b = b + piece;', '    c = c + "-" + "cd";', '    i = i + 1;', '}'],
                     'toStr(len(a)) + " " + toStr(len(b)) + " " + toStr(len(c))')


def io_big_src(doublings):
    # 从 16 字节起翻倍：23 次即 128 MiB。工作目录是临时目录，文件留在其中。
    return main_wrap(['let s = "0123456789abcdef";', 'let i = 0;', f'while (i < {doublings}) {{', '    s = s + s;', '    i = i + 1;', '}',
                      'writeFile("io-big.tmp", s) catch panic("writeFile 失败");',
                      'const r = readFile("io-big.tmp") catch "ERR";',
                      'const b = readFileBytes("io-big.tmp") catch bytesNew(0);'],
                     'toStr(len(s)) + " " + toStr(len(r)) + " " + toStr(bytesLen(b)) + " " + toStr(r == s)')


def print_big_src(doublings):
    return main_wrap(['let s = "太";', 'let i = 0;', f'while (i < {doublings}) {{', '    s = s + s;', '    i = i + 1;', '}', 'print(s);'],
                     '"end"')


def task_churn_src(n):
    return ('async function worker(i: int): int {\n    await sleepMs(0) catch (e) {\n        return -1;\n    };\n    return i;\n}\n\n'
            'async function driver(n: int): int {\n    let s = 0;\n    let i = 0;\n    while (i < n) {\n'
            '        const v = await worker(i) catch -1;\n        s = s + v;\n        i = i + 1;\n    }\n'
            '    print(toStr(s));\n    return s;\n}\n\n'
            f'export function main(args: []string): int {{\n    const d = driver({n});\n    return 0;\n}}\n')


def gen_deep_src():
    return ('function deep<T>(x: T, n: int): int {\n    if (n == 0) {\n        return 0;\n    }\n'
            '    const l: List< T> = listNew();\n    listPush(l, x);\n    return 1 + deep(l, n - 1);\n}\n\n'
            + main_wrap([], 'toStr(deep(1, 3))'))


def gen_expo_src():
    return ('struct Pair<A, B> {\n    a: A,\n    b: B,\n}\n\n'
            'function deep<T>(x: T, n: int): int {\n    if (n == 0) {\n        return 0;\n    }\n'
            '    const p = Pair<T, T> { a: x, b: x };\n    return 1 + deep(p, n - 1);\n}\n\n'
            + main_wrap([], 'toStr(deep(1, 3))'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True, help='原生编译器')
    ap.add_argument('--work', type=Path)
    args = ap.parse_args()
    # 用例在 work 目录里编译：编译器路径先转绝对（POSIX 上相对路径按子进程的 cwd 解析）。
    args.compiler = args.compiler.resolve()
    work = (args.work or Path(tempfile.mkdtemp(prefix='taixu-limits-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    linux = os.name != 'nt'
    host = 'windows' if not linux else ('macos' if sys.platform == 'darwin' else 'linux')
    exe = '' if linux else '.exe'
    # (名, 源码, 期望诊断或 None, 期望输出, 编译时限秒[, 峰值常驻内存上限 MiB])
    cases = [
        ('globals-300', globals_src(300), None, '0 10 7', 900),
        ('struct-200', struct_src(200), None, '0 200', 900),
        ('enum-200', enum_src(200), None, '199000', 900),
        ('captures-130', captures_src(130), None, str(sum(range(130))), 900),
        ('consts-70000', consts_src(70000), None, '0 69999', 900),
        ('jumps-40000', jumps_src(40000), None, '120000 3', 120),
        ('externs-300', externs_src(300, host), None, 'true', 900),
        ('gen-chain-200', gen_chain_src(200), None, '199', 900),
        ('locals-300', locals_src(300), None, str(sum(range(300))), 900),
        ('locals-5000', locals_src(5000), None, str(sum(range(5000))), 900),
        ('locals-60000', locals_shallow_src(60000), None, '59999', 900),
        ('params-300', params_src(300), None, str(sum(range(300)) + 299), 900),
        ('locals-70000', locals_shallow_src(70000), 'error 3101', None, 900),
        ('gen-deep', gen_deep_src(), 'error 3020', None, 900),
        ('gen-expo', gen_expo_src(), 'error 3020', None, 120),
        ('fns-16000', fns_src(16000), None, '16000', 120),
        ('globals-32000', globals_src(32000), None, '0 10 7', 120),
        ('strs-80000', strs_src(80000), None, 's79999', 120),
        ('variants-60000', variants_src(60000), None, '5', 120),
        ('str-append-400k', str_append_src(400000), None, '1199999 800000 1200000', 120),
        ('io-128m', io_big_src(23), None, '134217728 134217728 134217728 true', 120),
        ('print-24m', print_big_src(23), None, '太' * (1 << 23) + '\nend', 120),
        ('tasks-2m', task_churn_src(2000000), None, str(2000000 * 1999999 // 2), 120, 64),
    ]
    fails = 0
    for name, src, want_err, want_out, limit, *mem in cases:
        (work / (name + '.tx')).write_text(src, encoding='utf-8', newline='\n')
        out_dir = work / ('out-' + name)
        try:
            code, text = run([args.compiler, 'build', name + '.tx', '--out', out_dir], work, limit)
        except subprocess.TimeoutExpired:
            print(f'FAIL {name}  编译超过 {limit} 秒', flush=True)
            fails += 1
            continue
        ok = False
        if want_err:
            ok = code == 1 and want_err in text
            detail = f'exit={code} ' + text.strip().splitlines()[0][:160] if text.strip() else f'exit={code}'
        elif code == 0:
            try:
                if mem:
                    rc, got, peak = run_peak([out_dir / (name + exe)], work, 120)
                else:
                    rc, got = run([out_dir / (name + exe)], work, 120)
                ok = rc == 0 and got.strip() == want_out
                detail = f'run exit={rc} out={got.strip()[:80]!r}' + (f' len={len(got)}' if len(got) > 200 else '')
                if mem:
                    ok = ok and 0 < peak <= mem[0] * 1048576
                    detail += f' peak={peak / 1048576:.1f}MiB（上限 {mem[0]}）'
            except subprocess.TimeoutExpired:
                detail = '运行超过 120 秒'
        else:
            detail = f'build exit={code} ' + text.strip()[-160:]
        print(('PASS ' if ok else 'FAIL ') + name + '  ' + detail, flush=True)
        fails += 0 if ok else 1
    print('SUMMARY ' + ('PASS' if fails == 0 else f'FAIL={fails}'))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
