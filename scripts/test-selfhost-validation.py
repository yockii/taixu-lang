"""Regression checks for environment delivery and actual OS process isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

from selfhost_validation import clean_env, require_success, run


class ValidationTests(unittest.TestCase):
    def test_platform_edges_cover_both_hosts_without_weakening_boundaries(self):
        from repo_hygiene import check_edges
        with tempfile.TemporaryDirectory(prefix='txedge-test-') as directory:
            root = Path(directory)
            boot = root / 'bootstrap'
            boot.mkdir()
            entry = boot / 'entry.tx'
            entry.write_text('import { value } from "./platform";\n', encoding='utf-8')
            windows, linux, macos = [boot / ('platform_' + host + '.tx') for host in ['windows', 'linux', 'macos']]
            for path in [windows, linux, macos]:
                path.write_text('export function value(): int { return 1; }\n', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                # 编译器自身要编成的每个目标（Windows、Linux 两端 x86_64，M1 原生自举的 aarch64-macos，交叉编的
                # aarch64-linux）沿自己的后缀链都要解析得到：缺 macOS 一端或 Linux 一端都不够。
                self.assertTrue(check_edges(root, [entry, windows, linux, macos]))
                self.assertFalse(check_edges(root, [entry, windows, linux]))
                self.assertFalse(check_edges(root, [entry, windows, macos]))
                self.assertFalse(check_edges(root, [entry, linux, macos]))
                linux.write_text('import { bad } from "../outside";\n', encoding='utf-8')
                self.assertFalse(check_edges(root, [entry, windows, linux, macos]))
                linux.write_text('import { value } from "./entry";\n', encoding='utf-8')
                self.assertFalse(check_edges(root, [entry, windows, linux, macos]))
                # 类 Unix 的 _unix 同时满足 Linux 与 macOS 两端。
                unix = boot / 'platform_unix.tx'
                unix.write_text('export function value(): int { return 2; }\n', encoding='utf-8')
                self.assertTrue(check_edges(root, [entry, windows, unix]))
                # 带目标后缀的文件只在选中它的目标上要求解析：x_linux 导入只有 y_linux 的 ./y 成立（只有 Linux 两个
                # 目标选中 x_linux）；x_unix 导入同样的 ./y 不成立（aarch64-macos 也选中它，那里解析不到）。
                x_linux, x_unix, y_linux = boot / 'x_linux.tx', boot / 'x_unix.tx', boot / 'y_linux.tx'
                y_linux.write_text('export function y(): int { return 3; }\n', encoding='utf-8')
                for path in [x_linux, x_unix]:
                    path.write_text('import { y } from "./y";\n', encoding='utf-8')
                self.assertTrue(check_edges(root, [x_linux, y_linux]))
                self.assertFalse(check_edges(root, [x_unix, y_linux]))

    def test_environment_reaches_child_and_removes_overrides(self):
        with patch.dict(os.environ, {'TAIXU_EMIT_MIRROR': '1', 'TAIXU_REGALLOC_MIRROR': '1',
                                     'TAIXU_SEMAMIRROR_NATIVE': 'invalid-override'}):
            env = clean_env()
        row = run([sys.executable, '-c', 'import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith("TAIXU_")}))'], Path.cwd(), env)
        require_success(row)
        actual = json.loads(row['stdout'])
        self.assertEqual(actual['TAIXU_EMIT_MIRROR'], '0')
        self.assertEqual(actual['TAIXU_REGALLOC_MIRROR'], '0')
        self.assertEqual(actual['TAIXU_MIRROR_NO_CACHE'], '1')
        self.assertNotIn('TAIXU_SEMAMIRROR_NATIVE', actual)

    def test_failed_child_cannot_be_accepted(self):
        row = run([sys.executable, '-c', 'raise SystemExit(2)'], Path.cwd(), clean_env())
        with self.assertRaises(RuntimeError):
            require_success(row)

    def test_timeout_is_not_success(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run([sys.executable, '-c', 'import time; time.sleep(3)'], Path.cwd(), clean_env(), timeout=.1)

    @unittest.skipUnless(os.name == 'nt', 'Windows process job')
    def test_job_actually_forbids_child_process(self):
        from selfhost_windows_job import run_job
        with tempfile.TemporaryDirectory(prefix='txjob-test-') as directory:
            root = Path(directory)
            probe = root / 'probe.py'
            probe.write_text('import subprocess,sys\ntry:\n subprocess.run([sys.executable,"-c","pass"],check=True)\nexcept OSError:\n sys.exit(0)\nsys.exit(99)\n', encoding='utf-8', newline='\n')
            self.assertEqual(subprocess.run([sys.executable, probe], timeout=30).returncode, 99)
            row = run_job([sys.executable, probe], root, clean_env(), root / 'probe', timeout=30)
            self.assertEqual(row['exit'], 0, row['stderr'])
            self.assertEqual(row['active_process_limit'], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL restoration')
    def test_task_allow_and_deny_aces_restored_after_exception(self):
        import ctypes as c
        from ctypes import wintypes as w
        from selfhost_restricted_token import RestrictedFiles, a, k, checked

        def entries(path):
            acl=c.c_void_p();descriptor=c.c_void_p()
            code=a.GetNamedSecurityInfoW(str(path),1,4,None,None,c.byref(acl),None,c.byref(descriptor))
            if code:raise c.WinError(code)
            try:
                info=(w.DWORD*3)()
                checked(a.GetAclInformation(acl,c.byref(info),c.sizeof(info),2))
                rows=[]
                for index in range(info[0]):
                    ace=c.c_void_p();checked(a.GetAce(acl,index,c.byref(ace)))
                    size=c.c_ushort.from_address(ace.value+2).value
                    rows.append(c.string_at(ace,size))
                return rows
            finally:k.LocalFree(descriptor)

        with tempfile.TemporaryDirectory(prefix='v050-acl-roundtrip-') as directory:
            root=Path(directory).resolve()
            self.assertEqual(root.parent,Path(tempfile.gettempdir()).resolve())
            file=root/'control.txt';file.write_text('control\n',encoding='utf-8')
            original_root,original_file=entries(root),entries(file)
            with self.assertRaisesRegex(RuntimeError,'intentional cleanup control'):
                with RestrictedFiles() as policy:
                    policy.allow_workspace(root)
                    policy.deny([file])
                    self.assertTrue(any(row[0]==1 for row in entries(file)))
                    raise RuntimeError('intentional cleanup control')
            self.assertTrue(policy.cleaned)
            self.assertEqual(entries(root),original_root)
            self.assertEqual(entries(file),original_file)


if __name__ == '__main__':
    unittest.main()
