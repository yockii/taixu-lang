"""Staged NRT bootstrap from the frozen Zig seed (docs/design/v052-bytes-embed-stage0-v0.1-20261013.md §二、§四).

Chain:
  1. stage0 = the commit in bootstrap/STAGE0 (or the current tree when the file is absent);
     only its bootstrap/ (and prelude/, when present) is extracted (git archive), not a full checkout;
  2. the Zig seed's VM runs stage0's kernel generator -> K0 and stage0's compiler -> A0;
  2b. later pinned stages bootstrap/STAGE1, STAGE2, ... (each a commit whose compiler
     sources the previous stage can compile) are built in order: the previous compiler
     builds the stage's kernel generator, whose kernels the stage's compiler embeds, then
     the stage's compiler -> S1, S2, ...; the last one stands in for A0 below. This lets the
     compiler sources use language features and builtins newer than stage0;
  3. A0 builds the current kernel generator, which writes both current kernels
     (Windows COFF, Linux ELF) to bootstrap/nrt-kernel/ (not in Git);
  4. A0 builds the current compiler -> B, which embeds those kernels (embedBytes);
     B -> C -> D use the embedded kernel; C == D is the fixpoint;
  5. D builds the kernel generator again; its kernels must equal the embedded ones.
  When stage0 is the current tree, A0 == C is also required (VM == native).
With --compiler, an existing native compiler stands in for A0 (steps 1-2 skipped); pinned stages
newer than it are still built first (step 2b). Old stages carry no version number, so each stage
has a feature probe (a builtin it introduced that later compiler sources use, as autoconf probes
features): the compiler starts after the newest stage whose probe it compiles. --start-stage K
overrides the probe.
Products target this host (windows on Windows, linux on Linux, macos on macOS). Local scratch only.
"""
import argparse
import io
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selfhost_validation import ROOT, sha  # noqa: E402

KERNEL_FLAGS = ['--with-move', '--with-alloc', '--with-rc', '--with-str', '--with-strb', '--with-zero',
                '--with-list', '--with-file', '--with-argv', '--with-store-slots', '--with-nrt']
# 名字带 -heapcheck 的是 --heap-check 用的检测内核（页堆＋对象号隔离），生成时加 --generic=heapcheck（同 build.tx）。
KERNELS = [('x86_64-windows', 'kernel.obj'), ('x86_64-linux', 'kernel-linux.o'), ('x86_64-macos', 'kernel-macos.o'),
           ('aarch64-macos', 'kernel-macos-aarch64.o'), ('x86_64-windows', 'kernel-heapcheck.obj'),
           ('x86_64-linux', 'kernel-linux-heapcheck.o'), ('x86_64-macos', 'kernel-macos-heapcheck.o'),
           ('aarch64-macos', 'kernel-macos-aarch64-heapcheck.o')]


def run(argv, cwd):
    print('+', ' '.join(str(a) for a in argv), flush=True)
    p = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, timeout=3600)
    if p.returncode != 0:
        sys.stdout.write(p.stdout.decode('utf-8', 'replace')[-2000:])
        sys.stdout.write(p.stderr.decode('utf-8', 'replace')[-2000:])
        raise RuntimeError('exit %d: %s' % (p.returncode, argv[0]))


def extract_stage0(commit, dest):
    """Write bootstrap/ (and prelude/ when the commit has it) of `commit` under dest."""
    paths = ['bootstrap']
    if subprocess.run(['git', 'cat-file', '-e', commit + ':prelude'], cwd=ROOT, capture_output=True).returncode == 0:
        paths.append('prelude')
    p = subprocess.run(['git', 'archive', '--format=tar', commit, *paths], cwd=ROOT, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError('git archive failed: ' + p.stderr.decode('utf-8', 'replace'))
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as archive:
        archive.extractall(dest, filter='data')


def kernels_of(tree):
    """The kernels a source tree embeds (its bootstrap/taixu.tx names them); older pinned stages predate macOS."""
    text = (tree / 'bootstrap' / 'taixu.tx').read_text(encoding='utf-8')
    return [(target, name) for target, name in KERNELS if 'nrt-kernel/' + name in text]


def generate_kernels(generator, out_dir, kernels):
    out_dir.mkdir(parents=True, exist_ok=True)
    for target, name in kernels:
        run([generator, '--target', target, out_dir / name, *KERNEL_FLAGS, *(['--generic=heapcheck'] if '-heapcheck' in name else [])], ROOT)
    return {name: sha(out_dir / name) for _, name in kernels}


# Per pinned stage: a program using what the stage introduced (compiled with --toolchain).
STAGE_PROBES = {
    1: 'export function main(args: []string): int { print(hostTarget() + toStr(monoNs())); return 0; }\n',
    2: 'export function main(args: []string): int { const b = bytesNew(1); return stdinRead(b, 0); }\n',
    3: 'export function main(args: []string): int { print(currentDir() catch ""); return 0; }\n',
    4: 'export function main(args: []string): int { return fileSize("x") catch 0; }\n',
    5: 'export function main(args: []string): int {\n    const n = localFileSize(".", "x") catch 0;\n'
       '    return procTryWrite(0, 0, bytesNew(0)) + n;\n}\n',
    6: 'export function main(args: []string): int {\n    return listLen(localListDir(".", "") catch (e) { return 0; });\n}\n',
    # 7 引入的是语法：外部函数签名里的 string 与 C 变参参数（只声明不调用，不检查目标、不链接）。
    7: 'declare function snprintf(buf: Bytes, size: usize, format: string, ...value: int): i32 from "libc.so.6";\n'
       'export function main(args: []string): int { return 0; }\n',
}


def pinned_stages():
    k, out = 1, []
    while (ROOT / 'bootstrap' / ('STAGE%d' % k)).exists():
        out.append((k, (ROOT / 'bootstrap' / ('STAGE%d' % k)).read_text(encoding='utf-8').strip()))
        k += 1
    return out


def first_needed_stage(compiler, link, tflag, work):
    """The first pinned stage `compiler` cannot stand in for (the one after the newest probe it compiles)."""
    for k, _ in reversed(pinned_stages()):
        if k not in STAGE_PROBES:
            raise RuntimeError('bootstrap/STAGE%d has no feature probe in nrt-bootstrap.py' % k)
        probe = work / ('probe-%d' % k)
        probe.mkdir()
        (probe / 'probe.tx').write_text(STAGE_PROBES[k], encoding='utf-8', newline='\n')
        p = subprocess.run([str(a) for a in [compiler, 'build', 'probe.tx', '--toolchain', *tflag, *link,
                                             '--out', probe / 'out']], cwd=probe, capture_output=True, timeout=600)
        shutil.rmtree(probe)
        if p.returncode == 0:
            return k + 1
    return 1


def remove_stage(source, work):
    source = source.resolve()
    if source == work or not source.is_relative_to(work):
        raise ValueError('Refuse stage removal outside this bootstrap work directory')
    shutil.rmtree(source)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seed', type=Path, help='frozen Zig taixu (ReleaseFast recommended)')
    ap.add_argument('--compiler', type=Path, help='existing native compiler used in place of A0')
    ap.add_argument('--link-object', type=Path, help='kernel object for --compiler when it predates embedded kernels')
    ap.add_argument('--start-stage', type=int, help='with --compiler: first pinned stage to build (default: probed)')
    ap.add_argument('--work', type=Path, required=True)
    args = ap.parse_args()
    if (args.seed is None) == (args.compiler is None):
        ap.error('give exactly one of --seed and --compiler')
    windows = os.name == 'nt'
    exe = '.exe' if windows else ''
    target = 'x86_64-windows' if windows else 'x86_64-linux'
    tflag = [] if windows else ['--target=linux']
    kname = 'kernel.obj' if windows else 'kernel-linux.o'
    if sys.platform == 'darwin':  # macOS 适配 M3 设计 G11：测试机上自举，产物为本机的 Mach-O。
        target, tflag, kname = 'x86_64-macos', ['--target=macos'], 'kernel-macos.o'
        if platform.machine() == 'arm64':  # M4f：arm64 宿主（Rosetta 下的 Python 报 x86_64，照 x86_64 自举）。
            target, tflag, kname = 'aarch64-macos', ['--target=aarch64-macos'], 'kernel-macos-aarch64.o'
    work = args.work.resolve()
    if work.exists():
        ap.error('use a fresh --work directory; existing work is preserved')
    work.mkdir(parents=True)
    commit = None
    stages = []
    if args.start_stage is not None and args.compiler is None:
        ap.error('--start-stage needs --compiler')
    start = 1
    if args.compiler is not None:
        a0 = args.compiler.resolve()
        a0_link = ['--link-object', args.link_object.resolve()] if args.link_object else []
        start = args.start_stage if args.start_stage is not None else first_needed_stage(a0, a0_link, tflag, work)
        print('compiler', a0, 'starts at pinned stage', start, flush=True)
    else:
        seed = args.seed.resolve()
        pin = ROOT / 'bootstrap' / 'STAGE0'
        stage0 = ROOT
        if pin.exists():
            commit = pin.read_text(encoding='utf-8').strip()
            stage0 = work / 'stage0-src'
            extract_stage0(commit, stage0)
        k0 = work / ('k0-' + kname)
        run([seed, 'run', '--frontend=zig', 'bootstrap/runtime_memory_gen.tx', '--target', target, k0, *KERNEL_FLAGS], stage0)
        run([seed, 'run', '--toolchain', '--frontend=zig', 'bootstrap/taixu.tx', 'build', 'bootstrap/taixu.tx',
             '--toolchain', '--runtime=native', *tflag, '--link-object', k0, '--out', work / 'a0'], stage0)
        if commit is not None:
            remove_stage(stage0, work)
        a0 = work / 'a0' / ('taixu' + exe)
        a0_link = ['--link-object', k0]
    # Later pinned stages, each built by the previous compiler (step 2b).
    for k, pin_k in pinned_stages():
        if k < start:
            continue
        name = 's%d' % k
        src = work / (name + '-src')
        extract_stage0(pin_k, src)
        run([a0, 'build', 'bootstrap/runtime_memory_gen.tx', '--runtime=native', *tflag, *a0_link,
             '--out', work / (name + '-gen')], src)
        generate_kernels(work / (name + '-gen') / ('runtime_memory_gen' + exe), src / 'bootstrap' / 'nrt-kernel', kernels_of(src))
        run([a0, 'build', 'bootstrap/taixu.tx', '--toolchain', '--runtime=native', *tflag, *a0_link,
             '--out', work / name], src)
        remove_stage(src, work)
        stages.append((name, pin_k))
        a0 = work / name / ('taixu' + exe)
        a0_link = []
    # Current kernels, generated by the previous compiler, then embedded.
    run([a0, 'build', 'bootstrap/runtime_memory_gen.tx', '--runtime=native', *tflag, *a0_link,
         '--out', work / 'gen'], ROOT)
    current = kernels_of(ROOT)
    embedded = generate_kernels(work / 'gen' / ('runtime_memory_gen' + exe), ROOT / 'bootstrap' / 'nrt-kernel', current)
    for _, name in current:
        shutil.copyfile(ROOT / 'bootstrap' / 'nrt-kernel' / name, work / name)
    # B is produced by the previous compiler and may run on its older kernel.
    # It embeds the current kernels, which B then uses for C. Compare C with D;
    # forcing B onto a newer runtime ABI is neither necessary nor generally safe.
    run([a0, 'build', 'bootstrap/taixu.tx', '--toolchain', '--runtime=native', *tflag, *a0_link,
         '--out', work / 'b'], ROOT)
    prev = work / 'b' / ('taixu' + exe)
    for stage in ['c', 'd']:
        run([prev, 'build', 'bootstrap/taixu.tx', '--toolchain', *tflag, '--out', work / stage], ROOT)
        prev = work / stage / ('taixu' + exe)
    # The embedded kernels must be what the current generator produces.
    run([prev, 'build', 'bootstrap/runtime_memory_gen.tx', *tflag, '--out', work / 'gen-d'], ROOT)
    again = generate_kernels(work / 'gen-d' / ('runtime_memory_gen' + exe), work / 'kernels-d', current)
    names = [*[n for n, _ in stages], 'b', 'c', 'd'] if args.compiler is not None else ['a0', *[n for n, _ in stages], 'b', 'c', 'd']
    hashes = {name: sha(work / name / ('taixu' + exe)) for name in names}
    print('stage0', commit or ('compiler ' + str(args.compiler.resolve()) if args.compiler is not None else 'current tree'))
    for name, pin_k in stages:
        print('stage' + name[1:], pin_k)
    for _, name in current:
        print('kernel', name, embedded[name])
    for name, value in hashes.items():
        print(name, value)
    if hashes['c'] != hashes['d']:
        raise RuntimeError('No fixpoint: C != D')
    if again != embedded:
        raise RuntimeError('Kernels from the D-built generator differ from the embedded kernels')
    if args.compiler is None and commit is None and hashes['a0'] != hashes['c']:
        raise RuntimeError('VM-built A0 differs from the native fixpoint')
    print('PASS staged bootstrap; compiler', work / 'd' / ('taixu' + exe))
    return 0


if __name__ == '__main__':
    sys.exit(main())
