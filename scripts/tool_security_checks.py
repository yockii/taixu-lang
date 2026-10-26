"""工具链合同门的增量检查：实际本地句柄、硬限额与失败后恢复。"""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def security_checks(compiler, work, check):
    work = Path(work) / 'tool-security'
    work.mkdir(parents=True, exist_ok=True)
    root, outside = work / 'local-root', work / 'outside'
    root.mkdir(exist_ok=True)
    outside.mkdir(exist_ok=True)
    (root / 'ok.bin').write_bytes(b'\0\xffa')
    (root / 'empty.bin').write_bytes(b'')
    (root / 'sub').mkdir(exist_ok=True)  # localListDir：大小写与中文名按字节排序
    (root / 'sub' / 'Case.tx').write_bytes(b'')
    (root / 'sub' / '中文 名.tx').write_bytes(b'')
    (outside / 'secret.bin').write_bytes(b'controlled-outside-marker')
    (outside / 'variant.tx').write_text('export function value(): int { return 1; }\n', encoding='utf-8')
    for target in ['windows', 'linux', 'macos']:
        (outside / ('variant_' + target + '.tx')).write_text('export function controlledOutsideExport(): int { return 1; }\n', encoding='utf-8')
    junction = root / 'junction'
    if not junction.exists():
        if os.name == 'nt':
            from _winapi import CreateJunction
            CreateJunction(str(outside), str(junction))
        else:
            junction.symlink_to(outside, target_is_directory=True)
    exe_suffix = '.exe' if os.name == 'nt' else ''

    def run(program, args, label):
        if os.name == 'nt':
            from selfhost_windows_job import run_job
            return run_job([program, *args], ROOT, os.environ.copy(), work / label,
                           timeout=35, process_limit=32, memory_bytes=2 * 1024**3)
        p = subprocess.run([str(program), *map(str, args)], cwd=ROOT, capture_output=True, timeout=35)
        return {'exit': p.returncode, 'stdout': p.stdout.decode('utf-8', 'replace'),
                'stderr': p.stderr.decode('utf-8', 'replace')}

    for mode, flags in [('normal', []), ('leak', ['--leak-check']), ('debug', ['--debug'])]:
        for fixture, marker in [('local-files', 'PASS_LOCAL_BINARY_METADATA_BOUNDARY_RECOVERY'),
                                ('process-budget', 'PASS_HARD_CPU_OOM_RECOVERY'),
                                ('reaper', 'PASS_REAPER_PROTECTED_STATUS_AND_DRAIN'),
                                ('try-write', 'PASS_TRY_WRITE_STOPPED_READER_FULL_RECOVERY_BLOCKING_WRITE_CLOSED_PEER')]:
            out = work / (fixture + '-' + mode)
            p = subprocess.run([str(compiler), 'build', str(ROOT / 'tests/tool-security' / (fixture + '.tx')),
                                '--toolchain', *flags, '--out', str(out)], cwd=ROOT, capture_output=True, timeout=180)
            check('security:build:' + fixture + ':' + mode, p.returncode == 0, (p.stdout + p.stderr)[-1000:])
            if p.returncode:
                continue
            exe = out / (fixture + exe_suffix)
            row = run(exe, [root if fixture == 'local-files' else exe], fixture + '-' + mode)
            check('security:run:' + fixture + ':' + mode,
                  row['exit'] == 0 and marker in row['stdout'] and '[runtime:leak]' not in row['stderr'], row)
            if fixture == 'reaper' and os.name != 'nt':
                import signal
                p = subprocess.run([str(exe), str(exe)], cwd=ROOT, capture_output=True, timeout=15,
                                   preexec_fn=lambda: signal.signal(signal.SIGCHLD, signal.SIG_IGN))
                check('security:reaper-inherited-ignore-restored:' + mode,
                      p.returncode == 0 and marker.encode() in p.stdout and b'[runtime:leak]' not in p.stderr,
                      (p.returncode, p.stdout, p.stderr))
            if fixture in ('process-budget', 'try-write'):
                row = run(exe, [exe, 'invalid'], fixture + '-invalid-' + mode)
                check('security:invalid-budget:' + mode,
                      row['exit'] == 3 and ('procSpawnLimited' if fixture == 'process-budget' else 'procTryWrite') in row['stderr'], row)
    # 标准输出读端已关闭（macOS 适配 M3 设计 G5）：stdoutWrite 得 −1；之后的 print 在 Unix 上按 SIGPIPE 结束（macOS 的
    # 工具链例程装过空处理函数也一样），父进程交下 SIG_IGN 时继续执行；Windows 没有 SIGPIPE，继续执行。
    out = work / 'print-pipe'
    p = subprocess.run([str(compiler), 'build', str(ROOT / 'tests/tool-security/print-pipe.tx'), '--toolchain',
                        '--out', str(out)], cwd=ROOT, capture_output=True, timeout=180)
    check('security:build:print-pipe', p.returncode == 0, (p.stdout + p.stderr)[-1000:])
    if p.returncode == 0:
        import signal
        cases = [('default', None)]
        if os.name != 'nt':
            cases.append(('ignored', lambda: signal.signal(signal.SIGPIPE, signal.SIG_IGN)))
        for label, pre in cases:
            q = subprocess.Popen([str(out / ('print-pipe' + exe_suffix))], cwd=ROOT, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=pre)
            q.stdout.close()
            q.stdin.close()
            err = q.stderr.read()
            code = q.wait(timeout=30)
            wrote = b'stdoutWrite -1' in err
            survived = b'PASS_PRINT_PIPE_SURVIVED' in err
            if os.name != 'nt' and label == 'default':
                ok = code == -signal.SIGPIPE and wrote and not survived
            else:
                ok = code == 0 and wrote and survived
            check('security:print-pipe:' + label, ok, (code, err[-400:]))
    for name, expr in [
        ('procTryWrite', 'procTryWrite(0, 0, bytesNew(0))'),
        ('procSpawnLimited', 'procSpawnLimited("x", listNew(), "", 0, 1, 1)'),
        ('readLocalFileBytes', 'readLocalFileBytes("/", "x", 1)'),
        ('localEntryKind', 'localEntryKind("/", "x")'),
        ('localFileSize', 'localFileSize("/", "x")'),
        ('localListDir', 'localListDir("/", "x")'),
    ]:
        source = work / ('deny-' + name + '.tx')
        source.write_text('function main(args: []string): int { ' + expr + '; return 0; }\n', encoding='utf-8', newline='\n')
        p = subprocess.run([str(compiler), 'build', str(source), '--out', str(work / ('deny-out-' + name))],
                           cwd=ROOT, capture_output=True, timeout=30)
        text = (p.stdout + p.stderr).decode('utf-8', 'replace')
        check('security:toolchain-only:' + name, p.returncode == 1 and '3001' in text and name in text, text[-800:])

    # 复用同一门及已有的受限执行驱动；协议测试另设私有源码根，不接触真实数据。
    binary = work / 'binary'
    binary.mkdir(exist_ok=True)
    (binary / 'all.bin').write_bytes(bytes(range(256)))
    (binary / 'empty.bin').write_bytes(b'')
    source = binary / 'main.tx'
    source.write_text('const a: Bytes = embedBytes("all.bin");\nconst b: Bytes = embedBytes("all.bin");\nconst c: Bytes = embedBytes("empty.bin");\nfunction main(args: []string): int { return bytesLen(a) + bytesLen(b) + bytesLen(c); }\n', encoding='utf-8', newline='\n')
    for fixture, marker, arguments in [
        ('service-policy', 'PASS_POLICY_ROOT_BINARY_OUTSIDE_LINK_NETWORK', [root]),
        ('mount-policy', 'PASS_MOUNT_POLICY_DEFAULT_OPTIN_NETWORK_NESTED_ESCAPE_SHADOW_FAILCLOSED', []),
        ('worker-proof', 'PASS_ACTUAL_WORKER_LIMITS', None),
        ('scratch', 'PASS_SCRATCH_EXCLUSIVE_CAPACITY_REUSE_CONSERVATIVE_CLEANUP', []),
        ('proxy-stop', 'PASS_STOPPED_READER_FULL_PIPE_DEADLINE_REAP_RECOVERY', []),
        ('binary-context', 'PASS_BINARY_CONTEXT_EXACT_BYTES_RECOVERY', [binary, source]),
    ]:
        out = work / (fixture + '-normal')
        p = subprocess.run([str(compiler), 'build', str(ROOT / 'tests/tool-security' / (fixture + '.tx')),
                            '--toolchain', '--out', str(out)], cwd=ROOT, capture_output=True, timeout=300)
        check('security:service-build:' + fixture, p.returncode == 0, (p.stdout + p.stderr)[-1200:])
        if p.returncode:
            continue
        executable = out / (fixture + exe_suffix)
        row = run(executable, [executable] if arguments is None else arguments, fixture + '-normal')
        check('security:service-run:' + fixture, row['exit'] == 0 and marker in row['stdout'], row)
    if sys.platform.startswith('linux'):
        # waitid 不可用（沙箱的 ENOSYS）时 procWait 须退回 wait4 得到真实退出码，并按组补杀同组后代；用 seccomp 屏蔽 waitid 复现。
        out = work / 'wait-nopeek'
        p = subprocess.run([str(compiler), 'build', str(ROOT / 'tests/tool-security/wait-nopeek.tx'), '--toolchain',
                            '--out', str(out)], cwd=ROOT, capture_output=True, timeout=180)
        check('security:build:wait-nopeek', p.returncode == 0, (p.stdout + p.stderr)[-1000:])
        if p.returncode == 0:
            pidfile = work / 'wait-nopeek.pid'
            pidfile.unlink(missing_ok=True)
            p = subprocess.run([sys.executable, str(ROOT / 'scripts/run-without-syscall.py'), '247', '--',
                                str(out / 'wait-nopeek'), str(pidfile)], cwd=ROOT, capture_output=True, timeout=60)
            text = pidfile.read_text().strip() if pidfile.exists() else ''
            pid = int(text) if text.isdigit() else 0
            time.sleep(0.5)
            try:  # 按命令行认人：PID 若已被复用，不算存活，也不误杀
                alive = pid > 0 and Path('/proc/%d/cmdline' % pid).read_bytes().startswith(b'sleep\0') \
                    and 'State:\tZ' not in Path('/proc/%d/status' % pid).read_text()
            except OSError:
                alive = False
            check('security:wait-without-waitid-reaps-group', p.returncode == 0 and b'wait 0' in p.stdout and pid > 0 and not alive,
                  (p.returncode, p.stdout, p.stderr[-500:], pid, alive))
            if alive:
                os.kill(pid, 9)
    from service_security_checks import service_security_checks
    service_security_checks(compiler, work, check)
