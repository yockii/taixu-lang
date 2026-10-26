"""构建与门的运行目录保留策略（selfhost_validation.fresh_run_dir）：只修剪带标记、已完成或已超时的旧运行；
正在进行的运行、不带标记的目录与目录联接指向的内容都不动。用法：python scripts/test-run-retention.py"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selfhost_validation import fresh_run_dir, finish_run_dir, RUN_MARK  # noqa: E402

root = Path(tempfile.mkdtemp(prefix='taixu-retention-')).resolve()
base = root / 'base'
outside = root / 'outside'
outside.mkdir(parents=True)
(outside / 'keep.txt').write_text('must survive')
(base / 'unrelated-gate-like').mkdir(parents=True)          # 不带标记：不动
(base / 'gate-unmarked').mkdir()                            # 前缀相同但不带标记：不动
fails = []


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        fails.append(name)


runs = []
for i in range(5):
    d = fresh_run_dir(base, 'gate-', 3)
    t = time.time() - (100 - i)                               # 依次变新
    os.utime(d / RUN_MARK, (t, t))
    if i == 0:
        if os.name == 'nt':
            from _winapi import CreateJunction
            CreateJunction(str(outside), str(d / 'junction'))
        else:
            (d / 'junction').symlink_to(outside, target_is_directory=True)
    if i == 1:
        pass                                                  # 第 2 次不完成：模拟进行中
    else:
        finish_run_dir(d)
    runs.append(d)
# 第 6 次触发修剪：最近 3 次为 runs[3]、runs[4] 与本次；runs[1] 未完成且不足一天，保留；runs[0]、runs[2] 删除。
new = fresh_run_dir(base, 'gate-', 3)
check('newest kept', new.is_dir() and runs[4].is_dir() and runs[3].is_dir())
check('finished old pruned', not runs[0].exists() and not runs[2].exists())
check('in-progress old kept', runs[1].is_dir())
check('junction target intact', (outside / 'keep.txt').read_text() == 'must survive')
check('unmarked untouched', (base / 'unrelated-gate-like').is_dir() and (base / 'gate-unmarked').is_dir())
check('no trash left', not list(base.glob('.trash-*')))
# 进行中的那次超过一天：视为崩溃，下一次修剪删除。
t = time.time() - 2 * 86400
os.utime(runs[1] / RUN_MARK, (t, t))
fresh_run_dir(base, 'gate-', 3)
check('stale in-progress pruned', not runs[1].exists())
# keep=0 不修剪。
before = sorted(p.name for p in base.iterdir())
fresh_run_dir(base, 'gate-', 0)
check('keep=0 prunes nothing', set(before) <= {p.name for p in base.iterdir()})
print('SUMMARY', 'PASS' if not fails else 'FAIL %d' % len(fails))
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if fails else 0)
