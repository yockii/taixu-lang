"""独立的 Mach-O 可执行文件校验器（macOS 适配 M1 设计 E10，docs/design/v052-macos-m1-macho-v0.1-20261024.md）。

不依赖编译器的任何代码，按 Mach-O 格式逐项核对太虚产出的 x86_64 与 arm64 可执行文件：头与 load commands 的尺寸与
对齐，段的范围、对齐与保护（W^X、__TEXT 不可写），节落在段内，LC_MAIN 落在 __text 内，rebase／bind 指令逐条解码后
每个目标都落在可写段里，符号表、间接符号表与 __got 的绑定一一对应，function starts 单调且在 __text 内，
代码签名逐页重算 SHA-256。arm64（macOS 适配 M4 设计 A3）另逐字解码 __text：B／BL／B.cond／CBZ／TBZ 的目标在 __text 内，
每条 ADRP 紧跟用同一寄存器的 ADD 或无符号偏移的取存，合成的地址落在某个节里；经 __got 的只有 64 位 LDR，取存不写只读段
（__DATA_CONST 带 SG_READ_ONLY，dyld 修补后改为只读）。这一条依赖写出端不在 __text 里放数据。

用法：python scripts/macho_check.py <可执行文件>...      逐个校验，全部通过退出 0
      python scripts/macho_check.py --self-test <文件>    另对篡改过的副本确认能查出（负例）
"""
import hashlib
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
CPU_X86_64 = 0x01000007
CPU_ARM64 = 0x0100000C
SUBTYPES = {CPU_X86_64: 3, CPU_ARM64: 0}  # CPU_SUBTYPE_X86_64_ALL、CPU_SUBTYPE_ARM64_ALL
SG_READ_ONLY = 0x10
MH_EXECUTE = 2
REQUIRED_FLAGS = 0x1 | 0x4 | 0x80 | 0x200000  # NOUNDEFS DYLDLINK TWOLEVEL PIE
SEG_ALIGN = 0x4000
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xB
LC_LOAD_DYLINKER = 0xE
LC_LOAD_DYLIB = 0xC
LC_UUID = 0x1B
LC_BUILD_VERSION = 0x32
LC_MAIN = 0x80000028
LC_DYLD_INFO_ONLY = 0x80000022
LC_FUNCTION_STARTS = 0x26
LC_CODE_SIGNATURE = 0x1D
LC_SOURCE_VERSION = 0x2A
KNOWN = {LC_SEGMENT_64, LC_SYMTAB, LC_DYSYMTAB, LC_LOAD_DYLINKER, LC_LOAD_DYLIB, LC_UUID, LC_BUILD_VERSION, LC_MAIN,
         LC_DYLD_INFO_ONLY, LC_FUNCTION_STARTS, LC_CODE_SIGNATURE, LC_SOURCE_VERSION}
S_ZEROFILL = 0x1
S_NON_LAZY_SYMBOL_POINTERS = 0x6
S_THREAD_LOCAL_ZEROFILL = 0x12
S_THREAD_LOCAL_VARIABLES = 0x13
S_ATTR_PURE_INSTRUCTIONS = 0x80000000


class Bad(Exception):
    pass


def need(cond, message):
    if not cond:
        raise Bad(message)


def cstr(data, at, limit):
    end = data.find(b'\0', at, limit)
    need(end >= 0, 'unterminated string at %#x' % at)
    return data[at:end].decode('utf-8', 'replace')


def uleb(data, at, limit):
    value = shift = 0
    while True:
        need(at < limit, 'ULEB past end')
        b = data[at]
        at += 1
        value |= (b & 0x7F) << shift
        shift += 7
        need(shift < 70, 'ULEB too long')
        if not b & 0x80:
            return value, at


def sleb(data, at, limit):
    value = shift = 0
    while True:
        need(at < limit, 'SLEB past end')
        b = data[at]
        at += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            if b & 0x40:
                value -= 1 << shift
            return value, at


def check(data):
    """校验一个映像；返回摘要字典，问题即抛 Bad。"""
    size = len(data)
    need(size >= 32, 'file too small')
    magic, cpu, sub, ftype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<IiiIIIII', data, 0)
    need(magic == MH_MAGIC_64, 'not MH_MAGIC_64')
    need(cpu in SUBTYPES, 'cputype %#x is neither x86_64 nor arm64' % cpu)
    need(sub & 0xFFFFFF == SUBTYPES[cpu], 'cpusubtype %#x for cputype %#x' % (sub, cpu))
    need(ftype == MH_EXECUTE, 'not MH_EXECUTE')
    need(flags & REQUIRED_FLAGS == REQUIRED_FLAGS, 'header flags %#x lack NOUNDEFS|DYLDLINK|TWOLEVEL|PIE' % flags)
    need(32 + sizeofcmds <= size, 'load commands past end of file')
    cmds = []
    at = 32
    for _ in range(ncmds):
        need(at + 8 <= 32 + sizeofcmds, 'load command header past sizeofcmds')
        cmd, cmdsize = struct.unpack_from('<II', data, at)
        need(cmdsize >= 8 and cmdsize % 8 == 0, 'cmdsize %d of cmd %#x not a multiple of 8' % (cmdsize, cmd))
        need(at + cmdsize <= 32 + sizeofcmds, 'load command %#x past sizeofcmds' % cmd)
        need(cmd in KNOWN, 'unexpected load command %#x' % cmd)
        cmds.append((cmd, at, cmdsize))
        at += cmdsize
    need(at == 32 + sizeofcmds, 'sizeofcmds %d != sum of cmdsize %d' % (sizeofcmds, at - 32))

    def one(kind):
        found = [c for c in cmds if c[0] == kind]
        need(len(found) == 1, 'expected exactly one load command %#x, found %d' % (kind, len(found)))
        return found[0]

    # 段与节。
    segs = []
    sections = []
    for cmd, at, cmdsize in cmds:
        if cmd != LC_SEGMENT_64:
            continue
        name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, sflags = struct.unpack_from('<16sQQQQiiII', data, at + 8)
        name = name.rstrip(b'\0').decode()
        need(cmdsize == 72 + 80 * nsects, 'segment %s cmdsize does not match nsects' % name)
        seg = dict(name=name, vm=vmaddr, vmsize=vmsize, off=fileoff, filesize=filesize, maxprot=maxprot, initprot=initprot, flags=sflags, index=len(segs))
        segs.append(seg)
        for k in range(nsects):
            s_at = at + 72 + 80 * k
            sect, segname, addr, ssize, offset, align, reloff, nreloc, sfl, r1, r2, r3 = struct.unpack_from('<16s16sQQIIIIIIII', data, s_at)
            sec = dict(name=sect.rstrip(b'\0').decode(), seg=segname.rstrip(b'\0').decode(), addr=addr, size=ssize, off=offset,
                       align=align, flags=sfl, reserved1=r1, segment=seg, number=len(sections) + 1)
            need(sec['seg'] == name, 'section %s names segment %s inside %s' % (sec['name'], sec['seg'], name))
            need(nreloc == 0 and reloff == 0, 'section %s has relocations' % sec['name'])
            sections.append(sec)
    need(len(segs) >= 3, 'too few segments')
    zero = segs[0]
    need(zero['name'] == '__PAGEZERO' and zero['vm'] == 0 and zero['vmsize'] >= 1 << 32, '__PAGEZERO must come first and cover 4 GiB')
    need(zero['filesize'] == 0 and zero['initprot'] == 0 and zero['maxprot'] == 0, '__PAGEZERO must be unmapped and inaccessible')
    text = [s for s in segs if s['name'] == '__TEXT']
    need(len(text) == 1, 'exactly one __TEXT')
    text = text[0]
    need(text['off'] == 0 and text['filesize'] > 32 + sizeofcmds, '__TEXT must start at file offset 0 and hold the header')
    need(text['maxprot'] & 2 == 0 and text['initprot'] == 5, '__TEXT must be r-x and never writable')
    linkedit = segs[-1]
    need(linkedit['name'] == '__LINKEDIT', '__LINKEDIT must be the last segment')
    need(linkedit['off'] + linkedit['filesize'] == size, '__LINKEDIT must end at end of file')
    for seg in segs[1:]:
        need(seg['vm'] % SEG_ALIGN == 0 and seg['off'] % SEG_ALIGN == 0, 'segment %s not 16 KiB aligned' % seg['name'])
        need(seg['vmsize'] % SEG_ALIGN == 0 and seg['vmsize'] >= seg['filesize'], 'segment %s vmsize' % seg['name'])
        need(seg['off'] + seg['filesize'] <= size, 'segment %s past end of file' % seg['name'])
        for prot in (seg['maxprot'], seg['initprot']):
            need(not (prot & 2 and prot & 4), 'segment %s is writable and executable (W^X)' % seg['name'])
        need(seg['initprot'] & ~seg['maxprot'] == 0, 'segment %s initprot exceeds maxprot' % seg['name'])
    ordered = segs[1:]
    for a, b in zip(ordered, ordered[1:]):
        need(a['vm'] + a['vmsize'] <= b['vm'], 'segments %s and %s overlap in memory' % (a['name'], b['name']))
        need(a['off'] + a['filesize'] <= b['off'], 'segments %s and %s overlap in the file' % (a['name'], b['name']))
    for sec in sections:
        seg = sec['segment']
        need(seg['vm'] <= sec['addr'] and sec['addr'] + sec['size'] <= seg['vm'] + seg['vmsize'], 'section %s outside its segment' % sec['name'])
        need(sec['align'] <= 14 and sec['addr'] % (1 << sec['align']) == 0, 'section %s misaligned' % sec['name'])
        kind = sec['flags'] & 0xFF
        if kind in (S_ZEROFILL, S_THREAD_LOCAL_ZEROFILL):
            need(sec['off'] == 0, 'zerofill section %s has a file offset' % sec['name'])
        elif sec['size']:
            need(sec['off'] == seg['off'] + (sec['addr'] - seg['vm']), 'section %s file offset does not match its address' % sec['name'])
            need(sec['off'] + sec['size'] <= seg['off'] + seg['filesize'], 'section %s past its segment file range' % sec['name'])
    textsec = [s for s in sections if s['name'] == '__text' and s['seg'] == '__TEXT']
    need(len(textsec) == 1 and textsec[0]['flags'] & S_ATTR_PURE_INSTRUCTIONS, '__TEXT,__text with S_ATTR_PURE_INSTRUCTIONS')
    textsec = textsec[0]

    def seg_of_addr(addr, width):
        for seg in segs[1:]:
            if seg['vm'] <= addr and addr + width <= seg['vm'] + seg['vmsize']:
                return seg
        return None

    # 入口、dyld、库、版本、UUID。
    _, at, _ = one(LC_MAIN)
    entryoff, stack = struct.unpack_from('<QQ', data, at + 8)
    need(textsec['off'] <= entryoff < textsec['off'] + textsec['size'], 'LC_MAIN entry outside __text')
    _, at, cmdsize = one(LC_LOAD_DYLINKER)
    need(cstr(data, at + struct.unpack_from('<I', data, at + 8)[0], at + cmdsize) == '/usr/lib/dyld', 'dylinker must be /usr/lib/dyld')
    dylibs = []
    for cmd, at, cmdsize in cmds:
        if cmd == LC_LOAD_DYLIB:
            dylibs.append(cstr(data, at + struct.unpack_from('<I', data, at + 8)[0], at + cmdsize))
    need(dylibs and dylibs[0] == '/usr/lib/libSystem.B.dylib', 'first dylib must be libSystem')
    _, at, _ = one(LC_BUILD_VERSION)
    platform, minos, sdk, ntools = struct.unpack_from('<IIII', data, at + 8)
    need(platform == 1, 'LC_BUILD_VERSION platform is not macOS')
    need(minos >= 0x0D0000, 'minos below 13.0')
    _, at, _ = one(LC_UUID)
    uuid = data[at + 8:at + 24]
    need(any(uuid), 'UUID is zero')

    # 修补：rebase 与 bind 逐条解码。
    _, at, _ = one(LC_DYLD_INFO_ONLY)
    rb_off, rb_size, bd_off, bd_size, wb_off, wb_size, lb_off, lb_size, ex_off, ex_size = struct.unpack_from('<10I', data, at + 8)
    le_lo, le_hi = linkedit['off'], linkedit['off'] + linkedit['filesize']
    for off, n, what in [(rb_off, rb_size, 'rebase'), (bd_off, bd_size, 'bind'), (ex_off, ex_size, 'export')]:
        need(n == 0 or (le_lo <= off and off + n <= le_hi), '%s info outside __LINKEDIT' % what)
    need(wb_size == 0 and lb_size == 0, 'weak or lazy binds present (only non-lazy binds are emitted)')

    def writable_target(seg_index, offset, what):
        need(0 < seg_index < len(segs), '%s names segment %d' % (what, seg_index))
        seg = segs[seg_index]
        need(seg['initprot'] & 2, '%s into non-writable segment %s' % (what, seg['name']))
        need(offset + 8 <= seg['vmsize'], '%s past segment %s' % (what, seg['name']))
        return seg['vm'] + offset

    rebases = []
    p, end = rb_off, rb_off + rb_size
    seg_index, offset = -1, 0
    while p < end:
        b = data[p]
        p += 1
        op, imm = b & 0xF0, b & 0x0F
        if op == 0x00:
            break
        elif op == 0x10:
            need(imm == 1, 'rebase type %d is not POINTER' % imm)
        elif op == 0x20:
            seg_index = imm
            offset, p = uleb(data, p, end)
        elif op == 0x30:
            v, p = uleb(data, p, end)
            offset += v
        elif op == 0x40:
            offset += imm * 8
        elif op == 0x50:
            for _ in range(imm):
                rebases.append(writable_target(seg_index, offset, 'rebase'))
                offset += 8
        elif op == 0x60:
            n, p = uleb(data, p, end)
            for _ in range(n):
                rebases.append(writable_target(seg_index, offset, 'rebase'))
                offset += 8
        elif op == 0x70:
            rebases.append(writable_target(seg_index, offset, 'rebase'))
            v, p = uleb(data, p, end)
            offset += 8 + v
        elif op == 0x80:
            n, p = uleb(data, p, end)
            skip, p = uleb(data, p, end)
            for _ in range(n):
                rebases.append(writable_target(seg_index, offset, 'rebase'))
                offset += 8 + skip
        else:
            raise Bad('unknown rebase opcode %#x' % b)
    binds = []
    p, end = bd_off, bd_off + bd_size
    seg_index, offset, ordinal, symbol = -1, 0, 0, None
    while p < end:
        b = data[p]
        p += 1
        op, imm = b & 0xF0, b & 0x0F
        if op == 0x00:
            break
        elif op == 0x10:
            ordinal = imm
        elif op == 0x20:
            ordinal, p = uleb(data, p, end)
        elif op == 0x40:
            symbol = cstr(data, p, end)
            p += len(symbol.encode()) + 1
        elif op == 0x50:
            need(imm == 1, 'bind type %d is not POINTER' % imm)
        elif op == 0x60:
            _, p = sleb(data, p, end)
            raise Bad('bind addend is not used by the writer')
        elif op == 0x70:
            seg_index = imm
            offset, p = uleb(data, p, end)
        elif op == 0x80:
            v, p = uleb(data, p, end)
            offset += v
        elif op == 0x90:
            need(symbol, 'bind without a symbol')
            need(1 <= ordinal <= len(dylibs), 'bind ordinal %d out of range' % ordinal)
            binds.append((writable_target(seg_index, offset, 'bind'), symbol, ordinal))
            offset += 8
        else:
            raise Bad('unexpected bind opcode %#x' % b)
    addrs = [a for a, _, _ in binds]
    need(len(set(addrs)) == len(addrs), 'two binds to one address')
    need(not set(addrs) & set(rebases), 'an address is both rebased and bound')
    need(len(set(rebases)) == len(rebases), 'duplicate rebase')

    # 符号表与间接符号表。
    _, at, _ = one(LC_SYMTAB)
    symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', data, at + 8)
    need(le_lo <= symoff and symoff + 16 * nsyms <= le_hi, 'symbol table outside __LINKEDIT')
    need(le_lo <= stroff and stroff + strsize <= le_hi, 'string table outside __LINKEDIT')
    syms = []
    for k in range(nsyms):
        strx, ntype, nsect, ndesc, nvalue = struct.unpack_from('<IBBHQ', data, symoff + 16 * k)
        need(strx < strsize, 'symbol %d name outside string table' % k)
        name = cstr(data, stroff + strx, stroff + strsize)
        kind = ntype & 0x0E
        if kind == 0x0E:
            need(1 <= nsect <= len(sections), 'symbol %s section %d' % (name, nsect))
            sec = sections[nsect - 1]
            if name == '__mh_execute_header':
                # ld64 的惯例：记在第一个节，值为 Mach-O 头（__TEXT 起点），在 __text 之前。
                need(nvalue == text['vm'], '__mh_execute_header is not the __TEXT start')
            else:
                need(sec['addr'] <= nvalue <= sec['addr'] + sec['size'], 'symbol %s outside its section %s' % (name, sec['name']))
        syms.append((name, ntype, nsect, ndesc, nvalue))
    _, at, _ = one(LC_DYSYMTAB)
    f = struct.unpack_from('<18I', data, at + 8)
    ilocal, nlocal, iext, next_, iundef, nundef = f[:6]
    indoff, nind = f[12], f[13]
    need(ilocal == 0 and iext == nlocal and iundef == iext + next_ and iundef + nundef == nsyms, 'dysymtab ranges do not partition the symbol table')
    for name, ntype, nsect, ndesc, nvalue in syms[:nlocal]:
        need(not ntype & 1, 'local symbol %s is external' % name)
    for name, ntype, nsect, ndesc, nvalue in syms[iext:iext + next_]:
        need(ntype & 1 and ntype & 0x0E == 0x0E, 'extdef %s is not an external definition' % name)
    for name, ntype, nsect, ndesc, nvalue in syms[iundef:]:
        need(ntype == 1, 'undefined symbol %s type %#x' % (name, ntype))
        need(1 <= ndesc >> 8 <= len(dylibs), 'undefined symbol %s library ordinal %d' % (name, ndesc >> 8))
    need(nind == 0 or (le_lo <= indoff and indoff + 4 * nind <= le_hi), 'indirect table outside __LINKEDIT')
    indirect = struct.unpack_from('<%dI' % nind, data, indoff) if nind else ()
    bound = {a: (s, o) for a, s, o in binds}
    for sec in sections:
        if sec['flags'] & 0xFF == S_NON_LAZY_SYMBOL_POINTERS:
            need(sec['size'] % 8 == 0 and sec['reserved1'] + sec['size'] // 8 <= nind, '%s indirect range' % sec['name'])
            for k in range(sec['size'] // 8):
                si = indirect[sec['reserved1'] + k]
                need(si < nsyms, 'indirect entry out of range')
                name, _, _, ndesc, _ = syms[si]
                slot = sec['addr'] + 8 * k
                need(bound.get(slot) == (name, ndesc >> 8), 'GOT slot %#x is not bound to %s' % (slot, name))
        if sec['flags'] & 0xFF == S_THREAD_LOCAL_VARIABLES:
            need(sec['size'] % 24 == 0, 'TLV descriptors must be 24 bytes each')
            for k in range(sec['size'] // 24):
                need(bound.get(sec['addr'] + 24 * k, (None,))[0] == '__tlv_bootstrap', 'TLV descriptor %d thunk is not bound to __tlv_bootstrap' % k)
    need(flags & 0x800000 or not any(s['flags'] & 0xFF == S_THREAD_LOCAL_VARIABLES for s in sections), 'TLV sections without MH_HAS_TLV_DESCRIPTORS')
    undef_names = {s[0] for s in syms[iundef:]}
    for _, symbol, _ in binds:
        need(symbol in undef_names, 'bound symbol %s is not an undefined symbol' % symbol)

    # function starts。
    _, at, _ = one(LC_FUNCTION_STARTS)
    fs_off, fs_size = struct.unpack_from('<II', data, at + 8)
    need(le_lo <= fs_off and fs_off + fs_size <= le_hi, 'function starts outside __LINKEDIT')
    p, end, addr, starts = fs_off, fs_off + fs_size, 0, 0
    # 带函数符号的产物（v052-image-symbols，未 --strip）：每个 function start 恰是某个 __text 本地符号的地址。
    named = {v for _, t, ns, _, v in syms[:nlocal] if t & 0x0E == 0x0E and sections[ns - 1] is textsec}
    while p < end:
        delta, p = uleb(data, p, end)
        if delta == 0:
            break
        addr += delta
        need(textsec['off'] <= addr < textsec['off'] + textsec['size'], 'function start %#x outside __text' % addr)
        need(cpu != CPU_ARM64 or addr % 4 == 0, 'function start %#x not 4-byte aligned' % addr)
        need(not named or text['vm'] + addr in named, 'function start %#x has no local symbol' % addr)
        starts += 1
    a64 = check_a64(data, textsec, sections, entryoff) if cpu == CPU_ARM64 else {}

    # 代码签名。
    _, at, _ = one(LC_CODE_SIGNATURE)
    sig_off, sig_size = struct.unpack_from('<II', data, at + 8)
    need(sig_off + sig_size == size and le_lo <= sig_off, 'code signature must be the last thing in __LINKEDIT')
    smagic, slength, scount = struct.unpack_from('>III', data, sig_off)
    need(smagic == 0xFADE0CC0 and slength <= sig_size and scount >= 1, 'bad SuperBlob')
    cd_at = None
    for k in range(scount):
        btype, boff = struct.unpack_from('>II', data, sig_off + 12 + 8 * k)
        if btype == 0:
            cd_at = sig_off + boff
    need(cd_at is not None, 'no CodeDirectory')
    (cmagic, clen, version, cflags, hashoff, identoff, nspecial, ncode, codelimit, hsize, htype, platform_, pshift,
     spare2, scatter, team, spare3, codelimit64, eseg_base, eseg_limit, eseg_flags) = struct.unpack_from('>9I4B4I4Q', data, cd_at)
    need(cmagic == 0xFADE0C02 and version >= 0x20400, 'bad CodeDirectory header')
    need(cflags & 0x20002 == 0x20002, 'CodeDirectory is not adhoc and linker-signed')
    need(htype == 2 and hsize == 32 and pshift == 12, 'CodeDirectory must use SHA-256 over 4 KiB pages')
    need(codelimit == sig_off and ncode == (codelimit + 4095) // 4096, 'codeLimit / nCodeSlots')
    need(eseg_base == text['off'] and eseg_limit == text['filesize'] and eseg_flags & 1, 'execSeg does not describe __TEXT')
    ident = cstr(data, cd_at + identoff, cd_at + clen)
    for k in range(ncode):
        page = data[4096 * k:min(4096 * (k + 1), codelimit)]
        want = data[cd_at + hashoff + 32 * k:cd_at + hashoff + 32 * (k + 1)]
        need(hashlib.sha256(page).digest() == want, 'page %d hash mismatch' % k)
    return dict(arch='arm64' if cpu == CPU_ARM64 else 'x86_64', size=size, segments=len(segs) - 1, sections=len(sections),
                rebases=len(rebases), binds=len(binds), symbols=nsyms, locals=nlocal, starts=starts, pages=ncode, identifier=ident, **a64,
                imports=sorted(s for _, s, _ in binds))


def sx(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def check_a64(data, textsec, sections, entryoff):
    """arm64 的 __text 逐字解码：分支目标、ADRP 配对与合成地址、经 __got 的取数、写只读段。返回计数。"""
    need(textsec['addr'] % 4 == 0 and textsec['size'] % 4 == 0 and entryoff % 4 == 0, '__text or LC_MAIN not 4-byte aligned')
    lo, hi = textsec['addr'], textsec['addr'] + textsec['size']

    def section_of(addr, width):
        for sec in sections:
            if sec['addr'] <= addr and addr + width <= sec['addr'] + sec['size']:
                return sec
        raise Bad('ADRP pair at %#x addresses %#x outside every section' % (pc, addr))

    n = textsec['size'] // 4
    words = struct.unpack_from('<%dI' % n, data, textsec['off'])
    branches = pairs = 0
    for i, w in enumerate(words):
        pc = lo + 4 * i
        target = None
        if w & 0x7C000000 == 0x14000000:  # B、BL
            target = pc + 4 * sx(w & 0x3FFFFFF, 26)
        elif w & 0xFF000010 == 0x54000000 or w & 0x7E000000 == 0x34000000:  # B.cond、CBZ／CBNZ
            target = pc + 4 * sx((w >> 5) & 0x7FFFF, 19)
        elif w & 0x7E000000 == 0x36000000:  # TBZ／TBNZ
            target = pc + 4 * sx((w >> 5) & 0x3FFF, 14)
        if target is not None:
            need(lo <= target < hi, 'branch at %#x targets %#x outside __text' % (pc, target))
            branches += 1
            continue
        if w & 0x9F000000 != 0x90000000:  # 只看 ADRP
            continue
        rd = w & 31
        page = (pc & ~0xFFF) + (sx((((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3), 21) << 12)
        need(i + 1 < n, 'ADRP at %#x is the last word' % pc)
        nxt = words[i + 1]
        need((nxt >> 5) & 31 == rd, 'ADRP at %#x is not followed by a use of x%d' % (pc, rd))
        if nxt & 0xFFC00000 == 0x91000000:  # ADD Xd, Xn, #imm12（不移位）
            section_of(page + ((nxt >> 10) & 0xFFF), 1)
        elif nxt & 0x3B000000 == 0x39000000:  # 取存，无符号偏移
            size, vector, opc = nxt >> 30, (nxt >> 26) & 1, (nxt >> 22) & 3
            scale = 4 if vector and opc & 2 and size == 0 else size
            addr = page + (((nxt >> 10) & 0xFFF) << scale)
            sec = section_of(addr, 1 << scale)
            store = opc & 1 == 0 if vector else opc == 0
            need(not (store and (sec['segment']['flags'] & SG_READ_ONLY or not sec['segment']['initprot'] & 2)),
                 'store at %#x into read-only %s' % (pc + 4, sec['name']))
            if sec['flags'] & 0xFF == S_NON_LAZY_SYMBOL_POINTERS:
                need(nxt & 0xFFC00000 == 0xF9400000, 'access at %#x to %s is not a 64-bit LDR' % (pc + 4, sec['name']))
        else:
            raise Bad('ADRP at %#x is followed by %#010x, neither ADD nor a load/store' % (pc, nxt))
        pairs += 1
    return dict(branches=branches, adrp_pairs=pairs)


def commands(data):
    at = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, cmdsize = struct.unpack_from('<II', data, at)
        yield cmd, at
        at += cmdsize


def resign(d):
    """篡改代码页后重算 CodeDirectory 的页哈希，让负例只撞上要测的那一条核对。"""
    sig_off = [struct.unpack_from('<I', d, at + 8)[0] for cmd, at in commands(d) if cmd == LC_CODE_SIGNATURE][0]
    scount = struct.unpack_from('>I', d, sig_off + 8)[0]
    for k in range(scount):
        btype, boff = struct.unpack_from('>II', d, sig_off + 12 + 8 * k)
        if btype == 0:
            cd = sig_off + boff
            hashoff, ncode, codelimit = (struct.unpack_from('>I', d, cd + o)[0] for o in (16, 28, 32))
            for page in range(ncode):
                d[cd + hashoff + 32 * page:cd + hashoff + 32 * (page + 1)] = hashlib.sha256(bytes(d[4096 * page:min(4096 * (page + 1), codelimit)])).digest()
    return d


def a64_cases(data):
    """arm64 的负例：分支出 __text、ADRP 指到节外、经 __got 的取数改成存数；都重新签名。"""
    text = None
    for cmd, at in commands(data):
        if cmd == LC_SEGMENT_64:
            for k in range(struct.unpack_from('<I', data, at + 64)[0]):
                sect, _, addr, ssize, offset = struct.unpack_from('<16s16sQQI', data, at + 72 + 80 * k)
                if sect.rstrip(b'\0') == b'__text':
                    text = (addr, ssize, offset)
    addr, ssize, offset = text
    words = struct.unpack_from('<%dI' % (ssize // 4), data, offset)
    bl = next(i for i, w in enumerate(words) if w & 0xFC000000 == 0x94000000)
    adrp = next(i for i, w in enumerate(words) if w & 0x9F000000 == 0x90000000)
    got_ldr = next(i + 1 for i, w in enumerate(words) if w & 0x9F000000 == 0x90000000 and words[i + 1] & 0xFFC00000 == 0xF9400000)
    cases = []
    d = bytearray(data)
    struct.pack_into('<I', d, offset + 4 * bl, 0x94000000 | ((ssize // 4 - bl) & 0x3FFFFFF))  # 目标恰在 __text 之后
    cases.append(('branch retargeted past __text', resign(d), 'outside __text'))
    d = bytearray(data)
    imm = (1 << 20) - 1  # ADRP 的页偏移约 +4 GiB
    struct.pack_into('<I', d, offset + 4 * adrp, (words[adrp] & 0x9F00001F) | ((imm & 3) << 29) | ((imm >> 2) << 5))
    cases.append(('ADRP moved outside the image', resign(d), 'outside every section'))
    d = bytearray(data)
    struct.pack_into('<I', d, offset + 4 * got_ldr, words[got_ldr] & ~0x00400000)  # LDR → STR
    cases.append(('GOT load turned into a store', resign(d), 'into read-only'))
    return cases


def self_test(data):
    """负例：篡改副本后必须被查出，且由要测的那一条核对查出（核对错误文本）。"""
    good = check(data)
    cases = []
    # 1. 代码页改一个字节：签名哈希不符。
    entry = [struct.unpack_from('<Q', data, at + 8)[0] for cmd, at in commands(data) if cmd == LC_MAIN][0]
    d = bytearray(data)
    d[entry] ^= 0xFF
    cases.append(('code byte flipped', d, 'hash mismatch'))
    # 2. __TEXT 改成可写可执行。
    d = bytearray(data)
    for cmd, at in commands(data):
        if cmd == LC_SEGMENT_64 and data[at + 8:at + 14] == b'__TEXT':
            struct.pack_into('<ii', d, at + 56, 7, 7)
    cases.append(('__TEXT made writable', d, 'never writable'))
    # 3. 去掉 bind 信息（大小置零）：__got 的槽不再绑定。
    d = bytearray(data)
    for cmd, at in commands(data):
        if cmd == LC_DYLD_INFO_ONLY:
            struct.pack_into('<I', d, at + 20, 0)
    cases.append(('bind info removed', d, 'is not bound'))
    if good['arch'] == 'arm64':
        cases += a64_cases(data)
    failures = 0
    for name, mutated, needle in cases:
        try:
            check(bytes(mutated))
            print('FAIL negative case not detected:', name)
            failures += 1
        except Bad as e:
            if needle in str(e):
                print('PASS negative case detected:', name, '-', e)
            else:
                print('FAIL negative case caught by another check:', name, '-', e)
                failures += 1
    return good, failures


def main(argv):
    if not argv:
        print(__doc__)
        return 64
    if argv[0] == '--self-test':
        bad = 0
        for path in argv[1:]:
            data = open(path, 'rb').read()
            try:
                good, failures = self_test(data)
            except Bad as e:
                print('FAIL', path, e)
                bad += 1
                continue
            bad += failures
        return 1 if bad else 0
    bad = 0
    for path in argv:
        data = open(path, 'rb').read()
        try:
            info = check(data)
            print('PASS', path, ' '.join('%s=%s' % (k, v) for k, v in info.items() if k != 'imports'))
        except Bad as e:
            print('FAIL', path, e)
            bad += 1
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
