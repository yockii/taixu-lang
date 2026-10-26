"""在 macOS 测试机上批量运行产物（macOS 适配 M1 设计 E10，docs/design/v052-macos-m1-macho-v0.1-20261024.md）。

一次 ssh 送一个 tar（产物、清单、远端执行器，以及可选的目录树），测试机上在新建的临时目录里逐个带时限运行，结果
（退出码、标准输出、标准错误）以 JSON 经标准输出取回；结束后临时目录移入废纸篓（不做不可恢复的删除）。测试机须能
`ssh <host>` 免交互登录，且有 Python 3（缺省 ~/miniforge3/bin/python3）。纳入门留到 M5。

宿主写 local 时不经 ssh，在本机照同一流程运行（测试机上直接跑门时用；执行器用当前的 Python）。
库用法：run_batch(host, items, timeout, trees) → {key: (exit, stdout, stderr)}；items 为 (key, 本地产物路径, argv, env[, cwd])
列表。产物保留文件名（各放一个子目录，程序可按名字认出自己）；trees 为 {名字: 本地目录}，整棵带过去，cwd 写树的名字时
产物在该树里运行——与本地在用例目录里运行相同（夹具按相对路径找到）。
命令行：python scripts/macos_remote.py [--host <ssh 主机>] <产物> [实参...]（缺省取环境变量 TAIXU_MACOS_HOST）   运行一个产物并原样转出输出与退出码。
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

REMOTE_PYTHON = '~/miniforge3/bin/python3'
LOCAL = 'local'

# 远端执行器：读清单，逐个运行（chmod +x 已在打包时设好），退出码与输出写成一行 JSON。
RUNNER = r'''
import base64, json, os, subprocess, sys
root = os.path.dirname(os.path.abspath(__file__))
items = json.load(open(os.path.join(root, 'manifest.json'), encoding='utf-8'))
for it in items:
    exe = os.path.join(root, it['file'])
    env = dict(os.environ)
    env.update(it.get('env', {}))
    cwd = os.path.join(root, 'trees', it['cwd']) if it.get('cwd') else root
    try:
        p = subprocess.run([exe] + it['argv'], cwd=cwd, capture_output=True, timeout=it['timeout'], env=env)
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        code, out, err = -1, b'', b'TIMEOUT'
    print(json.dumps({'key': it['key'], 'exit': code, 'stdout': base64.b64encode(out).decode(),
                      'stderr': base64.b64encode(err).decode()}), flush=True)
'''


def ssh(host, command, stdin=None, timeout=3600):
    argv = ['bash', '-c', command] if host == LOCAL else ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, command]
    p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _plain(info):
    """Tree members keep their modes but lose the local owner."""
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    return info


def run_batch(host, items, timeout=120, trees=None):
    """items: [(key, path, argv, env[, cwd])]. Returns {key: (exit, stdout, stderr)}; missing keys mean the batch broke."""
    buf = io.BytesIO()
    manifest = []
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for tree, local in (trees or {}).items():
            tar.add(str(local), arcname='trees/' + tree, filter=_plain)
        for n, item in enumerate(items):
            key, path, argv, env = item[:4]
            cwd = item[4] if len(item) > 4 else None
            name = 'p%04d/%s' % (n, Path(path).name)
            info = tar.gettarinfo(str(path), arcname=name)
            info.mode = 0o755
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            with open(path, 'rb') as f:
                tar.addfile(info, f)
            manifest.append({'key': key, 'file': name, 'argv': [str(a) for a in argv], 'env': env or {}, 'timeout': timeout,
                             'cwd': cwd})
        for name, text in (('manifest.json', json.dumps(manifest, ensure_ascii=False)), ('runner.py', RUNNER)):
            data = text.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    python = shlex.quote(sys.executable) if host == LOCAL else REMOTE_PYTHON
    script = ('set -e; d=$(mktemp -d /tmp/taixu-remote.XXXXXX); tar -x -C "$d"; '
              + python + ' "$d/runner.py"; cd /; mv "$d" ~/.Trash/ 2>/dev/null || true')
    code, out, err = ssh(host, script, stdin=buf.getvalue(), timeout=timeout * max(1, len(items)) + 600)
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
    host = os.environ.get('TAIXU_MACOS_HOST', '')
    if len(argv) >= 2 and argv[0] == '--host':
        host, argv = argv[1], argv[2:]
    if not argv or not host:
        print(__doc__)
        return 64
    exe = Path(argv[0])
    res = run_batch(host, [('one', exe, argv[1:], {})])
    code, out, err = res['one']
    sys.stdout.buffer.write(out)
    sys.stderr.buffer.write(err)
    return code if 0 <= code < 256 else 125


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
