"""Run one Windows process in a job that forbids every child process."""
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import time

k = c.WinDLL('kernel32', use_last_error=True)


class Basic(c.Structure):
    _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                ('process_limit', w.DWORD), ('affinity', c.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class Limits(c.Structure):
    _fields_ = [('basic', Basic), ('io', c.c_uint64 * 6),
                ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]


class Startup(c.Structure):
    _fields_ = [('size', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR),
                ('title', w.LPWSTR), ('x', w.DWORD), ('y', w.DWORD),
                ('xsize', w.DWORD), ('ysize', w.DWORD), ('xchars', w.DWORD),
                ('ychars', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                ('show', w.WORD), ('reserved_size', w.WORD),
                ('reserved_bytes', c.POINTER(c.c_byte)), ('stdin', w.HANDLE),
                ('stdout', w.HANDLE), ('stderr', w.HANDLE)]


class Process(c.Structure):
    _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE),
                ('pid', w.DWORD), ('tid', w.DWORD)]


class Module(c.Structure):
    _fields_ = [('size', w.DWORD), ('id', w.DWORD), ('pid', w.DWORD),
                ('global_count', w.DWORD), ('process_count', w.DWORD),
                ('base', c.c_void_p), ('base_size', w.DWORD), ('module', w.HANDLE),
                ('name', w.WCHAR * 256), ('path', w.WCHAR * 260)]


for name, result, args in [
    ('CreateJobObjectW', w.HANDLE, [c.c_void_p, w.LPCWSTR]),
    ('SetInformationJobObject', w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]),
    ('AssignProcessToJobObject', w.BOOL, [w.HANDLE, w.HANDLE]),
    ('CreateProcessW', w.BOOL, [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p,
                              w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR,
                              c.POINTER(Startup), c.POINTER(Process)]),
    ('ResumeThread', w.DWORD, [w.HANDLE]),
    ('WaitForSingleObject', w.DWORD, [w.HANDLE, w.DWORD]),
    ('GetExitCodeProcess', w.BOOL, [w.HANDLE, c.POINTER(w.DWORD)]),
    ('CloseHandle', w.BOOL, [w.HANDLE]),
    ('TerminateProcess', w.BOOL, [w.HANDLE, w.UINT]),
    ('CreateToolhelp32Snapshot', w.HANDLE, [w.DWORD, w.DWORD]),
    ('Module32FirstW', w.BOOL, [w.HANDLE, c.POINTER(Module)]),
    ('Module32NextW', w.BOOL, [w.HANDLE, c.POINTER(Module)]),
]:
    fn = getattr(k, name)
    fn.restype, fn.argtypes = result, args


def checked(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


def modules(pid):
    handle = k.CreateToolhelp32Snapshot(0x18, pid)
    if handle == c.c_void_p(-1).value:
        return set()
    names = set()
    try:
        item = Module(); item.size = c.sizeof(item)
        if k.Module32FirstW(handle, c.byref(item)):
            while True:
                names.add(item.path)
                if not k.Module32NextW(handle, c.byref(item)):
                    break
    finally:
        k.CloseHandle(handle)
    return names


def run_job(cmd, cwd, env, logs, timeout=1800, restricted_files=None, process_limit=1, memory_bytes=None):
    import msvcrt
    if process_limit < 1 or (memory_bytes is not None and memory_bytes <= 0):
        raise ValueError('process_limit and memory_bytes must be positive')
    logs = Path(logs)
    logs.parent.mkdir(parents=True, exist_ok=True)
    job = k.CreateJobObjectW(None, None); checked(job)
    limits = Limits()
    limits.basic.flags = 0x8 | 0x2000  # ACTIVE_PROCESS + KILL_ON_JOB_CLOSE
    limits.basic.process_limit = process_limit
    if memory_bytes is not None:
        limits.basic.flags |= 0x200  # aggregate committed bytes; test harness limit
        limits.job_memory = memory_bytes
    pi = Process()
    deps = set()
    try:
        checked(k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        with open(logs.with_suffix('.stdout'), 'wb') as stdout, \
             open(logs.with_suffix('.stderr'), 'wb') as stderr, open(os.devnull, 'rb') as stdin:
            for file in (stdout, stderr, stdin):
                os.set_handle_inheritable(msvcrt.get_osfhandle(file.fileno()), True)
            si = Startup(); si.size = c.sizeof(si); si.flags = 0x100
            si.stdin, si.stdout, si.stderr = [msvcrt.get_osfhandle(f.fileno())
                                             for f in (stdin, stdout, stderr)]
            flags = 0x404
            startup_ptr = c.byref(si)
            block = c.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in
                                             sorted(env.items())) + '\0\0')
            line = c.create_unicode_buffer(subprocess.list2cmdline([str(p) for p in cmd]))
            start = time.perf_counter_ns()
            if restricted_files is not None:
                from selfhost_restricted_token import a
                checked(a.CreateProcessAsUserW(restricted_files.token, str(cmd[0]), line, None, None, True,
                                               flags, block, str(cwd), startup_ptr, c.byref(pi)))
            else:
                checked(k.CreateProcessW(str(cmd[0]), line, None, None, True, flags,
                                         block, str(cwd), startup_ptr, c.byref(pi)))
            checked(k.AssignProcessToJobObject(job, pi.process))
            if k.ResumeThread(pi.thread) == 0xffffffff:
                raise c.WinError(c.get_last_error())
            while True:
                deps.update(modules(pi.pid))
                if k.WaitForSingleObject(pi.process, 100) == 0:
                    break
                if time.perf_counter_ns() - start > timeout * 1e9:
                    raise TimeoutError(str(cmd))
            code = w.DWORD(); checked(k.GetExitCodeProcess(pi.process, c.byref(code)))
            return {'argv': [str(p) for p in cmd], 'cwd': str(cwd),
                    'exit': code.value, 'ns': time.perf_counter_ns() - start,
                    'pid': pi.pid, 'active_process_limit': process_limit,
                    'restricting_sid': restricted_files.sid_string if restricted_files else None,
                    'modules_observed': sorted(deps),
                    'module_observation': 'Toolhelp snapshots every 100ms; not a complete DLL load trace',
                    'stdout': logs.with_suffix('.stdout').read_text(encoding='utf-8', errors='replace'),
                    'stderr': logs.with_suffix('.stderr').read_text(encoding='utf-8', errors='replace')}
    finally:
        if pi.process:
            k.TerminateProcess(pi.process, 125)
            k.CloseHandle(pi.process)
        if pi.thread:
            k.CloseHandle(pi.thread)
        k.CloseHandle(job)
