"""A64 编码表的生成与核对（macOS 适配 M4 设计 A2）。

每种指令形式按 bootstrap/selfhost/a64_encode.tx 里编码函数的参数顺序列出字段，生成对应的汇编文本，交给一个独立的
汇编器（测试机的 Apple clang，或 zig 自带的 LLVM：`zig cc -target aarch64-macos`）汇编，从 Mach-O 目标文件的
__text 取出编码，写成 tests/a64/encodings.txt。负例（编码函数必须拒绝的字段）不经汇编器，单列在表尾。

  python scripts/a64-encodings.py --cc "clang -arch arm64"            # 重新生成（测试机）
  python scripts/a64-encodings.py --cc "zig cc -target aarch64-macos" --check   # 与已入库的表核对

两个汇编器给出的表须逐字节相同才入库。表里的汇编文本只用基本助记符（不用 mov、cmp、lsl 等别名），反汇编器的往返
核对以它为准。
"""
import argparse
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'tests' / 'a64' / 'encodings.txt'
COND = ['eq', 'ne', 'hs', 'lo', 'mi', 'pl', 'vs', 'vc', 'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al']
EXT = ['uxtb', 'uxth', 'uxtw', 'uxtx', 'sxtb', 'sxth', 'sxtw', 'sxtx']
SHIFT = ['lsl', 'lsr', 'asr', 'ror']


def r(n, sf=1, sp=False):
    if n == 31:
        return 'sp' if sp else ('xzr' if sf else 'wzr')
    return ('x' if sf else 'w') + str(n)


def wsp(n, sf):
    # 32 位形里可取栈指针的位置写作 wsp。
    return ('sp' if sf else 'wsp') if n == 31 else r(n, sf)


def fr(n, ftype):
    return ('d' if ftype else 's') + str(n)


def signed64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def addsub_imm(sf, op, s, rd, rn, imm12, sh12):
    m = ['add', 'sub'][op] + ('s' if s else '')
    d = r(rd, sf) if s else wsp(rd, sf)
    return '%s %s, %s, #%d%s' % (m, d, wsp(rn, sf), imm12, ', lsl #12' if sh12 else '')


def logical_imm(sf, opc, rd, rn, value):
    m = ['and', 'orr', 'eor', 'ands'][opc]
    d = r(rd, sf) if opc == 3 else wsp(rd, sf)
    return '%s %s, %s, #0x%x' % (m, d, r(rn, sf), value & ((1 << (64 if sf else 32)) - 1))


def move_wide(sf, opc, rd, imm16, hw):
    return '%s %s, #0x%x, lsl #%d' % ({0: 'movn', 2: 'movz', 3: 'movk'}[opc], r(rd, sf), imm16, hw * 16)


def bitfield(sf, opc, rd, rn, immr, imms):
    return '%s %s, %s, #%d, #%d' % (['sbfm', 'bfm', 'ubfm'][opc], r(rd, sf), r(rn, sf), immr, imms)


def adr(page, rd, offset):
    return 'adrp %s, #%d' % (r(rd), offset * 4096) if page else 'adr %s, #%d' % (r(rd), offset)


def addsub_shifted(sf, op, s, rd, rn, rm, shift, amount):
    m = ['add', 'sub'][op] + ('s' if s else '')
    return '%s %s, %s, %s, %s #%d' % (m, r(rd, sf), r(rn, sf), r(rm, sf), SHIFT[shift], amount)


def addsub_ext(sf, op, s, rd, rn, rm, option, amount):
    m = ['add', 'sub'][op] + ('s' if s else '')
    d = r(rd, sf) if s else wsp(rd, sf)
    rmsf = 1 if (sf and option in (3, 7)) else 0
    return '%s %s, %s, %s, %s #%d' % (m, d, wsp(rn, sf), r(rm, rmsf), EXT[option], amount)


def logical_shifted(sf, opc, n, rd, rn, rm, shift, amount):
    m = [['and', 'orr', 'eor', 'ands'], ['bic', 'orn', 'eon', 'bics']][n][opc]
    return '%s %s, %s, %s, %s #%d' % (m, r(rd, sf), r(rn, sf), r(rm, sf), SHIFT[shift], amount)


def dp2(sf, opcode, rd, rn, rm):
    m = {2: 'udiv', 3: 'sdiv', 8: 'lslv', 9: 'lsrv', 10: 'asrv', 11: 'rorv'}[opcode]
    return '%s %s, %s, %s' % (m, r(rd, sf), r(rn, sf), r(rm, sf))


def dp3(sf, op31, o0, rd, rn, rm, ra):
    if op31 == 0:
        return '%s %s, %s, %s, %s' % (['madd', 'msub'][o0], r(rd, sf), r(rn, sf), r(rm, sf), r(ra, sf))
    if op31 in (1, 5):
        m = {1: ['smaddl', 'smsubl'], 5: ['umaddl', 'umsubl']}[op31][o0]
        return '%s %s, %s, %s, %s' % (m, r(rd), r(rn, 0), r(rm, 0), r(ra))
    return '%s %s, %s, %s' % ({2: 'smulh', 6: 'umulh'}[op31], r(rd), r(rn), r(rm))


def cond_select(sf, op, o2, rd, rn, rm, cond):
    m = [['csel', 'csinc'], ['csinv', 'csneg']][op][o2]
    return '%s %s, %s, %s, %s' % (m, r(rd, sf), r(rn, sf), r(rm, sf), COND[cond])


def ldst_name(size, v, opc, rt):
    if v:
        return ['str', 'ldr'][opc], fr(rt, 1 if size == 3 else 0)
    if opc <= 1:
        m = ['str', 'ldr'][opc] + {0: 'b', 1: 'h', 2: '', 3: ''}[size]
        return m, r(rt, 1 if size == 3 else 0)
    m = 'ldrs' + {0: 'b', 1: 'h', 2: 'w'}[size]
    return m, r(rt, 1 if opc == 2 else 0)


def ldst_uimm(size, v, opc, rt, rn, offset):
    m, t = ldst_name(size, v, opc, rt)
    return '%s %s, [%s, #%d]' % (m, t, r(rn, sp=True), offset)


def ldst_imm9(size, v, opc, rt, rn, imm9, mode):
    m, t = ldst_name(size, v, opc, rt)
    if mode == 0:
        m = m[:2] + 'u' + m[2:]  # str→stur、ldrsb→ldursb……
        return '%s %s, [%s, #%d]' % (m, t, r(rn, sp=True), imm9)
    if mode == 1:
        return '%s %s, [%s], #%d' % (m, t, r(rn, sp=True), imm9)
    return '%s %s, [%s, #%d]!' % (m, t, r(rn, sp=True), imm9)


def ldst_reg(size, v, opc, rt, rn, rm, option, scale):
    m, t = ldst_name(size, v, opc, rt)
    idx = r(rm, 1 if option in (3, 7) else 0)
    # 不缩放时不写移位量（字节访问写 #0 表示 S＝1）；缩放量等于访问宽度的 log2。
    if not scale:
        tail = '' if option == 3 else ', ' + EXT[option]
    else:
        tail = ', %s #%d' % ('lsl' if option == 3 else EXT[option], size)
    return '%s %s, [%s, %s%s]' % (m, t, r(rn, sp=True), idx, tail)


def ldst_pair(opc, v, l, rt, rt2, rn, offset, mode):
    m = ['stp', 'ldp'][l]
    if v:
        a, b = fr(rt, opc), fr(rt2, opc)
    else:
        a, b = r(rt, 1 if opc == 2 else 0), r(rt2, 1 if opc == 2 else 0)
    base = r(rn, sp=True)
    if mode == 1:
        return '%s %s, %s, [%s], #%d' % (m, a, b, base, offset)
    if mode == 2:
        return '%s %s, %s, [%s, #%d]' % (m, a, b, base, offset)
    return '%s %s, %s, [%s, #%d]!' % (m, a, b, base, offset)


def atomic_al(size, swp, rs, rt, rn):
    return '%s %s, %s, [%s]' % (['ldaddal', 'swpal'][swp], r(rs, size - 2), r(rt, size - 2), r(rn, sp=True))


def casal(size, rs, rt, rn):
    return 'casal %s, %s, [%s]' % (r(rs, size - 2), r(rt, size - 2), r(rn, sp=True))


def acq_rel(kind, size, rt, rn):
    m = ['ldar', 'stlr', 'ldapr'][kind] + {0: 'b', 1: 'h', 2: '', 3: ''}[size]
    return '%s %s, [%s]' % (m, r(rt, 1 if size == 3 else 0), r(rn, sp=True))


def branch(link, offset):
    return '%s #%d' % (['b', 'bl'][link], offset)


def branch_cond(cond, offset):
    return 'b.%s #%d' % (COND[cond], offset)


def compare_branch(sf, nz, rt, offset):
    return '%s %s, #%d' % (['cbz', 'cbnz'][nz], r(rt, sf), offset)


def test_branch(nz, rt, bit, offset):
    return '%s %s, #%d, #%d' % (['tbz', 'tbnz'][nz], r(rt, 1 if bit >= 32 else 0), bit, offset)


def branch_reg(kind, rn):
    return '%s %s' % (['br', 'blr', 'ret'][kind], r(rn))


def brk(imm16):
    return 'brk #0x%x' % imm16


def fp_int(sf, ftype, rmode, opcode, rd, rn):
    m = {(0, 6): 'fmov', (0, 7): 'fmov', (0, 2): 'scvtf', (0, 3): 'ucvtf', (3, 0): 'fcvtzs', (3, 1): 'fcvtzu'}[(rmode, opcode)]
    if (rmode, opcode) in ((0, 7), (0, 2), (0, 3)):
        return '%s %s, %s' % (m, fr(rd, ftype), r(rn, sf))
    return '%s %s, %s' % (m, r(rd, sf), fr(rn, ftype))


def fp_unary(ftype, opcode, rd, rn):
    if opcode == 4:
        return 'fcvt %s, %s' % (fr(rd, 0), fr(rn, ftype))
    if opcode == 5:
        return 'fcvt %s, %s' % (fr(rd, 1), fr(rn, ftype))
    return '%s %s, %s' % (['fmov', 'fabs', 'fneg', 'fsqrt'][opcode], fr(rd, ftype), fr(rn, ftype))


def fp_binary(ftype, opcode, rd, rn, rm):
    return '%s %s, %s, %s' % (['fmul', 'fdiv', 'fadd', 'fsub'][opcode], fr(rd, ftype), fr(rn, ftype), fr(rm, ftype))


def fcmp(ftype, rn, rm, zero):
    return 'fcmp %s, #0.0' % fr(rn, ftype) if zero else 'fcmp %s, %s' % (fr(rn, ftype), fr(rm, ftype))


FORMS = {
    'addsub_imm': addsub_imm, 'logical_imm': logical_imm, 'move_wide': move_wide, 'bitfield': bitfield, 'adr': adr,
    'addsub_shifted': addsub_shifted, 'addsub_ext': addsub_ext, 'logical_shifted': logical_shifted, 'dp2': dp2, 'dp3': dp3,
    'cond_select': cond_select, 'ldst_uimm': ldst_uimm, 'ldst_imm9': ldst_imm9, 'ldst_reg': ldst_reg, 'ldst_pair': ldst_pair,
    'atomic_al': atomic_al, 'casal': casal, 'acq_rel': acq_rel, 'branch': branch, 'branch_cond': branch_cond,
    'compare_branch': compare_branch, 'test_branch': test_branch, 'branch_reg': branch_reg, 'brk': brk,
    'fp_int': fp_int, 'fp_unary': fp_unary, 'fp_binary': fp_binary, 'fcmp': fcmp,
}

REGS = [(0, 1, 2), (31, 31, 31), (16, 19, 30), (29, 17, 8)]


def positive_cases():
    c = []
    for sf in (0, 1):
        for op in (0, 1):
            for s in (0, 1):
                for rd, rn, _ in REGS:
                    for imm12, sh in ((0, 0), (1, 0), (4095, 0), (4095, 1), (16, 1)):
                        c.append(('addsub_imm', sf, op, s, rd, rn, imm12, sh))
    values64 = [0xfffffffff0000000, 0xff, 1, 0x8000000000000000, 0x7fffffffffffffff, 0xfffffffffffffffe, 0x5555555555555555,
                0x0f0f0f0f0f0f0f0f, 0x3333333333333333, 0x00ff00ff00ff00ff, 0x0000ffff0000ffff, 0xffff, 0x00000000ffffffff,
                0xc000000000000003, 0x8181818181818181, 0x7ffffffffffffffe]
    values32 = [0xff, 1, 0x80000000, 0x7fffffff, 0xfffffffe, 0x55555555, 0x0f0f0f0f, 0xff00ff00, 0xc0000003, 0xffff]
    for opc in range(4):
        for rd, rn, _ in REGS:
            for v in values64:
                c.append(('logical_imm', 1, opc, rd, rn, signed64(v)))
            for v in values32:
                c.append(('logical_imm', 0, opc, rd, rn, v))
    for sf in (0, 1):
        for opc in (0, 2, 3):
            for rd in (0, 16, 31):
                for imm16 in (0, 1, 0x1234, 0xffff):
                    for hw in range(2 + 2 * sf):
                        c.append(('move_wide', sf, opc, rd, imm16, hw))
    for sf in (0, 1):
        top = 31 + 32 * sf
        for opc in range(3):
            for rd, rn, _ in REGS:
                for immr, imms in ((0, top), (1, top), (top, top - 1), (0, 7), (0, 31 if sf else 15), (top - 3, top - 4), (5, 2)):
                    c.append(('bitfield', sf, opc, rd, rn, immr, imms))
    for page in (0, 1):
        for rd in (0, 17, 31):
            for off in (0, 1, 3, 4, -1, -4, 1048575, -1048576, 12345, -54321):
                c.append(('adr', page, rd, off))
    for sf in (0, 1):
        top = 31 + 32 * sf
        for op in (0, 1):
            for s in (0, 1):
                for rd, rn, rm in REGS:
                    for shift in range(3):
                        for amount in (0, 1, top):
                            c.append(('addsub_shifted', sf, op, s, rd, rn, rm, shift, amount))
    for sf in (0, 1):
        for op in (0, 1):
            for s in (0, 1):
                for rd, rn, rm in REGS:
                    for option in range(8):
                        for amount in (0, 2, 4):
                            c.append(('addsub_ext', sf, op, s, rd, rn, rm, option, amount))
    for sf in (0, 1):
        top = 31 + 32 * sf
        for opc in range(4):
            for n in (0, 1):
                for rd, rn, rm in REGS:
                    for shift in range(4):
                        for amount in (0, 7, top):
                            c.append(('logical_shifted', sf, opc, n, rd, rn, rm, shift, amount))
    for sf in (0, 1):
        for opcode in (2, 3, 8, 9, 10, 11):
            for rd, rn, rm in REGS:
                c.append(('dp2', sf, opcode, rd, rn, rm))
    for sf in (0, 1):
        for o0 in (0, 1):
            for rd, rn, rm in REGS:
                for ra in (0, 31, 5):
                    c.append(('dp3', sf, 0, o0, rd, rn, rm, ra))
    for op31 in (1, 5):
        for o0 in (0, 1):
            for rd, rn, rm in REGS:
                c.append(('dp3', 1, op31, o0, rd, rn, rm, 7))
    for op31 in (2, 6):
        for rd, rn, rm in REGS:
            c.append(('dp3', 1, op31, 0, rd, rn, rm, 31))
    for sf in (0, 1):
        for op in (0, 1):
            for o2 in (0, 1):
                for rd, rn, rm in REGS:
                    for cond in range(15):
                        c.append(('cond_select', sf, op, o2, rd, rn, rm, cond))
    kinds = [(0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 0, 3), (1, 0, 0), (1, 0, 1), (1, 0, 2), (1, 0, 3), (2, 0, 0), (2, 0, 1),
             (2, 0, 2), (3, 0, 0), (3, 0, 1), (2, 1, 0), (2, 1, 1), (3, 1, 0), (3, 1, 1)]
    for size, v, opc in kinds:
        unit = 1 << size
        for rt, rn in ((0, 1), (31, 31), (30, 19), (7, 29)):
            for k in (0, 1, 4095, 2048):
                c.append(('ldst_uimm', size, v, opc, rt, rn, k * unit))
            for imm9 in (0, 1, -1, 255, -256):
                for mode in (0, 1, 3):
                    c.append(('ldst_imm9', size, v, opc, rt, rn, imm9, mode))
            for rm in (2, 31):
                for option in (2, 3, 6, 7):
                    for scale in (0, 1):
                        c.append(('ldst_reg', size, v, opc, rt, rn, rm, option, scale))
    for opc, v in ((0, 0), (2, 0), (0, 1), (1, 1)):
        unit = 8 if (opc == 2 or (v == 1 and opc == 1)) else 4
        for l in (0, 1):
            for rt, rt2, rn in ((29, 30, 31), (19, 20, 31), (0, 1, 2), (8, 9, 29)):
                for k in (0, 1, -1, 63, -64, 2, -2):
                    for mode in (1, 2, 3):
                        c.append(('ldst_pair', opc, v, l, rt, rt2, rn, k * unit, mode))
    # 与负例里 CONSTRAINED UNPREDICTABLE 紧邻的合法写法：不写回时同号、sp 基址写回、stp 两源同号。
    c += [('ldst_imm9', 3, 0, 1, 5, 5, 8, 0), ('ldst_imm9', 3, 0, 0, 31, 31, -16, 3), ('ldst_pair', 2, 0, 0, 6, 6, 31, 16, 2),
          ('ldst_pair', 2, 0, 0, 6, 6, 31, -16, 3), ('ldst_pair', 2, 0, 1, 3, 4, 5, 16, 1)]
    for size in (2, 3):
        for rs, rt, rn in ((8, 0, 0), (1, 2, 31), (31, 31, 19), (16, 17, 29)):
            for swp in (0, 1):
                c.append(('atomic_al', size, swp, rs, rt, rn))
            c.append(('casal', size, rs, rt, rn))
    for kind in range(3):
        for size in range(4):
            for rt, rn in ((0, 1), (31, 31), (30, 19)):
                c.append(('acq_rel', kind, size, rt, rn))
    for link in (0, 1):
        for off in (0, 4, -4, 8, 134217724, -134217728, 1048576, -1048580):
            c.append(('branch', link, off))
    for cond in range(15):
        for off in (0, 4, -4, 1048572, -1048576, 40000):
            c.append(('branch_cond', cond, off))
    for sf in (0, 1):
        for nz in (0, 1):
            for rt in (0, 31, 17):
                for off in (0, 4, -4, 1048572, -1048576):
                    c.append(('compare_branch', sf, nz, rt, off))
    for nz in (0, 1):
        for rt in (0, 31, 17):
            for bit in (0, 1, 31, 32, 63):
                for off in (0, 4, -4, 32764, -32768):
                    c.append(('test_branch', nz, rt, bit, off))
    for kind in range(3):
        for rn in (0, 16, 30):
            c.append(('branch_reg', kind, rn))
    for imm in (0, 1, 0xf000, 0xffff):
        c.append(('brk', imm))
    for sf in (0, 1):
        for ftype in (0, 1):
            for rmode, opcode in ((0, 2), (0, 3), (3, 0), (3, 1)):
                for rd, rn in ((0, 1), (31, 31), (16, 30)):
                    c.append(('fp_int', sf, ftype, rmode, opcode, rd, rn))
    for ftype, sf in ((0, 0), (1, 1)):
        for opcode in (6, 7):
            for rd, rn in ((0, 1), (31, 31), (16, 30)):
                c.append(('fp_int', sf, ftype, 0, opcode, rd, rn))
    for ftype in (0, 1):
        for opcode in range(6):
            if (opcode == 4 and ftype == 0) or (opcode == 5 and ftype == 1):
                continue
            for rd, rn in ((0, 1), (31, 31), (16, 30)):
                c.append(('fp_unary', ftype, opcode, rd, rn))
        for opcode in range(4):
            for rd, rn, rm in ((0, 1, 2), (31, 31, 31), (16, 17, 30)):
                c.append(('fp_binary', ftype, opcode, rd, rn, rm))
        for rn, rm in ((0, 1), (31, 31), (16, 2)):
            c.append(('fcmp', ftype, rn, rm, 0))
        for rn in (0, 31):
            c.append(('fcmp', ftype, rn, 0, 1))
    return c


# 负例：编码函数必须返回 -1。
NEGATIVE = [
    ('addsub_imm', 1, 0, 0, 0, 1, 4096, 0), ('addsub_imm', 1, 0, 0, 0, 1, -1, 0), ('addsub_imm', 2, 0, 0, 0, 1, 1, 0),
    ('addsub_imm', 1, 0, 0, 32, 1, 1, 0), ('addsub_imm', 1, 0, 0, 0, 1, 1, 2),
    ('logical_imm', 1, 0, 0, 1, 0), ('logical_imm', 1, 0, 0, 1, -1), ('logical_imm', 1, 0, 0, 1, 0x1234),
    ('logical_imm', 0, 0, 0, 1, 0xffffffff), ('logical_imm', 0, 0, 0, 1, 0x100000000), ('logical_imm', 0, 0, 0, 1, -1),
    ('logical_imm', 1, 0, 0, 1, 5), ('logical_imm', 1, 4, 0, 1, 1),
    ('move_wide', 1, 1, 0, 1, 0), ('move_wide', 0, 2, 0, 1, 2), ('move_wide', 1, 2, 0, 65536, 0), ('move_wide', 1, 2, 0, 1, 4),
    ('bitfield', 0, 0, 0, 1, 32, 0), ('bitfield', 1, 3, 0, 1, 0, 0), ('bitfield', 1, 0, 0, 1, 0, 64),
    ('adr', 0, 0, 1048576), ('adr', 1, 0, -1048577),
    ('addsub_shifted', 1, 0, 0, 0, 1, 2, 3, 0), ('addsub_shifted', 0, 0, 0, 0, 1, 2, 0, 32), ('addsub_shifted', 1, 0, 0, 0, 1, 2, 0, 64),
    ('addsub_ext', 1, 0, 0, 0, 1, 2, 3, 5), ('addsub_ext', 1, 0, 0, 0, 1, 2, 8, 0),
    ('logical_shifted', 0, 0, 0, 0, 1, 2, 0, 32), ('logical_shifted', 1, 0, 2, 0, 1, 2, 0, 0),
    ('dp2', 1, 4, 0, 1, 2), ('dp2', 1, 12, 0, 1, 2),
    ('dp3', 0, 2, 0, 0, 1, 2, 31), ('dp3', 1, 2, 1, 0, 1, 2, 31), ('dp3', 1, 2, 0, 0, 1, 2, 3), ('dp3', 0, 1, 0, 0, 1, 2, 3),
    ('dp3', 1, 3, 0, 0, 1, 2, 3),
    ('cond_select', 1, 0, 0, 0, 1, 2, 16),
    ('ldst_uimm', 3, 0, 1, 0, 1, 4), ('ldst_uimm', 3, 0, 1, 0, 1, 32768), ('ldst_uimm', 3, 0, 1, 0, 1, -8), ('ldst_uimm', 3, 0, 2, 0, 1, 0),
    ('ldst_uimm', 2, 0, 3, 0, 1, 0), ('ldst_uimm', 1, 1, 1, 0, 1, 0), ('ldst_uimm', 3, 1, 2, 0, 1, 0), ('ldst_uimm', 0, 0, 1, 0, 1, 4096),
    ('ldst_imm9', 3, 0, 1, 0, 1, 256, 0), ('ldst_imm9', 3, 0, 1, 0, 1, -257, 1), ('ldst_imm9', 3, 0, 1, 0, 1, 0, 2),
    ('ldst_reg', 3, 0, 1, 0, 1, 2, 0, 0), ('ldst_reg', 3, 0, 1, 0, 1, 2, 3, 2),
    ('ldst_pair', 2, 0, 0, 29, 30, 31, 4, 3), ('ldst_pair', 2, 0, 0, 29, 30, 31, 512, 3), ('ldst_pair', 2, 0, 0, 29, 30, 31, -520, 3),
    ('ldst_pair', 1, 0, 0, 29, 30, 31, 8, 3), ('ldst_pair', 2, 0, 0, 29, 30, 31, 8, 0), ('ldst_pair', 3, 1, 0, 0, 1, 31, 8, 2),
    # 架构上 CONSTRAINED UNPREDICTABLE（独立汇编器同样拒收）：写回时基址即整数数据寄存器、ldp 两目标同号。
    ('ldst_imm9', 0, 0, 1, 21, 21, 7, 1), ('ldst_imm9', 3, 0, 0, 5, 5, -8, 3), ('ldst_imm9', 2, 0, 2, 9, 9, 4, 1),
    ('ldst_pair', 2, 0, 1, 3, 4, 3, 16, 1), ('ldst_pair', 2, 0, 0, 3, 4, 4, -16, 3), ('ldst_pair', 0, 0, 0, 7, 8, 7, 8, 3),
    ('ldst_pair', 2, 0, 1, 6, 6, 31, 16, 2), ('ldst_pair', 1, 1, 1, 6, 6, 31, 16, 2),
    ('atomic_al', 1, 0, 0, 1, 2), ('casal', 0, 0, 1, 2), ('acq_rel', 3, 3, 0, 1),
    ('branch', 0, 2), ('branch', 0, 134217728), ('branch', 1, -134217732),
    ('branch_cond', 15, 0), ('branch_cond', 0, 1048576), ('branch_cond', 0, 6),
    ('compare_branch', 1, 0, 0, -1048580), ('test_branch', 0, 0, 64, 0), ('test_branch', 0, 0, 0, 32768),
    ('branch_reg', 3, 0), ('brk', 65536), ('brk', -1),
    ('fp_int', 1, 0, 0, 7, 0, 1), ('fp_int', 0, 1, 0, 6, 0, 1), ('fp_int', 1, 1, 1, 0, 0, 1), ('fp_int', 1, 1, 0, 4, 0, 1),
    ('fp_unary', 0, 4, 0, 1), ('fp_unary', 1, 5, 0, 1), ('fp_unary', 1, 6, 0, 1),
    ('fp_binary', 1, 4, 0, 1, 2), ('fcmp', 1, 0, 1, 1), ('fcmp', 2, 0, 1, 0),
]


def text_words(obj):
    b = obj.read_bytes()
    assert struct.unpack_from('<I', b, 0)[0] == 0xfeedfacf, 'not a 64-bit Mach-O object'
    ncmds = struct.unpack_from('<I', b, 16)[0]
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', b, off)
        if cmd == 0x19:
            for i in range(struct.unpack_from('<I', b, off + 64)[0]):
                s = off + 72 + i * 80
                if b[s:s + 16].rstrip(b'\0') == b'__text':
                    n = struct.unpack_from('<Q', b, s + 40)[0]
                    at = struct.unpack_from('<I', b, s + 48)[0]
                    assert struct.unpack_from('<I', b, s + 60)[0] == 0, 'unexpected relocations'
                    return [struct.unpack_from('<I', b, at + 4 * k)[0] for k in range(n // 4)]
        off += size
    raise SystemExit('no __text section')


def assemble(cc, lines):
    with tempfile.TemporaryDirectory() as d:
        src, obj = Path(d) / 'a.s', Path(d) / 'a.o'
        src.write_text('.text\n' + ''.join(l + '\n' for l in lines), encoding='ascii', newline='\n')
        p = subprocess.run(shlex.split(cc) + ['-c', str(src), '-o', str(obj)], capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit('assembler failed:\n' + p.stderr[:4000])
        return text_words(obj)


def render(cc):
    cases = positive_cases()
    texts = [FORMS[c[0]](*c[1:]) for c in cases]
    words = assemble(cc, texts)
    assert len(words) == len(cases), (len(words), len(cases))
    out = ['# A64 编码表：scripts/a64-encodings.py 生成（独立汇编器：Apple clang 或 zig cc -target aarch64-macos，两者须一致）。',
           '# 正例：形式 字段… | 汇编 | 编码（十六进制）；负例以 ! 开头，编码函数须返回 -1。']
    for c, t, w in zip(cases, texts, words):
        out.append('%s | %s | %08x' % (' '.join(map(str, c)), t, w))
    for c in NEGATIVE:
        out.append('! ' + ' '.join(map(str, c)))
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cc', required=True, help='汇编器命令，如 "clang -arch arm64" 或 "zig cc -target aarch64-macos"')
    ap.add_argument('--check', action='store_true', help='只与已入库的表核对，不写文件')
    a = ap.parse_args()
    text = render(a.cc)
    if a.check:
        old = TABLE.read_text(encoding='utf-8')
        if old != text:
            for k, (x, y) in enumerate(zip(old.splitlines(), text.splitlines())):
                if x != y:
                    print('first difference at line %d:\n  table: %s\n  now:   %s' % (k + 1, x, y))
                    break
            sys.exit(1)
        print('PASS %d lines match' % len(text.splitlines()))
        return
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    TABLE.write_bytes(text.encode('utf-8'))
    print('wrote %s (%d lines)' % (TABLE.relative_to(ROOT), len(text.splitlines())))


if __name__ == '__main__':
    main()
