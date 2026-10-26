"""固定 seed 帧头变异与分片差分。只在已核验的无网络、无宿主挂载 Docker 容器内运行探针。

先由当前候选构建 tests/tool-security/frame-probe.tx --toolchain --target=linux，
把探针复制到专用容器的 exec tmpfs；本脚本不复制任何宿主环境、源码或真实数据。
"""
import argparse
import json
import queue
import random
import subprocess
import threading

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

HEAD_MAX = 65536
SEED = 20261023


def reference(data):
    out = []
    while data:
        pos, length = 0, None
        while True:
            lf = data.find(b'\n', pos)
            if lf < 0 or lf + 1 > HEAD_MAX:
                return 3, out + ['ERROR BadFrame']
            line = data[pos:lf]
            pos = lf + 1
            if line.endswith(b'\r'):
                line = line[:-1]
            if b'\r' in line:
                return 3, out + ['ERROR BadFrame']
            if not line:
                if length is None:
                    return 3, out + ['ERROR BadFrame']
                break
            key, colon, value = line.partition(b':')
            if colon and key.strip(b' \t').lower() == b'content-length':
                value = value.strip(b' \t')
                if length is not None or not value or len(value) > 18 or any(c < 48 or c > 57 for c in value):
                    return 3, out + ['ERROR BadFrame']
                length = int(value)
                if length > 128:
                    return 3, out + ['ERROR MessageTooLong']
        if len(data) - pos < length:
            return 3, out + ['ERROR BadFrame']
        out.append('BODY ' + (data[pos:pos + length] or b' ').hex())
        data = data[pos + length:]
    return 0, out


def exercise(command, data, pieces):
    p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    q = queue.Queue()

    def read():
        for line in p.stdout:
            q.put(line.decode('ascii').strip())
        q.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    out = []
    ended = False
    try:
        for part in pieces:
            try:
                p.stdin.write(part)
                p.stdin.flush()
            except BrokenPipeError:
                break
            while True:
                line = q.get(timeout=10)
                if line is None or line == 'READ':
                    break
                out.append(line)
            if line is None:
                ended = True
                break
        try:
            p.stdin.close()
        except BrokenPipeError:
            pass
        while not ended:
            line = q.get(timeout=10)
            if line is None:
                break
            if line != 'READ':
                out.append(line)
        code = p.wait(timeout=5)
        err = p.stderr.read(4096)
        assert not err, err
        return code, out
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)
        reader.join(timeout=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--container', required=True)
    ap.add_argument('--probe', default='/work/frame-probe')
    ap.add_argument('--report', required=True)
    a = ap.parse_args()
    info = json.loads(subprocess.check_output(['docker', 'inspect', a.container]))[0]
    host = info['HostConfig']
    assert host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] and not info['Mounts']
    assert info['Config']['User'] == '65534:65534' and host['Memory'] > 0 and host['PidsLimit'] > 0
    assert 'ALL' in host['CapDrop'] and any(x.startswith('no-new-privileges') for x in host['SecurityOpt'])
    assert a.probe.startswith('/work/') and '..' not in a.probe.split('/')
    rng = random.Random(SEED)
    base = b'Content-Length: 2\r\n\r\n{}'
    cases = [b'', base, base * 3, b'Content-Length: 0\r\n\r\n', base[:-1],
             b'X: a\r\n' * 11000 + base, b'X: ' + b'x' * 70000 + b'\r\n' + base]
    for size in (65535, 65536, 65537):
        cases.append(b'X: ' + b'x' * (size - (len(base) - 2) - 5) + b'\r\n' + base)
    for _ in range(80):
        body = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 140)))
        head = b'Content-Length: %d\r\n' % len(body)
        if rng.randrange(4) == 0:
            head += b'CONTENT-LENGTH: %d\r\n' % len(body)
        data = b'X: a\n' * rng.randrange(10) + head + b'\r\n' + body
        if rng.randrange(2) == 0:
            data = bytearray(data)
            at = rng.randrange(len(data))
            data[at] = rng.choice([0, 10, 13, 43, 45, 58, 255])
            data = bytes(data)
        cases.append(data)
    rows = []
    for i, data in enumerate(cases):
        want = reference(data)
        splits = [[data], [data[j:j + 257] for j in range(0, len(data), 257)]]
        if len(data) < 256:
            splits.append([data[j:j + 1] for j in range(len(data))])
        else:
            splits.append([data[:65535], data[65535:65536], data[65536:]])
        for variant, pieces in enumerate(splits):
            got = exercise(['docker', 'exec', '-i', a.container, a.probe, 'ack'], data, [p for p in pieces if p])
            assert got == want, (i, variant, len(data), want, got)
            rows.append({'case': i, 'fragmentation': variant, 'bytes': len(data), 'status': 'PASS'})
    from pathlib import Path
    Path(a.report).write_text(json.dumps({'seed': SEED, 'status': 'PASS', 'cases': len(cases), 'runs': len(rows), 'rows': rows}, indent=2) + '\n', encoding='utf-8', newline='\n')
    print('PASS frame fragments seed=%d cases=%d runs=%d' % (SEED, len(cases), len(rows)), flush=True)


if __name__ == '__main__':
    main()
