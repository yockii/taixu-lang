"""A64 编码表双向核对（macOS 适配 M4 设计 A2、A11）：拿 tests/a64/encodings.txt（独立汇编器生成，见
scripts/a64-encodings.py）逐条核对——bootstrap/a64_encode_probe.tx 核编码函数（文本 → 字节，负例须被拒绝），
bootstrap/a64_disasm_probe.tx 核反汇编器（字节 → 文本；另有固定例，子集外的字须得 .inst；再拿随机字段经编码函数
编出的字，须都译得出）。"""
import argparse
import os
from pathlib import Path
import subprocess

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parents[1]


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
    run([a.compiler.resolve(), 'build', 'bootstrap/a64_encode_probe.tx', '--toolchain', '--out', work / 'probe'])
    exe = work / 'probe' / ('a64_encode_probe' + ('.exe' if os.name == 'nt' else ''))
    table = ROOT / 'tests' / 'a64' / 'encodings.txt'
    p = run([exe, table])
    words = p.stdout.decode().split()
    assert len(words) == 3 and words[0] == 'PASS_A64_ENCODINGS', p.stdout
    positive, negative = int(words[1]), int(words[2])
    lines = [l for l in table.read_text(encoding='utf-8').splitlines() if l and not l.startswith('#')]
    assert positive == sum(1 for l in lines if not l.startswith('!')) and negative == sum(1 for l in lines if l.startswith('!'))
    assert positive >= 7000 and negative >= 80, (positive, negative)
    print('PASS A64 encodings %d, rejected %d' % (positive, negative), flush=True)

    run([a.compiler.resolve(), 'build', 'bootstrap/a64_disasm_probe.tx', '--toolchain', '--out', work / 'disasm'])
    exe = work / 'disasm' / ('a64_disasm_probe' + ('.exe' if os.name == 'nt' else ''))
    p = run([exe, table])
    words = p.stdout.decode().split()
    assert len(words) == 3 and words[0] == 'PASS_A64_DISASM', (p.stdout, p.stderr[-4000:])
    assert int(words[1]) == positive and int(words[2]) >= 20, words
    # 编码表之外：随机字段经编码函数编出的字都须反汇编得出（两边子集一致）。
    p = run([exe, '--fuzz', '1', '280000'])
    fz = p.stdout.decode().split()
    assert len(fz) == 3 and fz[0] == 'FUZZ_A64_DISASM' and int(fz[1]) >= 200000 and fz[2] == '0', (p.stdout, p.stderr[-4000:])
    print('PASS A64 disasm %d, fixed %d, fuzz %d' % (int(words[1]), int(words[2]), int(fz[1])), flush=True)


if __name__ == '__main__':
    main()
