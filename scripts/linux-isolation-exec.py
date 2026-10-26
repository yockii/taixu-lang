"""Linux x86_64: restrict this process, then exec one native product. No fallback."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import struct


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--record',type=Path,required=True)
    ap.add_argument('command',nargs=argparse.REMAINDER)
    a=ap.parse_args()
    assert os.name=='posix' and os.uname().machine=='x86_64'
    cmd=a.command[1:] if a.command[:1]==['--'] else a.command
    root=a.root.resolve(strict=True)
    assert root!=Path('/') and Path(cmd[0]).resolve().is_relative_to(root)
    libc=c.CDLL(None,use_errno=True)
    libc.syscall.restype=c.c_long
    libc.prctl.argtypes=[c.c_int,c.c_ulong,c.c_ulong,c.c_ulong,c.c_ulong]
    def checked(value):
        if value<0:raise OSError(c.get_errno(),os.strerror(c.get_errno()))
        return value
    abi=checked(libc.syscall(444,None,0,1))
    if abi<5:raise RuntimeError('Landlock ABI >=5 is required')
    mask=(1<<16)-1
    attr=c.c_uint64(mask)
    fd=checked(libc.syscall(444,c.byref(attr),c.sizeof(attr),0))
    class Beneath(c.Structure):
        _pack_=1
        _fields_=[('access',c.c_uint64),('parent',c.c_int32)]
    rules=[(root,mask),(Path('/proc'),12),(Path('/dev/null'),6),(Path('/dev/zero'),4)]
    try:
        for path,rights in rules:
            handle=os.open(path,os.O_PATH|os.O_CLOEXEC)
            try:
                rule=Beneath(rights,handle)
                checked(libc.syscall(445,fd,1,c.byref(rule),0))
            finally:os.close(handle)
        checked(libc.prctl(38,1,0,0,0))
        # Read arch, kill an unexpected ABI, then reject process creation and
        # sockets with EPERM. execve remains for the one initial native image;
        # Landlock permits executing only files in the private workspace.
        def ins(code,jt,jf,k):return struct.pack('<HBBI',code,jt,jf,k)
        denied=[56,57,58,435,41,53]
        code=[ins(0x20,0,0,4),ins(0x15,1,0,0xc000003e),ins(6,0,0,0x80000000),ins(0x20,0,0,0)]
        for i,nr in enumerate(denied):code.append(ins(0x15,len(denied)-i,0,nr))
        code += [ins(6,0,0,0x7fff0000),ins(6,0,0,0x50001)]
        buf=c.create_string_buffer(b''.join(code))
        prog=c.create_string_buffer(struct.pack('<HxxxxxxQ',len(code),c.addressof(buf)))
        a.record.write_text(json.dumps({'landlock_abi':abi,'paths':[(str(p),v) for p,v in rules],
                                        'seccomp_eperm':denied})+'\n',encoding='utf-8')
        checked(libc.syscall(446,fd,0))
        checked(libc.prctl(22,2,c.addressof(prog),0,0))
    finally:os.close(fd)
    os.execve(cmd[0],cmd,os.environ)


if __name__=='__main__':main()
