"""现有工具链合同门的服务边界增量；只使用私有目录和受控样本。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import proc_probe

ROOT = Path(__file__).resolve().parent.parent
# Linux：子进程 setsid、关掉标准流后常驻；父进程打印子进程 PID 后退出。
DAEMON = '''declare function lxFork(): int from syscall 57;
declare function lxSetsid(): int from syscall 112;
declare function lxClose(fd: int): int from syscall 3;
declare function lxPause(): int from syscall 34;
export function main(args: []string): int {
    const pid = lxFork();
    if (pid == 0) {
        lxSetsid();
        lxClose(0);
        lxClose(1);
        lxClose(2);
        while (true) { lxPause(); }
    }
    print(toStr(pid));
    return 0;
}
'''
# macOS：同上，经 libSystem（M3 设计 G9：父进程退出后守护进程过继给 launchd，服务凭回收 cookie 找回）。
DAEMON_MACOS = '''declare function fork(): i32 from "libSystem";
declare function setsid(): i32 from "libSystem";
declare function close(fd: i32): i32 from "libSystem";
declare function pause(): i32 from "libSystem";
export function main(args: []string): int {
    const pid = fork();
    if (pid == 0) {
        setsid();
        close(0);
        close(1);
        close(2);
        while (true) { pause(); }
    }
    print(toStr(pid));
    return 0;
}
'''
UNIX = 'macos' if sys.platform == 'darwin' else 'linux'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kill_owned_worker(parent, compiler):
    """只终止该私有MCP实例的直接worker，检查映像，持有OS句柄避免盲发PID。"""
    if sys.platform == 'darwin':
        # 没有 pidfd：先记启动标记、再按命令行认出 worker，发信号前核对标记未变（同号已换成别的进程则不发）。
        import signal
        found = []
        for pid in proc_probe.children(parent):
            stamp = proc_probe.start(pid)
            a = proc_probe.argv(pid)
            if stamp and a and 'mcp' in a and '--worker-generation' in a and Path(a[0]).resolve() == compiler.resolve():
                found.append((pid, stamp))
        if len(found) != 1:
            return False
        pid, stamp = found[0]
        if proc_probe.start(pid) == stamp:
            os.kill(pid, signal.SIGKILL)
        return True
    if os.name != 'nt':
        import signal
        children = Path('/proc/%d/task/%d/children' % (parent, parent)).read_text().split()
        matches = []
        for child in children:
            data = Path('/proc/' + child + '/cmdline').read_bytes().split(b'\0')
            if b'mcp' in data and b'--worker-generation' in data and Path(os.fsdecode(data[0])).resolve() == compiler.resolve():
                matches.append(int(child))
        if len(matches) != 1:
            return False
        descriptor = os.pidfd_open(matches[0])
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        finally:
            os.close(descriptor)
        return True
    import ctypes as c
    from ctypes import wintypes as w
    class Entry(c.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD), ('heap', c.c_size_t),
                    ('module', w.DWORD), ('threads', w.DWORD), ('parent', w.DWORD), ('priority', w.LONG),
                    ('flags', w.DWORD), ('exe', w.WCHAR * 260)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, c.c_void_p(-1).value):
        raise c.WinError(c.get_last_error())
    matches = []
    try:
        entry = Entry(); entry.size = c.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, c.byref(entry))
        while present:
            if entry.parent == parent:
                matches.append(entry.pid)
            present = kernel.Process32NextW(snapshot, c.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if len(matches) != 1:
        return False
    process = kernel.OpenProcess(0x1001, False, matches[0])
    if not process:
        return False
    try:
        name = c.create_unicode_buffer(32768); size = w.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(process, 0, name, c.byref(size)):
            return False
        if os.path.normcase(str(Path(name.value).resolve())) != os.path.normcase(str(compiler.resolve())):
            return False
        return bool(kernel.TerminateProcess(process, 125))
    finally:
        kernel.CloseHandle(process)


# 后代、记录与是否已不在（Linux 读 /proc，macOS 经 ps／pgrep；僵尸仍占 PID，不能把终止当作已回收）：见 proc_probe。
unix_descendants = proc_probe.descendants
unix_records = proc_probe.records
unix_gone = proc_probe.gone


def service_security_checks(compiler, work, check):
    work = Path(work) / 'service-security'
    work.mkdir(parents=True, exist_ok=True)
    inside, outside, library, products = [work / n for n in ['workspace', 'outside', 'library', 'products']]
    for p in [inside, outside, library, products]:
        p.mkdir(exist_ok=True)
    good = 'export function main(args: []string): int { print("safe"); return 0; }\n'
    for directory in [inside, outside, library]:
        (directory / 'main.tx').write_text(good, encoding='utf-8', newline='\n')
    (inside / 'spin.tx').write_text('function main(args: []string): int { let n = 0; while (true) { n = n + 1; } return n; }\n', encoding='utf-8')
    (outside / 'secret.bin').write_bytes(b'controlled-outside-marker')
    (inside / 'escape.tx').write_text('const b: Bytes = embedBytes("../outside/secret.bin");\nfunction main(args: []string): int { return bytesLen(b); }\n', encoding='utf-8')
    (inside / 'invalid.tx').write_text('function main(args: []string): int { return missing; }\n', encoding='utf-8')
    (inside / 'import-outside.tx').write_text('import { value } from "../outside/variant";\nfunction main(args: []string): int { return value(); }\n', encoding='utf-8')
    (outside / 'variant.tx').write_text('export function value(): int { return 1; }\n', encoding='utf-8')
    for target in ['windows', 'linux', 'macos']:
        (outside / ('variant_' + target + '.tx')).write_text('export function controlledOutsideExport(): int { return 1; }\n', encoding='utf-8')
    junction = inside / 'junction'
    if not junction.exists():
        if os.name == 'nt':
            from _winapi import CreateJunction
            CreateJunction(str(outside), str(junction))
        else:
            junction.symlink_to(outside, target_is_directory=True)
    mcp = load('security_mcp', 'mcp-debug-e2e.py')
    args = ('--workspace', str(inside), '--library-root', str(library))
    m = mcp.Mcp(str(compiler), str(inside), str(products), server_args=args)
    try:
        discover = m.call('server/discover')
        policy = (discover or {}).get('result', {}).get('security', {})
        check('service:discover-safe-defaults', policy.get('network_paths') is False
              and policy.get('workspace_execution_trusted') is False and policy.get('untrusted_execution') is False
              and policy.get('os_sandbox_verified') is False and policy.get('worker_limit_verification') == 'required_before_requests'
              and policy.get('worker_memory_bytes') == 4294967296 and policy.get('worker_cpu_seconds') == 60
              and policy.get('request_timeout_ms') == 10000 and policy.get('client_stdout_write_nonblocking') is False
              and policy.get('host_share_mount_types') == [], discover)
        check('service:discover-process-reaping', policy.get('worker_reaping') == {'nt': 'windows_job'}.get(os.name, {'darwin': 'macos_env_cookie'}.get(sys.platform, 'linux_subreaper')), policy)
        for tool in ['run', 'test', 'debug_start']:
            s, e, err = m.tool(tool, {'path': 'main.tx', 'trust': True, 'trusted': True})
            check('service:default-deny:' + tool, e and err is None and (s or {}).get('schema') == 'tool-error/1'
                  and s.get('reason') == 'untrusted_execution_denied', (s, e, err))
        s, e, err = m.tool('compile', {'path': 'main.tx'})
        digest = (s or {}).get('txc_sha256')
        check('service:default-compile', bool(digest) and not e and err is None, (s, e, err))
        for path in [outside / 'main.tx', '//security-test.invalid/share/main.tx']:
            s, e, err = m.tool('compile', {'path': str(path)})
            check('service:outside-network-deny:' + ('network' if str(path).startswith('//') else 'outside'),
                  e and err is None and (s or {}).get('reason') == 'path_not_trusted', (s, e, err))
        for path in ['junction/main.tx', 'escape.tx', 'invalid.tx', 'import-outside.tx']:
            s, e, err = m.tool('compile', {'path': path})
            check('service:unsafe-input-reject:' + path, e and err is None and (s or {}).get('schema') == 'compile/1'
                  and s.get('ok') is False and 'controlled-outside-marker' not in json.dumps(s)
                  and 'controlledOutsideExport' not in json.dumps(s), (s, e, err))
        s, e, err = m.tool('compile', {'path': 'main.tx'})
        check('service:failure-recovery', not e and (s or {}).get('txc_sha256') == digest, (s, e, err))
        m.raw(b'{"jsonrpc":"2.0","id":991,"method":"pi\rng"}\n')
        r = m.wait_id(None)
        check('service:literal-CR-is-parse-error', (r or {}).get('error', {}).get('code') == -32700, r)
        m.raw(('[' + ','.join('0' for _ in range(20000)) + ']\n').encode())
        r = m.wait_id(None)
        check('service:json-token-budget', (r or {}).get('error', {}).get('message') == 'RequestTooComplex', r)
        s, e, err = m.tool('x' * 65, {})
        check('service:unknown-tool-name-bound', err == {'code': -32602, 'message': 'Unknown tool: name too long'}, (s, e, err))
        for value in [0, -1, True, 1.5, '1', None, 10001]:
            s, e, err = m.tool('compile', {'path': 'main.tx', 'timeout_ms': value})
            check('service:budget-deny:' + repr(value), s is None and e and isinstance(err, str)
                  and '1..10000' in err, (s, e, err))
        check('service:parse-budget-recovery', (m.call('ping') or {}).get('result') == {}, m.pending())
    finally:
        check('service:default-close', m.finish() == 0 and not m.bad, m.err)
    check('service:default-product-cleanup', not list(products.iterdir()), list(products.iterdir()))
    # 信任根经链接给出（类 Unix 为符号链接，Windows 为目录联接；macOS 的 /tmp、/var 即如此）：启动时解析为真实路径一次（用户裁决
    # 2026-10-26），roots 报真实根、root_aliases 报写法与真实根；按写法与按真实路径都能编译，根之下的链接照样拒绝。
    alias = work / 'workspace-alias'
    if not os.path.lexists(alias):
        if os.name == 'nt':
            from _winapi import CreateJunction
            CreateJunction(str(inside), str(alias))
        else:
            alias.symlink_to(inside, target_is_directory=True)

    def same(a, b):
        a, b = str(a).replace('\\', '/').rstrip('/'), str(b).replace('\\', '/').rstrip('/')
        return a.lower() == b.lower() if os.name == 'nt' else a == b

    real = os.path.realpath(inside)
    m = mcp.Mcp(str(compiler), str(alias), str(products), server_args=('--workspace', str(alias), '--library-root', str(library)))
    try:
        policy = ((m.call('server/discover') or {}).get('result', {}).get('security', {}))
        roots, aliases = policy.get('roots') or [], policy.get('root_aliases') or []
        check('service:link-root-real', any(same(r, real) for r in roots) and not any(same(r, alias) for r in roots), policy)
        check('service:link-root-alias', any(same(x.get('given'), alias) and same(x.get('real'), real) for x in aliases), policy)
        for case, path in [('given', str(alias / 'main.tx')), ('real', os.path.join(real, 'main.tx')), ('relative', 'main.tx')]:
            s, e, err = m.tool('compile', {'path': path})
            check('service:link-root-compile:' + case, not e and err is None and (s or {}).get('ok') is True, (s, e, err))
        s, e, err = m.tool('compile', {'path': str(alias / 'junction' / 'main.tx')})
        check('service:link-root-inner-link-reject', e and err is None and (s or {}).get('ok') is False
              and 'controlled-outside-marker' not in json.dumps(s), (s, e, err))
    finally:
        check('service:link-root-close', m.finish() == 0 and not m.bad, m.err)

    m = mcp.Mcp(str(compiler), str(inside), str(products), server_args=(*args, '--trust-workspace'))
    try:
        s, e, err = m.tool('run', {'path': 'main.tx'})
        check('service:explicit-trusted-run', not e and err is None and (s or {}).get('stdout') == 'safe\n', (s, e, err))
        s, e, err = m.tool('compile', {'path': str(library / 'main.tx')})
        check('service:library-read', not e and (s or {}).get('ok') is True, (s, e, err))
        s, e, err = m.tool('run', {'path': str(library / 'main.tx')})
        check('service:library-execution-denied', e and (s or {}).get('reason') == 'untrusted_execution_denied', (s, e, err))
        s, e, err = m.tool('debug_start', {'path': 'main.tx'})
        old_id = (s or {}).get('session', '')
        check('service:session-generation', bool(old_id) and old_id.startswith('1.'), (s, e, err))
        descendants = unix_descendants(m.proc.pid) if os.name != 'nt' else []
        if os.name != 'nt':
            check('service:' + UNIX + '-worker-and-live-debug-identified', len(descendants) >= 2, descendants)
        begin = time.monotonic()
        s, e, err = m.tool('compile', {'path': 'main.tx', 'timeout_ms': 1})
        check('service:hard-compile-timeout', mcp.hard_timeout(s) and e and err is None and time.monotonic() - begin < 5, (s, e, err))
        if os.name != 'nt':
            check('service:' + UNIX + '-timeout-descendants-reaped', mcp.wait_until(lambda: unix_gone(descendants), 5), descendants)
        s, e, err = m.tool('compile', {'path': 'main.tx'})
        check('service:worker-restart-compile', not e and (s or {}).get('ok') is True, (s, e, err))
        s, e, err = m.tool('debug_frames', {'session': old_id})
        check('service:old-session-invalid', e and (s or {}).get('error') == mcp.UNKNOWN_SESSION, (s, e, err))
        s, e, err = m.tool('debug_start', {'path': 'main.tx'})
        new_id = (s or {}).get('session', '')
        check('service:session-no-reuse', new_id.startswith('2.') and new_id != old_id, (s, e, err))
        m.tool('debug_kill', {'session': new_id})
    finally:
        check('service:trusted-close', m.finish() == 0 and not m.bad, m.err)
    check('service:restart-product-cleanup', not list(products.iterdir()), list(products.iterdir()))
    if os.name != 'nt':
        # 受信程序按守护进程的通常做法 fork、setsid、关掉标准流后常驻：它脱离进程组，产物退出后过继给服务。
        # 服务须杀掉并收割它，否则重建 worker 时每个 tools/call 都停在 cleanup_pending。
        (inside / 'daemon.tx').write_text(DAEMON_MACOS if sys.platform == 'darwin' else DAEMON, encoding='utf-8', newline='\n')
        records = []
        m = mcp.Mcp(str(compiler), str(inside), str(products), server_args=(*args, '--trust-workspace'))
        try:
            s, e, err = m.tool('run', {'path': 'daemon.tx'})
            text = ((s or {}).get('stdout') or '').strip()
            pid = int(text) if text.isdigit() else 0
            check('service:' + UNIX + '-daemon-started', not e and pid > 0, (s, e, err))
            records = unix_records([pid]) if pid > 0 else []
            s, e, err = m.tool('compile', {'path': 'main.tx', 'timeout_ms': 1})
            check('service:' + UNIX + '-daemon-worker-retired', mcp.hard_timeout(s) and e and err is None, (s, e, err))
            s, e, err = m.tool('compile', {'path': 'main.tx'})
            check('service:' + UNIX + '-daemon-no-cleanup-pending', not e and (s or {}).get('ok') is True, (s, e, err))
            check('service:' + UNIX + '-daemon-killed-and-reaped', pid > 0 and mcp.wait_until(lambda: unix_gone(records), 5), records)
        finally:
            check('service:' + UNIX + '-daemon-close', m.finish() == 0 and not m.bad, m.err)
            for record in records:  # 失败时不把守护进程留在机器上；按启动时刻核对，不误杀复用 PID 的进程。
                if not unix_gone([record]):
                    os.kill(record[0], 9)
        check('service:' + UNIX + '-daemon-product-cleanup', not list(products.iterdir()), list(products.iterdir()))
    m = mcp.Mcp(str(compiler), str(inside), str(products), server_args=(*args, '--trust-workspace'))
    try:
        s, e, err = m.tool('compile', {'path': 'main.tx'})
        check('service:idle-worker-start', not e and (s or {}).get('ok') is True and bool(list(products.iterdir())), (s, e, err))
        check('service:kill-owned-idle-worker', kill_owned_worker(m.proc.pid, Path(compiler)), m.proc.pid)
        check('service:idle-crash-cleanup-without-request', mcp.wait_until(lambda: not list(products.iterdir()), 5)
              and m.proc.poll() is None, list(products.iterdir()))
        s, e, err = m.tool('debug_start', {'path': 'main.tx'})
        check('service:idle-crash-next-generation', not e and (s or {}).get('session', '').startswith('2.'), (s, e, err))
    finally:
        check('service:idle-recovery-close', m.finish() == 0 and not m.bad, m.err)
    check('service:idle-recovery-product-cleanup', not list(products.iterdir()), list(products.iterdir()))
    for flags in [('--worker-generation', '1'), ('--worker-generation', '1', '--worker-scratch', str(products)),
                  ('--request-timeout-ms', '0'), ('--worker-memory-mib', '1048577'), ('--worker-cpu-s', '86401'),
                  ('--allow-mount-type', 'nfs4'), ('--allow-mount-type', 'fuse.sshfs'), ('--allow-mount-type',)]:
        p = subprocess.run([compiler, 'mcp', *flags], cwd=inside, capture_output=True, input=b'', timeout=10)
        check('service:startup-fail-closed:' + ' '.join(flags), p.returncode == 64 and b'READY' not in p.stdout, p.stderr[-500:])

    lsp = load('security_lsp', 'lsp-e2e.py')
    session = lsp.Session(str(compiler), ('lsp', '--stdio', '--workspace', str(inside), '--library-root', str(library)))
    try:
        r = session.request('initialize', {'capabilities': {}, 'workspaceFolders': [{'uri': outside.as_uri(), 'name': 'untrusted'}]})
        access = ((r or {}).get('result') or {}).get('capabilities', {}).get('experimental', {}).get('taixuFileAccess', {})
        check('service:lsp-initialize', bool((r or {}).get('result')) and access.get('network_paths') is False
              and access.get('host_share_mount_types') == [], r)
        session.notify('initialized', {})
        for directory, expected in [(outside, False), (inside, True), (library, True)]:
            uri = (directory / 'main.tx').as_uri()
            session.open(uri, good)
            r = session.request('textDocument/documentSymbol', {'textDocument': {'uri': uri}})
            symbols = (r or {}).get('result')
            check('service:lsp-document-root:' + directory.name, isinstance(symbols, list) and bool(symbols) == expected, r)
        for uri in ['file://security-test.invalid/share/main.tx', 'file:////security-test.invalid/share/main.tx']:
            session.open(uri, good)
            r = session.request('textDocument/documentSymbol', {'textDocument': {'uri': uri}})
            check('service:lsp-network-uri-deny', (r or {}).get('result') == [], r)
        # 导入写法与磁盘逐字节核对在服务模式下经受根约束的 localListDir（D3 v0.35 §6.1-4）：与检查命令同一条 3008。
        (inside / 'caselib').mkdir(exist_ok=True)
        (inside / 'caselib' / 'Case.tx').write_text('export function caseValue(): int { return 3; }\n', encoding='utf-8', newline='\n')
        for name, spec, expected in [('case-mismatch', './caselib/case', True), ('case-exact', './caselib/Case', False)]:
            uri = (inside / (name + '.tx')).as_uri()
            session.open(uri, 'import { caseValue } from "' + spec + '";\nexport function main(args: []string): int { return caseValue(); }\n')
            ds = session.diags(uri) or []
            hit = any("在磁盘上为 'Case.tx'" in (d.get('message') or '') for d in ds)
            check('service:lsp-import-spelling:' + name, hit == expected and (expected or ds == []), ds)
    finally:
        check('service:lsp-close', session.finish() == 0, b''.join(session.err)[-500:])

    # 与可执行文件同目录的 taixu-stdlib/src 自动纳入库根（缺省安装的 std/ 导入可用）；失效的 TAIXU_STDLIB 只告警。
    install, user = work / 'install', work / 'user-workspace'
    (install / 'taixu-stdlib' / 'src').mkdir(parents=True, exist_ok=True)
    user.mkdir(exist_ok=True)
    (install / 'taixu-stdlib' / 'src' / 'probe.tx').write_text('export function probeValue(): int { return 7; }\n',
                                                              encoding='utf-8', newline='\n')
    (user / 'main.tx').write_text('import { probeValue } from "std/probe";\n'
                                  'export function main(args: []string): int { return probeValue(); }\n', encoding='utf-8', newline='\n')
    installed = install / Path(compiler).name
    installed.write_bytes(Path(compiler).read_bytes())
    installed.chmod(0o755)
    saved = {k: os.environ.pop(k) for k in ('TAIXU_STDLIB', 'TAIXU_GUI_ROOT') if k in os.environ}
    try:
        m = mcp.Mcp(str(installed), str(user), str(products), server_args=('--workspace', str(user)))
        try:
            s, e, err = m.tool('compile', {'path': 'main.tx'})
            check('service:sibling-stdlib-authorized', not e and err is None and (s or {}).get('ok') is True, (s, e, err))
        finally:
            check('service:sibling-stdlib-close', m.finish() == 0 and not m.bad, m.err)
        # 发布包与公开仓的布局：标准库在编译器旁的 stdlib/src（设计 v052-public-release §三），同一信任域、同样自动授权。
        release = work / 'release-install'
        (release / 'stdlib' / 'src').mkdir(parents=True, exist_ok=True)
        (release / 'stdlib' / 'src' / 'probe.tx').write_text('export function probeValue(): int { return 7; }\n',
                                                             encoding='utf-8', newline='\n')
        packaged = release / Path(compiler).name
        packaged.write_bytes(Path(compiler).read_bytes())
        packaged.chmod(0o755)
        m = mcp.Mcp(str(packaged), str(user), str(products), server_args=('--workspace', str(user)))
        try:
            s, e, err = m.tool('compile', {'path': 'main.tx'})
            check('service:release-stdlib-authorized', not e and err is None and (s or {}).get('ok') is True, (s, e, err))
        finally:
            check('service:release-stdlib-close', m.finish() == 0 and not m.bad, m.err)
        os.environ['TAIXU_STDLIB'] = str(work / 'missing-stdlib')
        p = subprocess.run([str(installed), 'mcp', '--workspace', str(user)], cwd=user, capture_output=True, input=b'', timeout=30)
        check('service:stale-stdlib-env-warns', p.returncode == 0 and '忽略 TAIXU_STDLIB'.encode('utf-8') in p.stderr,
              (p.returncode, p.stderr[-300:]))
    finally:
        os.environ.pop('TAIXU_STDLIB', None)
        os.environ.update(saved)
    host_share_checks(compiler, work, check, mcp, good)


def discover_policy(compiler, cwd, flags):
    """不经 initialize 直接问 server/discover；返回 (security, 退出码, stderr)。"""
    line = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover'}) + '\n'
    p = subprocess.run([str(compiler), 'mcp', *flags], cwd=cwd, capture_output=True, input=line.encode(), timeout=30)
    for row in p.stdout.decode('utf-8', 'replace').splitlines():
        try:
            doc = json.loads(row)
        except ValueError:
            continue
        if doc.get('id') == 1:
            return doc.get('result', {}).get('security', {}), p.returncode, p.stderr
    return {}, p.returncode, p.stderr


def host_share_dir():
    """(可写目录, 类型) 或 None：TAIXU_TEST_HOST_SHARE_DIR 优先，否则在本机的主机共享挂载点下试建临时目录。"""
    kinds = {'9p', 'drvfs', 'virtiofs', 'fuse.grpcfuse', 'fakeowner'}
    rows = []
    if not sys.platform.startswith('linux'):
        return None  # 这几类主机共享挂载只出现在 Linux（WSL、虚拟机、容器）上。
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split(' ')
        at = fields.index('-') if '-' in fields else -1
        if at < 0 or at + 3 >= len(fields) or fields[at + 1] not in kinds:
            continue
        if fields[at + 1] == '9p' and {'trans=tcp', 'trans=rdma'} & set(fields[at + 3].split(',')):
            continue
        rows.append((fields[4].replace('\\040', ' '), fields[at + 1]))
    configured = os.environ.get('TAIXU_TEST_HOST_SHARE_DIR', '')
    if configured:
        best = max((r for r in rows if configured == r[0] or configured.startswith(r[0].rstrip('/') + '/')),
                   key=lambda r: len(r[0]), default=None)
        if best is None:
            return None
        path = Path(tempfile.mkdtemp(prefix='taixu-host-share-', dir=configured))
        return path, best[1]
    for mount, kind in rows:
        try:
            return Path(tempfile.mkdtemp(prefix='taixu-host-share-', dir=mount)), kind
        except OSError:
            continue
    return None


def host_share_checks(compiler, work, check, mcp, good):
    """主机共享挂载作服务根（docs/design/v052-host-share-roots-v0.1-20261023.md §五-2、§五-3）。"""
    plain = work / 'workspace'
    policy, code, err = discover_policy(compiler, plain, ('--workspace', str(plain), '--allow-mount-type', '9p'))
    if os.name == 'nt':
        check('service:host-share-windows-ignored', code == 0 and policy.get('host_share_mount_types') == []
              and '忽略 --allow-mount-type'.encode('utf-8') in err, (code, policy, err[-300:]))
        return
    check('service:host-share-discover-opt-in', code == 0 and policy.get('host_share_mount_types') == ['9p']
          and policy.get('network_paths') is False, (code, policy, err[-300:]))
    found = host_share_dir()
    if found is None:
        print('NOT-RUN service:host-share-live 本机没有可写的主机共享挂载（可设 TAIXU_TEST_HOST_SHARE_DIR）', flush=True)
        return
    share, kind = found
    try:
        (share / 'ok.bin').write_bytes(b'abc')
        (share / 'main.tx').write_text(good, encoding='utf-8', newline='\n')
        outside = work / 'outside'
        try:
            (share / 'link').symlink_to(outside, target_is_directory=True)
        except OSError as e:
            print('NOTE service:host-share-live 共享上建不了符号链接（%s），链接拒绝一项只剩不存在路径' % e, flush=True)
        out = work / 'mount-policy-live'
        p = subprocess.run([str(compiler), 'build', str(ROOT / 'tests/tool-security/mount-policy.tx'), '--toolchain', '--out', str(out)],
                           cwd=ROOT, capture_output=True, timeout=300)
        check('service:host-share-live-build', p.returncode == 0, (p.stdout + p.stderr)[-800:])
        if p.returncode == 0:
            p = subprocess.run([str(out / 'mount-policy'), str(share), kind], cwd=ROOT, capture_output=True, timeout=60)
            check('service:host-share-live-policy', p.returncode == 0 and ('PASS_MOUNT_POLICY_LIVE_' + kind).encode() in p.stdout,
                  (p.returncode, p.stdout[-300:], p.stderr[-300:]))
        p = subprocess.run([str(compiler), 'lsp', '--stdio', '--workspace', str(share)], cwd=ROOT, capture_output=True, input=b'', timeout=30)
        check('service:host-share-default-deny', p.returncode == 64 and ('--allow-mount-type ' + kind).encode() in p.stderr,
              (p.returncode, p.stderr[-300:]))
        # 开启后 worker 按转发的选项重新核验根：编译走 worker，转发漏掉就编不了。
        products = work / 'host-share-products'
        products.mkdir(exist_ok=True)
        m = mcp.Mcp(str(compiler), str(share), str(products), server_args=('--workspace', str(share), '--allow-mount-type', kind))
        try:
            discover = m.call('server/discover')
            types = (discover or {}).get('result', {}).get('security', {}).get('host_share_mount_types')
            s, e, err = m.tool('compile', {'path': 'main.tx'})
            check('service:host-share-worker-compile', types == [kind] and not e and err is None and (s or {}).get('ok') is True,
                  (types, s, e, err))
        finally:
            check('service:host-share-close', m.finish() == 0 and not m.bad, m.err)
    finally:
        shutil.rmtree(share, ignore_errors=True)
