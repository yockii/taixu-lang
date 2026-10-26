"""doc/init 的原生 CLI 行为检查；输出基线来自 6d-6 已验证编译器。"""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def call(compiler, args, cwd=ROOT):
    return subprocess.run([str(compiler), *map(str, args)], cwd=cwd, capture_output=True, timeout=300)


def doc_json(compiler, path):
    p = call(compiler, ['doc', '--format=json', path])
    return json.loads(p.stdout) if p.returncode == 0 else None


def tool_rows(compiler):
    rows = []
    fixture = json.loads((ROOT/'tests/doc-native.json').read_text(encoding='utf-8'))
    templates = json.loads((ROOT/'tests/init-native.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='taixu-tools-') as temp:
        work = Path(temp)
        api = work/'api.tx'
        api.write_text(fixture['source'], encoding='utf-8', newline='\n')
        data = doc_json(compiler, api)
        rows.append(('doc:frozen-declarations', data is not None and data['decls'] == fixture['decls'], data))
        want = [('tests/nrt-cases/gen-basic.tx', 'function', 'function* range(from: int, to: int): int'),
                ('tests/nrt-cases/iface-values.tx', 'interface', 'interface Shape（2 个方法）'),
                ('tests/nrt-cases/ffi-linux-lib.tx', 'extern', 'declare function strlen(s: string): usize from "libc.so.6"')]
        for name, kind, signature in want:
            data = doc_json(compiler, ROOT/name)
            ok = data is not None and any(d['kind']==kind and d['signature']==signature for d in data['decls'])
            rows.append(('doc:syntax-'+name, ok, data))
        out = work/'doc/a/b'
        p = call(compiler, ['doc', '--format=json', '--out-dir='+str(out), 'examples'])
        paths = list(out.glob('*.json')) if out.exists() else []
        good = p.returncode == 0 and bool(paths)
        for path in paths:
            item = json.loads(path.read_text(encoding='utf-8'))
            good = good and isinstance(item.get('decls'), list)
        rows.append(('doc:out-dir', bool(good), p.stderr.decode('utf-8','replace')[-500:]))
        project = work/'projects'
        project.mkdir()
        expected = {}
        for case in templates['cases']:
            p = call(compiler, ['init', *case['args']], project)
            expected.update(case['files'])
            actual = {f.relative_to(project).as_posix(): f.read_text(encoding='utf-8') for f in project.rglob('*') if f.is_file()}
            ok = p.returncode == 0 and p.stdout.decode('utf-8').replace('\r\n','\n') == case['stdout'] and actual == expected
            rows.append(('init:frozen-'+'-'.join(case['args']), ok, p.stderr.decode('utf-8','replace')[-300:]))
        personal = project/'p1/main.tx'
        personal.write_bytes(b'// user content\n')
        p = call(compiler, ['init', 'p1'], project)
        rows.append(('init:no-overwrite', p.returncode == 0 and personal.read_bytes()==b'// user content\n', p.stderr.decode('utf-8','replace')))
        before = {f.relative_to(project): f.read_bytes() for f in project.rglob('*') if f.is_file()}
        for args in [['--template=desktop','desktop-project'], ['--template=gui','gui-project'], ['gui','project'],
                     ['bad/name'], ['--template=nope','p5'], ['a','b','c'], ['--help'], ['-x'], ['console','-p7']]:
            p = call(compiler, ['init', *args], project)
            after = {f.relative_to(project): f.read_bytes() for f in project.rglob('*') if f.is_file()}
            rows.append(('init:reject-'+' '.join(args), p.returncode == 64 and before == after, p.stderr.decode('utf-8','replace')[-300:]))
    return rows
