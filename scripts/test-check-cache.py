#!/usr/bin/env python3
"""原生检查缓存：冷/热逐项差分、工作计数、完整语料和连续 200 个结构变更。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BAD = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ((' ' + detail) if detail else ''), flush=True)
    if not ok:
        BAD.append(name)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path)
    ap.add_argument('--only', default='behavior,corpus,soak')
    a = ap.parse_args()
    work = a.work.resolve() if a.work else Path(tempfile.mkdtemp(prefix='taixu-cache-'))
    work.mkdir(parents=True, exist_ok=True)
    groups = set(a.only.split(','))
    compiler = a.compiler.resolve()
    p = subprocess.run([compiler, 'build', 'bootstrap/selfhost/check-cache-probe.tx', '--toolchain', '--leak-check', '--out', work / 'probe'],
                       cwd=ROOT, capture_output=True, timeout=600)
    (work / 'build.log').write_bytes(p.stdout + p.stderr)
    check('build --leak-check probe', p.returncode == 0, '' if p.returncode == 0 else p.stderr.decode('utf-8', 'replace')[-1200:])
    if p.returncode:
        return 1
    exe = work / 'probe' / ('check-cache-probe.exe' if os.name == 'nt' else 'check-cache-probe')

    def run(name, entry, revisions, target=0):
        config = work / (name + '.json')
        config.write_text(json.dumps({'entry': str(entry), 'target': target, 'revisions': revisions}), encoding='utf-8')
        p = subprocess.run([exe, config], cwd=ROOT, capture_output=True, timeout=3600)
        (work / (name + '.log')).write_bytes(p.stdout + p.stderr)
        ok = p.returncode == 0 and not p.stderr
        rows = []
        try:
            rows = [json.loads(line) for line in p.stdout.splitlines()]
            ok = ok and len(rows) == len(revisions)
        except (ValueError, TypeError):
            ok = False
        check(name + ' cold/miss/hit', ok, '' if ok else 'exit=%s %s' % (p.returncode, p.stderr.decode('utf-8', 'replace')[-1800:]))
        if ok:
            # A complete hit must neither parse source nor decode AST nor check a module body.
            check(name + ' hit counters', all(r['hit'][:3] == [0, 0, 0] and r['hit'][3] == r['hit'][5] for r in rows))
        return rows if ok else []

    def versions(folder, initial, changes):
        folder.mkdir(exist_ok=True)
        for path, text in initial.items():
            (folder / path).write_text(text, encoding='utf-8', newline='\n')
        texts = dict(initial)
        out = []
        for change in [{}] + changes:
            texts.update(change)
            out.append({'paths': [str(folder / p) for p in texts], 'texts': list(texts.values())})
        return out

    if 'behavior' in groups:
        entry_text = 'import { value } from "./left";\nimport { other } from "./right";\nfunction main(args: []string): int { return value() + other(); }\n'
        initial = {'main.tx': entry_text,
                   'left.tx': 'import { leaf } from "./leaf";\nexport function value(): int { return leaf(); }\n',
                   'leaf.tx': 'export function leaf(): int { return 1; }\n',
                   'right.tx': 'export function other(): int { return 7; }\n',
                   'extra.tx': 'export function extra(): int { return 9; }\n'}
        revisions = versions(work / 'graph', initial, [
            {'main.tx': entry_text + 'const added: int = 1;\n'},
            {'left.tx': initial['left.tx'].replace('return leaf();', 'return leaf() + 1;')},
            {'leaf.tx': initial['leaf.tx'].replace('return 1;', 'return 2;')},
            {'main.tx': entry_text},
            {'main.tx': 'import { extra } from "./extra";\n' + entry_text},
            {'main.tx': entry_text}])
        for target in (0, 1):
            rows = run('graph-%d' % target, work / 'graph/main.tx', revisions, target)
            if rows:
                check('graph-%d affected bodies only' % target, [r['miss'][2] for r in rows[:5]] == [4, 1, 2, 3, 1])
                check('graph-%d changed import graph' % target, all(r['miss'][2] == r['miss'][5] for r in rows[5:]))

        entry_text = 'import { identity } from "./generic";\nimport { other } from "./right";\nfunction main(args: []string): int { return other(); }\n'
        revisions = versions(work / 'generic', {'main.tx': entry_text,
            'generic.tx': 'export function identity<T>(x: T): T { const copy = x; return copy; }\n',
            'right.tx': 'export function other(): int { return 2; }\n'}, [
            {'main.tx': entry_text.replace('return other();', 'return identity<int>(1);')},
            {'main.tx': entry_text.replace('return other();', 'return len(identity<string>("x"));')}])
        for target in (0, 1):
            rows = run('generic-%d' % target, work / 'generic/main.tx', revisions, target)
            if rows:
                check('generic-%d new semantic edge' % target, rows[1]['miss'][4] >= 1 and rows[1]['miss'][3] == 1)
                check('generic-%d caller affects definition facts' % target, rows[2]['miss'][2] == 2)

        folder = work / 'metadata'
        revisions = versions(folder, {'main.tx': 'function main(args: []string): int { return bytesLen(embedBytes("payload.bin")); }\n'}, [])
        asset = folder / 'payload.bin'
        asset.write_bytes(b'x')
        revisions += [{**revisions[0], 'remove': str(asset)}, {**revisions[0], 'write': str(asset)}]
        rows = run('metadata', folder / 'main.tx', revisions)
        if rows:
            check('metadata changes with cached semantics', all(r['miss'][:3] == [0, 0, 0] for r in rows[1:]))

        folder = work / 'embed-order'
        text = 'import { load } from "./generic";\nfunction main(args: []string): int { const a = embedBytes("root-missing.bin"); return load<int>(1) + bytesLen(a); }\n'
        revisions = versions(folder, {'main.tx': text,
            'generic.tx': 'export function load<T>(x: T): int { return bytesLen(embedBytes("generic-missing.bin")); }\n'}, [
            {'main.tx': text + 'const bigger: int = 1;\n'}, {'main.tx': text}])
        run('embed-order', folder / 'main.tx', revisions)

        folder = work / 'instance-limit'
        text = 'struct Pair<A, B> { a: A, b: B, }\nfunction duplicate<T>(x: T): Ref<Pair<T, T>> { return new Pair<T, T> { a: x, b: x }; }\nfunction main(args: []string): int {\nconst v0 = 1;\n'
        text += ''.join('const v%d = duplicate(v%d);\n' % (i, i - 1) for i in range(1, 22))
        text += 'return 0;\n}\n'
        revisions = versions(folder, {'main.tx': text}, [{'main.tx': text + 'const changed: int = 1;\n'}])
        for revision in revisions:
            revision['must_code'] = 3020
        run('instance-limit', folder / 'main.tx', revisions)

        cases = ['tests/ana/closure.tx', 'tests/nrt-cases/iface-values.tx', 'tests/nrt-cases/iface-generics.tx',
                 'tests/nrt-cases/iface-bad-defcheck.tx', 'tests/nrt-cases/gen-generic.tx', 'tests/nrt-cases/gen-more.tx']
        for index, name in enumerate(cases):
            entry = ROOT / name
            text = entry.read_text(encoding='utf-8')
            rev = [{'paths': [str(entry)], 'texts': [text + '\n' + 'const cacheProbeExtra: int = %d;\n' % n]} for n in (1, 2)]
            run('advanced-%d' % index, entry, rev)

    if 'corpus' in groups:
        spec = importlib.util.spec_from_file_location('ana_cache_corpus', ROOT / 'scripts/test-ana.py')
        lib = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lib)
        for target in (0, 1):
            revisions = [{'entry': str(ROOT / name), 'paths': [], 'texts': [], 'toolchain': '--toolchain' in flags}
                         for flags, name in lib.corpus_entries()]
            run('corpus-%d' % target, ROOT / 'examples/hello.tx', revisions, target)

    if 'soak' in groups:
        folder = work / 'soak'
        text = 'import { leaf } from "./leaf";\nfunction main(args: []string): int { return leaf(); }\n'
        revisions = versions(folder, {'main.tx': text, 'leaf.tx': 'export function leaf(): int { return 1; }\n'}, [])
        paths = revisions[0]['paths']
        for i in range(200):
            altered = text + '\n' + '\n'.join('const cacheProbe%d: int = %d;' % (j, i + j) for j in range(1 + i % 2)) + '\n'
            revisions.append({'paths': paths, 'texts': [altered, 'export function leaf(): int { return 1; }\n']})
        rows = run('soak-200', folder / 'main.tx', revisions)
        if rows:
            check('200 structural changes reuse independent module', all(r['miss'][0] == r['miss'][2] == 1 and r['miss'][3] == 1 for r in rows[1:]))
    print('SUMMARY ' + ('PASS' if not BAD else 'FAIL=%d' % len(BAD)))
    return int(bool(BAD))


if __name__ == '__main__':
    sys.exit(main())
