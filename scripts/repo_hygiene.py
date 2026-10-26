#!/usr/bin/env python3
"""原生仓库的行尾、行数、导入边界与 Zig 清退检查。"""
import argparse
from collections import Counter
import fnmatch
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
IGNORED = {'.git', '.zig-cache', '.tools', 'zig-out', 'zig-out-rel', 'work', '.tmp', '__pycache__', 'node_modules', 'dist'}


def source_files(root):
    try:
        p = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=root, capture_output=True, check=True)
        if Path(os.fsdecode(p.stdout.strip())).resolve() == root:
            p = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                               cwd=root, capture_output=True, check=True)
            return sorted({root / os.fsdecode(name) for name in p.stdout.split(b'\0') if name and (root / os.fsdecode(name)).is_file()})
    except (OSError, subprocess.SubprocessError):
        pass
    files = []
    for current, dirs, names in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = [name for name in dirs if name not in IGNORED and not (folder / name).is_symlink()
                   and not getattr(folder / name, 'is_junction', lambda: False)()]
        files.extend(folder / name for name in names)
    # .gitignore deliberately keeps these bench sources while ignoring SDKs,
    # generated host objects and other .tools payloads. Preserve that source
    # surface even when Git is absent from PATH.
    bench = root / '.tools/bench'
    if bench.is_dir() and bench.resolve().is_relative_to(root) and not bench.is_symlink() and not getattr(bench,'is_junction',lambda:False)():
        for current, dirs, names in os.walk(bench,followlinks=False):
            folder=Path(current)
            dirs[:]=[name for name in dirs if name not in IGNORED and not (folder/name).is_symlink()
                     and not getattr(folder/name,'is_junction',lambda:False)()]
            files.extend(folder/name for name in names if Path(name).suffix=='.s' or
                         (folder==bench and Path(name).suffix in {'.py','.sh','.ps1','.mjs'}))
    return sorted(files)


def binary_patterns(root):
    patterns = []
    for line in (root / '.gitattributes').read_text(encoding='utf-8').splitlines():
        fields = line.split()
        if fields and not fields[0].startswith('#') and ('-text' in fields[1:] or 'binary' in fields[1:]):
            patterns.append(fields[0])
    return patterns


def check_eol(root, files):
    patterns = binary_patterns(root)
    count, bad = 0, []
    for path in files:
        name = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatchcase(name, p) or ('/' not in p and fnmatch.fnmatchcase(path.name, p)) for p in patterns):
            continue
        data = path.read_bytes()
        if b'\0' in data:  # text=auto：含 NUL 的文件为二进制。
            continue
        count += 1
        if b'\r' in data:
            bad.append(name)
    for name in bad:
        print('FAIL CR', name)
    print('eol:', count, 'text files,', len(bad), 'with CR')
    return not bad


def linecount(root, files):
    rows = []
    for path in files:
        if path.suffix == '.tx' and path.is_relative_to(root / 'bootstrap'):
            data = path.read_bytes()
            rows.append((data.count(b'\n') + int(bool(data) and not data.endswith(b'\n')), path.relative_to(root).as_posix()))
    rows.sort(reverse=True)
    print('linecount: report-only; new .tx files soft limit 1000')
    for count, name in rows[:10]:
        print(count, name)
    print('linecount:', len(rows), 'files,', sum(n > 1000 for n, _ in rows), 'over soft limit')


def imports(text):
    # 保留字符串、剥离注释；只读顶层 import 声明，不把注释中的示例当作依赖。
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[1] if m[1] is not None else re.sub(r'[^\n]', ' ', m[0]), text)
    return re.findall(r'^\s*import\s+[^;]*?\sfrom\s+"([^"\n]+)"\s*;', text, re.MULTILINE)


# 按目标选文件（D3 v0.35，同 bootstrap/selfhost/target.tx 的 targetFileSuffixes／targetFileSuffixesAll）：产品目标各自的
# 后缀链，与同组兄弟文件的全部后缀。macOS 目前只用于分析，不要求编译器源码对它可解析。
TARGET_CHAINS = {'windows': ['windows_x86_64', 'windows'], 'linux': ['linux_x86_64', 'linux', 'unix']}
ALL_SUFFIXES = ['windows', 'linux', 'macos', 'unix'] + [o + '_' + a for o in ['windows', 'linux', 'macos'] for a in ['x86_64', 'aarch64']]


def check_edges(root, files):
    # 根也取真实路径：节点与导入目标都经 resolve，根含符号链接时（macOS 的临时目录在 /var 下，/var 链到 /private/var）
    # 未解析的根与它们比不上。
    root = root.resolve()
    boot = root / 'bootstrap'
    nodes = {path.resolve() for path in files if path.suffix == '.tx' and path.resolve().is_relative_to(boot)}
    graph, bad, matrix = {}, [], Counter()
    basic = {'lexer.tx', 'parser.tx', 'semamirror.tx', 'runtime_memory_gen.tx'}
    commands = {'taixu.tx', 'driver.tx', 'lsp.tx', 'dap.tx', 'mcp.tx', 'init.tx', 'ana_dev.tx'}
    for path in sorted(nodes):
        deps = []
        for spec in imports(path.read_text(encoding='utf-8')):
            if not spec.startswith(('./', '../')):
                bad.append((path, 'compiler import must be relative: ' + spec))
                continue
            target = path.parent / (spec if spec.endswith('.tx') else spec + '.tx')
            target = target.resolve()
            variant = lambda sfx: target.with_name(target.stem + '_' + sfx + '.tx')
            targets = [p for p in [target, *map(variant, ALL_SUFFIXES)] if p in nodes]
            # 与原生目标选择一致：每个产品目标沿自己的后缀链都要解析得到；依赖取同组全部现存文件。
            if not targets or not all(any(p in nodes for p in [*map(variant, chain), target]) for chain in TARGET_CHAINS.values()):
                bad.append((path, 'missing or outside compiler source: ' + spec))
                continue
            for target in targets:
                if path.name in basic and target.name in commands:
                    bad.append((path, 'foundation imports command/orchestrator: ' + spec))
                if not path.stem.endswith('-probe') and target.stem.endswith('-probe'):
                    bad.append((path, 'production imports development probe: ' + spec))
                deps.append(target)
                matrix[(path.parent.relative_to(boot).as_posix(), target.parent.relative_to(boot).as_posix())] += 1
        graph[path] = deps
    seen, active = set(), set()

    def visit(path):
        if path in active:
            bad.append((path, 'import cycle'))
            return
        if path in seen:
            return
        active.add(path)
        for target in graph[path]:
            visit(target)
        active.remove(path)
        seen.add(path)
    for path in graph:
        visit(path)
    for path, reason in bad:
        print('FAIL edge', path.relative_to(root), reason)
    print('edges:', len(nodes), 'modules,', sum(map(len, graph.values())), 'imports,', len(bad), 'violations')
    for (source, target), count in sorted(matrix.items()):
        print('matrix:', source, '->', target, count)
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--only', choices=['eol', 'linecount', 'edges', 'no-zig', 'all'], default='all')
    a = ap.parse_args()
    root = a.root.resolve()
    files = source_files(root)
    ok = True
    if a.only in ('all', 'eol'):
        ok = check_eol(root, files) and ok
    if a.only in ('all', 'linecount'):
        linecount(root, files)
    if a.only in ('all', 'edges'):
        ok = check_edges(root, files) and ok
    if a.only in ('all', 'no-zig'):
        zig = [p.relative_to(root).as_posix() for p in files if p.suffix.lower() == '.zig']
        for path in zig:
            print('FAIL Zig source', path)
        print('no-zig:', len(zig), 'Zig source files')
        ok = not zig and ok
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
