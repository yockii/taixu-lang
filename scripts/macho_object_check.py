"""Mach-O 目标文件的独立核对（docs/design/v052-macho-object-v0.1-20261026.md）：同一汇编映像写出的 ELF64 可重定位对象与
Mach-O MH_OBJECT 各自独立解析后对照——节内容（除重定位字段）相同，重定位一一对应（位置、目标符号、种类、加数），外部定义
在同一节的同一偏移。不导入 bootstrap/ 的任何代码（门与测试迁移计划原则 2）。

用法：python scripts/macho_object_check.py <elf.o> <macho.o>
"""
import struct
import sys

# ELF 节名 → Mach-O 节名
SECTIONS = {'.text': '__text', '.rodata': '__const', '.data': '__data', '.bss': '__bss'}
# R_X86_64_*：1 为 64，2 为 PC32。R_AARCH64_*：257 ABS64，282 JUMP26，283 CALL26，275 ADR_PREL_PG_HI21，277 ADD_ABS_LO12_NC，
# 278／284／285／286／299 为 LDST8／16／32／64／128_ABS_LO12_NC。
X86 = {1: ('UNSIGNED', 8), 2: ('PC32', 4)}
A64 = {257: ('UNSIGNED', 8), 282: ('BRANCH26', 4), 283: ('BRANCH26', 4), 275: ('PAGE21', 4),
       277: ('PAGEOFF12', 4), 278: ('PAGEOFF12', 4), 284: ('PAGEOFF12', 4), 285: ('PAGEOFF12', 4), 286: ('PAGEOFF12', 4), 299: ('PAGEOFF12', 4)}


def cstr(data, at):
    return data[at:data.index(b'\0', at)].decode('utf-8')


def parse_elf(data):
    assert data[:4] == b'\x7fELF' and data[4] == 2 and struct.unpack_from('<H', data, 16)[0] == 1, 'not ELF64 ET_REL'
    machine = struct.unpack_from('<H', data, 18)[0]
    shoff = struct.unpack_from('<Q', data, 40)[0]; shnum, shstrndx = struct.unpack_from('<HH', data, 60)
    heads = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + 64 * i) for i in range(shnum)]
    names = [cstr(data, heads[shstrndx][4] + h[0]) for h in heads]
    sections = {n: (b'' if h[1] == 8 else data[h[4]:h[4] + h[5]], h[5]) for n, h in zip(names, heads) if n in SECTIONS}
    symtab = names.index('.symtab'); strings = heads[heads[symtab][6]][4]
    syms = []
    for k in range(heads[symtab][5] // 24):
        name, info, _, shndx, value, _ = struct.unpack_from('<IBBHQQ', data, heads[symtab][4] + 24 * k)
        syms.append((cstr(data, strings + name), info >> 4, names[shndx] if 0 < shndx < len(names) else None, value))
    relocs = []
    for n, h in zip(names, heads):
        if h[1] == 4:
            target = names[h[7]]
            for k in range(h[5] // 24):
                at, info, addend = struct.unpack_from('<QQq', data, h[4] + 24 * k)
                relocs.append((target, at, info & 0xffffffff, syms[info >> 32][0], addend))
    return machine, sections, syms, relocs


def parse_macho(data):
    magic, cputype, _, filetype, ncmds = struct.unpack_from('<IIIII', data, 0)
    assert magic == 0xFEEDFACF and filetype == 1, 'not MH_OBJECT'
    at = 32; sections = []; symtab = dysym = None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x19:
            for k in range(struct.unpack_from('<I', data, at + 64)[0]):
                h = at + 72 + 80 * k
                addr, length, off, align, reloff, nreloc, flags = struct.unpack_from('<QQIIIII', data, h + 32)
                sections.append(dict(name=data[h:h + 16].rstrip(b'\0').decode(), seg=data[h + 16:h + 32].rstrip(b'\0').decode(),
                                     addr=addr, size=length, bytes=b'' if flags & 0xff == 1 else data[off:off + length],
                                     reloff=reloff, nreloc=nreloc, flags=flags))
        if cmd == 0x2: symtab = struct.unpack_from('<IIII', data, at + 8)
        if cmd == 0xB: dysym = struct.unpack_from('<6I', data, at + 8)
        at += size
    symoff, nsyms, stroff, _ = symtab
    syms = []
    for k in range(nsyms):
        strx, ntype, nsect, _, value = struct.unpack_from('<IBBHQ', data, symoff + 16 * k)
        syms.append((cstr(data, stroff + strx), ntype, nsect, value))
    assert dysym[0] == 0 and dysym[2] == dysym[1] and dysym[4] == dysym[2] + dysym[3] and dysym[4] + dysym[5] == nsyms, 'dysymtab partition'
    relocs = []
    for s in sections:
        pending = 0
        for k in range(s['nreloc']):
            address, word = struct.unpack_from('<iI', data, s['reloff'] + 8 * k)
            symbolnum, pcrel, length, extern, typ = word & 0xffffff, (word >> 24) & 1, (word >> 25) & 3, (word >> 27) & 1, word >> 28
            if cputype == 0x0100000C and typ == 10:  # ARM64_RELOC_ADDEND：r_symbolnum 为 24 位有符号加数，修饰下一条
                pending = symbolnum - (1 << 24) if symbolnum & (1 << 23) else symbolnum
                continue
            assert extern == 1, 'non-extern relocation'
            relocs.append((s['name'], address, typ, pcrel, length, syms[symbolnum][0], pending))
            pending = 0
        assert pending == 0, 'dangling ARM64_RELOC_ADDEND'
    return cputype, sections, syms, dysym, relocs


def check(elf_bytes, macho_bytes):
    """返回问题列表（空即一致）。"""
    problems = []
    machine, esecs, esyms, erels = parse_elf(elf_bytes)
    cputype, msecs, msyms, dysym, mrels = parse_macho(macho_bytes)
    a64 = machine == 183
    if (a64 and cputype != 0x0100000C) or (not a64 and (machine != 62 or cputype != 0x01000007)):
        return ['machine %d vs cputype %#x' % (machine, cputype)]
    byname = {s['name']: s for s in msecs}
    table = A64 if a64 else X86
    # 重定位：按（节, 位置）对上。
    mrel = {}
    for sect, address, typ, pcrel, length, name, addend in mrels:
        mrel[(sect, address)] = (typ, pcrel, length, name, addend)
    masks = {}
    for target, at, typ, name, addend in erels:
        sect = SECTIONS.get(target)
        kind, width = table.get(typ, (None, 0))
        got = mrel.pop((sect, at), None)
        if kind is None or got is None:
            problems.append('relocation %s+%#x type %d %s: no Mach-O counterpart' % (target, at, typ, name)); continue
        mtyp, pcrel, length, mname, maddend = got
        body = byname[sect]['bytes']
        if mname != '_' + name:
            problems.append('relocation %s+%#x symbol %s vs %s' % (sect, at, name, mname))
        if a64:
            want = {'UNSIGNED': (0, 0, 3), 'BRANCH26': (2, 1, 2), 'PAGE21': (3, 1, 2), 'PAGEOFF12': (4, 0, 2)}[kind]
            if (mtyp, pcrel, length) != want:
                problems.append('relocation %s+%#x kind %s vs (%d,%d,%d)' % (sect, at, kind, mtyp, pcrel, length))
            inplace = struct.unpack_from('<q', body, at)[0] if kind == 'UNSIGNED' else maddend
            if inplace != addend:
                problems.append('relocation %s+%#x addend %d vs %d' % (sect, at, addend, inplace))
        else:
            ok = (mtyp, pcrel, length) == (0, 0, 3) if kind == 'UNSIGNED' else (mtyp in (1, 2) and pcrel == 1 and length == 2)
            if not ok:
                problems.append('relocation %s+%#x kind %s vs (%d,%d,%d)' % (sect, at, kind, mtyp, pcrel, length))
            if kind == 'UNSIGNED':
                inplace = struct.unpack_from('<q', body, at)[0]
            else:
                # Mach-O 的原位加数相对字段末尾；ELF 的 RELA 以字段起点为 P，故差 4。分支（BRANCH）只出现在 call／jmp／jcc 上。
                inplace = struct.unpack_from('<i', body, at)[0] - 4
                is_branch = body[at - 1] in (0xE8, 0xE9) or (at >= 2 and body[at - 2] == 0x0F and 0x80 <= body[at - 1] <= 0x8F)
                if (mtyp == 2) != (sect == '__text' and is_branch):
                    problems.append('relocation %s+%#x BRANCH vs SIGNED' % (sect, at))
            if inplace != addend:
                problems.append('relocation %s+%#x addend %d vs %d' % (sect, at, addend, inplace))
        masks.setdefault(sect, []).append((at, width if not a64 or kind == 'UNSIGNED' else 0))
    for (sect, address), rest in mrel.items():
        problems.append('Mach-O relocation %s+%#x %s without ELF counterpart' % (sect, address, rest[3]))
    # 节内容：重定位字段之外逐字节相同（x86 的 RELA 字段与 UNSIGNED 字段 ELF 写 0、Mach-O 写原位加数；A64 指令字相同）。
    for ename, mname in SECTIONS.items():
        ebody, esize = esecs.get(ename, (b'', 0)); s = byname.get(mname)
        if s is None or s['size'] != esize:
            problems.append('section %s size' % mname); continue
        a, b = bytearray(ebody), bytearray(s['bytes'])
        for at, width in masks.get(mname, []):
            a[at:at + width] = bytes(width); b[at:at + width] = bytes(width)
        if a != b:
            problems.append('section %s bytes differ' % mname)
    # 外部定义：同名（加前导 _）、同节、同节内偏移。
    exports = {n: (sect, value) for n, bind, sect, value in esyms if bind == 1 and sect in SECTIONS}
    mexports = {}
    for name, ntype, nsect, value in msyms:
        if ntype == 0x0F:
            s = msecs[nsect - 1]; mexports[name] = (s['name'], value - s['addr'])
    want = {'_' + n: (SECTIONS[sect], value) for n, (sect, value) in exports.items()}
    if want != mexports:
        problems.append('exports %s vs %s' % (sorted(want.items())[:4], sorted(mexports.items())[:4]))
    if sorted(s['name'] for s in msecs) != sorted(SECTIONS.values()):
        problems.append('Mach-O sections %s' % [s['name'] for s in msecs])
    return problems


if __name__ == '__main__':
    found = check(open(sys.argv[1], 'rb').read(), open(sys.argv[2], 'rb').read())
    for p in found:
        print('MISMATCH', p)
    print('PASS' if not found else 'FAIL', 'Mach-O object vs ELF', len(found), 'problems')
    raise SystemExit(1 if found else 0)
