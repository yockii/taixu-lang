"""NRT bring-up differential: build e2e cases with --runtime=native (no Zig host) and compare.

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

`--leak-check` builds every case with that option (exit-time leak check, TOOL-84 step 1):
results must match as before and no run may print a leak report (`[runtime:leak]`)
unless the case's own expectation names it (cases that leak on purpose). A `<case>.leakok`
companion (one line: why the program keeps objects alive on purpose, e.g. intentional
cycles) keeps that case out of the leak-check build.

`--target macos` (macOS adaptation M1, docs/design/v052-macos-m1-macho-v0.1-20261024.md E10) cross-builds every case
as an x86_64 Mach-O and runs the products in one batch on the macOS test host over ssh (`--remote <ssh host>`, default
$TAIXU_MACOS_HOST; on a macOS host the default is `local`, which runs them on this machine; scripts/macos_remote.py), each in
its own case directory as locally. Features the macOS target still
rejects at build time say "macOS 目标暂不支持" and count as PENDING (none since M3, which added tool builtins and --debug).
`--target aarch64-macos` (macOS adaptation M4c) does the same with arm64 Mach-O products run natively on the (Apple
silicon) test host; what the A64 target still rejects ("aarch64-macos 暂不支持": foreign functions until M4d, --debug and
tool builtins until M4e) counts as PENDING.

`--build-only` builds every case and checks only the build outcome (a product, or the expected compile-time
rejection): successful builds count as BUILT and are not run. The gate uses it for the arm64 products on hosts that
cannot run them. Every Mach-O product (both architectures, with or without --build-only) first passes the
independent checker scripts/macho_check.py; a rejected image counts as BUILD_FAIL.

`--debug` builds every case as an instrumented debug build (round 6 step 6c,
docs/design/v052-round6-debug-v0.1-20261018.md): run without a debug session the
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
    # tests/nrt-cases: language features newer than the frozen Zig seed (D3 v0.18+);
    # the Zig e2e harness does not scan this directory.
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


def skip_reason(tx, target):
    for only in ('windows', 'linux', 'macos'):
        if tx.with_suffix('.' + only).exists() and target != only:
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
    # 缺省为宿主自己的目标：macOS 宿主按处理器取 aarch64-macos 或 macos（x86_64），此前一律取 linux，门在 Mac 上编出 ELF 去跑。
    host = 'windows' if os.name == 'nt' else (('aarch64-macos' if platform.machine() == 'arm64' else 'macos') if sys.platform == 'darwin' else 'linux')
    ap.add_argument('--target', choices=['windows', 'linux', 'macos', 'aarch64-macos'], default=host,
                    help='linux on a Windows host runs the ELF products through wsl.exe; macos runs them on --remote over ssh')
    ap.add_argument('--remote', default='local' if sys.platform == 'darwin' else os.environ.get('TAIXU_MACOS_HOST', ''),
                    help='ssh host of the macOS test machine (--target macos); local runs on this Mac (the default there)')
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
    if args.target in ('macos', 'aarch64-macos') and not args.build_only and not args.remote:
        raise SystemExit('running macOS products from this host needs --remote <ssh host> (or TAIXU_MACOS_HOST)')
    linux = args.target == 'linux'
    a64 = args.target == 'aarch64-macos'
    macos = args.target == 'macos' or a64
    # 目标专属用例（<case>.windows／.linux／.macos）按操作系统挑选，两种 macOS 架构共用 .macos。
    os_name = 'macos' if macos else args.target
    via_wsl = linux and os.name == 'nt'
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
    for tx in cases(args.cases):
        name = str(tx.relative_to(ROOT)).replace('\\', '/')
        row = {'case': name}
        reason = skip_reason(tx, os_name)
        if reason:
            row['status'] = 'SKIP'; row['reason'] = reason
        else:
            exp = expected(tx)
            outdir = work / tx.stem
            outdir.mkdir(exist_ok=True)
            target_flag = ['--target=linux'] if linux else (['--target=aarch64-macos'] if a64 else (['--target=macos'] if macos else []))
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
                launch = [exe]
                if via_wsl:
                    launch = ['wsl.exe', '-e', os.path.relpath(exe, tx.parent).replace(os.sep, '/')]
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
    print('SUMMARY ' + ' '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    if args.report:
        args.report.write_text(json.dumps({'counts': counts, 'rows': rows}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if counts.get('FAIL', 0) or counts.get('BUILD_FAIL', 0) or counts.get('REJECT_MISMATCH', 0) else 0)


if __name__ == '__main__':
    main()
