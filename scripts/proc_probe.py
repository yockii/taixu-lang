"""门脚本用的进程探查（类 Unix 宿主）：Linux 读 /proc，macOS 经 ps／pgrep（macOS 适配 M3 设计 G11）。

记录为 (pid, 启动标记)：启动标记取不到即视为已不在；同号进程启动标记不同即为 PID 复用，不算原进程。僵尸仍占着 PID，
启动标记照样取得到——“已回收”以 PID 消失为准，不能把终止当作回收。
"""
import os
from pathlib import Path
import subprocess
import sys

LINUX = sys.platform.startswith('linux')


def _ps(field, pid):
    p = subprocess.run(['ps', '-o', field + '=', '-p', str(pid)], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ''


def children(pid):
    """pid 的直接子进程。"""
    if LINUX:
        return [int(x) for x in Path('/proc/%d/task/%d/children' % (pid, pid)).read_text().split()]
    p = subprocess.run(['pgrep', '-P', str(pid)], capture_output=True, text=True)
    return [int(x) for x in p.stdout.split()]


def argv(pid):
    """命令行（macOS 的 ps 以空白拼接，含空白的实参会被拆开；门里只用来认可执行文件与标志）。"""
    if LINUX:
        return [os.fsdecode(x) for x in Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0') if x]
    return _ps('args', pid).split()


def start(pid):
    """启动标记；进程已不在时为空串。"""
    if LINUX:
        try:
            return Path('/proc/%d/stat' % pid).read_text().rsplit(') ', 1)[1].split()[19]
        except (FileNotFoundError, ProcessLookupError, IndexError):
            return ''
    return _ps('lstart', pid)


def records(pids):
    return [(pid, s) for pid, s in ((pid, start(pid)) for pid in pids) if s]


def descendants(parent, limit=64):
    out, pending = [], [parent]
    while pending and len(out) < limit:
        for pid in children(pending.pop()):
            s = start(pid)
            if s:
                out.append((pid, s))
                pending.append(pid)
    return out


def gone(recs):
    """记录里的进程都已不在（PID 消失，或同号已是别的进程）。"""
    return all(start(pid) != s for pid, s in recs)
