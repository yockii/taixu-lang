#!/usr/bin/env python3
"""编译器工作区连续结构编辑：同步 hover 正确、关闭入口后零泄漏，记录实际响应耗时。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--source', type=Path, default=ROOT)
    ap.add_argument('--work', type=Path)
    ap.add_argument('--changes', type=int, default=200)
    ap.add_argument('--skip-build', action='store_true', help='use an already leak-instrumented compiler (development only)')
    a = ap.parse_args()
    if a.changes < 1:
        ap.error('--changes must be positive')
    source = a.source.resolve()
    work = a.work.resolve() if a.work else Path(tempfile.mkdtemp(prefix='taixu-lsp-cache-'))
    work.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('lsp_cache_session', ROOT / 'scripts/lsp-e2e.py')
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    lib.ROOT = source
    entry = source / 'bootstrap/taixu.tx'
    original = entry.read_text(encoding='utf-8')
    uri = entry.as_uri()
    compiler = a.compiler.resolve()
    if not a.skip_build:
        with (work / 'build.log').open('wb') as out:
            build = subprocess.run([compiler, 'build', 'bootstrap/taixu.tx', '--toolchain', '--leak-check', '--out', work / 'compiler'],
                                   cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, timeout=900)
        if build.returncode:
            print('FAIL build leak-instrumented compiler, see', work / 'build.log')
            return 1
        compiler = work / 'compiler' / ('taixu.exe' if os.name == 'nt' else 'taixu')
    session = lib.Session(str(compiler), args=('lsp', '--stdio', '--toolchain', '--workspace', str(source), '--workspace', str(work)))
    rows, failures, rss = [], [], []

    def memory():
        if os.name == 'nt':
            import ctypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in (
                    'peak', 'working', 'peak_pool_paged', 'pool_paged', 'peak_pool_nonpaged', 'pool_nonpaged', 'pagefile', 'peak_pagefile')]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            get = ctypes.WinDLL('psapi').GetProcessMemoryInfo
            get.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
            if get(int(session.p._handle), ctypes.byref(c), c.cb):
                return c.working
        else:
            p = Path('/proc') / str(session.p.pid) / 'status'
            if p.exists():
                for line in p.read_text().splitlines():
                    if line.startswith('VmRSS:'):
                        return int(line.split()[1]) * 1024
        return None

    try:
        assert session.init(), 'initialize failed'
        for i in range(a.changes + 1):
            value = '"x"' if i % 2 else str(i)
            text = original + '\nconst lspCacheSoakValue = ' + value + ';\n'
            if i % 2:
                text += 'const lspCacheSoakExtra: int = %d;\n' % i
            offset = text.rindex('lspCacheSoakValue')
            position = {'line': text[:offset].count('\n'),
                        'character': len(text[:offset].rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}
            started = time.perf_counter_ns()
            if i == 0:
                session.open(uri, text)
            else:
                session.change(uri, i + 1, [{'text': text}])
            rid = 1000 + i
            session.send({'jsonrpc': '2.0', 'id': rid, 'method': 'textDocument/hover',
                          'params': {'textDocument': {'uri': uri}, 'position': position}})
            response = session.wait_id(rid, timeout=180)
            elapsed = time.perf_counter_ns() - started
            expected = 'string' if i % 2 else 'int'
            body = json.dumps((response or {}).get('result'), ensure_ascii=False)
            if not response or 'error' in response or 'lspCacheSoakValue' not in body or (': ' + expected) not in body:
                failures.append('revision %d hover %r' % (i, response))
                break
            for note in session.notes:
                if note.get('method') == 'textDocument/publishDiagnostics' and note['params']['diagnostics']:
                    failures.append('revision %d diagnostics %r' % (i, note['params']))
            session.notes.clear()
            rows.append({'revision': i, 'ns': elapsed})
            if i % 20 == 0 or i == a.changes:
                current = memory()
                rss.append({'revision': i, 'bytes': current})
                print('revision=%d seconds=%.3f rss=%s' % (i, elapsed / 1e9, current), flush=True)
            if failures:
                break
        session.close(uri)
    finally:
        code = session.finish()
    stderr = b''.join(session.err).decode('utf-8', 'replace')
    if code != 0 or stderr:
        failures.append('exit=%r stderr=%s' % (code, stderr))
    hot = sorted(row['ns'] for row in rows if row['revision'] > 0)
    report = {'requested_changes': a.changes, 'samples': rows, 'rss': rss, 'exit': code,
              'stderr': stderr, 'failures': failures, 'status': 'FAIL' if failures else 'PASS',
              'median_hot_ns': statistics.median(hot) if hot else None,
              'p95_hot_ns': hot[max(0, (len(hot) * 95 + 99) // 100 - 1)] if hot else None,
              'note': 'LSP response measurements; D102 and compiler regression timing use separate formal windows.'}
    (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    for failure in failures:
        print('FAIL', failure)
    print('SUMMARY', report['status'], 'changes=%d' % max(0, len(rows) - 1),
          'median-hot=%.3fs' % ((report['median_hot_ns'] or 0) / 1e9))
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
