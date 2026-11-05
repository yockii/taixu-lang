"""在 Windows ARM64 测试机上批量运行产物（照 scripts/macos_remote.py）。

一次 ssh 把 tar（产物、清单、PowerShell 执行器，以及可选的目录树）送进测试机上新建的临时目录，再在那里逐个带时限运行，结果
（退出码、标准输出、标准错误）以 JSON 行经标准输出取回；临时目录是本批自己建的，结束后删掉。测试机须能免交互 ssh 登录、自带
tar.exe（Windows 10 1803 起）与 Windows PowerShell 5.1（OpenSSH 服务端的缺省外壳为 cmd）。

宿主写成 ssh 的实参串（shlex 切分后接在 `ssh -o BatchMode=yes` 之后），例如经 M1 跳到虚拟机：
  -J <跳板机> -p 2222 -i ~/.ssh/taixu_winarm_vm -o StrictHostKeyChecking=accept-new tx@127.0.0.1
库用法：run_batch(host, items, timeout, trees) → {key: (exit, stdout, stderr)}；items 为 (key, 本地产物路径, argv, env[, cwd])。
产物保留文件名（各放一个子目录）；trees 为 {名字: 本地目录}，整棵带过去，cwd 写树的名字时产物在该树里运行（夹具按相对路径找到）。
退出码按 Win32 的 32 位无符号值给出（同本机 subprocess；异常终止为 NTSTATUS，如 0xC0000005）。
命令行：python scripts/windows_remote.py [--host <ssh 实参串>] <产物> [实参...]（缺省取环境变量 TAIXU_WINARM_HOST）  运行一个产物并原样转出。
"""
import base64
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

# 远端执行器（PowerShell 5.1）：读清单，逐个运行；实参按 CommandLineToArgvW 的规则加引号；标准输出与标准错误异步读入内存。
RUNNER = r'''
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$items = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'manifest.json') | ConvertFrom-Json
function Quote([string]$a) {
    if ($a -ne '' -and $a -notmatch '[\s"]') { return $a }
    $s = '"'; $bs = 0
    foreach ($ch in $a.ToCharArray()) {
        if ($ch -eq [char]92) { $bs++ }
        elseif ($ch -eq [char]34) { $s += ([string][char]92 * (2 * $bs + 1)) + '"'; $bs = 0 }
        else { $s += ([string][char]92 * $bs) + $ch; $bs = 0 }
    }
    return $s + ([string][char]92 * (2 * $bs)) + '"'
}
foreach ($it in $items) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $root $it.file
    $psi.Arguments = (@($it.argv) | ForEach-Object { Quote $_ }) -join ' '
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.WorkingDirectory = $(if ($it.cwd) { Join-Path (Join-Path $root 'trees') $it.cwd } else { $root })
    if ($it.env) { foreach ($e in $it.env.PSObject.Properties) { $psi.EnvironmentVariables[$e.Name] = [string]$e.Value } }
    $out = New-Object System.IO.MemoryStream
    $err = New-Object System.IO.MemoryStream
    try {
        $p = [System.Diagnostics.Process]::Start($psi)
        $t1 = $p.StandardOutput.BaseStream.CopyToAsync($out)
        $t2 = $p.StandardError.BaseStream.CopyToAsync($err)
        if ($p.WaitForExit([int]$it.timeout * 1000)) {
            $p.WaitForExit(); [void]$t1.Wait(10000); [void]$t2.Wait(10000)
            $code = [int64][uint32]($p.ExitCode -band 0xFFFFFFFFL)
            $o = $out.ToArray(); $e2 = $err.ToArray()
        } else {
            try { $p.Kill() } catch {}
            $code = -1; $o = @(); $e2 = [System.Text.Encoding]::ASCII.GetBytes('TIMEOUT')
        }
    } catch {
        $code = -2; $o = @(); $e2 = [System.Text.Encoding]::UTF8.GetBytes([string]$_)
    }
    $row = [ordered]@{ key = $it.key; exit = $code; stdout = [Convert]::ToBase64String([byte[]]$o); stderr = [Convert]::ToBase64String([byte[]]$e2) }
    [Console]::Out.WriteLine(($row | ConvertTo-Json -Compress))
}
'''


def ssh(host, command, stdin=None, timeout=3600):
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', *shlex.split(host), command]
    p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _plain(info):
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    return info


def run_batch(host, items, timeout=120, trees=None):
    """items: [(key, path, argv, env[, cwd])]. Returns {key: (exit, stdout, stderr)}; missing keys mean the batch broke."""
    buf = io.BytesIO()
    manifest = []
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for tree, local in (trees or {}).items():
            tar.add(str(local), arcname='trees/' + tree, filter=_plain)
        for n, item in enumerate(items):
            key, path, argv, env = item[:4]
            cwd = item[4] if len(item) > 4 else None
            name = 'p%04d/%s' % (n, Path(path).name)
            tar.add(str(path), arcname=name, filter=_plain)
            manifest.append({'key': key, 'file': name.replace('/', '\\'), 'argv': [str(a) for a in argv], 'env': env or {},
                             'timeout': timeout, 'cwd': cwd})
        for name, text in (('manifest.json', json.dumps(manifest, ensure_ascii=False)), ('runner.ps1', '﻿' + RUNNER)):
            data = text.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    # 临时目录：本批新建（%TEMP%\taixu-remote-<guid>），解包、运行、删掉。
    code, out, err = ssh(host, 'powershell -NoProfile -Command "$d = Join-Path $env:TEMP (\'taixu-remote-\' + [guid]::NewGuid()); '
                               '[void](New-Item -ItemType Directory $d); $d"')
    remote = out.decode('utf-8', 'replace').strip()
    if code != 0 or not remote:
        raise RuntimeError('remote mkdir failed (exit %d): %s' % (code, err.decode('utf-8', 'replace')[-400:]))
    try:
        code, out, err = ssh(host, 'tar -x -f - -C "%s"' % remote, stdin=buf.getvalue(), timeout=1800)
        if code != 0:
            raise RuntimeError('remote unpack failed (exit %d): %s' % (code, err.decode('utf-8', 'replace')[-400:]))
        code, out, err = ssh(host, 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s\\runner.ps1"' % remote,
                             timeout=timeout * max(1, len(items)) + 600)
    finally:
        ssh(host, 'powershell -NoProfile -Command "Remove-Item -Recurse -Force -LiteralPath \'%s\'"' % remote)
    results = {}
    for line in out.decode('utf-8', 'replace').splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        row = json.loads(line)
        results[row['key']] = (row['exit'], base64.b64decode(row['stdout']), base64.b64decode(row['stderr']))
    if code != 0 and len(results) < len(items):
        raise RuntimeError('remote batch failed (exit %d): %s' % (code, err.decode('utf-8', 'replace')[-600:]))
    return results


def main(argv):
    host = os.environ.get('TAIXU_WINARM_HOST', '')
    if len(argv) >= 2 and argv[0] == '--host':
        host, argv = argv[1], argv[2:]
    if not argv or not host:
        print(__doc__)
        return 64
    res = run_batch(host, [('one', Path(argv[0]), argv[1:], {})])
    code, out, err = res['one']
    sys.stdout.buffer.write(out)
    sys.stderr.buffer.write(err)
    return code if 0 <= code < 256 else 125


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
