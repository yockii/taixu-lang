"""Shared evidence primitives; no compiler algorithms."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent



def require_zig_runnable_tree(gate):
    """Stop gates that run the current compiler or kernel generator on the frozen Zig seed.

    The Zig seed is frozen at D3 v0.17. Once bootstrap/STAGE0 is pinned, the current
    compiler sources use later features, so the seed's VM and `aot --frontend=zig`
    can no longer run or compile them. Such gates run in a checkout of the stage0
    commit; current-tree products come from scripts/nrt-bootstrap.py.
    """
    if (ROOT / 'bootstrap' / 'STAGE0').exists():
        raise SystemExit(gate + ': the frozen Zig seed (D3 v0.17) cannot run the current compiler sources. '
                         'Run this gate in a worktree of the commit in bootstrap/STAGE0, or use the '
                         'products of scripts/nrt-bootstrap.py.')


def require_shim_host(gate):
    """Stop gates that build with the native compiler's removed --shim Zig host path.

    M7 removed `--shim` (and `--object`) from the self-hosted compiler: native products
    always link the NRT kernel. Gates measuring the old Zig-host products run in a
    checkout of the commit in bootstrap/STAGE0, whose compiler still has that path.
    """
    if (ROOT / 'bootstrap' / 'STAGE0').exists():
        raise SystemExit(gate + ': the current compiler has no --shim Zig host path (removed in M7). '
                         'Run this gate in a checkout of the commit in bootstrap/STAGE0.')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pe_imports(path):
    """Read PE32+ import DLL names for evidence, never change a product."""
    data = Path(path).read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Not PE')
    count = struct.unpack_from('<H', data, pe + 6)[0]
    opt_size = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    if struct.unpack_from('<H', data, opt)[0] != 0x20b:
        raise ValueError('Expected PE32+')
    sections = opt + opt_size
    def offset(rva):
        for i in range(count):
            size, address, raw_size, raw = struct.unpack_from('<IIII', data, sections + 40 * i + 8)
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError('RVA outside sections')
    rva, size = struct.unpack_from('<II', data, opt + 120)
    if not rva:
        return []
    imports = []
    for i in range(size // 20):
        desc = struct.unpack_from('<IIIII', data, offset(rva + i * 20))
        if not any(desc):
            return imports
        start = offset(desc[3])
        imports.append(data[start:data.index(b'\0', start)].decode('ascii'))
    raise ValueError('Unterminated import descriptors')


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_')}
    env.update(TAIXU_EMIT_MIRROR='0', TAIXU_REGALLOC_MIRROR='0',
               TAIXU_MIRROR_NO_CACHE='1', TAIXU_RC_SCALAR='0')
    return env


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n',
                       encoding='utf-8', newline='\n')
    pending.replace(path)


def run(cmd, cwd, env, timeout=900):
    start = time.perf_counter_ns()
    result = subprocess.run([str(p) for p in cmd], cwd=cwd, env=env,
                            capture_output=True, timeout=timeout)
    duration = time.perf_counter_ns() - start
    return {'argv': [str(p) for p in cmd], 'cwd': str(cwd), 'ns': duration,
            'exit': result.returncode, 'env': {k: v for k, v in env.items()
                                              if k.upper().startswith('TAIXU_')},
            'stdout': result.stdout.decode('utf-8', 'replace'),
            'stderr': result.stderr.decode('utf-8', 'replace')}


RUN_MARK = '.taixu-run'
DONE_MARK = '.taixu-run-done'
STALE_SECONDS = 24 * 3600


def remove_tree(path):
    """先改名再删：删到一半失败时，留下的是 base 下以 .trash- 开头的目录，下次修剪会再删，不会变成失去标记、再也
    清不掉的运行目录。Windows 的只读文件去掉只读再删；目录联接只删联接本身（Python 3.8 起 rmtree 不跟随联接）。"""
    import shutil
    import stat
    path = Path(path)
    trash = path.with_name('.trash-' + path.name)
    try:
        path.replace(trash)
    except OSError:
        trash = path

    def force(func, target, _):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(trash, onexc=force)
        else:
            shutil.rmtree(trash, onerror=force)
        return True
    except OSError as e:
        print('WARN 未能删除', trash, e, flush=True)
        return False


def fresh_run_dir(base, prefix, keep):
    """在 base 下新建本次运行的目录（prefix 开头、带标记），并修剪同一工具更早的运行：只留最近 keep 次（含本次；0 为
    全部保留）。只删带标记、且已完成或一天没有完成（视为中途崩溃）的目录，正在进行的运行不受影响；base 里别的东西
    一律不动。同 pytest 的 tmp_path_retention_count（缺省 3）。"""
    import tempfile
    base = Path(base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    for leftover in base.glob('.trash-' + prefix + '*'):
        remove_tree(leftover)
    current = Path(tempfile.mkdtemp(prefix=prefix, dir=base)).resolve()
    (current / RUN_MARK).write_text(json.dumps({'pid': os.getpid(), 'started': time.time()}) + '\n', encoding='utf-8')
    if keep > 0:
        runs = sorted((p for p in base.iterdir() if p.is_dir() and p.name.startswith(prefix) and (p / RUN_MARK).is_file()),
                      key=lambda p: (p / RUN_MARK).stat().st_mtime, reverse=True)
        now = time.time()
        for old in runs[keep:]:
            if (old / DONE_MARK).is_file() or now - (old / RUN_MARK).stat().st_mtime > STALE_SECONDS:
                remove_tree(old)
    return current


def finish_run_dir(path):
    """本次运行结束（无论通过与否），之后可被修剪。"""
    (Path(path) / DONE_MARK).write_text('', encoding='utf-8')


def require_success(row):
    if row['exit'] != 0:
        raise RuntimeError(json.dumps(row, ensure_ascii=True))
