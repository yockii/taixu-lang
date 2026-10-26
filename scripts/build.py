#!/usr/bin/env python3
"""用已有原生编译器构建太虚：转交太虚写的构建驱动 scripts/build.tx（docs/design/v052-build-driver-v0.1-20261025.md）；
历史 Zig 起点（--seed／--zig）须显式选择，仍由本脚本经 nrt-bootstrap.py 重放。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
EXE = '.exe' if os.name == 'nt' else ''


def run(argv, cwd=ROOT, **kwargs):
    print('+', ' '.join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), cwd=cwd, check=True,
                          env={**os.environ, 'PYTHONUTF8': '1'}, **kwargs)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_compiler(value):
    candidate = value or os.environ.get('TAIXU_NATIVE') or shutil.which('taixu')
    if not candidate:
        raise ValueError('请给出 --compiler <原生 taixu>；历史起点用 --seed 或 --zig')
    compiler = Path(candidate).resolve()
    p = subprocess.run([compiler, 'capabilities', '--format=json'], capture_output=True, timeout=30, check=True)
    data = json.loads(p.stdout)
    if data.get('capabilities', {}).get('compiler') != 'native-tx':
        raise ValueError('所选编译器不是原生太虚；Zig seed 请经 --seed 显式提供')
    return compiler


def archived_seed(zig, work):
    zig = zig.resolve()
    version = subprocess.check_output([zig, 'version'], text=True).strip()
    if version != '0.16.0':
        raise ValueError('历史 seed 需要 Zig 0.16.0，实际为 ' + version)
    pin = (ROOT / 'bootstrap/ZIG_SEED').read_text(encoding='utf-8').strip()
    run(['git', 'cat-file', '-e', pin + '^{commit}'])
    archive = work / 'seed-source.tar'
    with archive.open('wb') as out:
        run(['git', 'archive', '--format=tar', pin], stdout=out)
    source = work / 'seed-src'
    source.mkdir()
    with tarfile.open(archive) as tf:
        tf.extractall(source, filter='data')
    cache = work / 'seed-cache'
    global_cache = work / 'seed-global-cache'
    prefix = work / 'seed-bin'
    run([zig, 'build', '-Doptimize=ReleaseFast', '--cache-dir', cache,
         '--global-cache-dir', global_cache, '--prefix', prefix], cwd=source)
    seed = prefix / 'bin' / ('taixu' + EXE)
    return seed, {'kind': 'archived-zig', 'commit': pin, 'zig': version, 'sha256': sha(seed)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    start = ap.add_mutually_exclusive_group()
    start.add_argument('--compiler', type=Path, help='已有的原生太虚编译器')
    start.add_argument('--seed', type=Path, help='归档 Zig taixu 二进制，重放完整历史链')
    start.add_argument('--zig', type=Path, help='Zig 0.16.0；从固定历史提交构建 seed 再重放')
    ap.add_argument('--work', type=Path, default=ROOT / 'work/native-build')
    ap.add_argument('--out', type=Path, default=ROOT / 'work/native/bin')
    ap.add_argument('--sync-docs', action='store_true', help='C=D 后用新编译器同步派生文档')
    ap.add_argument('--keep-runs', type=int, default=3, help='--work 里保留最近几次构建的工作目录（0 为全部保留）')
    a = ap.parse_args()
    if not a.zig and not a.seed:
        # 原生路径：等同 taixu run --toolchain scripts/build.tx …（Python 只剩这一层转交）。
        compiler = native_compiler(a.compiler)
        argv = [compiler, 'run', '--toolchain', ROOT / 'scripts/build.tx', '--root', ROOT, '--compiler', compiler,
                '--work', a.work.resolve(), '--out', a.out.resolve(), '--keep-runs', a.keep_runs]
        if a.sync_docs:
            argv.append('--sync-docs')
        return subprocess.run(list(map(str, argv)), cwd=ROOT).returncode
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    # 每次调用在新的子目录工作，已有调用的源码、缓存和产物均不覆盖；只留最近 --keep-runs 次（selfhost_validation）。
    from selfhost_validation import fresh_run_dir, finish_run_dir
    current = fresh_run_dir(work, 'build-', a.keep_runs)
    assert current.parent == work
    if a.zig:
        seed, origin = archived_seed(a.zig, current)
        entry = ['--seed', seed]
    elif a.seed:
        seed = a.seed.resolve()
        entry = ['--seed', seed]
        origin = {'kind': 'zig-seed', 'sha256': sha(seed)}
    else:
        compiler = native_compiler(a.compiler)
        entry = ['--compiler', compiler]
        origin = {'kind': 'native', 'sha256': sha(compiler)}
    bootstrap = current / 'bootstrap'
    run([sys.executable, ROOT / 'scripts/nrt-bootstrap.py', *entry, '--work', bootstrap])
    hashes = {stage: sha(bootstrap / stage / ('taixu' + EXE)) for stage in ('b', 'c', 'd')}
    if hashes['c'] != hashes['d']:
        raise ValueError('自举未达到 C=D：' + json.dumps(hashes))
    if a.sync_docs:
        from sync_toolchain_docs import sync_docs
        changed = sync_docs(bootstrap / 'd' / ('taixu' + EXE))
        print('派生文档：', '已同步' if changed else '已一致', flush=True)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    binary = out / ('taixu' + EXE)
    pending = binary.with_suffix(binary.suffix + '.pending')
    shutil.copy2(bootstrap / 'd' / ('taixu' + EXE), pending)
    pending.replace(binary)
    record = {'compiler': str(binary), 'sha256': hashes['d'], 'stages': hashes,
              'initial_generation_matches_fixpoint': hashes['b'] == hashes['d'],
              'origin': origin, 'platform': platform.platform(), 'work': str(current)}
    (out / 'build.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf-8', newline='\n')
    finish_run_dir(current)
    print('PASS C=D', hashes['d'], 'B=D:', hashes['b'] == hashes['d'], '\n编译器:', binary, flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('构建失败:', error, file=sys.stderr)
        sys.exit(1)
