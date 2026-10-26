"""测试专用页堆的正负控制（安全审查攻关第 2 项，docs/design/v052-heap-check-v0.1-20261024.md 五、六）。由给定编译器编出内核生成器，
生成本宿主目标的页堆内核（--generic=heapcheck），分四部分检查：

  driver   C 驱动（tests/runtime-security/heap-check.c）直接链接页堆内核：分配器本身（ok）与九个正控制——重复释放、非法释放、
           内部指针释放、块尾空隙写、块头之前写、改块头尺寸（账本与哨兵，报 4012），越过保护页写、释放后写、重分配后写旧址
           （访问违例）。内核的 __tx_trap 要读线程块，C 调用方没有，链接前经 objcopy 弱化（连同 Linux 内核自带的 _start、main），
           由驱动的定义代替。
  product  太虚产物经外部库（heap-probe.c / heap-probe.tx）越过 Bytes 末尾写、写块尾空隙、释放后写：`--heap-check`（编译器内嵌的
           检测内核：页堆＋对象号隔离，设计 v052-heap-check-tool）下须查出；标准内核下照常跑完（说明控制确实做了坏访问，而生产
           内核看不见）。
  mutants  去掉保护页、去掉释放时的解除提交（隔离失效）、去掉块尾哨兵核对的三个变异页堆，须分别让对应控制漏检——证明控制能区分
           检测器有没有效。变异按锚点改 bootstrap 副本里的 kernel_alloc_check.tx；锚点不唯一即失败，不静默变成空变异。
  corpus   全量语料（SKIP 清单除外）以 `--heap-check` 构建，与标准内核逐例同结果。标准结果可由 --standard-normal／--standard-leak／
           --standard-debug 给出已有报告（门里复用 products、leak、instrumented 三步的报告），否则就地跑一遍。

  python scripts/test-heap-check.py --compiler <taixu> --work <dir> [--leak-check] [--debug] [--standard-normal <json>] [part…]

宿主为 Windows、Linux（x86_64）或 Apple silicon 的 macOS（产物为 aarch64-macos）。macOS 没有 GNU objcopy，内核又是交给太虚链接器
的 ELF 对象：驱动与变异体改链只含页堆的 Mach-O 目标文件（bootstrap/native_heap_check_probe.tx，不含 __tx_trap，libSystem 调用由
驱动定义），arm64 原生与 x86_64（Rosetta）各跑一遍。
C 编译器取 TAIXU_TEST_CC，否则 PATH 上的 cc／gcc／clang；objcopy（Windows、Linux）取 TAIXU_TEST_OBJCOPY，否则 C 编译器旁边或
PATH 上的。缺工具即失败，不记通过。
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'runtime-security'
sys.path.insert(0, str(ROOT / 'scripts'))
from importlib import import_module

products = import_module('test-nrt-products')

WINDOWS = os.name == 'nt'
MACOS = sys.platform == 'darwin'
EXE = '.exe' if WINDOWS else ''
TARGET = 'x86_64-windows' if WINDOWS else ('aarch64-macos' if MACOS else 'x86_64-linux')
PRODUCTS_TARGET = 'windows' if WINDOWS else ('aarch64-macos' if MACOS else 'linux')
KERNEL = 'kernel.obj' if WINDOWS else ('kernel-macos-aarch64.o' if MACOS else 'kernel-linux.o')
# macOS 上访问不可访问页多报 SIGBUS。
ACCESS_VIOLATION = (3221225477,) if WINDOWS else ((-11, -10) if MACOS else (-11,))
# macOS 驱动的两种架构：(目标三元组, cc 的架构参数, 名字后缀)。
MACOS_ARCHES = (('aarch64-macos', [], '-aarch64'), ('x86_64-macos', ['-arch', 'x86_64'], '-x86_64'))

# 页堆模式下按清单跳过的语料用例（设计 三-6）：每项写明为何不跑，不静默放宽。
SKIP = {
    'tests/nrt-cases/thread-many.tx': '382 个线程各分配两万个字符串（约 760 万块），页堆每块一次映射、解除提交与归还，'
                                      '超过单例 120 秒时限（2026-10-24 不限时跑完约 10 分钟，输出与期望相同）',
}
# 驱动控制 → 期望：字符串为 4012 报告里须含的消息，None 为访问违例。
DRIVER = {
    'double-free': '重复释放',
    'invalid-free': '释放了不是分配器给出的指针',
    'interior-free': '释放了不是分配器给出的指针',
    'slack-write': '块外的字节被改写',
    'left-write': '块外的字节被改写',
    'header-write': '块头被改写',
    'guard-write': None,
    'use-after-free': None,
    'realloc-stale': None,
}
# 变异名 → (须漏检的驱动控制, [(锚点, 替换)])。锚点在两端分支里各有一份的分别列出，每个锚点须恰好出现一次。
MUTANTS = {
    'guard': ('guard-write', [
        ('rtCall4(f, "x", "VirtualAlloc", L("base"), L("total"), I(MEM_COMMIT), I(PAGE_RW));',
         'rtCall4(f, "x", "VirtualAlloc", L("base"), L("maplen"), I(MEM_COMMIT), I(PAGE_RW));'),
        ('uxSys(f, t, "", 10, ops3(L("x"), I(page), I(0)));', 'uxSys(f, t, "", 10, ops3(L("x"), I(page), I(3)));'),
    ]),
    'quarantine': ('use-after-free', [
        ('rtCall3(f, "", "VirtualFree", L("base"), L("total"), I(MEM_DECOMMIT));', ''),
        ('uxSys(f, t, "", 10, ops3(L("base"), L("total"), I(0)));', ''),
    ]),
    'canary': ('slack-write', [
        ('rtIf(f, L("w"), "ne", I(165), "canary");', ''),
    ]),
}
PARTS = ('driver', 'product', 'mutants', 'corpus')


def sh(argv, cwd=ROOT, timeout=600, env=None):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True, timeout=timeout, env=env)
    return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')


def tool(env_name, names, near=None):
    if os.environ.get(env_name):
        return os.environ[env_name]
    for name in names:
        if near:
            cand = Path(near).parent / (name + EXE)
            if cand.is_file():
                return str(cand)
        found = shutil.which(name)
        if found:
            return found
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--leak-check', action='store_true', help='语料部分另跑 --leak-check')
    ap.add_argument('--debug', action='store_true', help='语料部分另跑 --debug')
    ap.add_argument('--standard-normal', type=Path, help='已有的标准内核语料报告（普通）')
    ap.add_argument('--standard-leak', type=Path, help='已有的标准内核语料报告（--leak-check）')
    ap.add_argument('--standard-debug', type=Path, help='已有的标准内核语料报告（--debug）')
    ap.add_argument('parts', nargs='*', choices=[[], *PARTS], default=[])
    a = ap.parse_args()
    parts = a.parts or list(PARTS)
    compiler = a.compiler.resolve()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    bad = 0

    def check(label, ok, detail=''):
        nonlocal bad
        rows.append({'check': label, 'ok': bool(ok), 'detail': detail if ok else str(detail)[:2000]})
        print('%s %s%s' % ('PASS' if ok else 'FAIL', label, '' if ok else ': ' + str(detail)[:400]), flush=True)
        if not ok:
            bad += 1
        return ok

    cc = tool('TAIXU_TEST_CC', ['cc', 'gcc', 'clang'])
    objcopy = tool('TAIXU_TEST_OBJCOPY', ['objcopy', 'llvm-objcopy'], near=shutil.which(cc) if cc else None)

    def generator(src_root, out):
        tflag = ['--target=linux'] if not WINDOWS and not MACOS else []
        rc, o, e = sh([compiler, 'build', 'bootstrap/runtime_memory_gen.tx', '--runtime=native', *tflag, '--out', out], cwd=src_root)
        if not check('generator build ' + out.name, rc == 0, o + e):
            return None
        return out / ('runtime_memory_gen' + EXE)

    def kernel(gen, out, generic):
        out.parent.mkdir(parents=True, exist_ok=True)
        rc, o, e = sh([gen, '--target', TARGET, out, *products.KERNEL_FLAGS, *(['--generic=' + generic] if generic else [])])
        return out if check('kernel ' + str(out.relative_to(work)), rc == 0 and out.is_file(), o + e) else None

    def probes(src_root, out):
        """macOS：由 src_root 的源码编出页堆探针生成器，为两种架构各出一个 Mach-O 目标文件 → [(对象, cc 架构参数, 后缀)]。"""
        out.mkdir(parents=True, exist_ok=True)  # build --out 只建最后一层
        rc, o, e = sh([compiler, 'build', 'bootstrap/native_heap_check_probe.tx', '--toolchain', '--out', out / 'probe-gen'], cwd=src_root)
        if not check('probe generator build ' + str(out.relative_to(work)), rc == 0, o + e):
            return []
        objs = []
        for triple, archflags, tag in MACOS_ARCHES:
            obj = out / ('heap-check' + tag + '.o')
            rc, o, e = sh([out / 'probe-gen' / 'native_heap_check_probe', '--target', triple, obj])
            if check('probe ' + str(obj.relative_to(work)), rc == 0 and obj.is_file(), o + e):
                objs.append((obj, archflags, tag))
        return objs

    def driver(kobj, name, archflags=()):
        if MACOS:
            # 探针对象不含 __tx_trap，无须弱化。
            if not check('tools: C compiler', cc, 'cc=None'):
                return None
            linked = kobj
        else:
            if not check('tools: C compiler and objcopy', cc and objcopy, 'cc=%s objcopy=%s' % (cc, objcopy)):
                return None
            linked = kobj.with_name(kobj.stem + '-weak' + kobj.suffix)
            rc, o, e = sh([objcopy, '-W', '__tx_trap', '-W', '_start', '-W', 'main', kobj, linked])
            if not check('objcopy ' + name, rc == 0, o + e):
                return None
        shutil.copyfile(FIXTURES / 'heap-check.c', kobj.parent / 'heap-check.c')
        rc, o, e = sh([cc, '-O2', '-Wall', '-Wextra', *archflags, 'heap-check.c', linked.name, '-o', name + EXE], cwd=kobj.parent)
        return kobj.parent / (name + EXE) if check('driver build ' + name, rc == 0, o + e) else None

    def control(exe, name, expect, label):
        """expect: 'pass'、'missed'、消息串（4012 报告）或 None（访问违例）。"""
        rc, o, e = sh([exe, name], cwd=exe.parent, timeout=300)
        if expect == 'pass':
            ok = rc == 0 and 'PASS heapcheck: allocator' in o
        elif expect == 'missed':
            ok = rc == 1 and ('MISSED heapcheck control ' + name) in o
        elif expect is None:
            ok = rc in ACCESS_VIOLATION
        else:
            ok = rc == 88 and 'TRAP code=4012 heapcheck: ' + expect in o
        check(label, ok, 'exit=%s stdout=%r stderr=%r' % (rc, o[-300:], e[-300:]))

    gen = generator(ROOT, work / 'gen')
    if gen is None:
        return finish(work, rows, bad)
    standard = kernel(gen, work / 'standard' / KERNEL, None)
    heap = kernel(gen, work / 'heapcheck' / KERNEL, 'heapcheck')
    if standard is None or heap is None:
        return finish(work, rows, bad)

    if 'driver' in parts:
        for kobj, archflags, tag in (probes(ROOT, work / 'probe') if MACOS else [(heap, [], '')]):
            exe = driver(kobj, 'heap-check' + tag, archflags)
            if exe:
                control(exe, 'ok', 'pass', 'driver%s: allocator' % tag)
                for name, expect in DRIVER.items():
                    control(exe, name, expect, 'driver%s: %s' % (tag, name))

    if 'product' in parts and check('tools: C compiler', cc, 'cc=None'):
        pw = work / 'product'
        pw.mkdir(exist_ok=True)
        library = 'heap_probe.dll' if WINDOWS else ('libheap_probe.dylib' if MACOS else 'libheap_probe.so')
        shutil.copyfile(FIXTURES / 'heap-probe.c', pw / 'heap-probe.c')
        flags = ['-shared', '-static-libgcc'] if WINDOWS else ['-shared', '-fPIC']
        rc, o, e = sh([cc, '-O2', '-Wall', '-Wextra', *flags, 'heap-probe.c', '-o', library], cwd=pw)
        if check('product: library build', rc == 0, o + e):
            source = pw / 'heap-probe.tx'
            # macOS 按 LC_LOAD_DYLIB 绑定库路径，给绝对路径；其余宿主产物旁边的库即可。
            named = str(pw / library) if MACOS else library
            source.write_text((FIXTURES / 'heap-probe.tx').read_text(encoding='utf-8').replace('from "heap_probe"', 'from "' + named + '"'),
                              encoding='utf-8', newline='\n')
            # 检测形态经用户旗标 --heap-check（编译器内嵌的检测内核，即用户拿到的那一份）。
            for kind, extra in (('standard', []), ('heapcheck', ['--heap-check'])):
                out = pw / kind
                rc, o, e = sh([compiler, 'build', source, *(['--target=aarch64-macos'] if MACOS else []), *extra, '--out', out])
                if not check('product: build ' + kind, rc == 0, o + e):
                    continue
                shutil.copyfile(pw / library, out / library)
                exe = out / ('heap-probe' + EXE)
                # Linux 按 DT_NEEDED 加载外部库，产物目录不在搜索路径里（同 runtime_security_checks.py）。
                penv = dict(os.environ, LD_LIBRARY_PATH=str(out)) if not WINDOWS and not MACOS else None
                for mode in ('clean', 'slack', 'guard', 'use-after-free'):
                    rc, o, e = sh([exe, mode], cwd=out, timeout=120, env=penv)
                    if kind == 'standard' or mode == 'clean':
                        ok = rc == 0 and ('PASS_HEAP_PROBE ' + mode) in o
                    elif mode == 'slack':
                        ok = rc == 3 and 'heapcheck: 块外的字节被改写' in e and 'PASS_HEAP_PROBE' not in o
                    else:
                        ok = rc in ACCESS_VIOLATION and 'PASS_HEAP_PROBE' not in o
                    check('product: %s %s' % (kind, mode), ok, 'exit=%s stdout=%r stderr=%r' % (rc, o[-300:], e[-300:]))

    if 'mutants' in parts:
        for name, (missed, edits) in MUTANTS.items():
            tree = work / ('mutant-' + name)
            if tree.exists():
                shutil.rmtree(tree)
            shutil.copytree(ROOT / 'bootstrap', tree / 'bootstrap')
            module = tree / 'bootstrap' / 'selfhost' / 'kernel_alloc_check.tx'
            text = module.read_text(encoding='utf-8')
            anchored = True
            for old, new in edits:
                anchored = check('mutant %s: anchor %s' % (name, old[:48]), text.count(old) == 1, 'count=%d' % text.count(old)) and anchored
                text = text.replace(old, new)
            if not anchored:
                continue
            module.write_text(text, encoding='utf-8', newline='\n')
            if MACOS:
                objs = probes(tree, tree / 'probe')
            else:
                mgen = generator(tree, tree / 'gen')
                mk = mgen and kernel(mgen, tree / 'kernel' / KERNEL, 'heapcheck')
                objs = [(mk, [], '')] if mk else []
            for kobj, archflags, tag in objs:
                exe = driver(kobj, 'heap-check-' + name + tag, archflags)
                if exe:
                    control(exe, missed, 'missed', 'mutant %s%s: %s goes undetected' % (name, tag, missed))

    if 'corpus' in parts:
        cases = [c.relative_to(ROOT).as_posix() for c in products.cases([])]
        for case, why in SKIP.items():
            print('SKIP %s: %s' % (case, why), flush=True)
        kept = [c for c in cases if c not in SKIP]
        modes = [('normal', [], a.standard_normal)]
        modes += [('leak', ['--leak-check'], a.standard_leak)] if a.leak_check else []
        modes += [('debug', ['--debug'], a.standard_debug)] if a.debug else []
        for label, mode, given in modes:
            if given is not None and given.is_file():
                std = load_rows(given)
            else:
                std = corpus(compiler, work, 'standard-' + label, [], mode, kept)
            got = corpus(compiler, work, 'heapcheck-' + label, ['--build-flag=--heap-check'], mode, kept)
            counts = {}
            for s_ in got.values():
                counts[s_] = counts.get(s_, 0) + 1
            print('corpus %s: %s' % (label, ' '.join('%s=%d' % kv for kv in sorted(counts.items()))), flush=True)
            diff = sorted(c for c in kept if std.get(c) != got.get(c))
            check('corpus %s: %d cases, page heap same as standard (%d skipped)' % (label, len(kept), len(SKIP)),
                  not diff and len(got) == len(kept), ['%s standard=%s heapcheck=%s' % (c, std.get(c), got.get(c)) for c in diff][:40])
    return finish(work, rows, bad)


def load_rows(report):
    rows = json.loads(report.read_text(encoding='utf-8'))
    rows = rows.get('rows', rows) if isinstance(rows, dict) else rows
    return {r['case']: r['status'] for r in rows}


def corpus(compiler, work, label, extra, mode, cases):
    """经 scripts/test-nrt-products.py 跑给定用例，返回 {用例: 状态}。用例以相对路径交给它（命令行长度）。"""
    report = work / ('corpus-%s.json' % label)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'test-nrt-products.py'), '--compiler', str(compiler),
                    '--work', str(work / ('run-' + label)), '--report', str(report), '--target', PRODUCTS_TARGET, *extra, *mode, *cases],
                   cwd=ROOT, capture_output=True)
    return load_rows(report) if report.is_file() else {}


def finish(work, rows, bad):
    (work / 'heap-check.json').write_text(json.dumps({'rows': rows, 'failed': bad}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('SUMMARY %s heap-check: %d checks, %d failed' % ('PASS' if bad == 0 else 'FAIL', len(rows), bad), flush=True)
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
