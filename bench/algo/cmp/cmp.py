# cmp.py —— bench/algo 九负载 CPython 移植（V0.19-S1；D50 §一）。
# 用法：python cmp.py <load>；stdout 必须与 .tx 校验和逐字节一致。
# 注：json 用标准库（惯用面）；separators=(",", ":") 紧凑对齐长度口径
#（D50 §一-4）；dict 保插入序＝taixu 键序同构。
import json
import sys


def fib_rec(n):
    if n < 2:
        return n
    return fib_rec(n - 1) + fib_rec(n - 2)


def load_fib_rec():
    print(fib_rec(27))


def load_fib_iter():
    a, b = 0, 1
    for _ in range(87):
        a, b = b, a + b
    print(a)


def load_qsort():
    n = 50000

    def lcg(state):
        return (state * 1103515245 + 12345) % 2147483648

    xs = []
    state = 42
    for _ in range(n):
        state = lcg(state)
        xs.append(state % 1000000)

    def partition(lo, hi):
        pivot = xs[hi]
        i = lo
        for j in range(lo, hi):
            if xs[j] < pivot:
                xs[i], xs[j] = xs[j], xs[i]
                i += 1
        xs[i], xs[hi] = xs[hi], xs[i]
        return i

    def qsort(lo, hi):
        if lo < hi:
            p = partition(lo, hi)
            qsort(lo, p - 1)
            qsort(p + 1, hi)

    qsort(0, n - 1)
    checksum = 0
    for i in range(n):
        checksum = checksum + (i * xs[i]) % 1000003
    print(checksum % 1000003)


def load_sieve():
    n = 500000
    comp = [False] * n
    p = 2
    while p * p < n:
        if not comp[p]:
            for m in range(p * p, n, p):
                comp[m] = True
        p += 1
    print(sum(1 for i in range(2, n) if not comp[i]))


def load_mandelbrot():
    size = 300
    total = 0
    for py in range(size):
        ci = py * 2.5 / size - 1.25
        for px in range(size):
            cr = px * 3.0 / size - 2.0
            zr = zi = 0.0
            it = 0
            while it < 255:
                t = zr * zr - zi * zi + cr
                zi = 2.0 * zr * zi + ci
                zr = t
                if zr * zr + zi * zi > 4.0:
                    break
                it += 1
            total += it
    print(total)


def load_matmul():
    n = 192
    a = [float(i % 13) * 0.5 for i in range(n * n)]
    b = [float(i % 7) * 0.25 + 0.5 for i in range(n * n)]
    c = [0.0] * (n * n)
    for r in range(n):
        for k in range(n):
            av = a[r * n + k]
            for cc in range(n):
                c[r * n + cc] = c[r * n + cc] + av * b[k * n + cc]
    diag = 0.0
    for i in range(n):
        diag += c[i * n + i]
    print(int(diag * 1000000.0))


def load_nbody():
    dt = 0.01
    bs = [
        [0.0, 0.0, 0.0, 0.5, 0.2, 0.1],
        [1.0, 0.5, 0.0, -0.3, 0.4, 0.2],
        [-0.5, 1.0, 0.5, 0.2, -0.5, 0.3],
        [2.0, -1.0, 1.0, 0.1, 0.1, -0.2],
        [-1.0, -0.5, -1.0, -0.2, 0.3, 0.0],
    ]
    n = 5

    def sqrt_approx(v):
        g = v
        for _ in range(8):
            g = 0.5 * (g + v / g)
        return g

    for _ in range(200000):
        for i in range(n):
            for j in range(n):
                if i != j:
                    dx = bs[j][0] - bs[i][0]
                    dy = bs[j][1] - bs[i][1]
                    dz = bs[j][2] - bs[i][2]
                    d2 = dx * dx + dy * dy + dz * dz + 0.01
                    inv = 1.0 / (d2 * sqrt_approx(d2))
                    bs[i][3] += dx * inv * dt
                    bs[i][4] += dy * inv * dt
                    bs[i][5] += dz * inv * dt
        for k in range(n):
            bs[k][0] += bs[k][3] * dt
            bs[k][1] += bs[k][4] * dt
            bs[k][2] += bs[k][5] * dt
    kinetic = 0.0
    for k in range(n):
        kinetic += 0.5 * (bs[k][3] ** 2 + bs[k][4] ** 2 + bs[k][5] ** 2)
    print(int(kinetic * 1000000.0))


def load_sbf():
    parts = []
    for i in range(5000):
        parts.append("item-" + str(i % 977) + ";")
    hay = "".join(parts)
    n = len(hay.encode())
    m = len("item-7;")
    hits = 0
    at = 0
    needle = b"item-7;"
    hb = hay.encode()
    while at <= n - m:
        j = 0
        ok = True
        while j < m:
            if hb[at + j] != needle[j]:
                ok = False
                break
            j += 1
        if ok:
            hits += 1
            at += m
        else:
            at += 1
    print((hits * 977 + n) % 1000003)


def load_json():
    checksum = 0
    for round_ in range(40):
        obj = {}
        for i in range(20):
            inner = {}
            for j in range(5):
                inner["i" + str(j)] = round_ * 100 + i * 5 + j
            obj["g" + str(i)] = inner
        text = json.dumps(obj, separators=(",", ":"))
        back = json.loads(text)  # parse 回读真实执行（对称成本）
        _ = back
        checksum += len(text) % 97
    print(checksum % 1000003)


LOADS = {
    "fib-rec": load_fib_rec,
    "fib-iter": load_fib_iter,
    "qsort": load_qsort,
    "sieve": load_sieve,
    "mandelbrot": load_mandelbrot,
    "matmul": load_matmul,
    "nbody": load_nbody,
    "string-build-find": load_sbf,
    "json-roundtrip": load_json,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in LOADS:
        print("usage: python cmp.py <load>", file=sys.stderr)
        sys.exit(2)
    LOADS[sys.argv[1]]()
