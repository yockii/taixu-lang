"""Native CLI behavior against declarative companions and frozen doc/init fixtures.

`.out` requires exit 0 and exact CR-normalized stdout. `.err` / `.test` require
their declared exit and every nonempty expectation line. JSON variants check
structured diagnostics/test results. Target-only cases skip on the other host;
retired VM flags are named skips. Native disassembly has its own gate.

Without explicit cases, check every emitted diagnostic's explain entry, native
capabilities, frozen doc/init behavior, newer-syntax docs, safe template creation,
gui/ root discovery and internal-builtin rejection, and debug/DAP/MCP flag errors.
The GUI engine is external; only pure layout and root-resolution behavior remain.
Frozen behavior was captured before the Zig cleanup; no Zig executable is used.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('nrt_products', ROOT / 'scripts' / 'test-nrt-products.py')
nrt_products = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nrt_products)

RETIRED_FLAGS = ('--mem-report', '--tier', '--format', '--debug', '--replay', '--frame', '--out-dir', '--auto')
COMPANIONS = ('.out', '.err', '.test', '.testjson', '.json', '.disasm', '.disasmjson')


def companion(tx, ext):
    p = tx.with_suffix(ext)
    return p.read_text(encoding='utf-8').replace('\r\n', '\n') if p.exists() else None


def exit_of(content, default):
    first = content.split('\n', 1)[0]
    if first.startswith('exit='):
        try:
            return int(first[5:].strip())
        except ValueError:
            return default
    return default


def needles(content):
    return [line for line in content.split('\n')[1:] if line.strip()]


def classify(tx):
    """([(kind, expected exit, expectation, extra flags, row suffix)...], None), or (None, reason).
    Precedence follows build_tools/e2e_runner.zig, except that `.test` and `.testjson` both run."""
    specs = []
    t = companion(tx, '.test')
    if t is not None:
        specs.append(('test', exit_of(t, 2), needles(t), [], ''))
    tj = companion(tx, '.testjson')
    if tj is not None:
        specs.append(('test', exit_of(tj, 2), needles(tj), ['--format=json'], ' [json]' if specs else ''))
    if specs:
        return specs, None
    j = companion(tx, '.json')
    if j is not None:
        return [('err', exit_of(j, 1), needles(j), ['--format=json'], '')], None
    for ext in ('.disasm', '.disasmjson'):
        if tx.with_suffix(ext).exists():
            return None, 'retired-cli-only:' + ext
    e = companion(tx, '.err')
    if e is not None:
        return [('err', exit_of(e, 1), needles(e), [], '')], None
    o = companion(tx, '.out')
    if o is not None:
        return [('out', 0, o, [], '')], None
    return None, 'no-companion'


def explain_rows(compiler):
    """Rows for the `taixu explain` table check: (name, ok, detail)."""
    srcs = list((ROOT / 'bootstrap').glob('*.tx')) + list((ROOT / 'bootstrap' / 'selfhost').glob('*.tx'))
    pat = re.compile(r'(?:emitDiag\([^,]+,\s*|recordErr\w*\([^,]+,\s*|error |export const C_\w+: int = )([1-3][0-9]{3})(?![0-9])')
    want = set()
    for f in srcs:
        want.update(int(m) for m in pat.findall(f.read_text(encoding='utf-8')))
    names = re.search(r'const RUNTIME_NAMES: string = "([^"]+)"', (ROOT / 'bootstrap/selfhost/nrt.tx').read_text(encoding='utf-8'))
    runtime = {4001 + i: n for i, n in enumerate(names.group(1).split())}
    p = subprocess.run([str(compiler), 'explain'], capture_output=True, timeout=60)
    listed = {}
    for line in p.stdout.decode('utf-8', 'replace').splitlines():
        m = re.match(r'(\d{4}) \[(\w+):(\w+)\] ', line)
        if m:
            listed[int(m.group(1))] = (m.group(2), m.group(3))
    rows = [('explain:list', p.returncode == 0 and len(listed) > 0, 'exit=%d entries=%d' % (p.returncode, len(listed)))]
    missing = sorted(c for c in want if c not in listed)
    rows.append(('explain:compiler-codes', not missing, 'codes=%d missing=%s' % (len(want), missing)))
    bad = sorted(c for c, n in runtime.items() if listed.get(c, ('', ''))[1] != n or listed.get(c, ('', ''))[0] != 'runtime')
    rows.append(('explain:runtime-codes', not bad, 'codes=%d mismatched=%s' % (len(runtime), bad)))
    one = subprocess.run([str(compiler), 'explain', '3002'], capture_output=True, timeout=60)
    text = one.stdout.decode('utf-8', 'replace')
    rows.append(('explain:one', one.returncode == 0 and text.startswith('3002 [sema:type_mismatch] ') and '\n修复建议：' in text, 'exit=%d' % one.returncode))
    unk = subprocess.run([str(compiler), 'explain', '9999'], capture_output=True, timeout=60)
    rows.append(('explain:unknown', unk.returncode == 64 and '未知诊断码 9999' in unk.stderr.decode('utf-8', 'replace'), 'exit=%d' % unk.returncode))
    cap = subprocess.run([str(compiler), 'capabilities', '--format=json'], capture_output=True, timeout=60)
    try:
        caps = json.loads(cap.stdout.decode('utf-8'))['capabilities']
        # 6c-7：调试三面（run --debug、dap、mcp 的 debug_*）为 native；6d-2：lsp 为 native，lsp_features 列已做的请求
        # （6d-3a2 增 definition、references、rename，6d-3a3 增 semanticTokens，6d-3b1 增 hover）。
        mcp_tools = 'compile,run,test,disasm,debug_start,debug_breakpoint,debug_step,debug_continue,debug_frames,debug_inspect,debug_stops,debug_kill'
        cap_ok = cap.returncode == 0 and caps.get('fmt') == 'native' and caps.get('explain') == 'native' \
            and caps.get('run_debug') == 'native' and caps.get('dap') == 'native' and caps.get('mcp') == 'native' \
            and caps.get('mcp_tools') == mcp_tools and caps.get('lsp') == 'native' \
            and caps.get('lsp_features') == 'diagnostics,formatting,documentSymbol,hover,signatureHelp,completion,definition,references,rename,semanticTokens' \
            and 'lsp' not in caps.get('not_implemented', '').split(',') and caps.get('disasm') == 'native' \
            and 'disasm' not in caps.get('not_implemented', '').split(',')
    except (ValueError, KeyError, TypeError):
        cap_ok = False
    rows.append(('capabilities:json', cap_ok, 'exit=%d' % cap.returncode))
    plain = subprocess.run([str(compiler), 'capabilities'], capture_output=True, timeout=60)
    text = plain.stdout.decode('utf-8', 'replace')
    plain_ok, detail = cap_ok and plain.returncode == 0 and not plain.stderr, 'exit=%d' % plain.returncode
    if plain_ok:
        plain_ok, detail = capabilities_text_matches(text, caps)
    rows.append(('capabilities:text-json', plain_ok, detail))
    return rows


def capabilities_text_matches(text, caps):
    """文本形的键值行按 "; " 切成 key=value，值里不得再有空白或 '='（漏写分号即失败），并逐项对 JSON；
    不含 '=' 的行是说明文字。"""
    pairs = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        for seg in line.split('; '):
            key, eq, value = seg.partition('=')
            if not eq or not key or not value or '=' in value or any(c.isspace() for c in value) or key in pairs:
                return False, 'bad segment %r' % seg
            pairs[key] = value
    if '未实现' in text:
        return False, 'text claims something is not implemented'
    lsp, mcp = 'native(%s)' % caps['lsp_features'], 'native(%s)' % caps['mcp_tools']
    if pairs.get('lsp') != lsp or pairs.get('mcp') != mcp or pairs.get('run --debug') != caps.get('run_debug'):
        return False, 'lsp/mcp/run --debug differ from JSON'
    for key in ('toolchain_version', 'diagnostic_version', 'version_gate', 'build', 'run', 'test', 'fmt', 'explain', 'doc',
                'init', 'bench', 'disasm', 'profile', 'link_map', 'dap', 'frontend', 'target', 'runtime', 'compiler',
                'lsp_file_policy', 'mcp_execution_default', 'mcp_worker', 'mcp_request_timeout', 'mcp_error_schema'):
        if pairs.get(key) != caps.get(key):
            return False, '%s: text %r, JSON %r' % (key, pairs.get(key), caps.get(key))
    for key in ('lsp_network_paths', 'mcp_untrusted_sandbox_verified'):
        if pairs.get(key) != str(caps.get(key)).lower():
            return False, '%s: text %r, JSON %r' % (key, pairs.get(key), caps.get(key))
    formats = 'format=json(run,test,fmt,doc,bench,capabilities,profile-compile,disasm)'
    if 'format=' + pairs.get('format', '') != formats:
        return False, 'format list %r' % pairs.get('format')
    return True, '%d keys' % len(pairs)


from native_tool_checks import tool_rows


def debug_build_rows(compiler):
    """Rows (name, ok, detail) for `build --debug`."""
    src = ROOT / 'tests' / 'debug' / 'basic.tx'
    rows = []
    work = Path(tempfile.mkdtemp(prefix='tx-dbgbuild-'))
    try:
        results = []
        for tag, flags in (('normal', []), ('debug', ['--debug'])):
            b = subprocess.run([str(compiler), 'build', str(src), *flags, '--out', str(work / tag)], capture_output=True, timeout=300)
            exe = work / tag / ('basic.exe' if os.name == 'nt' else 'basic')
            if b.returncode != 0 or not exe.exists():
                results.append((tag, None, b.stderr.decode('utf-8', 'replace')[:300]))
                continue
            r = subprocess.run([str(exe)], capture_output=True, timeout=60)
            results.append((tag, (r.returncode, r.stdout, r.stderr), exe.stat().st_size))
        (_, normal, nsize), (_, debug, dsize) = results
        ok = normal is not None and debug is not None and normal == debug and normal[0] == 10
        rows.append(('debug-build:transparent', ok, 'normal=%r debug=%r sizes=%r/%r' % (normal, debug, nsize, dsize)))
        for other in (['--leak-check'], ['--bytecode'], ['--lib']):
            r = subprocess.run([str(compiler), 'build', str(src), '--debug', *other, '--out', str(work / 'x')], capture_output=True, timeout=120)
            text = r.stderr.decode('utf-8', 'replace')
            rows.append(('debug-build:excl' + other[0], r.returncode == 64 and ('字节码产物已退场' if other == ['--bytecode'] else '--debug 与 --lib、--leak-check 互斥') in text,
                         'exit=%d err=%r' % (r.returncode, text[:200])))
        # 第 6 轮 6c-4b：run --debug 的旗标冲突（文案同 VM，退出码 64，stdout 无输出）与找不到入口文件（compile_failed 3008）。
        for other, want in ((['--format=json'], '--debug 与 --format=json 互斥'),
                            (['--leak-check'], '--debug 与 --lib、--leak-check 互斥')):
            r = subprocess.run([str(compiler), 'run', '--debug', *other, str(src)], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
            text = r.stderr.decode('utf-8', 'replace')
            rows.append(('debug-run:excl' + other[0], r.returncode == 64 and want in text and r.stdout == b'',
                         'exit=%d out=%r err=%r' % (r.returncode, r.stdout[:100], text[:200])))
        # 第 6 轮 6c-5b：`taixu dap` 不接受多余参数（64）；会话本身由 scripts/dap-e2e.py 与 test-native-debug.py 的 dap 用例覆盖。
        r = subprocess.run([str(compiler), 'dap', 'extra'], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        rows.append(('dap:extra-arg', r.returncode == 64 and r.stdout == b'', 'exit=%d out=%r' % (r.returncode, r.stdout[:100])))
        # 第 6 轮 6c-6：`taixu mcp` 同样不接受多余参数；服务器本身由 scripts/mcp-debug-e2e.py 与 test-native-debug.py 的 mcp 用例覆盖。
        r = subprocess.run([str(compiler), 'mcp', 'extra'], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        rows.append(('mcp:extra-arg', r.returncode == 64 and r.stdout == b'', 'exit=%d out=%r' % (r.returncode, r.stdout[:100])))
        missing = work / 'no-such.tx'
        r = subprocess.run([str(compiler), 'run', '--debug', str(missing)], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        out = r.stdout.decode('utf-8', 'replace')
        rows.append(('debug-run:missing-entry', r.returncode == 1 and '"event":"compile_failed"' in out and '"code":3008' in out
                     and 'FileNotFound' in out, 'exit=%d out=%r' % (r.returncode, out[:300])))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # 第 6 轮 6c-3（TOOL-103）：进程与标准输入内建的例程只进原生产物，字节码构建用到它们即报错、不出产物。
    work = Path(tempfile.mkdtemp(prefix='tx-toolbc-'))
    try:
        src = ROOT / 'tests' / 'nrt-cases' / 'proc-pipe.tx'
        r = subprocess.run([str(compiler), 'build', str(src), '--toolchain', '--bytecode', '--out', str(work / 'bc')], capture_output=True, timeout=300)
        text = (r.stdout + r.stderr).decode('utf-8', 'replace')
        made = (work / 'bc').exists()
        rows.append(('tool-builtins:bytecode-rejected', r.returncode == 64 and '字节码产物已退场' in text and not made,
                     'exit=%d out=%r files=%r' % (r.returncode, text[:200], made)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def lib_build_rows(compiler):
    """Rows (name, ok, detail) for `build --lib` on the host target: an entry without main builds only with --lib (3010
    otherwise), and the product traps with "程序没有 main 入口" (exit 3) when run. Same on every target, macOS included."""
    rows = []
    work = Path(tempfile.mkdtemp(prefix='tx-lib-'))
    try:
        src = work / 'nomain.tx'
        src.write_text('export function twice(x: int): int {\n    return x * 2;\n}\n', encoding='utf-8', newline='\n')
        r = subprocess.run([str(compiler), 'build', str(src), '--out', str(work / 'plain')], capture_output=True, timeout=300)
        text = r.stderr.decode('utf-8', 'replace')
        rows.append(('lib-build:no-main-rejected', r.returncode == 1 and '3010' in text, 'exit=%d err=%r' % (r.returncode, text[:200])))
        r = subprocess.run([str(compiler), 'build', str(src), '--lib', '--out', str(work / 'lib')], capture_output=True, timeout=300)
        exe = work / 'lib' / ('nomain.exe' if os.name == 'nt' else 'nomain')
        rows.append(('lib-build:built', r.returncode == 0 and exe.exists(),
                     'exit=%d err=%r' % (r.returncode, r.stderr.decode('utf-8', 'replace')[:200])))
        if exe.exists():
            p = subprocess.run([str(exe)], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            err = p.stderr.decode('utf-8', 'replace')
            rows.append(('lib-build:run-traps', p.returncode == 3 and '程序没有 main 入口' in err and p.stdout == b'',
                         'exit=%d out=%r err=%r' % (p.returncode, p.stdout[:100], err[:200])))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def heap_check_rows(compiler):
    """Rows (name, ok, detail) for `--heap-check` (设计 v052-heap-check-tool): the product links the embedded detection kernel
    (page heap + object-number quarantine; its messages start with "heapcheck:") and runs the same; it combines with --leak-check
    and `test`; it excludes --link-object; bench does not take it."""
    rows = []
    work = Path(tempfile.mkdtemp(prefix='tx-heapcheck-'))
    try:
        src = work / 'hc.tx'
        src.write_text('struct Node { next: Ref<Node>?, v: int }\n'
                       'export function main(args: []string): int {\n'
                       '    let head: Ref<Node>? = None;\n'
                       '    let i = 0;\n'
                       '    while (i < 1000) { head = new Node { next: head, v: i }; i = i + 1; }\n'
                       '    print("ok");\n'
                       '    return 0;\n'
                       '}\n', encoding='utf-8', newline='\n')
        exe = ('hc.exe' if os.name == 'nt' else 'hc')
        made = {}
        for tag, flags in (('standard', []), ('heapcheck', ['--heap-check'])):
            r = subprocess.run([str(compiler), 'build', str(src), *flags, '--out', str(work / tag)], capture_output=True, timeout=300)
            made[tag] = work / tag / exe
            rows.append(('heap-check:build-' + tag, r.returncode == 0 and made[tag].exists(),
                         'exit=%d err=%r' % (r.returncode, r.stderr.decode('utf-8', 'replace')[:200])))
        if made['standard'].exists() and made['heapcheck'].exists():
            rows.append(('heap-check:detection-kernel', b'heapcheck:' in made['heapcheck'].read_bytes()
                         and b'heapcheck:' not in made['standard'].read_bytes(), 'marker'))
            p = subprocess.run([str(made['heapcheck'])], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            rows.append(('heap-check:runs', p.returncode == 0 and p.stdout.replace(b'\r\n', b'\n') == b'ok\n',
                         'exit=%d out=%r err=%r' % (p.returncode, p.stdout[:100], p.stderr[:200])))
        r = subprocess.run([str(compiler), 'run', '--heap-check', '--leak-check', str(src)], capture_output=True, timeout=300,
                           stdin=subprocess.DEVNULL)
        rows.append(('heap-check:run-with-leak-check', r.returncode == 0 and r.stdout.replace(b'\r\n', b'\n') == b'ok\n',
                     'exit=%d out=%r err=%r' % (r.returncode, r.stdout[:100], r.stderr[:200])))
        t = work / 'hct.tx'
        t.write_text('test "quarantine" {\n    const xs: List<int> = listNew();\n    listPush(xs, 1);\n    assert(listLen(xs) == 1, "one element");\n}\n',
                     encoding='utf-8', newline='\n')
        r = subprocess.run([str(compiler), 'test', '--heap-check', str(t)], capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
        rows.append(('heap-check:test', r.returncode == 0, 'exit=%d out=%r err=%r' % (r.returncode, r.stdout[:200], r.stderr[:200])))
        r = subprocess.run([str(compiler), 'build', str(src), '--heap-check', '--link-object', str(made['standard']), '--out', str(work / 'x')],
                           capture_output=True, timeout=120)
        text = r.stderr.decode('utf-8', 'replace')
        rows.append(('heap-check:excludes-link-object', r.returncode == 64 and '--heap-check 与 --link-object 互斥' in text,
                     'exit=%d err=%r' % (r.returncode, text[:200])))
        r = subprocess.run([str(compiler), 'bench', '--heap-check', str(src)], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        rows.append(('heap-check:bench-rejects', r.returncode == 64, 'exit=%d err=%r' % (r.returncode, r.stderr[:200])))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def test_option_rows(compiler):
    """test 的选项可放在文件之后（taixu test main.tx --leak-check，同 build）；第二个文件仍被拒绝。"""
    work = Path(tempfile.mkdtemp(prefix='tx-testopt-'))
    rows = []
    try:
        (work / 'main.tx').write_text('export function main(args: []string): int { return 0; }\n'
                                      'test "加法" {\n    assert(1 + 1 == 2, "1 + 1");\n}\n', encoding='utf-8', newline='\n')
        for name, argv, want in (('test-options:after-file', ['test', 'main.tx', '--leak-check'], 0),
                                 ('test-options:before-file', ['test', '--leak-check', 'main.tx'], 0),
                                 ('test-options:two-files', ['test', 'main.tx', 'main.tx'], 64)):
            p = subprocess.run([str(compiler), *argv], cwd=work, capture_output=True, timeout=300)
            both = (p.stdout + p.stderr).decode('utf-8', 'replace')
            ok = p.returncode == want and (('1 passed, 0 failed' in both) if want == 0 else ('只接受一个 .tx 文件' in both))
            rows.append((name, ok, 'exit=%d out=%r' % (p.returncode, both[:300])))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def stdlib_root_rows(compiler):
    """发布包布局：编译器旁的 stdlib/src 即标准库根（设计 v052-public-release §三）；没有时 3008 的提示写出可用的位置。"""
    base = {k: v for k, v in os.environ.items() if k != 'TAIXU_STDLIB'}
    work = Path(tempfile.mkdtemp(prefix='tx-stdroot-'))
    rows = []
    try:
        (work / 'user').mkdir()
        (work / 'user' / 'use.tx').write_text('import { probeValue } from "std/probe";\n'
                                              'export function main(args: []string): int { print(toStr(probeValue())); return 0; }\n',
                                              encoding='utf-8', newline='\n')
        for name, with_lib in (('release-layout', True), ('missing', False)):
            pkg = work / ('pkg-' + name)
            pkg.mkdir()
            exe = pkg / compiler.name
            shutil.copyfile(compiler, exe)
            exe.chmod(0o755)
            if with_lib:
                (pkg / 'stdlib' / 'src').mkdir(parents=True)
                (pkg / 'stdlib' / 'src' / 'probe.tx').write_text('export function probeValue(): int { return 7; }\n',
                                                               encoding='utf-8', newline='\n')
            p = subprocess.run([str(exe), 'run', 'use.tx'], cwd=work / 'user', env=base, capture_output=True, timeout=300)
            out = p.stdout.decode('utf-8', 'replace').replace('\r\n', '\n')
            both = out + p.stderr.decode('utf-8', 'replace')
            if with_lib:
                rows.append(('stdlib-root:' + name, p.returncode == 0 and out == '7\n', 'exit=%d out=%r' % (p.returncode, both[:300])))
            else:
                rows.append(('stdlib-root:' + name, p.returncode != 0 and 'error 3008' in both and '编译器同目录的 stdlib/' in both,
                             'exit=%d out=%r' % (p.returncode, both[:300])))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def gui_root_rows(compiler):
    """Rows (name, ok, detail) for the native gui/ root; ok is None for a skipped row."""
    data = ROOT / 'tests' / 'nrt-cases' / 'data'
    fixture = data / 'gui-root'
    base = {k: v for k, v in os.environ.items() if k != 'TAIXU_GUI_ROOT'}

    def run(exe, args, cwd, env):
        p = subprocess.run([str(exe), *args], cwd=cwd, env=env, capture_output=True, timeout=300)
        out = p.stdout.decode('utf-8', 'replace').replace('\r\n', '\n')
        return p.returncode, out, out + p.stderr.decode('utf-8', 'replace')

    rows = []
    want = 'gui:太虚\n'
    work = Path(tempfile.mkdtemp(prefix='tx-guiroot-'))
    try:
        code, out, both = run(compiler, ['run', str(data / 'gui-use.tx')], ROOT, dict(base, TAIXU_GUI_ROOT=str(fixture)))
        rows.append(('gui-root:env', code == 0 and out == want, 'exit=%d out=%r' % (code, both[:300])))
        shutil.copyfile(data / 'gui-use.tx', work / 'use.tx')
        code, out, both = run(compiler, ['run', 'use.tx'], work, base)
        rows.append(('gui-root:missing', code != 0 and 'error 3008' in both and 'gui/ 导入需要 GUI 框架根' in both,
                     'exit=%d out=%r' % (code, both[:300])))
        bindir = work / 'bin'
        bindir.mkdir()
        exe2 = bindir / compiler.name
        shutil.copyfile(compiler, exe2)
        exe2.chmod(0o755)
        shutil.copytree(fixture, bindir / 'taixu-gui' / 'src')
        code, out, both = run(exe2, ['run', 'use.tx'], work, base)
        rows.append(('gui-root:exe-sibling', code == 0 and out == want, 'exit=%d out=%r' % (code, both[:300])))
        code, out, both = run(compiler, ['run', str(data / 'gui-env.tx')], ROOT, dict(base, TAIXU_GUI_ROOT=str(fixture)))
        rows.append(('gui-root:rt-builtin', code != 0 and 'error 3001' in both and '只供标准库模块使用' in both,
                     'exit=%d out=%r' % (code, both[:300])))
        sib = ROOT.parent / 'taixu-gui' / 'tests'
        if (sib / 'layout.tx').exists() and (sib / 'layout.out').exists():
            code, out, both = run(compiler, ['run', str(sib / 'layout.tx')], ROOT, base)
            expect = (sib / 'layout.out').read_text(encoding='utf-8').replace('\r\n', '\n')
            rows.append(('gui-root:sibling-layout', code == 0 and out == expect, 'exit=%d out=%r' % (code, both[:300])))
        else:
            rows.append(('gui-root:sibling-layout', None, 'no sibling taixu-gui checkout'))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def run_spec(compiler, tx, name, spec, argv):
    kind, want_exit, want, extra, suffix = spec
    row = {'case': name + suffix}
    tool = ['--toolchain'] if tx.with_suffix('.toolchain').exists() else []
    flags = companion(tx, '.buildflags')
    if flags is not None:
        tool += [l.strip() for l in flags.splitlines() if l.strip()]
    cmd = [str(compiler), 'test' if kind == 'test' else 'run', *tool, *extra, tx.name, *argv]
    p = subprocess.run(cmd, cwd=tx.parent, capture_output=True, timeout=600, env=nrt_products.fixture_env())
    out = p.stdout.decode('utf-8', 'replace').replace('\r\n', '\n')
    err = p.stderr.decode('utf-8', 'replace').replace('\r\n', '\n')
    ok = p.returncode == want_exit
    detail = ''
    if kind == 'out':
        if out != want:
            ok = False
            detail = 'stdout differs'
    elif any(n not in err and n not in out for n in want):
        ok = False
        detail = 'expected text not found'
    if not ok:
        detail = (detail + ' exit=%d want=%d' % (p.returncode, want_exit)).strip()
        row['detail'] = detail + ' | stdout: ' + out[-300:] + ' | stderr: ' + err[-300:]
    row['status'] = 'PASS' if ok else 'FAIL'
    return row


def path_rows(compiler):
    """路径规范形（docs/design/v052-path-canonical-v0.1-20261025.md）：类 Unix 上 \\ 是名字里的普通字符——名为 back\\slash.tx
    的入口导入同目录模块，构建、运行，产物名为 back\\slash；循环导入的诊断里路径按宿主显示（Windows 为 \\，其余为 /）。"""
    rows = []
    windows = os.name == 'nt'
    work = Path(tempfile.mkdtemp(prefix='tx-paths-'))

    def run(args):
        p = subprocess.run([str(compiler), *args], cwd=work, capture_output=True, timeout=300)
        out = p.stdout.decode('utf-8', 'replace').replace('\r\n', '\n')
        return p.returncode, out, out + p.stderr.decode('utf-8', 'replace')

    try:
        (work / 'cyc').mkdir()
        (work / 'cyc' / 'a.tx').write_text('import { b } from "./b";\nexport function a(): int { return 1; }\n'
                                           'function main(args: []string): int { return b(); }\n', encoding='utf-8', newline='\n')
        (work / 'cyc' / 'b.tx').write_text('import { a } from "./a";\nexport function b(): int { return a(); }\n',
                                           encoding='utf-8', newline='\n')
        code, out, both = run(['build', 'cyc/a.tx', '--out', 'cyc-out'])
        shown = 'cyc\\' if windows else 'cyc/'
        rows.append(('paths:cycle-diag-host-separator', code != 0 and '循环导入' in both and shown in both
                     and (windows or '\\' not in both), 'exit=%d out=%r' % (code, both[:400])))
        if windows:
            rows.append(('paths:posix-backslash-name', None, 'Windows 上 \\ 是分隔符'))
            return rows
        (work / 'back\\slash.tx').write_text('import { greet } from "./helper";\n'
                                             'function main(args: []string): int { print(greet()); return 0; }\n',
                                             encoding='utf-8', newline='\n')
        (work / 'helper.tx').write_text('export function greet(): string { return "back-slash ok"; }\n', encoding='utf-8', newline='\n')
        code, out, both = run(['build', 'back\\slash.tx', '--out', 'out'])
        product = work / 'out' / 'back\\slash'
        rows.append(('paths:posix-backslash-build', code == 0 and product.is_file() and not (work / 'back').exists(),
                     'exit=%d out=%r' % (code, both[:400])))
        if product.is_file():
            p = subprocess.run([str(product)], cwd=work, capture_output=True, timeout=60)
            rows.append(('paths:posix-backslash-product', p.returncode == 0 and p.stdout == b'back-slash ok\n',
                         'exit=%d out=%r' % (p.returncode, p.stdout[:200])))
        code, out, both = run(['run', 'back\\slash.tx'])
        rows.append(('paths:posix-backslash-run', code == 0 and out == 'back-slash ok\n', 'exit=%d out=%r' % (code, both[:400])))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True, help='native taixu compiler')
    ap.add_argument('--report', type=Path)
    ap.add_argument('cases', nargs='*')
    args = ap.parse_args()
    compiler = args.compiler.resolve()
    if args.cases:
        txs = [Path(c).resolve() for c in args.cases]
    else:
        txs = []
        for d in (ROOT / 'tests/cases', ROOT / 'tests/nrt-cases', ROOT / 'examples'):
            for tx in sorted(d.glob('*.tx')):
                if any(tx.with_suffix(x).exists() for x in COMPANIONS):
                    txs.append(tx)
    rows = []
    counts = {}
    for tx in txs:
        name = str(tx.relative_to(ROOT)).replace('\\', '/')
        specs, why = classify(tx)
        reason = why or nrt_products.skip_reason(tx, 'windows' if os.name == 'nt' else ('macos' if sys.platform == 'darwin' else 'linux'))
        argv = []
        a = companion(tx, '.argv')
        if a is not None:
            argv = [line.rstrip('\r') for line in a.rstrip('\n').split('\n')]
            if reason is None and any(x.split('=')[0] in RETIRED_FLAGS for x in argv):
                reason = 'retired-cli-flag-in-argv'
        if reason:
            case_rows = [{'case': name, 'status': 'SKIP', 'reason': reason}]
        else:
            case_rows = [run_spec(compiler, tx, name, spec, argv) for spec in specs]
        for row in case_rows:
            counts[row['status']] = counts.get(row['status'], 0) + 1
            rows.append(row)
            print(f"{row['status']:5} {row['case']}" + (f"  {row.get('detail', '')[:200]!r}" if row['status'] == 'FAIL' else ''), flush=True)
    if not args.cases:
        extra = explain_rows(compiler) + test_option_rows(compiler) + stdlib_root_rows(compiler) + gui_root_rows(compiler) + debug_build_rows(compiler) + lib_build_rows(compiler)
        extra += heap_check_rows(compiler)
        extra += tool_rows(compiler) + path_rows(compiler)
        for name, ok, detail in extra:
            if ok is None:
                row = {'case': name, 'status': 'SKIP', 'reason': detail}
            else:
                row = {'case': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail}
            counts[row['status']] = counts.get(row['status'], 0) + 1
            rows.append(row)
            print(f"{row['status']:5} {name}" + (f"  {detail!r}" if ok is False else ''), flush=True)
    print('SUMMARY', ' '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    if args.report:
        args.report.write_text(json.dumps({'compiler': str(compiler), 'rows': rows, 'counts': counts},
                                          ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if counts.get('FAIL', 0) else 0)


if __name__ == '__main__':
    main()
