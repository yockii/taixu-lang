"""A task-specific restricting SID denies helper files without changing normal users."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import subprocess
import uuid

from selfhost_windows_job import Process, Startup, checked, k

a = c.WinDLL('advapi32', use_last_error=True)


class SidAttributes(c.Structure):
    _fields_ = [('sid', c.c_void_p), ('attributes', w.DWORD)]


class Groups(c.Structure):
    _fields_ = [('count', w.DWORD), ('groups', SidAttributes * 1)]


for name, result, args in [
    ('OpenProcessToken', w.BOOL, [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]),
    ('GetTokenInformation', w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]),
    ('ConvertStringSidToSidW', w.BOOL, [w.LPCWSTR, c.POINTER(c.c_void_p)]),
    ('ConvertSidToStringSidW', w.BOOL, [c.c_void_p, c.POINTER(w.LPWSTR)]),
    ('CreateRestrictedToken', w.BOOL, [w.HANDLE, w.DWORD, w.DWORD, c.c_void_p,
                                    w.DWORD, c.c_void_p, w.DWORD, c.c_void_p, c.POINTER(w.HANDLE)]),
    ('CreateProcessAsUserW', w.BOOL, [w.HANDLE, w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p,
                                   w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR,
                                   c.POINTER(Startup), c.POINTER(Process)]),
]:
    fn = getattr(a, name)
    fn.restype, fn.argtypes = result, args
k.GetCurrentProcess.restype = w.HANDLE
k.GetCurrentProcess.argtypes = []
k.LocalFree.argtypes = [c.c_void_p]
k.LocalFree.restype = c.c_void_p


def token_info(token, kind):
    length = w.DWORD()
    a.GetTokenInformation(token, kind, None, 0, c.byref(length))
    buf = c.create_string_buffer(length.value)
    checked(a.GetTokenInformation(token, kind, buf, length.value, c.byref(length)))
    return buf


class Trustee(c.Structure):
    _fields_ = [('multiple', c.c_void_p), ('operation', c.c_int), ('form', c.c_int),
                ('kind', c.c_int), ('name', c.c_void_p)]


class Access(c.Structure):
    _fields_ = [('rights', w.DWORD), ('mode', c.c_int), ('inheritance', w.DWORD), ('trustee', Trustee)]


a.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, c.c_int, w.DWORD, c.c_void_p, c.c_void_p,
                                  c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p)]
a.GetNamedSecurityInfoW.restype = w.DWORD
a.SetEntriesInAclW.argtypes = [w.ULONG, c.POINTER(Access), c.c_void_p, c.POINTER(c.c_void_p)]
a.SetEntriesInAclW.restype = w.DWORD
a.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p]
a.SetNamedSecurityInfoW.restype = w.DWORD
a.GetAclInformation.argtypes = [c.c_void_p, c.c_void_p, w.DWORD, c.c_int]
a.GetAclInformation.restype = w.BOOL
a.GetAce.argtypes = [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]
a.GetAce.restype = w.BOOL
a.DeleteAce.argtypes = [c.c_void_p, w.DWORD]
a.DeleteAce.restype = w.BOOL
a.EqualSid.argtypes = [c.c_void_p, c.c_void_p]
a.EqualSid.restype = w.BOOL
k.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
k.CreateFileW.restype = w.HANDLE


def change_acl(path, sid, mode, rights=0x1f01ff, inherit=True):
    old = c.c_void_p(); descriptor = c.c_void_p(); new = c.c_void_p()
    code = a.GetNamedSecurityInfoW(str(path), 1, 4, None, None, c.byref(old), None, c.byref(descriptor))
    if code:
        raise c.WinError(code)
    try:
        if mode == 4:
            # REVOKE_ACCESS only removes allowed/audit ACEs, not denied ACEs.
            # Remove only this task's basic allow/deny entries explicitly.
            if not old:
                return
            info = (w.DWORD * 3)()
            checked(a.GetAclInformation(old, c.byref(info), c.sizeof(info), 2))
            removed = False
            for index in range(info[0] - 1, -1, -1):
                ace = c.c_void_p()
                checked(a.GetAce(old, index, c.byref(ace)))
                kind = c.c_ubyte.from_address(ace.value).value
                if kind in (0, 1) and a.EqualSid(c.c_void_p(ace.value + 8), sid):
                    checked(a.DeleteAce(old, index)); removed = True
            if not removed:
                return
            code = a.SetNamedSecurityInfoW(str(path), 1, 4, None, None, old, None)
            if code:
                raise c.WinError(code)
            return
        entry = Access(rights, mode, 3 if inherit and path.is_dir() else 0, Trustee(None, 0, 0, 0, sid))
        code = a.SetEntriesInAclW(1, c.byref(entry), old, c.byref(new))
        if code:
            raise c.WinError(code)
        code = a.SetNamedSecurityInfoW(str(path), 1, 4, None, None, new, None)
        if code:
            raise c.WinError(code)
    finally:
        if new:
            k.LocalFree(new)
        k.LocalFree(descriptor)


def acl_has_sid(path, sid):
    acl = c.c_void_p(); descriptor = c.c_void_p()
    code = a.GetNamedSecurityInfoW(str(path),1,4,None,None,c.byref(acl),None,c.byref(descriptor))
    if code:
        raise c.WinError(code)
    try:
        if not acl:
            return False
        info = (w.DWORD * 3)()
        checked(a.GetAclInformation(acl,c.byref(info),c.sizeof(info),2))
        for index in range(info[0]):
            ace = c.c_void_p(); checked(a.GetAce(acl,index,c.byref(ace)))
            if c.c_ubyte.from_address(ace.value).value in (0,1) and a.EqualSid(c.c_void_p(ace.value+8),sid):
                return True
        return False
    finally:
        if descriptor:
            k.LocalFree(descriptor)


class RestrictedFiles:
    def __init__(self):
        self.sid_string = 'S-1-5-21-' + '-'.join(str(int.from_bytes(uuid.uuid4().bytes[:4], 'little')) for _ in range(4))
        self.sid = c.c_void_p()
        self.token = w.HANDLE()
        self.paths = []
        self.allowed_paths = []
        self.ancestor_paths = []
        self.commands = []
        self.cleaned = False

    def __enter__(self):
        original = w.HANDLE()
        checked(a.OpenProcessToken(k.GetCurrentProcess(), 0xb, c.byref(original)))
        try:
            user = token_info(original, 1)
            groups = token_info(original, 2)
            checked(a.ConvertStringSidToSidW(self.sid_string, c.byref(self.sid)))
            pointers = [c.cast(user, c.POINTER(SidAttributes)).contents.sid]
            count = c.cast(groups, c.POINTER(w.DWORD)).contents.value
            for i in range(count):
                item = SidAttributes.from_buffer(groups, Groups.groups.offset + i * c.sizeof(SidAttributes))
                pointers.append(item.sid)
            pointers.append(self.sid.value)
            sids = (SidAttributes * len(pointers))(*(SidAttributes(p, 0) for p in pointers))
            # Both normal access and restricting-SID access are checked; the
            # unique SID explicitly denies registered helper files.
            checked(a.CreateRestrictedToken(original, 1, 0, None, 0, None, len(sids), sids, c.byref(self.token)))
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise
        finally:
            k.CloseHandle(original)

    def deny(self, paths):
        paths = [Path(item).resolve() for item in paths]
        # Verify WRITE_DAC on every named target before changing any of them.
        for path in paths:
            if path == Path(path.anchor) or not path.exists():
                raise ValueError('Refuse nonexistent/root ACL target: ' + str(path))
            handle = k.CreateFileW(str(path),0x60000,7,None,3,0x02000000,None)
            if handle == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            k.CloseHandle(handle)
        for item in paths:
            path = Path(item).resolve()
            if path == Path(path.anchor) or not path.exists():
                raise ValueError('Refuse nonexistent/root ACL target: ' + str(path))
            if path not in self.paths:
                change_acl(path, self.sid, 3)
                self.paths.append(path)
                self.commands.append({'api': 'SetNamedSecurityInfoW', 'path': str(path), 'mode': 'DENY', 'exit': 0})

    def allow_workspace(self, root):
        import tempfile
        root = Path(root).resolve()
        if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('v050-'):
            raise ValueError('Allow ACL only for a task-owned temporary workspace')
        self.workspace = root
        for path in [root, *root.rglob('*')]:
            self.allow_new_path(path)

    def allow_new_path(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError('Allow ACL outside private workspace')
        if path not in self.allowed_paths:
            self.allowed_paths.append(path)
            change_acl(path, self.sid, 1)
            self.commands.append({'api': 'SetNamedSecurityInfoW', 'path': str(path), 'mode': 'ALLOW', 'exit': 0})

    def __exit__(self, *ignored):
        if self.token:
            k.CloseHandle(self.token); self.token = w.HANDLE()
        failures = []
        for path in list(reversed(self.paths)) + list(reversed(self.allowed_paths)) + list(reversed(self.ancestor_paths)):
            try:
                change_acl(path, self.sid, 4)
                self.commands.append({'api': 'SetNamedSecurityInfoW', 'path': str(path), 'mode': 'REMOVE_ONLY_TASK_SID', 'exit': 0})
            except OSError as error:
                failures.append(str(path) + ': ' + str(error))
        # Check actual ACLs, including files created by the restricted compiler.
        verify = set(self.paths + self.allowed_paths + self.ancestor_paths)
        if hasattr(self,'workspace'):
            verify.update(self.workspace.rglob('*'))
        for path in verify:
            try:
                if acl_has_sid(path,self.sid):
                    failures.append(str(path)+': task SID ACE remains after cleanup')
            except OSError as error:
                failures.append(str(path)+': '+str(error))
        if self.sid:
            k.LocalFree(self.sid); self.sid = c.c_void_p()
        self.cleaned = not failures
        if failures:
            raise RuntimeError('Restricting SID ACL removal failed: ' + repr(failures))
