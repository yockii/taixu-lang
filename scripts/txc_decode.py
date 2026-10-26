"""独立的 .txc 解码器（门禁用，不依赖被测编译器）：解 v4（Zig 链）、v5（v4＋FFI 尾节）与 v6（原生链），
把指令字一律规整成 v6 布局，供字节码镜像门做结构比对。

TXC v6（docs/design/v052-capacity-limits-review-v0.1-20261016.md §八）：iABC＝op16|A16<<16|B16<<32|C16<<48，
iABx＝op16|A16<<16|Bx32<<32，sBx＝Bx−2^31；GETFLD/SETFLD 的 C 第 15 位为标志；callbi 外部函数编号从 4096 起。
v4：op8|A8<<8|B8<<16|C8<<24，Bx16 在高半，sBx＝Bx−32768；C 第 7 位为标志；外部函数编号从 64 起。
容器其余部分各版本相同（LEB128 计数与索引；可选尾节 TXEXT、TXFNT、TXFFI）。
"""

BX_OPS = {1, 25, 26, 27, 28, 29, 35, 39}
SBX_OPS = {15, 16, 17}
FIELD_OPS = {20, 21, 203, 210}
CALLBI = 31
MASK64 = (1 << 64) - 1


class TxcError(Exception):
    pass


def widen_v4(w):
    """v4 指令字 → v6 指令字（无符号 64 位）。"""
    op = w & 0xFF
    a = (w >> 8) & 0xFF
    bx = (w >> 16) & 0xFFFF
    if op in BX_OPS:
        return op | (a << 16) | (bx << 32)
    if op in SBX_OPS:
        return op | (a << 16) | ((bx - 32768 + (1 << 31)) << 32)
    b = (w >> 16) & 0xFF
    c = (w >> 24) & 0xFF
    if op in FIELD_OPS:
        c = (c & 0x7F) | ((c & 0x80) << 8)
    if op == CALLBI and b >= 64:
        b = b - 64 + 4096
    return op | (a << 16) | (b << 32) | (c << 48)


class _R:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def u8(self):
        if self.p >= len(self.d):
            raise TxcError('truncated')
        v = self.d[self.p]
        self.p += 1
        return v

    def var(self):
        v = 0
        shift = 0
        while True:
            b = self.u8()
            v |= (b & 0x7F) << shift
            if not b & 0x80:
                return v & MASK64
            shift += 7
            if shift >= 70:
                raise TxcError('varint too long')

    def zig(self):
        u = self.var()
        return (u >> 1) ^ -(u & 1)

    def raw(self, n):
        if self.p + n > len(self.d):
            raise TxcError('truncated bytes')
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def magic(self, m):
        if self.d[self.p:self.p + len(m)] == m:
            self.p += len(m)
            return True
        return False


def _const(r):
    tag = r.u8()
    if tag == 1:
        return (1, r.zig())
    if tag == 2:
        return (2, r.raw(8))
    if tag in (3, 4, 6, 9):
        return (tag, r.var())
    if tag == 5:
        n = r.var()
        return (5, tuple(r.var() for _ in range(n)))
    if tag in (7, 8):
        return (tag, r.var(), r.var())
    raise TxcError('bad const tag %d' % tag)


def decode(data):
    """返回 (版本, 规整后的结构)；结构中的指令字已是 v6 布局，可直接比对。"""
    r = _R(data)
    if r.raw(3) != b'TXC':
        raise TxcError('bad magic')
    ver = r.u8() | (r.u8() << 8)
    if ver not in (4, 5, 6):
        raise TxcError('bad version %d' % ver)
    r.var()  # flags
    strings = tuple(r.raw(r.var()) for _ in range(r.var()))
    tys = tuple((r.u8(), r.var(), r.var()) for _ in range(r.var()))
    decls = []
    for _ in range(r.var()):
        module, name, which = r.var(), r.var(), r.u8()
        if which == 1:
            body = tuple((r.var(), r.var()) for _ in range(r.var()))
        elif which == 2:
            body = []
            for _ in range(r.var()):
                vname = r.var()
                body.append((vname, tuple((r.var(), r.var()) for _ in range(r.var()))))
            body = tuple(body)
        else:
            raise TxcError('bad decl kind %d' % which)
        decls.append((module, name, which, body))
    funcs = []
    for _ in range(r.var()):
        name, module, reg, nparams = r.var(), r.var(), r.var(), r.var()
        code = []
        for _ in range(r.var()):
            w = r.var()
            code.append(w if ver == 6 else widen_v4(w))
        consts = tuple(_const(r) for _ in range(r.var()))
        lines = tuple((r.var(), r.var()) for _ in range(r.var()))
        ptys = tuple(r.var() for _ in range(r.var()))
        funcs.append((name, module, reg, nparams, tuple(code), consts, lines, ptys, r.var()))
    modules = tuple((r.var(), r.var(), r.var()) for _ in range(r.var()))
    tests = tuple((r.var(), r.var()) for _ in range(r.var()))
    entry = r.var()
    externs = ()
    if r.magic(b'TXEXT'):
        xs = []
        for _ in range(r.var()):
            nm = r.raw(r.var())
            tags = tuple(r.u8() for _ in range(r.var()))
            xs.append((nm, tags, r.u8()))
        externs = tuple(xs)
    sigs = ()
    if r.magic(b'TXFNT'):
        sigs = tuple((r.var(), tuple(r.var() for _ in range(r.var()))) for _ in range(r.var()))
    ffi = b''
    if ver == 5 or (ver == 6 and r.magic(b'TXFFI')):
        if ver == 5 and not r.magic(b'TXFFI'):
            raise TxcError('v5 without TXFFI')
        ffi = r.d[r.p:]
        r.p = len(r.d)
    if r.p != len(r.d):
        raise TxcError('trailing bytes at %d' % r.p)
    return ver, (strings, tys, tuple(decls), tuple(funcs), modules, tests, entry, externs, sigs, ffi)


def first_difference(a, b, path='program'):
    """两份规整结构的首个差异位置（相同返回 None）。"""
    if type(a) != type(b):
        return path + ': 类型不同'
    if isinstance(a, tuple):
        if len(a) != len(b):
            return '%s: 长度 %d vs %d' % (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, '%s[%d]' % (path, i))
            if d:
                return d
        return None
    return None if a == b else '%s: %r vs %r' % (path, a, b)
