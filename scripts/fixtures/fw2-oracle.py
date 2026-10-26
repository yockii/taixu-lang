"""生成 tests/nrt-cases/fw2-arith、fw2-f32、fw2-inline、fw2-list 的 .tx/.out（O4 第二步，定宽数值第二步实施设计 §三-4、§四-4、§七-4、§七-5）。

期望值由本脚本独立计算：整数按数学定义（Python 大整数，截断除法、按宽度环绕）、
f32 运算用 numpy.float32（原生单精度），toStr(f32) 用 numpy 的最短往返（Dragon4，位置记法）。
用法：python scripts/fixtures/fw2-oracle.py tests/nrt-cases（需要 numpy；只在修改用例时运行，各门不依赖它）
"""
import random
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1])

TYPES = {  # 名字: (宽度, 有符号)
    'i8': (8, True), 'i16': (16, True), 'i32': (32, True),
    'u8': (8, False), 'u16': (16, False), 'u32': (32, False),
    'u64': (64, False), 'isize': (64, True), 'usize': (64, False),
}


def rng(t):
    w, s = TYPES[t]
    if s:
        return -(1 << (w - 1)), (1 << (w - 1)) - 1
    return 0, (1 << w) - 1


def wrap(v, t):
    w, s = TYPES[t]
    v &= (1 << w) - 1
    if s and v >= 1 << (w - 1):
        v -= 1 << w
    return v


def ok(v, t):
    lo, hi = rng(t)
    assert lo <= v <= hi, (v, t)
    return v


def tdiv(a, b):
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def lit(v, t):
    """太虚里表示 t 类型值 v 的表达式（isize 最小值没有字面量写法，用 wrapTo）。"""
    lo, _ = rng(t)
    if t == 'isize' and v == lo:
        return 'wrapTo<isize>(-9223372036854775807 - 1)'
    return str(v)


def gen_arith():
    tx = ['// O4 第二步：定宽整数的算术、位运算与移位、环绕运算、字面量对称取型与 u64 大字面量。',
          '// 期望值由 scripts/fixtures/fw2-oracle.py 按数学定义独立计算（Python 大整数），不由被测实现生成。',
          '',
          'export function main(args: []string): int {']
    out = []
    n = [0]

    def emit(label, expr, value):
        tx.append(f'    print("{label} " + toStr({expr}));')
        out.append(f'{label} {value}')

    def var(t, v):
        n[0] += 1
        name = f'v{n[0]}'
        tx.append(f'    const {name}: {t} = {lit(v, t)};')
        return name

    for t, (w, s) in TYPES.items():
        lo, hi = rng(t)
        # 加减乘：贴边但不溢出
        a, b = var(t, hi - 5), var(t, 5)
        emit(f'{t}.add', f'{a} + {b}', ok(hi - 5 + 5, t))
        a2, b2 = var(t, lo + 5), var(t, 5)
        emit(f'{t}.sub', f'{a2} - {b2}', ok(lo, t))
        m = 1 << (w // 2 - 1) if s else (1 << (w // 2)) - 1
        a3 = var(t, m)
        emit(f'{t}.mul', f'{a3} * {a3}', ok(m * m, t))
        if s:
            a4 = var(t, -m)
            emit(f'{t}.mulneg', f'{a4} * {a3}', ok(-m * m, t))
        # 除与取余（含负数截断、最小值 % −1）
        pairs = [(hi, 7), (hi, 1)]
        if s:
            pairs += [(lo, 7), (lo, -7), (-hi, 3), (lo, 2)]
        for (x, y) in pairs:
            xa, ya = var(t, x), var(t, y)
            emit(f'{t}.div({x},{y})', f'{xa} / {ya}', ok(tdiv(x, y), t))
            emit(f'{t}.mod({x},{y})', f'{xa} % {ya}', ok(x - y * tdiv(x, y), t))
        if s:
            xa, ya = var(t, lo), var(t, -1)
            emit(f'{t}.mod(min,-1)', f'{xa} % {ya}', 0)
            # 取负
            xn = var(t, lo + 1)
            emit(f'{t}.neg', f'-{xn}', ok(-(lo + 1), t))
        # 位运算
        xb = var(t, wrap(0x5A5A5A5A5A5A5A5A, t))
        yb = var(t, wrap(0x0FF00FF00FF00FF0, t))
        xv, yv = wrap(0x5A5A5A5A5A5A5A5A, t), wrap(0x0FF00FF00FF00FF0, t)
        emit(f'{t}.and', f'{xb} & {yb}', wrap(xv & yv, t))
        emit(f'{t}.or', f'{xb} | {yb}', wrap(xv | yv, t))
        emit(f'{t}.xor', f'{xb} ^ {yb}', wrap(xv ^ yv, t))
        emit(f'{t}.not', f'~{xb}', wrap(~xv, t))
        xh = var(t, hi)
        emit(f'{t}.nothi', f'~{xh}', wrap(~hi, t))
        # 移位：计数可为任一整数类型；<< 截到宽度，>> 有符号算术、无符号逻辑，>>> 宽度内逻辑右移
        for v in sorted({hi, lo, wrap(-3, t), 1}):
            xv2 = var(t, v)
            for c in (0, 1, w - 1):
                cn = var('u8', c)
                emit(f'{t}.shl({v},{c})', f'{xv2} << {cn}', wrap(v << c, t))
                emit(f'{t}.shr({v},{c})', f'{xv2} >> {c}', v >> c)
                emit(f'{t}.ushr({v},{c})', f'{xv2} >>> {c}', wrap((v & ((1 << w) - 1)) >> c, t))
        # 环绕运算
        xa, xb2 = var(t, hi), var(t, lo)
        emit(f'{t}.wadd', f'wrapAdd({xa}, {xa})', wrap(hi + hi, t))
        emit(f'{t}.wsub', f'wrapSub({xb2}, {xa})', wrap(lo - hi, t))
        emit(f'{t}.wmul', f'wrapMul({xa}, {xa})', wrap(hi * hi, t))
        emit(f'{t}.wsub0', f'wrapSub<{t}>(0, 1)', wrap(-1, t))
        # 字面量对称取型：字面量在左时取右侧的类型
        y1 = var(t, 7)
        emit(f'{t}.lit', f'100 - {y1}', ok(100 - 7, t))
        emit(f'{t}.lit2', f'3 * {y1} + 1', ok(22, t))
    # 混合运算按公共类型；int 的环绕
    tx.append('    const ma: i8 = -5;')
    tx.append('    const mb: u8 = 200;')
    tx.append('    const mc: u32 = 4000000000;')
    tx.append('    const md: i32 = -2000000000;')
    emit('mix.i8+u8', 'ma + mb', 195)
    emit('mix.i8&u8', 'ma & mb', (-5) & 200)
    emit('mix.u32+i32', 'mc + md', 2000000000)
    emit('mix.u32*i32', 'mc * md', 4000000000 * -2000000000)
    emit('int.wadd', 'wrapAdd(9223372036854775807, 1)', -(1 << 63))
    emit('int.wsub', 'wrapSub(-9223372036854775807, 2)', (1 << 63) - 1)
    emit('int.wmul', 'wrapMul(4294967296, 4294967297)', wrap(4294967296 * 4294967297, 'isize'))
    # u64 大字面量（十进制、十六进制、八进制、二进制）与 u64 的无符号除法、比较
    tx.append('    const ua: u64 = 18446744073709551615;')
    tx.append('    const ub: u64 = 0xFFFF_FFFF_0000_0001;')
    tx.append('    const uc: usize = 0o1777777777777777777777;')
    tx.append('    const ud: u64 = 0b1000000000000000000000000000000000000000000000000000000000000000;')
    u = (1 << 64) - 1
    ubv = 0xFFFFFFFF00000001
    udv = 1 << 63
    emit('u64.max', 'ua', u)
    emit('u64.hex', 'ub', ubv)
    emit('usize.oct', 'uc', u)
    emit('u64.bin', 'ud', udv)
    emit('u64.div', 'ua / ub', u // ubv)
    emit('u64.mod', 'ua % ub', u % ubv)
    emit('u64.div10', 'ud / 10', udv // 10)
    emit('u64.mod10', 'ud % 10', udv % 10)
    emit('u64.lt', 'ub < ua', 'true')
    emit('u64.gt', 'ud > 5', 'true')
    emit('u64.sub', 'ua - ub', u - ubv)
    tx.append('    return 0;')
    tx.append('}')
    (OUT / 'fw2-arith.tx').write_text('\n'.join(tx) + '\n', encoding='utf-8', newline='\n')
    (OUT / 'fw2-arith.out').write_text('\n'.join(str(x) for x in out) + '\n', encoding='utf-8', newline='\n')
    print('fw2-arith', len(out), 'lines')


def f32s(v):
    return np.format_float_positional(np.float32(v), trim='-')


def f32_nearest(q):
    """有理数 q 的最近 f32（相等距离取尾数为偶者），用相邻 f32 精确比较。"""
    c = np.float32(float(q))
    with np.errstate(over='ignore'):
        cands = {c, np.nextafter(c, np.float32(np.inf)), np.nextafter(c, np.float32(-np.inf))}
    best = None
    for x in cands:
        if not np.isfinite(x):
            continue
        d = abs(Fraction(float(x)) - q)
        even = (int(np.float32(x).view(np.uint32)) & 1) == 0
        key = (d, 0 if even else 1)
        if best is None or key < best[0]:
            best = (key, x)
    return best[1]


def gen_f32():
    vals = [
        ('0.1', np.float32(0.1)), ('0.2', np.float32(0.2)), ('1.0', np.float32(1.0)),
        ('16777216.0', np.float32(16777216.0)), ('3.0e38', np.float32(3.0e38)),
        ('0.33333334', np.float32(0.33333334)), ('-2.5', np.float32(-2.5)),
        ('1.0e-40', np.float32(1.0e-40)), ('1.17549435e-38', np.float32(1.17549435e-38)),
        ('123456.789', np.float32(123456.789)), ('7.0', np.float32(7.0)),
    ]
    tx = ['// O4 第二步：f32 运算逐步舍入到 f32（与原生单精度一致）与 toStr(f32) 的最短往返表示。',
          '// 期望值由 scripts/fixtures/fw2-oracle.py 用 numpy.float32 独立计算（运算用原生单精度，格式化用 Dragon4 最短往返）。',
          '',
          'export function main(args: []string): int {']
    out = []
    names = []
    for i, (txt, v) in enumerate(vals):
        name = f'f{i}'
        names.append((name, v))
        tx.append(f'    const {name}: f32 = {txt};')
        tx.append(f'    print("lit {txt} " + toStr({name}));')
        out.append(f'lit {txt} {f32s(v)}')
    ops = [('+', np.add), ('-', np.subtract), ('*', np.multiply), ('/', np.divide)]
    with np.errstate(all='ignore'):
        for i in range(len(names)):
            for j in range(len(names)):
                if (i + j) % 3 != 0:
                    continue
                an, av = names[i]
                bn, bv = names[j]
                for sym, fn in ops:
                    r = fn(np.float32(av), np.float32(bv), dtype=np.float32)
                    tx.append(f'    print("{an}{sym}{bn} " + toStr({an} {sym} {bn}));')
                    out.append(f'{an}{sym}{bn} {f32s(r)}')
        # 逐步舍入：(a + b) - a 在 f32 里与在 float 里不同
        a, b = np.float32(16777216.0), np.float32(1.0)
        r = np.subtract(np.add(a, b, dtype=np.float32), a, dtype=np.float32)
        tx.append('    print("step " + toStr((f3 + f2) - f3));')
        out.append(f'step {f32s(r)}')
        # 字面量取另一侧的类型（f32），取负，混合 float 时得 float
        r = np.multiply(np.float32(0.1), np.float32(3.0), dtype=np.float32)
        tx.append('    print("lit3 " + toStr(f0 * 3.0) + " " + toStr(3 * f0));')
        out.append(f'lit3 {f32s(r)} {f32s(r)}')
        tx.append('    print("neg " + toStr(-f6) + " " + toStr(-f0));')
        out.append(f'neg {f32s(-np.float32(-2.5))} {f32s(-np.float32(0.1))}')
        tx.append('    const wide: float = 0.1;')
        tx.append('    print("mixed " + toStr(f0 + wide));')
        out.append(f'mixed {repr(float(np.float32(0.1)) + 0.1)}')
        # 溢出得 inf、0/0 得 nan、负零
        tx.append('    const zero: f32 = 0.0;')
        tx.append('    print("inf " + toStr(f4 * 10.0) + " " + toStr(-f4 * 10.0) + " " + toStr(zero / zero) + " " + toStr(-zero));')
        out.append('inf inf -inf -nan -0')  # 0/0 得 x86 缺省 NaN（符号位置位），toStr 同 float 写作 -nan
        # 最大值、最小正规值、最小次正规值、相邻值
        tx.append('    const big: f32 = 3.4028234663852886e38;')
        tx.append('    const tiny: f32 = 1.401298464324817e-45;')
        tx.append('    const near: f32 = 1.0000001;')
        tx.append('    print("edge " + toStr(big) + " " + toStr(tiny) + " " + toStr(near) + " " + toStr(f8));')
        out.append(f'edge {f32s(np.float32(3.4028234663852886e38))} {f32s(np.float32(1.401298464324817e-45))} {f32s(np.float32(1.0000001))} {f32s(np.float32(1.17549435e-38))}')
        # 字面量直接舍入到最近的 f32（不经 f64 两次舍入）：恰比 1 与下一个 f32 的中点大一点点、恰为中点、
        # 整数字面量 2^24+1 与 2^60+2^36+1（经 f64 会先丢掉末位）。期望值按有理数精确取最近的 f32。
        lits = ['1.0000000596046447753906251', '1.000000059604644775390625', '0.0000000000000000000000000000000000000000000007006492321624085354618647916449580656401309709382578858785341419448955413429303', '-3.4028235677973366e38']
        for k, txt in enumerate(lits[:3]):
            tx.append(f'    const dr{k}: f32 = {txt};')
            tx.append(f'    print("dr{k} " + toStr(dr{k}));')
            out.append(f'dr{k} {f32s(f32_nearest(Fraction(Decimal(txt))))}')
        tx.append('    const ir0: f32 = 16777217;')
        tx.append('    const ir1: f32 = 1152921573326323713;')
        tx.append('    const ir2: f32 = -16777219;')
        tx.append('    print("ir " + toStr(ir0) + " " + toStr(ir1) + " " + toStr(ir2));')
        out.append(f'ir {f32s(f32_nearest(Fraction(16777217)))} {f32s(f32_nearest(Fraction(1152921573326323713)))} {f32s(f32_nearest(Fraction(-16777219)))}')
    tx.append('    return 0;')
    tx.append('}')
    (OUT / 'fw2-f32.tx').write_text('\n'.join(tx) + '\n', encoding='utf-8', newline='\n')
    (OUT / 'fw2-f32.out').write_text('\n'.join(out) + '\n', encoding='utf-8', newline='\n')
    print('fw2-f32', len(out), 'lines')


def gen_inline():
    """2b（§七-4）：运算在函数参数、列表元素与循环上进行——静态标签未知时内联快路径先比较标签；
    isize 的 / −1 与 % −1 由冷路径完成（不 trap 的那些）；环绕运算在循环里累积（FNV 与 LCG）。"""
    rnd = random.Random(20261017)
    tx = ['// O4 第二步 2b：定宽运算的内联快路径（设计 §七-4）——操作数来自函数参数、列表元素与循环，',
          '// 以及只在冷路径上完成、不 trap 的运算（isize 的 / -1 与 % -1）。',
          '// 期望值由 scripts/fixtures/fw2-oracle.py 按数学定义与 numpy.float32 独立计算，不由被测实现生成。',
          '']
    out = []
    calls = []

    def pick(t):
        w, s = TYPES[t]
        lo, hi = rng(t)
        for _ in range(100000):
            def one():
                k = rnd.randint(1, w)
                v = rnd.randint(0, (1 << k) - 1)
                if s and rnd.random() < 0.5:
                    v = -v
                return max(lo, min(hi, v))
            a, b = one(), one()
            if b == 0 or (s and a == lo):
                continue
            if not all(lo <= v <= hi for v in (a + b, a - b, a * b, tdiv(a, b))):
                continue
            return a, b
        raise SystemExit('pick ' + t)

    for t, (w, s) in TYPES.items():
        fn = 'ops_' + t
        tx.append(f'function {fn}(a: {t}, b: {t}, n: int): void {{')
        tx.append(f'    print("{t} " + toStr(a) + " " + toStr(b) + " | " + toStr(a + b) + " " + toStr(a - b) + " " + toStr(a * b) + " " + toStr(a / b) + " " + toStr(a % b));')
        tx.append('    print("  bit " + toStr(a & b) + " " + toStr(a | b) + " " + toStr(a ^ b) + " " + toStr(~a) + " " + toStr(a << n) + " " + toStr(a >> n) + " " + toStr(a >>> n));')
        neg = ' + " " + toStr(-a)' if s else ''
        tx.append(f'    print("  wrap " + toStr(wrapAdd(a, b)) + " " + toStr(wrapSub(a, b)) + " " + toStr(wrapMul(a, b)){neg});')
        tx.append('}')
        tx.append('')
        mask = (1 << w) - 1
        for _ in range(4):
            a, b = pick(t)
            c = rnd.randint(0, w - 1)
            calls.append(f'    {fn}({lit(a, t)}, {lit(b, t)}, {c});')
            q = tdiv(a, b)
            out.append(f'{t} {a} {b} | {a + b} {a - b} {a * b} {q} {a - b * q}')
            out.append(f'  bit {wrap(a & b, t)} {wrap(a | b, t)} {wrap(a ^ b, t)} {wrap(~a, t)} {wrap(a << c, t)} {a >> c} {wrap((a & mask) >> c, t)}')
            line = f'  wrap {wrap(a + b, t)} {wrap(a - b, t)} {wrap(a * b, t)}'
            if s:
                line += f' {-a}'
            out.append(line)

    # 列表元素与循环：u32 带检查的累加、u64 FNV-1a、i32 LCG、u8/u16 环绕与 isize 累加
    tx += ['function sumU32(xs: List<u32>): u32 {',
           '    let s: u32 = 0;',
           '    let i = 0;',
           '    while (i < listLen(xs)) {',
           '        const x: u32 = listGet(xs, i) catch 0;',
           '        s = s + x * 3;',
           '        i = i + 1;',
           '    }',
           '    return s;',
           '}',
           '',
           'function fnv64(xs: List<u8>): u64 {',
           '    let h: u64 = 14695981039346656037;',
           '    let i = 0;',
           '    while (i < listLen(xs)) {',
           '        const x: u8 = listGet(xs, i) catch 0;',
           '        h = wrapMul(h ^ (x as u64), 1099511628211);',
           '        i = i + 1;',
           '    }',
           '    return h;',
           '}',
           '',
           'function lcg(seed: i32, k: int): i32 {',
           '    let x = seed;',
           '    let i = 0;',
           '    while (i < k) {',
           '        x = wrapAdd(wrapMul(x, 1103515245), 12345);',
           '        i = i + 1;',
           '    }',
           '    return x;',
           '}',
           '',
           'function spin(k: int): void {',
           '    let a: u8 = 0;',
           '    let b: u16 = 1;',
           '    let c: isize = 0;',
           '    let i = 0;',
           '    while (i < k) {',
           '        a = wrapAdd(a, 37);',
           '        b = wrapMul(b, 3);',
           '        c = c + (i as isize) - 7;',
           '        i = i + 1;',
           '    }',
           '    print("spin " + toStr(a) + " " + toStr(b) + " " + toStr(c));',
           '}',
           '',
           'function divIsize(a: isize, b: isize): void {',
           '    print("isize.div " + toStr(a / b) + " " + toStr(a % b));',
           '}',
           '',
           'function modIsize(a: isize, b: isize): void {',
           '    print("isize.mod " + toStr(a % b));',
           '}',
           '',
           'function divU64(a: u64, b: u64): void {',
           '    print("u64.div " + toStr(a / b) + " " + toStr(a % b));',
           '}',
           '',
           'function shifts(x: u64, n: u8, y: i8, m: u16): void {',
           '    print("shift " + toStr(x << n) + " " + toStr(x >> n) + " " + toStr(y >> m) + " " + toStr(y >>> m));',
           '}',
           '',
           'function opsF(a: f32, b: f32): void {',
           '    print("f32 " + toStr(a + b) + " " + toStr(a - b) + " " + toStr(a * b) + " " + toStr(a / b));',
           '}',
           '']
    tx.append('export function main(args: []string): int {')
    tx += calls
    u32s = [rnd.randint(0, 100000) for _ in range(50)]
    tx.append('    const us: List<u32> = listNew();')
    for k, v in enumerate(u32s):
        tx.append(f'    const u{k}: u32 = {v};')
        tx.append(f'    listPush(us, u{k});')
    tx.append('    print("sumU32 " + toStr(sumU32(us)));')
    out.append(f'sumU32 {sum(3 * v for v in u32s)}')
    data = 'taixu 太虚'.encode('utf-8')
    tx.append('    const bs: List<u8> = listNew();')
    for k, v in enumerate(data):
        tx.append(f'    const b{k}: u8 = {v};')
        tx.append(f'    listPush(bs, b{k});')
    h = 14695981039346656037
    for v in data:
        h = ((h ^ v) * 1099511628211) & ((1 << 64) - 1)
    tx.append('    print("fnv64 " + toStr(fnv64(bs)));')
    out.append(f'fnv64 {h}')
    x = 42
    for _ in range(1000):
        x = wrap(x * 1103515245 + 12345, 'i32')
    tx.append('    print("lcg " + toStr(lcg(42, 1000)));')
    out.append(f'lcg {x}')
    a, b, c = 0, 1, 0
    for i in range(100000):
        a, b, c = (a + 37) & 255, (b * 3) & 65535, c + i - 7
    tx.append('    spin(100000);')
    out.append(f'spin {a} {b} {c}')
    # 冷路径上完成、不 trap 的运算
    lo = -(1 << 63)
    for (p, q) in [(7, -1), (-(1 << 63) + 1, -1), (lo, 3), (lo, 1)]:
        tx.append(f'    divIsize({lit(p, "isize")}, {lit(q, "isize")});')
        out.append(f'isize.div {tdiv(p, q)} {p - q * tdiv(p, q)}')
    tx.append(f'    modIsize({lit(lo, "isize")}, -1);')
    out.append('isize.mod 0')
    for (p, q) in [((1 << 64) - 1, 7), (1 << 63, (1 << 63) + 1), (12345678901234567890, 10)]:
        tx.append(f'    divU64({p}, {q});')
        out.append(f'u64.div {p // q} {p % q}')
    for (xv, n, yv, m) in [((1 << 63) + 5, 1, -128, 7), (1, 63, -1, 1), (0xF0F0F0F0F0F0F0F0, 4, 100, 0)]:
        tx.append(f'    shifts({xv}, {n}, {yv}, {m});')
        out.append(f'shift {(xv << n) & ((1 << 64) - 1)} {xv >> n} {yv >> m} {wrap((yv & 255) >> m, "i8")}')
    with np.errstate(all='ignore'):
        for (ta, tb) in [('0.1', '0.2'), ('1.0e30', '1.0e10'), ('3.0e38', '10.0'), ('1.0e-40', '2.0'), ('-7.25', '0.5'), ('1.0', '0.0')]:
            fa = np.float32(f32_nearest(Fraction(Decimal(ta))))
            fb = np.float32(f32_nearest(Fraction(Decimal(tb))))
            tx.append(f'    opsF({ta}, {tb});')
            rs = [np.add(fa, fb, dtype=np.float32), np.subtract(fa, fb, dtype=np.float32),
                  np.multiply(fa, fb, dtype=np.float32), np.divide(fa, fb, dtype=np.float32)]
            out.append('f32 ' + ' '.join(f32s(r) for r in rs))
    tx.append('    return 0;')
    tx.append('}')
    (OUT / 'fw2-inline.tx').write_text('\n'.join(tx) + '\n', encoding='utf-8', newline='\n')
    (OUT / 'fw2-inline.out').write_text('\n'.join(out) + '\n', encoding='utf-8', newline='\n')
    print('fw2-inline', len(out), 'lines')


def gen_list():
    """2c（§三-4）：紧凑列表——List<i8..u32/f32> 按 1/2/4 字节存放。各类型的 listPush/listGet/listSet/listPop/
    listInsert/listRemoveAt、扩容、for-in，泛型实例里新建、嵌套、结构体字段、线程发送与 Shared 借用。"""
    rnd = random.Random(20261018)
    kinds = ['i8', 'i16', 'i32', 'u8', 'u16', 'u32', 'f32']
    tx = ['// O4 第二步 2c：紧凑列表——List<i8..u32/f32> 按元素宽度 1/2/4 字节存放（设计 §三-4），读出扩成 int/float。',
          '// 期望值由 scripts/fixtures/fw2-oracle.py 用 Python 列表模拟、numpy.float32 格式化，不由被测实现生成。',
          '',
          'struct Pix {',
          '    w: int,',
          '    data: List< u16>,',
          '}',
          '',
          'function rev<T>(xs: List< T>): List< T> {',
          '    const out: List< T> = listNew();',
          '    for (x in xs) {',
          '        listInsert(out, 0, x);',
          '    }',
          '    return out;',
          '}',
          '',
          'function sumU32(xs: List< u32>): int {',
          '    let s = 0;',
          '    for (x in xs) {',
          '        s = s + x;',
          '    }',
          '    return s;',
          '}',
          '']
    out = []

    def fmt(t, v):
        return f32s(v) if t == 'f32' else str(v)

    def litv(t, v):
        if t != 'f32':
            return lit(v, t)
        return v[0]

    def val(t, v):
        if t != 'f32':
            return v
        if v[0].startswith('-'):
            return -np.float32(f32_nearest(Fraction(Decimal(v[0][1:]))))
        return np.float32(f32_nearest(Fraction(Decimal(v[0]))))

    f32pool = ['0.1', '-2.5', '3.4028235e38', '1.0e-45', '-0.0', '16777217.0', '1.17549435e-38', '-123456.789', '7.0', '0.33333334']

    def pick(t):
        if t == 'f32':
            return (rnd.choice(f32pool),)
        lo, hi = rng(t)
        c = rnd.random()
        if c < 0.15:
            return lo
        if c < 0.3:
            return hi
        return rnd.randint(lo, hi)

    for t in kinds:
        fn = 'list_' + t
        body = [f'function {fn}(): void {{', f'    const xs: List< {t}> = listNew();']
        model = []
        vals = [pick(t) for _ in range(20)]
        if t != 'f32':
            lo, hi = rng(t)
            vals[0], vals[1] = lo, hi
        for k, v in enumerate(vals):
            body.append(f'    const p{k}: {t} = {litv(t, v)};')
            body.append(f'    listPush(xs, p{k});')
            model.append(val(t, v))

        def dump(tag):
            body.append(f'    let line{tag} = "{t} {tag} " + toStr(listLen(xs));')
            body.append('    for (x in xs) {')
            body.append(f'        line{tag} = line{tag} + " " + toStr(x);')
            body.append('    }')
            body.append(f'    print(line{tag});')
            out.append(f'{t} {tag} {len(model)}' + ''.join(' ' + fmt(t, v) for v in model))
        dump('push')
        a, b2, c = pick(t), pick(t), pick(t)
        body.append(f'    const s0: {t} = {litv(t, a)};')
        body.append(f'    const s1: {t} = {litv(t, b2)};')
        body.append('    listSet(xs, 3, s0);')
        body.append('    listSet(xs, 19, s1);')
        model[3] = val(t, a)
        model[19] = val(t, b2)
        body.append(f'    const zero: {t} = {litv(t, ("0.0",) if t == "f32" else 0)};')
        body.append(f'    print("{t} get " + toStr(listGet(xs, 3) catch zero) + " " + toStr(listGet(xs, 19) catch zero) + " " + toStr(listGet(xs, 20) catch zero) + " " + toStr(listGet(xs, -1) catch zero));')
        z = val(t, ('0.0',) if t == 'f32' else 0)
        out.append(f'{t} get {fmt(t, model[3])} {fmt(t, model[19])} {fmt(t, z)} {fmt(t, z)}')
        body.append(f'    print("{t} pop " + toStr(listPop(xs) catch zero));')
        out.append(f'{t} pop {fmt(t, model.pop())}')
        ins = [pick(t), pick(t), pick(t)]
        body.append(f'    const n0: {t} = {litv(t, ins[0])};')
        body.append(f'    const n1: {t} = {litv(t, ins[1])};')
        body.append(f'    const n2: {t} = {litv(t, ins[2])};')
        body.append('    listInsert(xs, 0, n0);')
        model.insert(0, val(t, ins[0]))
        body.append('    listInsert(xs, 5, n1);')
        model.insert(5, val(t, ins[1]))
        body.append('    listInsert(xs, listLen(xs), n2);')
        model.append(val(t, ins[2]))
        dump('ins')
        body.append('    listRemoveAt(xs, 0);')
        model.pop(0)
        body.append('    listRemoveAt(xs, 7);')
        model.pop(7)
        body.append('    listRemoveAt(xs, listLen(xs) - 1);')
        model.pop()
        dump('rm')
        body.append('}')
        body.append('')
        tx += body

    # 泛型实例里新建的列表也是紧凑的；嵌套；结构体字段；大列表
    tx += ['export function main(args: []string): int {']
    for t in kinds:
        tx.append(f'    list_{t}();')
    tx += ['    const ws: List< u16> = listNew();',
           '    const w0: u16 = 1;',
           '    const w1: u16 = 65535;',
           '    const w2: u16 = 300;',
           '    listPush(ws, w0);',
           '    listPush(ws, w1);',
           '    listPush(ws, w2);',
           '    const rw = rev(ws);',
           '    print("rev " + toStr(listGet(rw, 0) catch w0) + " " + toStr(listGet(rw, 1) catch w0) + " " + toStr(listGet(rw, 2) catch w0) + " " + toStr(listLen(rw)));',
           '    const fs: List< f32> = listNew();',
           '    const g0: f32 = 0.1;',
           '    const g1: f32 = -2.5;',
           '    listPush(fs, g0);',
           '    listPush(fs, g1);',
           '    const rf = rev(fs);',
           '    print("revf " + toStr(listGet(rf, 0) catch g0) + " " + toStr(listGet(rf, 1) catch g0));']
    out.append('rev 300 65535 1 3')
    out.append(f'revf {f32s(np.float32(-2.5))} {f32s(np.float32(f32_nearest(Fraction(Decimal("0.1")))))}')
    tx += ['    const outer: List< List< u8>> = listNew();',
           '    let k = 0;',
           '    while (k < 3) {',
           '        const inner: List< u8> = listNew();',
           '        let j = 0;',
           '        while (j < 10 + k * 7) {',
           '            listPush(inner, ((j * 37 + k) % 256) as u8);',
           '            j = j + 1;',
           '        }',
           '        listPush(outer, inner);',
           '        k = k + 1;',
           '    }',
           '    let nest = "nest";',
           '    for (inner in outer) {',
           '        let s = 0;',
           '        for (x in inner) {',
           '            s = s + x;',
           '        }',
           '        nest = nest + " " + toStr(listLen(inner)) + ":" + toStr(s);',
           '    }',
           '    print(nest);']
    nest = 'nest'
    for kk in range(3):
        vs = [(j * 37 + kk) % 256 for j in range(10 + kk * 7)]
        nest += f' {len(vs)}:{sum(vs)}'
    out.append(nest)
    tx += ['    const px = new Pix { w: 2, data: listNew() };',
           '    const d0: u16 = 40000;',
           '    const d1: u16 = 7;',
           '    listPush(px.data, d0);',
           '    listPush(px.data, d1);',
           '    listSet(px.data, 0, d1);',
           '    print("pix " + toStr(px.w) + " " + toStr(listLen(px.data)) + " " + toStr(listGet(px.data, 0) catch d0) + " " + toStr(listGet(px.data, 1) catch d0));',
           '    const big: List< u8> = listNew();',
           '    let i = 0;',
           '    while (i < 100000) {',
           '        listPush(big, (i % 251) as u8);',
           '        i = i + 1;',
           '    }',
           '    const zb: u8 = 0;',
           '    let bs = 0;',
           '    i = 0;',
           '    while (i < listLen(big)) {',
           '        bs = bs + (listGet(big, i) catch zb);',
           '        i = i + 1;',
           '    }',
           '    print("big " + toStr(listLen(big)) + " " + toStr(bs));']
    out.append('pix 2 2 7 7')
    out.append(f'big 100000 {sum(i % 251 for i in range(100000))}')
    # 线程：捕获发送紧凑列表，结果带回紧凑列表；Shared 借用
    us = [rnd.randint(0, (1 << 32) - 1) for _ in range(12)]
    tx.append('    const nums: List< u32> = listNew();')
    for k2, v in enumerate(us):
        tx.append(f'    const q{k2}: u32 = {v};')
        tx.append(f'    listPush(nums, q{k2});')
    tx += ['    const t1 = threadSpawn((): int => sumU32(nums));',
           '    print("thread sum " + toStr(threadJoin(t1) catch -1) + " main " + toStr(sumU32(nums)));',
           '    const t2 = threadSpawn((): List< i16> => {',
           '        const r: List< i16> = listNew();',
           '        let n = 0;',
           '        while (n < 5) {',
           '            listPush(r, (n * -1000) as i16);',
           '            n = n + 1;',
           '        }',
           '        return r;',
           '    });',
           '    const empty: List< i16> = listNew();',
           '    const back = threadJoin(t2) catch empty;',
           '    let tb = "thread back";',
           '    for (x in back) {',
           '        tb = tb + " " + toStr(x);',
           '    }',
           '    print(tb);',
           '    const sh = sharedNew(nums);',
           '    const view = sharedGet(sh);',
           '    const t3 = threadSpawn((): int => sumU32(sharedGet(sh)));',
           '    print("shared " + toStr(listLen(view)) + " " + toStr(listGet(view, 11) catch q0) + " " + toStr(sumU32(view)) + " " + toStr(threadJoin(t3) catch -1));',
           '    return 0;',
           '}']
    out.append(f'thread sum {sum(us)} main {sum(us)}')
    out.append('thread back ' + ' '.join(str(n * -1000) for n in range(5)))
    out.append(f'shared 12 {us[11]} {sum(us)} {sum(us)}')
    (OUT / 'fw2-list.tx').write_text('\n'.join(tx) + '\n', encoding='utf-8', newline='\n')
    (OUT / 'fw2-list.out').write_text('\n'.join(out) + '\n', encoding='utf-8', newline='\n')
    print('fw2-list', len(out), 'lines')


gen_arith()
gen_f32()
gen_inline()
gen_list()
