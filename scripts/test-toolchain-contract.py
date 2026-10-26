"""TOOL-98 public metadata, schema and compatibility regression gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from sync_toolchain_docs import product_version

ROOT = Path(__file__).resolve().parent.parent
EXE = '.exe' if os.name == 'nt' else ''
SCHEMAS = {'capabilities/1', 'changes/1', 'diag/1', 'tests/1', 'fmt/1',
           'doc/1', 'bench/1', 'disasm/1', 'compile/1', 'run/1', 'mcp-disasm/1', 'compile-profile/1', 'link-map/6', 'tool-error/1'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--report', type=Path)
    a = ap.parse_args()
    exe = a.compiler.resolve()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    rows = []

    def check(name, ok, detail=''):
        rows.append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:1000]})
        print('PASS' if ok else 'FAIL', name, '' if ok else str(detail)[:300], flush=True)

    def run(*args, input=None, timeout=180):
        return subprocess.run([exe, *map(str, args)], cwd=ROOT, input=input,
                              capture_output=True, timeout=timeout)

    def read(data):
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            return {}

    def source(name, text):
        path = work / (name + '.tx')
        path.write_text(text, encoding='utf-8', newline='\n')
        return path

    cap = run('capabilities', '--format=json')
    data = read(cap.stdout)
    c = data.get('capabilities', {})
    v = c.get('toolchain_version', '')
    check('product version agrees with single source', v == product_version(), v)
    check('capabilities schema', cap.returncode == 0 and data.get('schema') == 'capabilities/1', cap.stdout)
    # 产品版本为 SemVer，可带预发布后缀（0.3.0-beta.1；开发线另加 -dev）；同 sync_toolchain_docs.product_version 的规则。
    check('product is SemVer version', bool(re.fullmatch(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', v)), v)
    check('gate is separate', c.get('version_gate') == 'V0.52' and c.get('version_gate') != v, c)
    check('schema inventory', set(c.get('schemas', [])) == SCHEMAS and len(c.get('schemas', [])) == len(SCHEMAS), c)
    check('version CLI agrees', bool(v) and ('taixu ' + v + ' ') in run('--version').stdout.decode('utf8', 'replace'))
    plain = run('capabilities')
    check('human metadata agrees', bool(v) and ('toolchain_version=' + v) in plain.stdout.decode('utf8', 'replace')
          and 'diagnostic_version=1' in plain.stdout.decode('utf8', 'replace'))
    check('diagnostic version', c.get('diagnostic_version') == '1', c)
    registry = run('changes', '--format=json')
    changes = read(registry.stdout)
    entries = changes.get('changes', [])
    check('changes schema and version', registry.returncode == 0 and changes.get('schema') == 'changes/1'
          and changes.get('toolchain_version') == v, registry.stdout)
    summary = c.get('change_registry', {})
    check('registry SHA and count', summary.get('count') == len(entries) and len(entries) >= 2
          and summary.get('sha256') == hashlib.sha256((ROOT/'bootstrap/changes.tx').read_bytes()).hexdigest(), summary)
    check('registry identities', {'lang.literal-left-fixed-width', 'lang.literal-left-f32'} <= {e.get('id') for e in entries}
          and len({e.get('id') for e in entries}) == len(entries), entries)
    for e in entries:
        check('migration is not falsely automatic ' + e['id'],
              e.get('applicability') == 'unavailable' and e.get('gate') == 'alpha'
              and bool(re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', e.get('since', '')))
              and bool(e.get('fix')) and bool(e.get('implementedIn')))
        for key in ['d3', 'llmGuide']:
            check('registry document ' + e['id'] + ' ' + key,
                  (ROOT/e[key].split('#')[0]).is_file(), e[key])
        if e['id'] not in {'lang.literal-left-fixed-width', 'lang.literal-left-f32'}:
            continue
        for side in ['before', 'after']:
            path = source(e['id'].replace('.', '-') + '-' + side,
                          'export function main(args: []string): int { ' + e[side] + ' return 0; }\n')
            p = run('run', '--format=json', path)
            if e['kind'] == 2 and side == 'before':
                d = read(p.stderr)
                check('fixed-width left literal remains rejected', p.returncode == 1 and
                      d.get('schema') == 'diag/1' and any(x.get('code') == 3034 for x in d.get('diags', [])), p.stderr)
            else:
                want = b'false\n' if e['kind'] in [2, 3] and side == 'before' else (b'true\n' if e['kind'] == 3 else b'false\n')
                check('migration behavior ' + e['id'] + ' ' + side,
                      p.returncode == 0 and p.stdout.replace(b'\r\n', b'\n') == want and not p.stderr,
                      (p.returncode, p.stdout, p.stderr))
    for args in [('changes', 'bad'), ('changes', '--format=json', 'bad')]:
        p = run(*args)
        check('changes rejects invalid args ' + ' '.join(args), p.returncode == 64 and not p.stdout)

    for name, args, stdin, schema, code, stream in [
        ('doc', ['doc', '--format=json'], None, 'doc/1', 0, 'stdout'),
        ('fmt', ['fmt', '--check', '--format=json'], None, 'fmt/1', None, 'stdout'),
        ('bench', ['bench', '--rounds', '2', '--format=json'], None, 'bench/1', 0, 'stdout'),
        ('disasm', ['disasm', '--format=json'], None, 'disasm/1', 0, 'stdout'),
        ('test-pass', ['test', '--format=json'], None, 'tests/1', 0, 'stdout'),
        ('test-fail', ['test', '--format=json'], None, 'tests/1', 2, 'stdout'),
        ('compile-error', ['run', '--format=json'], None, 'diag/1', 1, 'stderr'),
        ('runtime-error', ['run', '--format=json'], None, 'diag/1', 3, 'stderr'),
    ]:
        body = 'export function main(args: []string): int { print("user-output"); return 0; }\n'
        if name == 'bench':
            body = 'export function main(args: []string): int { return 0; }\n'
        if name.startswith('test-'):
            body = 'test "contract" { assert(' + ('true' if name == 'test-pass' else 'false') + ', "contract"); }\n'
        elif name == 'compile-error':
            body = 'export function main(args: []string): int { return missing; }\n'
        elif name == 'runtime-error':
            body = 'export function main(args: []string): int { assert(false, "contract"); return 0; }\n'
        path = source(name, body)
        p = run(*args, path)
        doc = read(getattr(p, stream))
        check('JSON schema ' + name, (code is None or p.returncode == code) and doc.get('schema') == schema,
              (p.returncode, p.stdout, p.stderr))
        if name.startswith('test-'):
            check('test retains summary ' + name, doc.get('summary', {}).get('total') == 1 and len(doc.get('tests', [])) == 1, doc)

    path = source('raw-output', 'export function main(args: []string): int { print("{\\"schema\\":\\"user\\"}"); return 0; }\n')
    p = run('run', '--format=json', path)
    check('user stdout is raw', p.returncode == 0 and p.stdout.replace(b'\r\n', b'\n') == b'{"schema":"user"}\n' and not p.stderr)

    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'contract', 'version': '1'}}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'compile', 'arguments': {'path': str(path)}}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'run', 'arguments': {'path': str(path)}}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'test', 'arguments': {'path': str(work/'test-pass.tx')}}},
        {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call', 'params': {'name': 'run', 'arguments': {'path': str(work/'runtime-error.tx')}}},
        {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'disasm', 'arguments': {'path': str(path)}}},
    ]
    p = run('mcp', '--trust-workspace', '--workspace', ROOT, '--workspace', work, input=('\n'.join(json.dumps(r) for r in requests) + '\n').encode())
    responses = {r.get('id'): r for line in p.stdout.splitlines() if isinstance((r := read(line)), dict)}
    check('MCP protocol and version', p.returncode == 0 and responses.get(1, {}).get('result', {}).get('serverInfo', {}).get('version') == v and bool(v), p.stderr)
    for i, schema in [(2, 'compile/1'), (3, 'run/1'), (4, 'tests/1'), (5, 'mcp-disasm/1')]:
        r = responses.get(i, {})
        check('MCP application schema ' + schema, r.get('jsonrpc') == '2.0' and 'schema' not in r
              and r.get('result', {}).get('structuredContent', {}).get('schema') == schema, r)

    trapped = responses.get(6, {}).get('result', {}).get('structuredContent', {})
    check('MCP trap diagnostic schema', read(trapped.get('stderr', '')).get('schema') == 'diag/1', trapped)

    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'capabilities': {}}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown', 'params': None},
                {'jsonrpc': '2.0', 'method': 'exit'}]
    wire = b''
    for r in requests:
        b = json.dumps(r).encode()
        wire += ('Content-Length: %d\r\n\r\n' % len(b)).encode() + b
    p = run('lsp', input=wire)
    bodies = [read(x) for x in re.findall(rb'Content-Length: \d+\r\n\r\n(.*?)(?=Content-Length: |\Z)', p.stdout, re.S)]
    init = next((d for d in bodies if d.get('id') == 1), {})
    check('LSP protocol and version', p.returncode == 0 and bool(v) and
          init.get('result', {}).get('serverInfo', {}).get('version') == v and 'schema' not in init, p.stderr)

    inventory = json.loads((ROOT/'tests/diagnostic-codes.json').read_text(encoding='utf8'))
    lines = run('explain').stdout.decode('utf8').splitlines()
    actual = {m[1]: m[2] + ':' + m[3] for line in lines
              if (m := re.match(r'(\d{4}) \[(\w+):(\w+)\]', line))}
    check('current diagnostic inventory and version agree', actual == inventory['codes']
          and c.get('diagnostic_version') == inventory['version'], actual)
    built = run('build', 'bootstrap/selfhost/change-data-probe.tx', '--toolchain', '--out', work/'ast')
    check('native AST reader builds', built.returncode == 0, built.stderr)
    if built.returncode == 0:
        def allowed(path):
            p = subprocess.run([work/'ast'/('change-data-probe'+EXE), path], cwd=ROOT, capture_output=True, timeout=60)
            lines = p.stdout.decode('utf8').splitlines()
            kinds = [line.strip().split(' ', 1)[0] for line in lines if line.strip()]
            tops = [line.split(' ', 1)[0] for line in lines if line and not line.startswith(' ')]
            return p.returncode == 0 and bool(tops) and set(tops) <= {'struct', 'const'} and set(kinds) <= {
                'struct', 'field', 'tnamed', 'tslice', 'const', 'ty', 'tarray', 'array_lit', 'struct_lit', 'finit',
                'int', 'string', 'float', 'bool'}, p.stdout
        ok, detail = allowed(ROOT/'bootstrap/changes.tx')
        check('registry is native literal AST', ok, detail)
        for name, body in [('function', 'function f(): int { return 1; }\nconst x: int = 1;\n'),
                           ('call', 'const x: int = f();\n'),
                           ('import', 'import { x } from "./x";\nconst y: int = 1;\n')]:
            ok, detail = allowed(source('reject-'+name, body))
            check('data dialect rejects '+name, not ok, detail)

    path = ROOT/'docs/ai/toolchain-changes.md'
    human = run('changes')
    check('AI changes generated from registry', path.is_file() and human.returncode == 0 and
          path.read_bytes() == human.stdout.replace(b'\r\n', b'\n').rstrip(b'\n')+b'\n', human.stdout[:200])
    from tool_security_checks import security_checks
    security_checks(exe, work, check)
    result = {'compiler_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(), 'rows': rows,
              'status': 'PASS' if all(r['ok'] for r in rows) else 'FAIL'}
    report = a.report or work/'report.json'
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print(result['status'], len(rows), 'checks', report, flush=True)
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    sys.exit(main())
