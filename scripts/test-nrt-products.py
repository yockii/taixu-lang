"""NRT bring-up differential: build e2e cases with --runtime=native and compare.

Expected behavior comes from the declarative e2e companions (tests/README.md):
`.out` = exit 0 and byte-identical stdout; `.err` = first line `exit=N`, the
rest are substrings that must appear in stderr or stdout. Cases outside the NRT
contract (GUI/host extern at run time, runflags, json/test variants) are skipped by
rule; async is native since round 4 step 3, and a compile-time rejection (`.err`
with exit=1) never needs the host, so extern markers do not skip it.
Unimplemented NRT entries fail loudly with "taixu NRT: 未实现" and are counted
as PENDING, not as passes. A `<case>.windows` / `<case>.linux` companion marks a
target-only case (FFI against one platform's libraries or syscalls); it is skipped,
by name, on the other target. A `<case>.buildflags` companion lists extra build
options, one per line (e.g. `--static`).

`--leak-check` builds every case with that option (exit-time leak check, step 1):
results must match as before and no run may print a leak report (`[runtime:leak]`)
unless the case's own expectation names it (cases that leak on purpose). A `<case>.leakok`
companion (one line: why the program keeps objects alive on purpose, e.g. intentional
cycles) keeps that case out of the leak-check build.

`--target macos` (macOS adaptation M1, E10) cross-builds every case
as an x86_64 Mach-O and runs the products in one batch on the macOS test host over ssh (`--remote <ssh host>`, default
$TAIXU_MACOS_HOST; on a macOS host the default is `local`, which runs them on this machine; scripts/macos_remote.py), each in
its own case directory as locally. Features the macOS target still
rejects at build time say "macOS 目标暂不支持" and count as PENDING (none since M3, which added tool builtins and --debug).
`--target aarch64-macos` (macOS adaptation M4c) does the same with arm64 Mach-O products run natively on the (Apple
silicon) test host; what the A64 target still rejects ("aarch64-macos 暂不支持": foreign functions until M4d, --debug and
tool builtins until M4e) counts as PENDING.

`--target aarch64-linux` (multi-arch A2, B10) builds arm64 ELF products and
runs them natively on an aarch64 Linux host, otherwise through user-mode QEMU (`--qemu <path>`, default
$TAIXU_QEMU_AARCH64; on a Windows host the path is inside WSL and the products run as `wsl.exe -e <qemu> <product>`).
Under user-mode QEMU a product that runs another arm64 program (procSpawn of itself) needs binfmt_misc on the host;
such failures are reported as usual, not hidden. `<case>.x86_64` / `<case>.aarch64` / `<case>.loongarch64` / `<case>.riscv64` companions
mark architecture-only cases (skipped by name on the other architectures), alongside the operating-system markers.

`--target loongarch64-linux` (multi-arch A4e, design B16 decision 13) does the same for LoongArch64 ELF products: natively
on a loongarch64 Linux host, otherwise through `--qemu` (default $TAIXU_QEMU_LOONGARCH64). `--target riscv64-linux`
(multi-arch A5e, design B17) likewise for RV64GC hosts, otherwise through `--qemu` (default $TAIXU_QEMU_RISCV64).

`--target aarch64-windows` (multi-arch A6, design B18) builds ARM64 PE products; every product (with or without
--build-only) first passes the independent checker scripts/pe_arm64_check.py (headers, imports, base relocations, TLS,
unwind data), a rejected image counts as BUILD_FAIL. They run natively on a Windows ARM64 host, otherwise in one batch on
the Windows ARM64 test machine over ssh (`--winarm <ssh arguments>`, default $TAIXU_WINARM_HOST; scripts/windows_remote.py),
or use --build-only.

`--build-only` builds every case and checks only the build outcome (a product, or the expected compile-time
rejection): successful builds count as BUILT and are not run. The gate uses it for the arm64 products on hosts that
cannot run them. Every Mach-O product (both architectures, with or without --build-only) first passes the
independent checker scripts/macho_check.py; a rejected image counts as BUILD_FAIL.

`--debug` builds every case as an instrumented debug build (round 6 step 6c,
): run without a debug session the
checkpoints are inert, so every result must match exactly as in a normal build (the
semantic-transparency gate). Cases whose `.buildflags` ask for `--leak-check` are
skipped by name (the two options are exclusive).
"""
import argparse
import hashlib
import json
import os
import platform
import re
from pathlib import Path
import subprocess
import sys
import tempfile

import macho_check
import pe_arm64_check

ROOT = Path(__file__).resolve().parent.parent
KERNEL_FLAGS = ['--with-move', '--with-alloc', '--with-rc', '--with-str', '--with-strb', '--with-zero',
                '--with-list', '--with-file', '--with-argv', '--with-store-slots', '--with-nrt']


def fixture_env(env=None):
    # Explicit getEnv positive fixture; inherited PATH may be absent or empty. On a Windows host the Linux products
    # run through wsl.exe, which forwards only the variables WSLENV names.
    base = dict(os.environ if env is None else env)
    if os.name == 'nt':
        names = [n for n in base.get('WSLENV', '').split(':') if n]
        if 'TAIXU_SYS_PROC_FIXTURE' not in names:
            base['WSLENV'] = ':'.join(names + ['TAIXU_SYS_PROC_FIXTURE'])
    return {**base, 'TAIXU_SYS_PROC_FIXTURE': 'fixture 中文 🦊'}


def run(argv, cwd=ROOT, timeout=600, env=None):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True, timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr


def cases(selected):
    if selected:
        return [Path(s).resolve() for s in selected]
    found = []
    # tests/nrt-cases: language features from D3 v0.18 on;
    # scanned together with tests/cases and examples.
    for d in (ROOT / 'tests/cases', ROOT / 'tests/nrt-cases', ROOT / 'examples'):
        for tx in sorted(d.glob('*.tx')):
            if tx.with_suffix('.out').exists() or tx.with_suffix('.err').exists():
                found.append(tx)
    return found


def local_imports(tx, seen):
    """tx plus every ./relative module it imports, transitively."""
    if tx in seen or not tx.exists():
        return
    seen.add(tx)
    text = tx.read_text(encoding='utf-8', errors='replace')
    for spec in re.findall(r'from\s+"(\.[^"]+)"', text):
        target = (tx.parent / spec)
        local_imports(target if target.suffix == '.tx' else target.with_suffix('.tx'), seen)


def skip_reason(tx, target, arch):
    for only in ('windows', 'linux', 'macos'):
        if tx.with_suffix('.' + only).exists() and target != only:
            return 'target-only:' + only
    for only in ('x86_64', 'aarch64', 'loongarch64', 'riscv64'):
        if tx.with_suffix('.' + only).exists() and arch != only:
            return 'target-only:' + only
    if tx.with_suffix('.runflags').exists():
        return 'runflags'
    err = tx.with_suffix('.err')
    if err.exists() and 'retained by kind' in err.read_text(encoding='utf-8'):
        return 'interpreter-mem-report'
    if err.exists() and err.read_text(encoding='utf-8').splitlines()[0].strip() == 'exit=1':
        return None
    return None


def expected(tx):
    out = tx.with_suffix('.out')
    if out.exists():
        return {'kind': 'out', 'exit': 0, 'stdout': out.read_bytes().replace(b'\r\n', b'\n')}
    lines = tx.with_suffix('.err').read_text(encoding='utf-8').replace('\r\n', '\n').split('\n')
    code = int(lines[0].split('=', 1)[1])
    return {'kind': 'err', 'exit': code, 'needles': [l for l in lines[1:] if l]}


def judge(row, exp, code, out, err, leak_check):
    out_n = out.replace(b'\r\n', b'\n'); err_n = err.replace(b'\r\n', b'\n')
    text = (out_n + err_n).decode('utf-8', 'replace')
    if 'taixu NRT: 未实现' in text:
        row['status'] = 'PENDING'; row['detail'] = text.strip()[-200:]
    elif leak_check and '[runtime:leak]' in text and not (exp['kind'] == 'err' and any('[runtime:leak]' in n for n in exp['needles'])):
        row['status'] = 'FAIL'; row['detail'] = 'leak: ' + text[text.index('[runtime:leak]'):][:300]
    elif exp['kind'] == 'out':
        ok = code == 0 and out_n == exp['stdout']
        row['status'] = 'PASS' if ok else 'FAIL'
        if not ok:
            row['detail'] = f'exit={code} stdout={out_n[:200]!r} stderr={err_n[:200]!r}'
    else:
        ok = code == exp['exit'] and all(n in text for n in exp['needles'])
        row['status'] = 'PASS' if ok else 'FAIL'
        if not ok:
            row['detail'] = f'exit={code} want={exp["exit"]} text={text[:300]!r}'


def report_row(row):
    print(f"{row['status']:14} {row['case']}" + (f"  {row.get('detail', '')[:160]!r}" if row['status'] in ('FAIL', 'BUILD_FAIL', 'REJECT_MISMATCH') else ''), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True, help='Native Taixu compiler executable (faster than the VM)')
    ap.add_argument('--kernel', type=Path, help='Prebuilt NRT kernel object')
    ap.add_argument('--work', type=Path)
    ap.add_argument('--report', type=Path)
    # 缺省为宿主自己的目标：macOS 宿主按处理器取 aarch64-macos 或 macos（x86_64），此前一律取 linux，门在 Mac 上编出 ELF 去跑；
    # Linux 宿主同样按处理器取 aarch64-linux 或 linux（x86_64）。
    arm_host = platform.machine() in ('arm64', 'aarch64')
    la_host = platform.machine() == 'loongarch64'
    rv_host = platform.machine() == 'riscv64'
    host = ('aarch64-windows' if platform.machine().upper() == 'ARM64' else 'windows') if os.name == 'nt' else (('aarch64-macos' if arm_host else 'macos') if sys.platform == 'darwin' else
                                              ('aarch64-linux' if arm_host else ('loongarch64-linux' if la_host else
                                                                                 ('riscv64-linux' if rv_host else 'linux'))))
    ap.add_argument('--target', choices=['windows', 'aarch64-windows', 'linux', 'macos', 'aarch64-macos', 'aarch64-linux', 'loongarch64-linux', 'riscv64-linux'],
                    default=host,
                    help='linux on a Windows host runs the ELF products through wsl.exe; macos runs them on --remote over ssh; '
                         'aarch64-windows runs them natively on a Windows ARM64 host; '
                         'aarch64-linux, loongarch64-linux and riscv64-linux run them natively on a host of that architecture, otherwise '
                         'through --qemu')
    ap.add_argument('--qemu', default='',
                    help='qemu-user binary for --target aarch64-linux / loongarch64-linux / riscv64-linux off that architecture '
                         '(inside WSL on a Windows host); default $TAIXU_QEMU_AARCH64 / $TAIXU_QEMU_LOONGARCH64 / $TAIXU_QEMU_RISCV64')
    ap.add_argument('--remote', default='local' if sys.platform == 'darwin' else os.environ.get('TAIXU_MACOS_HOST', ''),
                    help='ssh host of the macOS test machine (--target macos); local runs on this Mac (the default there)')
    ap.add_argument('--winarm', default=os.environ.get('TAIXU_WINARM_HOST', ''),
                    help='ssh arguments of the Windows ARM64 test machine (--target aarch64-windows off such a host), '
                         'e.g. "-J <跳板机> -p 2222 -i <key> tx@127.0.0.1"')
    ap.add_argument('--leak-check', action='store_true', help='build every case with --leak-check; a leak report fails the case')
    ap.add_argument('--debug', action='store_true', help='build every case with --debug (instrumented); results must not change')
    ap.add_argument('--build-flag', action='append', default=[], help='extra build flag for every case (repeatable), e.g. --codegen=portable')
    ap.add_argument('--build-only', action='store_true', help='build every case and check only the build outcome; built products count as BUILT')
    ap.add_argument('cases', nargs='*')
    args = ap.parse_args()
    if args.debug and args.leak_check:
        raise SystemExit('--debug and --leak-check are exclusive')
    if args.target == 'windows' and os.name != 'nt':
        raise SystemExit('Windows PE products need a Windows host')
    a64_win = args.target == 'aarch64-windows'
    native_win = a64_win and os.name == 'nt' and platform.machine().upper() == 'ARM64'
    if a64_win and not args.build_only and not native_win and not args.winarm:
        raise SystemExit('running aarch64-windows products needs a Windows ARM64 host, --winarm <ssh arguments> '
                         '(or TAIXU_WINARM_HOST), or --build-only')
    if args.target in ('macos', 'aarch64-macos') and not args.build_only and not args.remote:
        raise SystemExit('running macOS products from this host needs --remote <ssh host> (or TAIXU_MACOS_HOST)')
    a64_linux = args.target == 'aarch64-linux'
    la64_linux = args.target == 'loongarch64-linux'
    rv64_linux = args.target == 'riscv64-linux'
    # x86_64 以外的 Linux：产物在同架构宿主上直接跑，否则经 qemu-user。
    cross_linux = a64_linux or la64_linux or rv64_linux
    linux = args.target == 'linux' or cross_linux
    a64 = args.target == 'aarch64-macos'
    macos = args.target == 'macos' or a64
    # 目标专属用例（<case>.windows／.linux／.macos）按操作系统挑选，两种 macOS 架构共用 .macos；.x86_64／.aarch64／.loongarch64／.riscv64
    # 按架构挑选。
    os_name = 'macos' if macos else ('linux' if linux else 'windows')
    arch = 'aarch64' if (a64 or a64_linux or a64_win) else ('loongarch64' if la64_linux else ('riscv64' if rv64_linux else 'x86_64'))
    via_wsl = linux and os.name == 'nt'
    on_linux = sys.platform.startswith('linux')
    native_cross = on_linux and ((a64_linux and platform.machine() in ('aarch64', 'arm64')) or (la64_linux and platform.machine() == 'loongarch64')
                                 or (rv64_linux and platform.machine() == 'riscv64'))
    qemu_env = 'TAIXU_QEMU_AARCH64' if a64_linux else ('TAIXU_QEMU_LOONGARCH64' if la64_linux else 'TAIXU_QEMU_RISCV64')
    if not args.qemu:
        args.qemu = os.environ.get(qemu_env, '') if cross_linux else ''
    if cross_linux and not native_cross and not args.build_only and not args.qemu:
        raise SystemExit('running %s products off that architecture needs --qemu <qemu-user> (or %s)' % (args.target, qemu_env))
    work = (args.work or Path(tempfile.mkdtemp(prefix='taixu-nrt-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_') or k.upper() in ('TAIXU_STDLIB', 'TAIXU_GUI_ROOT')}
    # getEnv 的正例由夹具提供；PATH 可以缺省或为空，不能依赖宿主配置。
    env = fixture_env(env)
    kernel = args.kernel
    compiler = [args.compiler.resolve()]
    rows = []
    counts = {}
    remote = []  # macOS：(row, exp, product, argv)，循环后在测试机上一批运行
    remote_win = []  # aarch64-windows（不在 ARM64 宿主上）：同上，送 Windows ARM64 测试机
    for tx in cases(args.cases):
        name = str(tx.relative_to(ROOT)).replace('\\', '/')
        row = {'case': name}
        reason = skip_reason(tx, os_name, arch)
        if reason:
            row['status'] = 'SKIP'; row['reason'] = reason
        else:
            exp = expected(tx)
            outdir = work / tx.stem
            outdir.mkdir(exist_ok=True)
            target_flag = ['--target=' + args.target] if cross_linux or a64_win else (['--target=linux'] if linux else (['--target=aarch64-macos'] if a64 else (['--target=macos'] if macos else [])))
            link = ['--link-object', kernel] if kernel is not None else []
            # A `<case>.toolchain` companion builds the case as a toolchain program
            # (D3 v0.21 toolchain-only system builtins).
            tool = ['--toolchain'] if tx.with_suffix('.toolchain').exists() else []
            flags_file = tx.with_suffix('.buildflags')
            if flags_file.exists():
                tool += [l.strip() for l in flags_file.read_text(encoding='utf-8').splitlines() if l.strip()]
            tool += args.build_flag
            if args.leak_check and '--leak-check' not in tool and not tx.with_suffix('.leakok').exists():
                tool.append('--leak-check')
            if args.debug:
                if '--leak-check' in tool:
                    row['status'] = 'SKIP'; row['reason'] = '--debug excludes the --leak-check build flag'
                    counts[row['status']] = counts.get(row['status'], 0) + 1
                    rows.append(row)
                    print(f"{row['status']:14} {name}", flush=True)
                    continue
                tool.append('--debug')
            code, out, err = run([*compiler, 'build', tx, '--runtime=native', *tool, *target_flag, *link, '--out', outdir], env=env)
            if code != 0:
                # A compile-time rejection is the expected behavior of many .err cases.
                text = out + err
                if macos and ('macOS 目标暂不支持' in text.decode('utf-8', 'replace') or 'aarch64-macos 暂不支持' in text.decode('utf-8', 'replace')):
                    row['status'] = 'PENDING'; row['detail'] = text.decode('utf-8', 'replace').strip()[-200:]
                elif (la64_linux and 'loongarch64-linux 暂不支持' in text.decode('utf-8', 'replace')) or \
                        (rv64_linux and 'riscv64-linux 暂不支持' in text.decode('utf-8', 'replace')):
                    # 随后一批补齐的部分（龙芯 A4f、RISC-V A5f：外部函数回调、按值结构体）。
                    row['status'] = 'PENDING'; row['detail'] = text.decode('utf-8', 'replace').strip()[-200:]
                elif exp['kind'] == 'err' and exp['exit'] == 1:
                    # The diagnostic must carry the expected text too, not only the exit code.
                    shown = text.decode('utf-8', 'replace')
                    if all(n in shown for n in exp['needles']):
                        row['status'] = 'COMPILE_REJECT'
                    else:
                        row['status'] = 'REJECT_MISMATCH'; row['detail'] = shown[-400:]
                else:
                    row['status'] = 'BUILD_FAIL'; row['detail'] = text.decode('utf-8', 'replace')[-400:]
            else:
                exe = outdir / (tx.stem if linux or macos else tx.stem + '.exe')
                # 供 gate.tx --baseline-compiler 比较新旧编译器的产物是否逐字节相同。
                row['product_sha256'] = hashlib.sha256(exe.read_bytes()).hexdigest()
                argv_file = tx.with_suffix('.argv')
                extra = argv_file.read_text(encoding='utf-8').splitlines() if argv_file.exists() else []
                if macos:
                    # 每个 Mach-O 产物先过独立校验器（arm64 另逐字解码分支与 ADRP），不合格记构建失败、不送测试机。
                    try:
                        macho_check.check(exe.read_bytes())
                    except macho_check.Bad as e:
                        row['status'] = 'BUILD_FAIL'; row['detail'] = 'macho_check: ' + str(e)
                        counts[row['status']] = counts.get(row['status'], 0) + 1
                        rows.append(row)
                        report_row(row)
                        continue
                if a64_win:
                    # 每个 ARM64 PE 产物先过独立核对器（头、导入、基址重定位、TLS、展开信息），不合格记构建失败、不运行。
                    errors, _ = pe_arm64_check.check(str(exe))
                    if errors:
                        row['status'] = 'BUILD_FAIL'; row['detail'] = 'pe_arm64_check: ' + '; '.join(errors[:3])
                        counts[row['status']] = counts.get(row['status'], 0) + 1
                        rows.append(row)
                        report_row(row)
                        continue
                if args.build_only:
                    row['status'] = 'BUILT'
                    counts[row['status']] = counts.get(row['status'], 0) + 1
                    rows.append(row)
                    report_row(row)
                    continue
                if macos:
                    remote.append((row, exp, exe, extra))
                    rows.append(row)
                    continue
                if a64_win and not native_win:
                    remote_win.append((row, exp, exe, extra))
                    rows.append(row)
                    continue
                launch = [exe]
                qemu = [args.qemu] if cross_linux and not native_cross else []
                if via_wsl:
                    launch = ['wsl.exe', '-e', *qemu, os.path.relpath(exe, tx.parent).replace(os.sep, '/')]
                elif qemu:
                    launch = [*qemu, exe]
                try:
                    code, out, err = run([*launch, *extra], cwd=tx.parent, timeout=120, env=env)
                except subprocess.TimeoutExpired:
                    code, out, err = -1, b'', b'TIMEOUT'
                judge(row, exp, code, out, err, args.leak_check)
        counts[row['status']] = counts.get(row['status'], 0) + 1
        rows.append(row)
        report_row(row)
    if remote:
        # 产物在测试机上一批运行：用例目录整棵带过去，产物在各自的用例目录里运行（同本地，夹具按相对路径找到）；
        # getEnv 的正例夹具一并带过去。
        from macos_remote import run_batch
        trees = {Path(r['case']).parent.name: ROOT / Path(r['case']).parent for r, _, _, _ in remote}
        results = run_batch(args.remote, [(r['case'], exe, extra, {'TAIXU_SYS_PROC_FIXTURE': fixture_env({})['TAIXU_SYS_PROC_FIXTURE']},
                                           Path(r['case']).parent.name)
                                          for r, _, exe, extra in remote], timeout=120, trees=trees)
        for row, exp, _, _ in remote:
            code, out, err = results.get(row['case'], (-2, b'', b'REMOTE_MISSING'))
            judge(row, exp, code, out, err, args.leak_check)
            counts[row['status']] = counts.get(row['status'], 0) + 1
            report_row(row)
    if remote_win:
        # 同 macOS：用例目录整棵带过去，产物在各自的用例目录里运行；getEnv 的正例夹具一并带过去。
        from windows_remote import run_batch as run_win_batch
        trees = {Path(r['case']).parent.name: ROOT / Path(r['case']).parent for r, _, _, _ in remote_win}
        results = run_win_batch(args.winarm, [(r['case'], exe, extra, {'TAIXU_SYS_PROC_FIXTURE': fixture_env({})['TAIXU_SYS_PROC_FIXTURE']},
                                               Path(r['case']).parent.name)
                                              for r, _, exe, extra in remote_win], timeout=120, trees=trees)
        for row, exp, _, _ in remote_win:
            code, out, err = results.get(row['case'], (-2, b'', b'REMOTE_MISSING'))
            judge(row, exp, code, out, err, args.leak_check)
            counts[row['status']] = counts.get(row['status'], 0) + 1
            report_row(row)
    print('SUMMARY ' + ' '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    if args.report:
        args.report.write_text(json.dumps({'counts': counts, 'rows': rows}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if counts.get('FAIL', 0) or counts.get('BUILD_FAIL', 0) or counts.get('REJECT_MISMATCH', 0) else 0)


if __name__ == '__main__':
    main()
