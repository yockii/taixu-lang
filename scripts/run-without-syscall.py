#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a command with some system calls unavailable (Linux x86_64 and aarch64; round 6 step
§六; aarch64 since multi-arch A2).

Installs a seccomp filter that makes the named system calls fail with ENOSYS, the way gVisor-style sandboxes and some
container seccomp profiles do, then executes the command. The filter is inherited by every child process, so a test
script run under it sees the same restriction in the products it starts.

Usage: python3 scripts/run-without-syscall.py <name|nr>[,<name|nr>...] -- <command> [args...]
       e.g. python3 scripts/run-without-syscall.py process_vm_readv -- python3 scripts/test-native-debug-stub.py --compiler ...
A name is looked up for the host architecture; a bare number is the host's own system call number.
"""
import ctypes
import os
import struct
import sys

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
# 宿主架构 → (seccomp 的 AUDIT_ARCH, 名字 → 号)。号取自 Linux v7.3 的 syscall_64.tbl 与通用表（同编译器的号表）；只收用到的。
ARCHES = {'x86_64': (0xC000003E, {'waitid': 247, 'process_vm_readv': 310}),
          'aarch64': (0xC00000B7, {'waitid': 95, 'process_vm_readv': 270})}
ENOSYS = 38
BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_RET_K = 0x06


def insn(code, jt, jf, k):
    return struct.pack('<HBBI', code, jt, jf, k)


def program(audit_arch, nrs):
    """arch != the host's → allow; nr in nrs → ENOSYS; otherwise allow."""
    out = [insn(BPF_LD_W_ABS, 0, 0, 4), insn(BPF_JEQ_K, 1, 0, audit_arch), insn(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW),
           insn(BPF_LD_W_ABS, 0, 0, 0)]
    for i, nr in enumerate(nrs):
        # equal → jump to the ENOSYS return after the remaining comparisons and the allow.
        out.append(insn(BPF_JEQ_K, len(nrs) - i, 0, nr))
    out.append(insn(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    out.append(insn(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | ENOSYS))
    return b''.join(out), len(out)


def main():
    argv = sys.argv[1:]
    arch = ARCHES.get(os.uname().machine) if sys.platform.startswith('linux') else None
    names = argv[0].split(',') if len(argv) >= 3 and argv[1] == '--' and arch else []
    if not names or not all(x.isdigit() or x in arch[1] for x in names):
        sys.stderr.write(__doc__.split('\n\n')[-1] + '\n')
        return 64
    nrs = [int(x) if x.isdigit() else arch[1][x] for x in names]
    code, n = program(arch[0], nrs)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    buf = ctypes.create_string_buffer(code)
    fprog = struct.pack('<HxxxxxxQ', n, ctypes.addressof(buf))
    fbuf = ctypes.create_string_buffer(fprog)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        sys.stderr.write('prctl(NO_NEW_PRIVS) failed: errno %d\n' % ctypes.get_errno())
        return 3
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(fbuf), 0, 0) != 0:
        sys.stderr.write('prctl(SECCOMP) failed: errno %d\n' % ctypes.get_errno())
        return 3
    os.execvp(argv[2], argv[2:])


if __name__ == '__main__':
    sys.exit(main())
