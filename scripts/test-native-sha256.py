"""Native SHA-256 vs hashlib: published vectors, every padding boundary through 300 bytes,
and binary inputs up to 1 MiB (round 6 step 6d-4a). The probe hashes all files in one process,
so its reusable constants and each invocation's state are checked together. Inputs must stay unchanged.
"""
import argparse
import hashlib
from pathlib import Path
import random
import subprocess
import sys

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    a = ap.parse_args()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    build = subprocess.run([str(a.compiler.resolve()), 'build', 'bootstrap/selfhost/sha256-probe.tx',
                            '--out', str(work / 'probe')], cwd=ROOT, capture_output=True, timeout=180)
    if build.returncode:
        print(build.stderr.decode('utf-8', 'replace'))
        return 1
    vectors = [
        (b'', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
        (b'abc', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'),
        (b'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq',
         '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1'),
        (b'abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu',
         'cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1'),
        (b'a' * 1000000, 'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0'),
    ]
    rng = random.Random(20261020)
    data = [x for x, _ in vectors]
    data += [rng.randbytes(n) for n in range(301)]
    data += [rng.randbytes(n) for n in (511, 512, 513, 65535, 65536, 1048576)]
    paths = []
    expected = [hashlib.sha256(b).hexdigest() for b in data]
    assert expected[:len(vectors)] == [x for _, x in vectors]
    for i, b in enumerate(data):
        p = work / ('input-%03d.bin' % i)
        p.write_bytes(b)
        paths.append(p)
    exe = work / 'probe' / ('sha256-probe.exe' if sys.platform == 'win32' else 'sha256-probe')
    p = subprocess.run([str(exe), *[p.name for p in paths]], cwd=work, capture_output=True, timeout=180)
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    ok = p.returncode == 0 and not p.stderr and got == expected
    print('%s sha256 (%d inputs)' % ('PASS' if ok else 'FAIL', len(data)))
    if not ok:
        print('exit=%d, stderr=%r, lines=%d' % (p.returncode, p.stderr[:500], len(got)))
        print('first differences:', [(i, x, got[i] if i < len(got) else None)
                                     for i, x in enumerate(expected) if i >= len(got) or got[i] != x][:5])
    unchanged = all(path.read_bytes() == b for path, b in zip(paths, data))
    print('%s inputs unchanged' % ('PASS' if unchanged else 'FAIL'))
    print('SUMMARY %s (2 checks)' % ('PASS' if ok and unchanged else 'FAIL'))
    return 0 if ok and unchanged else 1


if __name__ == '__main__':
    sys.exit(main())
