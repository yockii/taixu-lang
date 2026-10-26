"""Human/AI compiler profile contract, independent disasm/product/context checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PHASES = ['load+parse', 'sema', 'codegen', 'records', 'emit+assemble', 'data+tables', 'link']
CODEGEN_DETAILS = ['seb1-parse-diagnostics', 'seb1-parse', 'fab1-parse', 'generate']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    work = a.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    rows = []

    def check(name, ok, detail=''):
        rows.append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:1000]})
        print('PASS' if ok else 'FAIL', name, '' if ok else str(detail)[:400], flush=True)

    def run(*args, timeout=240):
        return subprocess.run([compiler, *map(str, args)], cwd=work, capture_output=True, timeout=timeout)

    def profile(path, *flags):
        p = run('profile', 'compile', path, '--format=json', *flags)
        (work/'last-profile.stdout').write_bytes(p.stdout)
        (work/'last-profile.stderr').write_bytes(p.stderr)
        return p, json.loads(p.stdout)

    source_dir = work/"画像 空间's"; source_dir.mkdir(exist_ok=True)
    entry = source_dir/'测量.tx'
    marker = work/'target-executed.txt'
    functions = ['function f%d(x: int): int { return x + %d; }' % (i, i) for i in range(26)]
    entry.write_text('\n'.join(functions) + '\nexport function main(args: []string): int {\n'
                     + 'writeFile(' + json.dumps(marker.as_posix(), ensure_ascii=False) + ', "EXECUTED") catch (e) {};\n'
                     + 'print(toStr(f25(len(args)))); return 0;\n}\n', encoding='utf8', newline='\n')
    bad = source_dir/'错误.tx'
    bad.write_text('export function main(args: []string): int { return unknownName; }\n', encoding='utf8', newline='\n')
    module = source_dir/'库.tx'
    module.write_text('export function identity(x: int): int { return x; }\n', encoding='utf8', newline='\n')

    cap = json.loads(run('capabilities', '--format=json').stdout)['capabilities']
    check('discovery', cap.get('profile') == 'compile' and cap.get('profile_schema') == 'compile-profile/1'
          and 'compile-profile/1' in cap.get('schemas', []), cap)
    help_out = run('profile', 'compile', '--help')
    check('help for human and AI', help_out.returncode == 0 and b'--top 0' in help_out.stdout
          and b'compile-profile/1' in help_out.stdout and b'JSON' in help_out.stdout)

    # 目标写全三元组：arm64 宿主上裸 macos 落到 aarch64-macos，本用例核对的是 x86_64 的清单与计数。
    for target in ['windows', 'linux', 'macos']:
        p, doc = profile(entry, '--target=x86_64-'+target, '--top', '0')
        x = doc.get('profile') or {}; functions = x.get('functions', []); summary = x.get('summary', {})
        check('profile:'+target, p.returncode == 0 and not p.stderr and doc.get('ok') is True
              and doc.get('schema') == 'compile-profile/1' and x.get('target') == 'x86_64-'+target, p.stderr)
        if p.returncode:
            continue
        (work/('profile-'+target+'.json')).write_bytes(p.stdout)
        numeric_keys = ['input_ir_words', 'bridge_ir_words_copied', 'bridge_fn_nodes', 'bridge_const_nodes',
                        'bridge_line_nodes', 'record_ir_words', 'record_parts', 'record_utf8_bytes', 'record_wall_ns',
                        'native_instructions', 'native_operand_uses', 'immediate_uses', 'zero_immediate_uses', 'memory_operand_uses',
                        'analysis_cells']
        check('exact numeric encoding:'+target, x.get('numeric_encoding') == 'decimal-string/int64'
              and all(isinstance(f[k], str) and re.fullmatch(r'0|[1-9][0-9]*', f[k]) for f in functions for k in numeric_keys))
        budget = int(summary['analysis_budget_cells'])
        check('analysis budget facts:'+target, budget > 0 and all(f['analysis_skipped'] is (int(f['analysis_cells']) > budget) for f in functions)
              and int(summary['analysis_skipped_functions']) == sum(f['analysis_skipped'] for f in functions) == 0
              and int(summary['analysis_max_cells']) == max(int(f['analysis_cells']) for f in functions))
        codegen = next(int(p['wall_ns']) for p in x['phases'] if p['name'] == 'codegen')
        check('codegen details and text hops:'+target, [p['name'] for p in x['codegen_details']] == CODEGEN_DETAILS
              and sum(int(p['wall_ns']) for p in x['codegen_details']) <= codegen
              and int(x['text_hops']['seb1_utf8_bytes']) > 0 and int(x['text_hops']['fab1_utf8_bytes']) > 0
              and x['text_hops']['record_utf8_bytes'] == summary['record_utf8_bytes'], x.get('text_hops'))
        check('complete function list:'+target, not x['truncated'] and len(functions) >= 28
              and int(summary['functions_total']) == int(summary['functions_returned']) == len(functions))
        check('default stable ranking:'+target, [(int(f['record_parts']), -f['id']) for f in functions]
              == sorted([(int(f['record_parts']), -f['id']) for f in functions], reverse=True))
        check('complete monotonic phases:'+target, [p['name'] for p in x['phases']] == PHASES
              and all(int(p['wall_ns']) >= 0 for p in x['phases'])
              and sum(int(p['wall_ns']) for p in x['phases']) <= int(x['measurement']['wall_ns']))
        check('scope and unknowns:'+target, x['measurement']['instrumented'] is True
              and x['measurement']['overhead_included'] is True
              and all(x['measurement'][k] is None for k in ['cpu_ns', 'peak_rss_bytes', 'heap_allocations', 'acceptance_verdict'])
              and x['coverage']['runtime_frequencies'] is False and 'runtime' in x['coverage']['excluded_native_counts'])
        check('record partition:'+target, int(summary['function_record_parts']) + 4 == int(summary['record_parts'])
              and sum(int(f['record_parts']) for f in functions) == int(summary['function_record_parts'])
              and sum(int(f['record_utf8_bytes']) for f in functions) == int(summary['function_record_utf8_bytes']))
        check('bridge partitions:'+target, int(summary['bridge_fn_nodes']) == len(functions)
              and all(f['input_ir_words'] == f['bridge_ir_words_copied'] and f['bridge_fn_nodes'] == '1' for f in functions))

        d = run('disasm', entry, '--target=x86_64-'+target, '--format=json')
        listing = json.loads(d.stdout)
        check('independent listing:'+target, d.returncode == 0 and listing['truncated'] is False, d.stderr)
        by_id = {f['index']: f for f in listing['functions']}
        totals = {'native_instructions': 0, 'native_operand_uses': 0, 'immediate_uses': 0,
                  'zero_immediate_uses': 0, 'memory_operand_uses': 0}
        mismatch = []
        for f in functions:
            other = by_id[f['id']]
            ins = [row for row in other['instructions'] if row['kind'] == 'instruction']
            ops = [op for row in ins for op in row['operands']]
            numbers = [int(op) for op in ops if re.fullmatch(r'-?\d+', op)]
            expected = {'native_instructions': len(ins), 'native_operand_uses': len(ops), 'immediate_uses': len(numbers),
                        'zero_immediate_uses': numbers.count(0), 'memory_operand_uses': sum('[' in op for op in ops)}
            if f['name'] != other['name'] or f['module'] != other['module'] or any(int(f[k]) != v for k, v in expected.items()):
                mismatch.append((f['id'], expected, f))
            for k, v in expected.items():
                totals[k] += v
        check('independent instruction/operand counts:'+target, not mismatch and len(by_id) == len(functions), mismatch[:1])
        check('unfiltered summary counts:'+target, all(int(summary[k]) == v for k, v in totals.items()))
        build = run('build', entry, '--target=x86_64-'+target, '--out', work/('ordinary-'+target))
        product = work/('ordinary-'+target)/(entry.stem+('.exe' if target == 'windows' else ''))
        check('production identity:'+target, build.returncode == 0 and product.is_file()
              and hashlib.sha256(product.read_bytes()).hexdigest() == x['product']['sha256']
              and len(product.read_bytes()) == int(x['product']['bytes']), build.stderr)
        _, top = profile(entry, '--target=x86_64-'+target, '--top', '1', '--sort=instructions')
        top = top['profile']
        check('visible top and full totals:'+target, top['truncated'] and len(top['functions']) == 1
              and top['summary']['functions_total'] == summary['functions_total']
              and top['summary']['native_instructions'] == summary['native_instructions']
              and top['functions'][0]['id'] == min(functions, key=lambda f: (-int(f['native_instructions']), f['id']))['id'])
        _, default = profile(entry, '--target=x86_64-'+target)
        check('default visible cap:'+target, default['profile']['truncated']
              and default['profile']['summary']['functions_returned'] == '20')
        _, timed = profile(entry, '--target=x86_64-'+target, '--sort=record-time', '--top', '0')
        ordered = timed['profile']['functions']
        check('timed sorting:'+target, [(int(f['record_wall_ns']), -f['id']) for f in ordered]
              == sorted([(int(f['record_wall_ns']), -f['id']) for f in ordered], reverse=True))
        text = run('profile', 'compile', entry, '--target=x86_64-'+target, '--top', '1')
        rendered = text.stdout.decode('utf8')
        check('human summary and scope:'+target, text.returncode == 0 and summary['record_parts'] in rendered
              and '记录桥接' in rendered and '零立即数' in rendered and '未写出、未运行' in rendered
              and '文本中转：SEB1 ' + x['text_hops']['seb1_utf8_bytes'] in rendered and '分析预算 ' + summary['analysis_budget_cells'] in rendered
              and '--top 0' in rendered and x['product']['sha256'] in rendered)
        check('no execution/write:'+target, not marker.exists() and not list(source_dir.glob('*.exe'))
              and all(not x['product'][k] for k in ['written', 'executed']))
    p, library = profile(module, '--top', '0')
    check('module without main', p.returncode == 0 and library['ok'], p.stderr)

    # 超过分析预算（指令数×寄存器数）的函数整段跳过分析：3000 个局部量约 1.2 万 IR 字 × 3000 余个寄存器。
    big = source_dir/'大函数.tx'
    body = ['    let v0 = x;'] + ['    let v%d = v%d + %d;' % (i, i - 1, i) for i in range(1, 3000)]
    big.write_text('export function big(x: int): int {\n' + '\n'.join(body) + '\n    return v2999;\n}\n'
                   + 'export function main(args: []string): int { print(toStr(big(len(args)))); return 0; }\n', encoding='utf8', newline='\n')
    p, doc = profile(big, '--top', '0')
    if check('profile:budget', p.returncode == 0 and doc.get('ok') is True, p.stderr):
        x = doc['profile']; budget = int(x['summary']['analysis_budget_cells'])
        skipped = [f['name'] for f in x['functions'] if f['analysis_skipped']]
        check('analysis budget exceeded', skipped == ['big'] and x['summary']['analysis_skipped_functions'] == '1'
              and int(x['summary']['analysis_max_cells']) > budget
              and all(f['analysis_skipped'] is (int(f['analysis_cells']) > budget) for f in x['functions']), skipped)

    for name, args in [('unknown', ['run', entry]), ('missing-entry', ['compile']), ('top-negative', ['compile', entry, '--top', '-1']),
                       ('top-overflow', ['compile', entry, '--top', '9223372036854775808']),
                       ('bad-sort', ['compile', entry, '--sort=bogus']), ('bad-target', ['compile', entry, '--target=bogus']),
                       ('conflicting-format', ['compile', entry, '--format=text'])]:
        p = run('profile', *args, '--format=json'); error = json.loads(p.stdout)
        check('usage:'+name, p.returncode == 64 and not p.stderr and error['schema'] == 'compile-profile/1'
              and not error['ok'] and error['profile'] is None and error['error']['kind'] == 'usage')
    for name, path in [('semantic', bad), ('missing-file', work/'no-such-file.tx')]:
        p, error = profile(path)
        check('compile-failure:'+name, p.returncode == 1 and not p.stderr and error['profile'] is None
              and error['error']['kind'] == 'compile' and error['error']['message'] != '编译失败', error)
    text_fail = run('profile', 'compile', bad)
    check('human actionable failure', text_fail.returncode == 1 and not text_fail.stdout
          and b'unknownName' in text_fail.stderr, text_fail.stderr)
    read_only_output = work/'read-only-stdout.txt'
    read_only_output.write_bytes(b'UNCHANGED')
    with read_only_output.open('rb') as sink:
        io_failure = subprocess.run([compiler, 'profile', 'compile', '--format=json'], cwd=work,
                                    stdout=sink, stderr=subprocess.PIPE, timeout=30)
    check('output failure is observable', io_failure.returncode == 3
          and '输出写入失败' in io_failure.stderr.decode('utf8', 'replace')
          and read_only_output.read_bytes() == b'UNCHANGED', io_failure.stderr)

    probe_dir = work/'probe'
    built = run('build', ROOT/'bootstrap/native_compile_profile_probe.tx', '--toolchain', '--leak-check', '--out', probe_dir)
    check('ownership probe build', built.returncode == 0, built.stderr)
    if built.returncode == 0:
        for target in ['windows', 'linux']:
            probe = subprocess.run([probe_dir/('native_compile_profile_probe'+('.exe' if os.name == 'nt' else '')), entry, target],
                                   cwd=work, capture_output=True, timeout=240)
            check('native context/ownership/reentry:'+target, probe.returncode == 0 and not probe.stderr
                  and probe.stdout.rstrip().endswith(b'PASS_PROFILE_CONTEXT_OWNERSHIP_REENTRY_PRECISION'), probe.stderr)
            if probe.returncode == 0:
                payload = json.loads(probe.stdout.splitlines()[0])['profile']
                f = payload['functions'][0]
                check('independent int64 and string escaping:'+target,
                      f['record_parts'] == '9007199254740993' and f['name'] == '引用"\\\n太虚' and f['id'] == 0)
    failures = sum(not r['ok'] for r in rows)
    (work/'report.json').write_text(json.dumps({'rows': rows, 'failures': failures}, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print('SUMMARY PASS' if not failures else 'SUMMARY FAIL', len(rows), 'checks', failures, 'failures', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
