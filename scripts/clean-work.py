"""清理构建与验证的遗留（同 cargo clean、go clean -cache，但缺省只报告，加 --apply 才删）。

类别（--only 选其中几类，缺省全部）：
  work     work/ 下的条目；--keep 指定的除外，work/native（build.py 缺省输出的当前编译器）恒保留
  zig      Zig 时代的缓存与输出：.zig-cache、zig-out、zig-out-rel（Zig 现在只用于 build.py --zig 重建历史
           seed，其缓存在 work/ 的运行目录里）
  tmp      .tmp/ 下不含 git 跟踪文件的条目（.tmp 里有跟踪文件，不能整个删）
  systemp  系统临时目录里一天前的太虚测试遗留（taixu-*、nrt-perf-*、v05x-*）
含 git 跟踪文件的路径一律不删；目录联接与符号链接只删链接本身。构建与门自身只留最近几次运行
（--keep-runs，见 selfhost_validation.fresh_run_dir），本工具清理此前积下的和别处的遗留。

用法：python scripts/clean-work.py [--repo <仓库根>] [--only work,zig] [--keep work/native-x ...] [--apply]
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selfhost_validation import ROOT, remove_tree  # noqa: E402

CATEGORIES = ('work', 'zig', 'tmp', 'systemp')
SYSTEMP = re.compile(r'^(taixu-|nrt-perf-|v05\d)')
DAY = 86400


def size_of(path):
    """字节数与文件数；不跟随联接与符号链接。"""
    if path.is_symlink() or not path.is_dir():
        try:
            return path.lstat().st_size, 1
        except OSError:
            return 0, 0
    total, files, stack = 0, 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False) and not os.path.isjunction(e.path):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total, files


def tracked_files(repo):
    p = subprocess.run(['git', 'ls-files', '-z'], cwd=repo, capture_output=True)
    if p.returncode != 0:
        raise SystemExit('git ls-files 失败：' + p.stderr.decode('utf-8', 'replace'))
    return [x for x in p.stdout.decode('utf-8').split('\0') if x]


def candidates(repo, only, keep, tracked):
    rel = lambda p: p.relative_to(repo).as_posix()

    def has_tracked(p):
        r = rel(p)
        return any(t == r or t.startswith(r + '/') for t in tracked)
    out = []
    if 'work' in only and (repo / 'work').is_dir():
        protected = {(repo / 'work/native').resolve(), *keep}
        for p in sorted((repo / 'work').iterdir()):
            if p.resolve() in protected or any(k.is_relative_to(p.resolve()) for k in protected):
                continue
            if not has_tracked(p):
                out.append(('work', p))
    if 'zig' in only:
        for name in ('.zig-cache', 'zig-out', 'zig-out-rel'):
            p = repo / name
            if p.exists() and not has_tracked(p):
                out.append(('zig', p))
    if 'tmp' in only and (repo / '.tmp').is_dir():
        for p in sorted((repo / '.tmp').iterdir()):
            if not has_tracked(p):
                out.append(('tmp', p))
    if 'systemp' in only:
        now = time.time()
        for p in sorted(Path(tempfile.gettempdir()).iterdir()):
            try:
                if SYSTEMP.match(p.name) and now - p.lstat().st_mtime > DAY:
                    out.append(('systemp', p))
            except OSError:
                pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--repo', type=Path, default=ROOT, help='仓库根（缺省本仓；兄弟仓如 ../taixu-gui 也可）')
    ap.add_argument('--only', default=','.join(CATEGORIES), help='逗号分隔：' + ','.join(CATEGORIES))
    ap.add_argument('--keep', type=Path, action='append', default=[], help='保留的路径（相对仓库根），可多次')
    ap.add_argument('--apply', action='store_true', help='真的删除（缺省只报告）')
    a = ap.parse_args()
    repo = a.repo.resolve()
    only = {x for x in a.only.split(',') if x}
    if only - set(CATEGORIES):
        ap.error('未知类别：' + ','.join(sorted(only - set(CATEGORIES))))
    keep = [(repo / k).resolve() for k in a.keep]
    rows = [(cat, p, *size_of(p)) for cat, p in candidates(repo, only, keep, tracked_files(repo))]
    for cat in CATEGORIES:
        sel = [r for r in rows if r[0] == cat]
        if sel:
            print('%-8s %8.2f GiB  %6d 项' % (cat, sum(r[2] for r in sel) / 2**30, len(sel)))
            for _, p, size, files in sorted(sel, key=lambda r: -r[2])[:8]:
                print('           %8.2f GiB  %7d 文件  %s' % (size / 2**30, files, p))
    total = sum(r[2] for r in rows)
    if not a.apply:
        print('合计 %.2f GiB；只是报告，加 --apply 才删除' % (total / 2**30))
        return 0
    freed, failed = 0, 0
    for _, p, size, _ in rows:
        try:
            if p.is_symlink() or os.path.isjunction(p) or not p.is_dir():
                p.unlink() if not p.is_dir() or p.is_symlink() else os.rmdir(p)
                ok = True
            else:
                ok = remove_tree(p)
        except OSError as e:
            print('WARN 未能删除', p, e)
            ok = False
        freed += size if ok else 0
        failed += 0 if ok else 1
    print('已删除 %.2f GiB；失败 %d 项' % (freed / 2**30, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
