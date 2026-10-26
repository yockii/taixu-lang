#!/usr/bin/env python3
"""原生格式化门：冻结输出、语法保持、全语料幂等、CLI 读写与错误形态。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
GUARDED = ('examples', 'tests/cases', 'skill/taixu/examples', 'docs/design', 'bootstrap', 'spike', 'scripts')
NEW_SYNTAX = ('tests/nrt-cases', 'prelude')


def run(argv, cwd=ROOT):
    return subprocess.run(list(map(str, argv)), cwd=cwd, capture_output=True, timeout=600)


def tx_files(base):
    out = []
    for folder, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        out.extend(Path(folder) / name for name in sorted(names) if name.endswith('.tx'))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--report', type=Path)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    compiler = a.compiler.resolve()
    rows = []

    def record(name, ok, detail=''):
        rows.append({'check': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
        if not ok:
            print('FAIL', name, detail[:500], flush=True)

    fixture = json.loads((ROOT / 'tests/fmt-native.json').read_text(encoding='utf-8'))
    rejects = {r['file']: r['source_sha256'] for r in json.loads(
        (ROOT / 'tests/fmt-native-rejects.json').read_text(encoding='utf-8'))['cases']}
    p = run([compiler, 'fmt', '--check', *GUARDED])
    record('guarded fmt-check', p.returncode == 0, p.stderr.decode('utf-8', 'replace')[-500:])
    with tempfile.TemporaryDirectory(prefix='taixu-fmt-') as temp:
        work = Path(temp)
        probe_dir = work / 'probe'
        p = run([compiler, 'build', 'bootstrap/selfhost/fmt-parse-probe.tx', '--toolchain', '--out', probe_dir])
        record('parse probe build', p.returncode == 0, p.stderr.decode('utf-8', 'replace')[-500:])
        probe = probe_dir / ('fmt-parse-probe.exe' if os.name == 'nt' else 'fmt-parse-probe')
        for case in fixture['cases']:
            source = work / (case['id'] + '.tx')
            source.write_bytes(case['source'].encode('utf-8'))
            p = run([compiler, 'fmt', source])
            ok = p.returncode == case['exit'] and p.stdout == case['expected'].encode('utf-8')
            record('golden ' + case['id'], ok, p.stderr.decode('utf-8', 'replace')[-500:])
            if case.get('lex_invalid'):
                record('golden lexical rejection ' + case['id'], not p.stdout and '无法格式化' in p.stderr.decode('utf-8', 'replace'))
            elif p.returncode == 0:
                formatted = work / (case['id'] + '-formatted.tx')
                formatted.write_bytes(p.stdout)
                second = run([compiler, 'fmt', formatted])
                record('golden idempotence ' + case['id'], second.returncode == 0 and second.stdout == p.stdout)
                if case['parse'] and probe.exists():
                    parsed = run([probe, *([source] if case.get('parse_original') else []), formatted])
                    record('golden syntax ' + case['id'], parsed.returncode == 0, parsed.stderr.decode('utf-8', 'replace')[-500:])

        files = [p for directory in GUARDED + NEW_SYNTAX for p in tx_files(ROOT / directory)]

        def corpus(path):
            name = path.relative_to(ROOT).as_posix()
            p = run([compiler, 'fmt', path])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if rejects.get(name) == digest:
                ok = p.returncode == 0 and not p.stdout and '无法格式化' in p.stderr.decode('utf-8', 'replace')
                return name, ok, 'frozen lexical rejection' if ok else 'frozen invalid input unexpectedly accepted'
            if p.returncode != 0:
                return name, False, p.stderr.decode('utf-8', 'replace')[-300:]
            output = work / ('corpus-' + hashlib.sha256(name.encode()).hexdigest() + '.tx')
            output.write_bytes(p.stdout)
            again = run([compiler, 'fmt', output])
            return name, again.returncode == 0 and again.stdout == p.stdout, 'idempotence'
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            for name, ok, detail in pool.map(corpus, files):
                record('corpus ' + name, ok, detail)
        print('corpus files:', len(files), flush=True)

        # 用冻结的三个非规范输入检查目录遍历、默认不写、write 和 JSON。
        selected = [next(c for c in fixture['cases'] if c['id'] == name) for name in (
            'unit-direct-0', 'unicode-comment-string', 'crlf')]
        tree = work / 'tree'
        targets = [tree / 'a.tx', tree / 'nested/太虚.tx', tree / 'nested/b.tx']
        for path, case in zip(targets, selected):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(case['source'].encode('utf-8'))
        non_tx = tree / 'keep.txt'
        non_tx.write_bytes(b'unchanged\r\n')
        before = {p: p.read_bytes() for p in [*targets, non_tx]}
        p = run([compiler, 'fmt', '--check', tree])
        record('directory check rejects noncanonical', p.returncode == 1)
        p = run([compiler, 'fmt', tree])
        changed = sum(c['source'] != c['expected'] for c in selected)
        record('directory default reports without writing', p.returncode == 0 and not p.stdout and p.stderr.decode('utf-8', 'replace').count('需要格式化') == changed)
        record('default/check do not write', all(p.read_bytes() == data for p, data in before.items()))
        p = run([compiler, 'fmt', '--check', '--format=json', tree])
        try:
            doc = json.loads(p.stdout)
            found = doc['files']
            ok = p.returncode == 1 and len(found) == 3 and all(f['ok'] and isinstance(f['formatted'], bool) for f in found)
            ok = ok and doc['summary']['needs_format'] == changed and doc['summary']['failed'] == 0
        except (ValueError, KeyError, TypeError):
            ok = False
        record('directory JSON', ok, p.stdout.decode('utf-8', 'replace')[:500])
        p = run([compiler, 'fmt', '--write', tree])
        record('directory write frozen bytes', p.returncode == 0 and all(
            p.read_bytes() == c['expected'].encode('utf-8') for p, c in zip(targets, selected)))
        record('directory ignores other files', non_tx.read_bytes() == before[non_tx])
        p = run([compiler, 'fmt', '--check', tree])
        record('directory check after write', p.returncode == 0)
        forms = [('no paths', [], 64), ('write+check', ['--write', '--check', targets[0]], 64),
                 ('non-tx', [non_tx], 64), ('missing', [work/'missing.tx'], 1)]
        for name, argv, code in forms:
            p = run([compiler, 'fmt', *argv])
            record('usage ' + name, p.returncode == code and bool(p.stderr))
        p = run([compiler, 'fmt', '--format=json', work/'missing.tx'])
        try:
            files = json.loads(p.stdout)['files']
            ok = p.returncode == 1 and len(files) == 1 and files[0]['ok'] is False and bool(files[0]['msg'])
        except (ValueError, KeyError, TypeError):
            ok = False
        record('missing JSON', ok, p.stdout.decode('utf-8', 'replace')[:300])
    failed = sum(row['status'] == 'FAIL' for row in rows)
    print('SUMMARY', 'FAIL' if failed else 'PASS', 'checks=%d failures=%d' % (len(rows), failed), flush=True)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps({'compiler': str(compiler), 'rows': rows, 'failed': failed},
                                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return int(bool(failed))


if __name__ == '__main__':
    sys.exit(main())
