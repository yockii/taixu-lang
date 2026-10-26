#!/usr/bin/env python3
"""实际控制台/PTY 与 UTF-8 重定向门；不改变调用者代码页。"""
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parent.parent
TEXT = '太虚 🦊 é e\u0301'
ARG = '参数 中文 & 🦊'
SOURCE = '''export function main(args: []string): int {
    print("PRINT %s");
    eprint("ERROR %s");
    const data = bytesFromStr("WRITE %s\\n");
    print("COUNT " + toStr(stdoutWrite(data)));
    if (len(args) > 0) { print("ARG " + args[0]); }
    return 0;
}
''' % (TEXT, TEXT, TEXT)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def inside(plan_path):
    """This helper is attached to the actual PTY; the native child inherits it."""
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    info = {'stdin_tty': os.isatty(0), 'stdout_tty': os.isatty(1), 'stderr_tty': os.isatty(2)}
    if os.name == 'nt':
        from ctypes import wintypes as w
        k = c.WinDLL('kernel32', use_last_error=True)
        k.GetStdHandle.argtypes = [w.DWORD]
        k.GetStdHandle.restype = w.HANDLE
        k.GetConsoleMode.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
        k.GetConsoleMode.restype = w.BOOL
        k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        k.CreateFileW.restype = w.HANDLE
        k.SetStdHandle.argtypes = [w.DWORD, w.HANDLE]
        k.SetStdHandle.restype = w.BOOL
        # A process attached through ConPTY can retain the launcher's redirected
        # standard handles. Explicitly open this new console's devices, then let
        # the native child inherit those actual handles (never the outer pipes).
        import msvcrt
        for descriptor, name in enumerate(('CONIN$', 'CONOUT$', 'CONOUT$')):
            handle = k.CreateFileW(name, 0xc0000000, 3, None, 3, 0, None)
            if handle in (None, c.c_void_p(-1).value):
                raise c.WinError(c.get_last_error())
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            os.dup2(fd, descriptor)
            os.close(fd)
            if not k.SetStdHandle((-10 - descriptor) & 0xffffffff, msvcrt.get_osfhandle(descriptor)):
                raise c.WinError(c.get_last_error())
        k.SetConsoleCP.argtypes = [w.UINT]
        k.SetConsoleOutputCP.argtypes = [w.UINT]
        page = plan['codepage']
        if not k.SetConsoleCP(page) or not k.SetConsoleOutputCP(page):
            raise c.WinError(c.get_last_error())
        info['console_handles'] = [bool(k.GetConsoleMode(k.GetStdHandle(n & 0xffffffff), c.byref(w.DWORD())))
                                   for n in (-10, -11, -12)]
        info['input_codepage'] = k.GetConsoleCP()
        info['output_codepage'] = k.GetConsoleOutputCP()
        assert info['console_handles'] == [True, True, True], info
        assert info['input_codepage'] == info['output_codepage'] == page, info
    else:
        assert all(info.values()), info
    write_json(Path(plan['info']), info)
    return subprocess.run(plan['argv'], cwd=plan['cwd'], stdin=0, stdout=1, stderr=2, timeout=60).returncode


def conpty(argv, cwd):
    """Windows ConPTY: native Console APIs inside, UTF-8/VT transport outside."""
    from ctypes import wintypes as w
    k = c.WinDLL('kernel32', use_last_error=True)

    class Coord(c.Structure):
        _fields_ = [('x', c.c_short), ('y', c.c_short)]

    class Startup(c.Structure):
        _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR), ('title', w.LPWSTR),
                    ('x', w.DWORD), ('y', w.DWORD), ('xs', w.DWORD), ('ys', w.DWORD),
                    ('xc', w.DWORD), ('yc', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                    ('show', w.WORD), ('reserved_size', w.WORD), ('reserved_ptr', c.c_void_p),
                    ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]

    class StartupEx(c.Structure):
        _fields_ = [('si', Startup), ('attributes', c.c_void_p)]

    class Process(c.Structure):
        _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]

    k.CreatePipe.argtypes = [c.POINTER(w.HANDLE), c.POINTER(w.HANDLE), c.c_void_p, w.DWORD]
    k.CreatePipe.restype = w.BOOL
    k.CreatePseudoConsole.argtypes = [Coord, w.HANDLE, w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    k.CreatePseudoConsole.restype = c.c_long
    k.ClosePseudoConsole.argtypes = [w.HANDLE]
    k.ClosePseudoConsole.restype = None
    k.InitializeProcThreadAttributeList.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)]
    k.InitializeProcThreadAttributeList.restype = w.BOOL
    k.UpdateProcThreadAttribute.argtypes = [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p]
    k.UpdateProcThreadAttribute.restype = w.BOOL
    k.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
    k.DeleteProcThreadAttributeList.restype = None
    k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD,
                                c.c_void_p, w.LPCWSTR, c.POINTER(StartupEx), c.POINTER(Process)]
    k.CreateProcessW.restype = w.BOOL
    k.ReadFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
    k.ReadFile.restype = w.BOOL
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    k.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    k.GetExitCodeProcess.restype = w.BOOL
    k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    k.CloseHandle.argtypes = [w.HANDLE]

    handles = []
    pseudo = w.HANDLE()
    process = Process()
    attributes = None
    initialized = False
    chunks = []
    errors = []
    reader = None

    def pipe():
        read, write = w.HANDLE(), w.HANDLE()
        if not k.CreatePipe(c.byref(read), c.byref(write), None, 0):
            raise c.WinError(c.get_last_error())
        handles.extend([read, write])
        return read, write

    def drain(handle):
        block = c.create_string_buffer(16384)
        count = w.DWORD()
        total = 0
        while k.ReadFile(handle, block, len(block), c.byref(count), None) and count.value:
            total += count.value
            if total > 4 * 1024 * 1024:
                errors.append('console output exceeded 4 MiB')
            else:
                chunks.append(block.raw[:count.value])
        if c.get_last_error() not in (0, 109, 232):
            errors.append(str(c.WinError(c.get_last_error())))

    try:
        input_read, input_write = pipe()
        output_read, output_write = pipe()
        status = k.CreatePseudoConsole(Coord(160, 50), input_read, output_write, 0, c.byref(pseudo))
        if status != 0:
            raise OSError('CreatePseudoConsole HRESULT ' + hex(status & 0xffffffff))
        size = c.c_size_t()
        k.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)):
            raise c.WinError(c.get_last_error())
        initialized = True
        if not k.UpdateProcThreadAttribute(attributes, 0, 0x20016, pseudo, c.sizeof(w.HANDLE), None, None):
            raise c.WinError(c.get_last_error())
        startup = StartupEx()
        startup.si.cb = c.sizeof(startup)
        startup.attributes = c.cast(attributes, c.c_void_p)
        line = c.create_unicode_buffer(subprocess.list2cmdline(list(map(str, argv))))
        reader = threading.Thread(target=drain, args=(output_read,), daemon=True)
        reader.start()
        if not k.CreateProcessW(None, line, None, None, False, 0x80000, None, str(cwd),
                                c.byref(startup), c.byref(process)):
            raise c.WinError(c.get_last_error())
        for handle in (input_read, output_write):
            k.CloseHandle(handle)
            handles.remove(handle)
        if k.WaitForSingleObject(process.process, 90000) != 0:
            k.TerminateProcess(process.process, 99)
            k.WaitForSingleObject(process.process, 5000)
            raise TimeoutError('ConPTY child exceeded 90 seconds')
        code = w.DWORD()
        if not k.GetExitCodeProcess(process.process, c.byref(code)):
            raise c.WinError(c.get_last_error())
        return code.value, chunks, errors
    finally:
        # Drain on another thread while ClosePseudoConsole flushes its screen.
        if pseudo.value:
            k.ClosePseudoConsole(pseudo)
        if reader:
            reader.join(10)
            if reader.is_alive():
                errors.append('ConPTY reader failed to stop')
        if initialized:
            k.DeleteProcThreadAttributeList(attributes)
        for handle in (process.thread, process.process, *handles):
            if handle:
                k.CloseHandle(handle)


def unix_pty(argv, cwd):
    import pty
    import select
    master, slave = pty.openpty()
    chunks = []
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True, env={**os.environ, 'LANG': 'C.UTF-8'})
        os.close(slave)
        slave = -1
        end = time.monotonic() + 90
        while time.monotonic() < end:
            if select.select([master], [], [], .1)[0]:
                try:
                    block = os.read(master, 16384)
                except OSError as error:
                    if error.errno == 5:
                        break
                    raise
                if not block:
                    break
                chunks.append(block)
        else:
            process.kill()
            raise TimeoutError('PTY child exceeded 90 seconds')
        return process.wait(timeout=5), chunks, []
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--inside':
        return inside(sys.argv[2])
    from build import native_compiler
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path)
    ap.add_argument('--work', type=Path, default=ROOT / 'work/native-console')
    ap.add_argument('--report', type=Path)
    a = ap.parse_args()
    compiler = native_compiler(a.compiler)
    a.work.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='console-', dir=a.work.resolve()))
    source = work / '控制台 & unicode.tx'
    source.write_text(SOURCE, encoding='utf-8', newline='\n')
    subprocess.run([compiler, 'build', source, '--toolchain', '--out', work / 'product'], cwd=ROOT,
                   check=True, capture_output=True, timeout=120)
    product = work / 'product' / (source.stem + ('.exe' if os.name == 'nt' else ''))
    expected_out = [f'PRINT {TEXT}', f'WRITE {TEXT}', f'COUNT {len(("WRITE " + TEXT + chr(10)).encode("utf-8"))}', f'ARG {ARG}']
    expected_err = [f'ERROR {TEXT}']
    report = {'compiler_sha256': hashlib.sha256(compiler.read_bytes()).hexdigest(), 'platform': sys.platform,
              'work': str(work), 'checks': [], 'status': 'RUNNING'}

    def check(name, ok, detail):
        report['checks'].append({'name': name, 'passed': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name, flush=True)
        if not ok:
            raise AssertionError(name + ': ' + str(detail))

    commands = {'product': [str(product), ARG],
                'cli-run': [str(compiler), 'run', '--toolchain', str(source), ARG]}
    try:
        for route, argv in commands.items():
            p = subprocess.run(argv, cwd=work, capture_output=True, timeout=90)
            check(route + ': redirected UTF-8 stdout', p.returncode == 0 and p.stdout.decode('utf-8').splitlines() == expected_out,
                  {'exit': p.returncode, 'stdout': p.stdout.decode('utf-8'), 'stderr': p.stderr.decode('utf-8')})
            check(route + ': redirected UTF-8 stderr', p.stderr.decode('utf-8').splitlines() == expected_err, {})
            pages = [437, 936, 65001] if os.name == 'nt' else [None]
            for page in pages:
                name = route + ': terminal ' + (str(page) if page else 'UTF-8')
                plan = work / (route + '-' + str(page) + '.json')
                info_path = plan.with_suffix('.inside.json')
                write_json(plan, {'argv': argv, 'cwd': str(work), 'codepage': page, 'info': str(info_path)})
                helper = [sys.executable, str(Path(__file__).resolve()), '--inside', str(plan)]
                code, chunks, errors = (conpty if os.name == 'nt' else unix_pty)(helper, work)
                raw = b''.join(chunks)
                plan.with_suffix('.vt.bin').write_bytes(raw)
                text = raw.decode('utf-8')
                # Remove VT positioning/style sequences, leaving actual Unicode text.
                plain = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[=>]', '', text)
                info = json.loads(info_path.read_text(encoding='utf-8')) if info_path.exists() else None
                check(name, code == 0 and not errors and info is not None and
                      all(line in plain for line in expected_out + expected_err),
                      {'exit': code, 'inside': info, 'errors': errors, 'text': plain})
        if os.name == 'nt':
            # Prove this is a real non-UTF-8 console, not a redirected pipe that
            # would also pass the positive Unicode checks with raw UTF-8 bytes.
            plan = work / 'raw-negative.json'
            info_path = work / 'raw-negative.inside.json'
            write_json(plan, {'argv': [sys.executable, '-c', 'import os; os.write(1, ' + repr(TEXT.encode('utf-8')) + ')'],
                              'cwd': str(work), 'codepage': 936, 'info': str(info_path)})
            code, chunks, errors = conpty([sys.executable, str(Path(__file__).resolve()), '--inside', str(plan)], work)
            raw = b''.join(chunks)
            plan.with_suffix('.vt.bin').write_bytes(raw)
            check('negative control: raw UTF-8 at CP936 is not valid Unicode output',
                  code == 0 and not errors and info_path.exists() and TEXT not in raw.decode('utf-8'),
                  {'exit': code, 'errors': errors, 'text': raw.decode('utf-8')})
            # D80's second consumer: Windows PowerShell 5.1 decodes native stdout
            # in a pipeline. UTF-8 is an explicit session setting, never a change
            # to the native pipe-byte contract or the user's persistent profile.
            powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            invoke = ' '.join(quote(value) for value in commands['cli-run'])
            script = '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ' + \
                '$lines = & ' + invoke + ' | ForEach-Object { $_ }; ' + \
                'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; ' + \
                '[Console]::Out.WriteLine(($lines -join "`n"))'
            p = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                               cwd=work, capture_output=True, timeout=90)
            check('Windows PowerShell UTF-8 pipeline', p.returncode == 0 and
                  p.stdout.decode('utf-8').splitlines() == expected_out and
                  expected_err[0] in p.stderr.decode('utf-8'),
                  {'exit': p.returncode, 'stdout': p.stdout.decode('utf-8'), 'stderr': p.stderr.decode('utf-8')})
        report['status'] = 'PASS'
        return 0
    finally:
        if report['status'] != 'PASS':
            report['status'] = 'FAIL'
        destination = a.report or work / 'report.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_json(destination, report)
        print('Report:', destination, flush=True)


if __name__ == '__main__':
    sys.exit(main())
