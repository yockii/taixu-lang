#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug-build variable table gate (round 6 step 6c-4a, docs/design/v052-round6-debug-v0.1-20261018.md §四-3, §六).

Builds bootstrap/selfhost/dbg-vars-probe.tx with --toolchain, then runs it on a few programs. The probe compiles
a program as a debug build up to the ProgramContext (no product) and, for every function with variables, lists
the variables visible at each statement checkpoint (callbi 3073) as name@slot:type, plus one `all` line with every
recorded variable (switch arms made only of expressions have no statement checkpoint). The output must equal the
golden file byte for byte, once for each target's front end (`windows` and `linux` give the same table).

Usage: python scripts/test-native-debug-vars.py --compiler <taixu> [--work <dir>]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = os.name == 'nt'
CASES = [
    ('tests/debug/native/vars.tx', 'tests/debug/native/vars.dbgvars'),
    ('tests/debug/native/gen.tx', 'tests/debug/native/gen.dbgvars'),
    ('tests/debug/async.tx', 'tests/debug/native/async.dbgvars'),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, type=Path)
    ap.add_argument('--work', type=Path)
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    work = (a.work or Path(tempfile.mkdtemp(prefix='tx-dbgvars-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out = work / 'probe'
    shutil.rmtree(out, ignore_errors=True)
    p = subprocess.run([str(compiler), 'build', 'bootstrap/selfhost/dbg-vars-probe.tx', '--toolchain', '--out', str(out)],
                       cwd=str(ROOT), capture_output=True, timeout=1200)
    probe = out / ('dbg-vars-probe.exe' if WIN else 'dbg-vars-probe')
    if p.returncode != 0 or not probe.exists():
        print('FAIL  probe-build          %s' % (p.stdout + p.stderr).decode('utf-8', 'replace')[-600:])
        print('SUMMARY FAIL=1 (1 checks)')
        return 1
    print('PASS  probe-build')
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_')}
    fails = 0
    total = 1
    for src, golden in CASES:
        want = (ROOT / golden).read_bytes()
        for front in ('windows', 'linux'):
            total += 1
            r = subprocess.run([str(probe), src, front], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=600)
            got = r.stdout.replace(b'\r\n', b'\n')
            ok = r.returncode == 0 and got == want
            label = '%s:%s' % (Path(src).stem, front)
            if ok:
                print('PASS  %s' % label)
            else:
                fails += 1
                gl = got.decode('utf-8', 'replace').split('\n')
                wl = want.decode('utf-8', 'replace').split('\n')
                i = 0
                while i < min(len(gl), len(wl)) and gl[i] == wl[i]:
                    i += 1
                print('FAIL  %-20s exit=%d line %d: got %r want %r stderr=%r' % (
                    label, r.returncode, i + 1, gl[i] if i < len(gl) else None, wl[i] if i < len(wl) else None,
                    r.stderr.decode('utf-8', 'replace')[-300:]))
    print('SUMMARY %s (%d checks)' % ('PASS' if fails == 0 else 'FAIL=%d' % fails, total))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
