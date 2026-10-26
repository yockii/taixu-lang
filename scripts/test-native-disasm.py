"""Native source assembly listing: source positions, CLI failures, and machine-code/fixup round trips.

aarch64-macos（macOS 适配 M4 设计 A11）没有文本汇编器可回环：探针逐行对照映像（每条指令行就是代码节里依次的一个字，
改写成标号、符号的操作数核对跳转距离与修补）。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    'tests/cases/09-float.tx', 'tests/cases/10-switch.tx', 'tests/cases/11-loop-concat.tx',
    'tests/cases/22-closure-fnarg.tx', 'tests/cases/36-generics.tx', 'tests/cases/38-generic-import.tx',
    'tests/cases/async-await-any.tx', 'tests/cases/async-tg-cancel.tx',
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', required=True, type=Path)
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    total = failed = 0

    def check(name, ok, detail=''):
        nonlocal total, failed
        total += 1
        failed += not ok
        print(('PASS ' if ok else 'FAIL ') + name + ((' ' + str(detail)[:400]) if not ok else ''), flush=True)

    def run(args, cwd=ROOT, timeout=120):
        return subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix='taixu-disasm-') as tmp:
        work = Path(tmp)
        probe_dir = work / 'probe'
        build = run([compiler, 'build', '--toolchain', 'bootstrap/selfhost/disasm-probe.tx', '--out', probe_dir], timeout=240)
        check('probe-build', build.returncode == 0, build.stderr.decode('utf-8', 'replace'))
        if build.returncode:
            return 1
        probe = probe_dir / ('disasm-probe.exe' if os.name == 'nt' else 'disasm-probe')
        for target in ('windows', 'linux'):
            for case in CASES:
                p = run([probe, case, target])
                check('roundtrip:' + target + ':' + case, p.returncode == 0 and p.stdout.startswith(b'PASS bytes='), p.stderr)
            for case in sorted((ROOT / 'tests/nrt-cases').glob('*.tx')):
                if not (case.name.startswith(('fw-', 'fw2-')) and case.with_suffix('.out').is_file()):
                    continue
                p = run([probe, case, target])
                check('roundtrip:' + target + ':' + case.name, p.returncode == 0, p.stderr)
            ffi_case = ROOT / ('tests/nrt-cases/ffi-win-call.tx' if target == 'windows' else 'tests/nrt-cases/ffi-linux-syscall.tx')
            p = run([probe, ffi_case, target])
            check('roundtrip:ffi-' + target, p.returncode == 0, p.stderr)
        a64_cases = CASES + sorted(str(c.relative_to(ROOT)) for c in (ROOT / 'tests/nrt-cases').glob('*.tx')
                                   if c.name.startswith(('fw-', 'fw2-', 'ffi-macos-')) and c.with_suffix('.out').is_file())
        for case in a64_cases:
            p = run([probe, case, 'aarch64'])
            check('listing:aarch64:' + case, p.returncode == 0 and p.stdout.startswith(b'PASS a64 '), p.stdout[-200:] + p.stderr)

        (work / 'lib.tx').write_text('export function plus(x: int): int {\n    return x + 1;\n}\n', encoding='utf-8')
        (work / 'main.tx').write_text('import { plus } from "./lib";\n\nfunction main(args: []string): int {\n    const x = plus(2);\n    print(toStr(x));\n    return x;\n}\n', encoding='utf-8')
        native_ops = {'windows': {'push', 'mov', 'call', 'ret'}, 'linux': {'push', 'mov', 'call', 'ret'}, 'aarch64-macos': {'stp', 'ldp', 'bl', 'ret'}}
        for target in ('windows', 'linux', 'aarch64-macos'):
            triple = target if '-' in target else 'x86_64-' + target
            cmd = [compiler, 'disasm', '--target=' + target, '--format=json', 'main.tx']
            p = run(cmd, work)
            check('json:' + target, p.returncode == 0 and not p.stderr, p.stderr)
            if p.returncode:
                continue
            doc = json.loads(p.stdout)
            check('target:' + target, doc.get('target') == triple and doc.get('truncated') is False, doc)
            functions = doc['functions']
            check('functions:' + target, {'main', 'plus'} <= {f['name'] for f in functions}
                  and len({f['index'] for f in functions}) == len(functions), functions)
            rows = [r for f in functions for r in f['instructions']]
            check('rows:' + target, rows and all(isinstance(r.get('fn'), str) and isinstance(r.get('op'), str)
                  and isinstance(r.get('operands'), list) and all(isinstance(x, str) for x in r['operands'])
                  and isinstance(r.get('line'), int) and r['line'] >= 0 and isinstance(r.get('pc'), int)
                  and r.get('kind') in ('label', 'instruction') for r in rows), rows[:1])
            plus_lines = {r['line'] for f in functions if f['name'] == 'plus' for r in f['instructions']}
            main_lines = {r['line'] for f in functions if f['name'] == 'main' for r in f['instructions']}
            check('source-lines:' + target, 2 in plus_lines and {4, 5, 6} <= main_lines and 0 in main_lines, (plus_lines, main_lines))
            check('native-instructions:' + target, native_ops[target] <= {r['op'] for r in rows} and '.inst' not in {r['op'] for r in rows}
                  and any(r['op'] == '.label' and r['kind'] == 'label' for r in rows), rows[:1])
            again = run(cmd, work)
            check('deterministic:' + target, again.stdout == p.stdout and again.returncode == 0, again.stderr)
            plain = run([compiler, 'disasm', '--target=' + target, 'main.tx'], work)
            check('text:' + target, plain.returncode == 0 and b'function main' in plain.stdout and b'function plus' in plain.stdout
                  and (b'bl ' if target == 'aarch64-macos' else b'call') in plain.stdout and b'f_' in plain.stdout, plain.stderr)
            library = run([compiler, 'disasm', '--target=' + target, '--format=json', 'lib.tx'], work)
            check('no-main:' + target, library.returncode == 0 and any(f['name'] == 'plus' for f in json.loads(library.stdout)['functions']), library.stderr)
            probe_run = run([probe, 'main.tx', 'aarch64' if target == 'aarch64-macos' else target], work)
            check('roundtrip:fixture-' + target, probe_run.returncode == 0, probe_run.stderr)

        for args in ([], ['--format=yaml', 'main.tx'], ['main.tx', 'lib.tx'], ['--target=other', 'main.tx']):
            p = run([compiler, 'disasm', *args], work)
            check('usage:' + repr(args), p.returncode == 64 and not p.stdout, p.stderr)
        for name, source in (('syntax.tx', 'function main( {\n'), ('sema.tx', 'function f(): int { return true; }\n')):
            (work / name).write_text(source, encoding='utf-8')
            p = run([compiler, 'disasm', '--format=json', name], work)
            check('diagnostic:' + name, p.returncode == 1 and not p.stdout and json.loads(p.stderr).get('diags'), p.stderr)
        p = run([compiler, 'disasm', '--format=json', 'absent.tx'], work)
        check('missing', p.returncode == 1 and not p.stdout and json.loads(p.stderr).get('diags'), p.stderr)
        (work / 'effect.tx').write_text('function main(args: []string): int { writeExecutable("side-effect", bytesFromStr("x")) catch (e) {}; return 0; }\n', encoding='utf-8')
        denied = run([compiler, 'disasm', 'effect.tx'], work)
        allowed = run([compiler, 'disasm', '--toolchain', 'effect.tx'], work)
        check('toolchain', denied.returncode == 1 and allowed.returncode == 0, (denied.stderr, allowed.stderr))
        check('not-executed', not (work / 'side-effect').exists())
        check('no-products', not list(work.glob('*.exe')) and not list(work.glob('*.txc')) and not list(work.glob('*.s')))
    print('SUMMARY %s (%d checks)' % ('PASS' if not failed else 'FAIL=%d' % failed, total))
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
