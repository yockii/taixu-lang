"""A64 写出链路（macOS 适配 M4a，设计 A2、A3）：编出 bootstrap/a64_image_probe.tx，由它手写两个 aarch64 目标文件并链接成
Mach-O arm64。任何宿主上都核对文件头与 load command、两次生成逐字节相同；在 Apple Silicon 宿主上另外运行它，核对输出
（经 __got 调 libSystem、跨度定长后的长跳转、跨对象 bl、.data 里的绝对地址）。"""
import argparse
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parents[1]
LC_MAIN, LC_CODE_SIGNATURE, LC_BUILD_VERSION, LC_SEGMENT_64 = 0x80000028, 0x1d, 0x32, 0x19
EXPECTED = b'a64 image: write ok\na64 image: relax ok\na64 image: call/data ok\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    a = ap.parse_args()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)

    def run(argv):
        p = subprocess.run(list(map(str, argv)), cwd=ROOT, capture_output=True, timeout=600)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr[-4000:])
        return p
    run([a.compiler.resolve(), 'build', 'bootstrap/a64_image_probe.tx', '--toolchain', '--out', work / 'probe'])
    exe = work / 'probe' / ('a64_image_probe' + ('.exe' if os.name == 'nt' else ''))
    images = []
    for name in ('a64_image', 'a64_image_again'):
        p = run([exe, work / name])
        assert p.stdout.split()[0] == b'PASS_A64_IMAGE', p.stdout
        images.append((work / name).read_bytes())
    b = images[0]
    assert b == images[1], 'nondeterministic image'
    magic, cputype, subtype, filetype, ncmds, sizeofcmds, flags = struct.unpack_from('<IIIIIII', b, 0)
    assert (magic, cputype, subtype, filetype) == (0xfeedfacf, 0x0100000c, 0, 2), (hex(magic), hex(cputype), subtype, filetype)
    assert flags & 0x200000, 'MH_PIE'
    cmds = []
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', b, at)
        cmds.append((cmd, at))
        at += size
    kinds = [c for c, _ in cmds]
    assert LC_MAIN in kinds and LC_CODE_SIGNATURE in kinds and LC_BUILD_VERSION in kinds, kinds
    segs = [b[o + 8:o + 24].rstrip(b'\0') for c, o in cmds if c == LC_SEGMENT_64]
    assert segs[:2] == [b'__PAGEZERO', b'__TEXT'], segs
    print('PASS A64 Mach-O header, load commands, deterministic (%d bytes)' % len(b), flush=True)
    # rtfn 的 A64 底层（M4b）：探针写出两个映像，一个逐项核对 rtfn 原语，一个除零须被 brk 终止。
    run([a.compiler.resolve(), 'build', 'bootstrap/a64_rtfn_probe.tx', '--toolchain', '--out', work / 'rtfn-probe'])
    rexe = work / 'rtfn-probe' / ('a64_rtfn_probe' + ('.exe' if os.name == 'nt' else ''))
    outs = []
    for name in ('rtfn', 'rtfn-again'):
        (work / name).mkdir(exist_ok=True)
        p = run([rexe, work / name])
        assert p.stdout.strip() == b'PASS_A64_RTFN', p.stdout
        outs.append([(work / name / f).read_bytes() for f in ('rtfn_ok', 'rtfn_divzero')])
    assert outs[0] == outs[1], 'nondeterministic rtfn images'
    for img in outs[0]:
        assert struct.unpack_from('<II', img, 0) == (0xfeedfacf, 0x0100000c)
    print('PASS A64 rtfn images built, deterministic', flush=True)
    # 生成代码的指令宏 gen_ops 的 A64 落地（M4c，设计 A9 修订）：探针逐项核对宏的各个族。
    run([a.compiler.resolve(), 'build', 'bootstrap/a64_gen_probe.tx', '--toolchain', '--out', work / 'gen-probe'])
    gexe = work / 'gen-probe' / ('a64_gen_probe' + ('.exe' if os.name == 'nt' else ''))
    gens = []
    for name in ('gen', 'gen-again'):
        (work / name).mkdir(exist_ok=True)
        p = run([gexe, work / name])
        assert p.stdout.strip() == b'PASS_A64_GEN', p.stdout
        gens.append((work / name / 'gen_ok').read_bytes())
    assert gens[0] == gens[1], 'nondeterministic gen image'
    assert struct.unpack_from('<II', gens[0], 0) == (0xfeedfacf, 0x0100000c)
    print('PASS A64 gen image built, deterministic', flush=True)
    # libSystem 蹦床的 A64 版（M4c，设计 A5）：探针经蹦床建文件、查权限位、设取描述符标志（变参经栈）、核对 −errno。
    run([a.compiler.resolve(), 'build', 'bootstrap/a64_sys_probe.tx', '--toolchain', '--out', work / 'sys-probe'])
    sexe = work / 'sys-probe' / ('a64_sys_probe' + ('.exe' if os.name == 'nt' else ''))
    syss = []
    for name in ('sys', 'sys-again'):
        (work / name).mkdir(exist_ok=True)
        p = run([sexe, work / name])
        assert p.stdout.strip() == b'PASS_A64_SYS', p.stdout
        syss.append((work / name / 'sys_ok').read_bytes())
    assert syss[0] == syss[1], 'nondeterministic sys image'
    assert struct.unpack_from('<II', syss[0], 0) == (0xfeedfacf, 0x0100000c)
    print('PASS A64 sys image built, deterministic', flush=True)
    if sys.platform == 'darwin' and platform.machine() == 'arm64':
        image = work / 'a64_image'
        os.chmod(image, 0o755)
        p = subprocess.run([image], capture_output=True, timeout=60)
        assert p.returncode == 0 and p.stdout == EXPECTED, (p.returncode, p.stdout, p.stderr)
        print('PASS A64 image runs natively', flush=True)
        for f in ('rtfn_ok', 'rtfn_divzero'):
            os.chmod(work / 'rtfn' / f, 0o755)
        p = subprocess.run([work / 'rtfn' / 'rtfn_ok'], capture_output=True, timeout=60)
        assert p.returncode == 0 and p.stdout == b'a64 rtfn ok\n', ('rtfn check failed (exit = index of the failing check)', p.returncode, p.stdout)
        p = subprocess.run([work / 'rtfn' / 'rtfn_divzero'], capture_output=True, timeout=60)
        assert p.returncode == -5, ('division by zero must stop with SIGTRAP', p.returncode)
        print('PASS A64 rtfn primitives and division trap run natively', flush=True)
        os.chmod(work / 'gen' / 'gen_ok', 0o755)
        p = subprocess.run([work / 'gen' / 'gen_ok'], capture_output=True, timeout=60)
        assert p.returncode == 0 and p.stdout == b'a64 gen ok\n', ('gen check failed (exit = index of the failing check)', p.returncode, p.stdout)
        print('PASS A64 gen_ops checks run natively', flush=True)
        # 探针在当前目录建删两个文件：每次在新的空目录里跑。
        os.chmod(work / 'sys' / 'sys_ok', 0o755)
        cwd = tempfile.mkdtemp(prefix='sys-run-', dir=work)
        p = subprocess.run([work / 'sys' / 'sys_ok'], cwd=cwd, capture_output=True, timeout=60)
        assert p.returncode == 0 and p.stdout == b'a64 sys ok\n', ('sys check failed (exit = index of the failing check)', p.returncode, p.stdout)
        print('PASS A64 libSystem trampolines run natively', flush=True)
    else:
        print('NOT-RUN A64 image execution (needs an Apple Silicon host)', flush=True)


if __name__ == '__main__':
    main()
