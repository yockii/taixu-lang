"""aarch64-windows 产物的独立 PE 核对：不经编译器的代码，按 PE 格式与 B18 的约定逐项读产物。

  python scripts/pe_arm64_check.py [--no-unwind] <产物.exe>…

--no-unwind 用于不带函数目录的手写映像（bootstrap/a64w_image_probe.tx）：没有异常目录不算错，有则照常核对。编译器的产物总带内核
的展开信息，不加此项。

核对项：DOS／PE 签名；COFF 头（机器号 0xAA64、可执行、未剥离重定位、大地址）；PE32+ 可选头（对齐、SizeOfImage、SizeOfHeaders、
DllCharacteristics 含 DYNAMIC_BASE｜NX_COMPAT｜HIGH_ENTROPY_VA——ARM64 不能关 ASLR）；节按地址排列、互不重叠、文件内容在文件内、
属性合乎各节（.text 可执行不可写，.rdata 只读，.data 可读写，.reloc 可丢弃）；入口在 .text；导入表（描述符、DLL 名、导入名表与
IAT 初值相同、提示名项）；基址重定位（块头、DIR64／ABSOLUTE 以外的类型即错、每处落在有文件内容的节里、所存的 VA 指向映像内）；
TLS 目录的四个 VA 都有 DIR64、索引槽在可写节；.text 里每个导入桩（adrp x16／ldr x16, [x16]／br x16）都指向 IAT 的槽；展开信息
（B18）：.pdata 在自己的只读节里、按函数起点升序、互不重叠、函数都在 .text 里、只用完整形式，每份 .xdata 在只读节里、头的版本
与异常处理位为 0、尾声范围在函数里，展开码逐个解开、按指令对照——序言码逆序对函数开头的指令，尾声码顺序对尾声的指令（ret 即 end），
每个码须与所对的指令相符（只认产物会用到的码与指令）；超过 1 MiB 的函数的后续分片码以 end_c 开头（幻影序言，不对指令）、须紧接
前一片，E＝1 而起点指向 end_c 表示本片没有尾声；.text 里每条 stp x29, x30, [sp, #-16]! 都须是某个 .pdata 函数的第一条指令。
退出码：0 全部通过，1 有不合。
"""
import struct
import sys

IMAGE_FILE_MACHINE_ARM64 = 0xAA64
DYNAMIC_BASE, NX_COMPAT, HIGH_ENTROPY_VA = 0x40, 0x100, 0x20
EXEC, READ, WRITE, DISCARD, CODE, IDATA = 0x20000000, 0x40000000, 0x80000000, 0x02000000, 0x20, 0x40
STP_FP_LR = 0xA9BF7BFD  # stp x29, x30, [sp, #-16]!
RET = 0xD65F03C0


def unwind_ops(codes, at):
    """从 at 起解开展开码直到 end（含）：[(名字, 参数, 起点)]；不认识的码返回 None。"""
    ops = []
    while at < len(codes):
        b0 = codes[at]
        b1 = codes[at + 1] if at + 1 < len(codes) else 0
        if b0 < 0x20:
            ops.append(('alloc', b0 * 16, at)); at += 1
        elif b0 < 0x40:
            ops.append(('save_r19r20_x', (b0 & 31) * 8, at)); at += 1
        elif 0x80 <= b0 < 0xC0:
            ops.append(('save_fplr_x', ((b0 & 63) + 1) * 8, at)); at += 1
        elif 0xC0 <= b0 < 0xC8:
            ops.append(('alloc', (((b0 & 7) << 8) | b1) * 16, at)); at += 2
        elif 0xCC <= b0 < 0xD0:
            ops.append(('save_regp_x', (19 + (((b0 & 3) << 2) | (b1 >> 6)), ((b1 & 63) + 1) * 8), at)); at += 2
        elif 0xD4 <= b0 < 0xD6:
            ops.append(('save_reg_x', (19 + (((b0 & 1) << 3) | (b1 >> 5)), ((b1 & 31) + 1) * 8), at)); at += 2
        elif b0 == 0xE0:
            ops.append(('alloc', ((codes[at + 1] << 16) | (codes[at + 2] << 8) | codes[at + 3]) * 16, at)); at += 4
        elif b0 == 0xE1:
            ops.append(('set_fp', 0, at)); at += 1
        elif b0 == 0xE2:
            ops.append(('add_fp', b1 * 8, at)); at += 2
        elif b0 == 0xE3:
            ops.append(('nop', 0, at)); at += 1
        elif b0 == 0xE4:
            ops.append(('end', 0, at))
            return ops
        elif b0 == 0xE5:
            ops.append(('end_c', 0, at)); at += 1
        else:
            return None
    return None


def stp_pre(r1, r2, off):
    return 0xA9800000 | (((-off // 8) & 127) << 15) | (r2 << 10) | (31 << 5) | r1


def ldp_post(r1, r2, off):
    return 0xA8C00000 | (((off // 8) & 127) << 15) | (r2 << 10) | (31 << 5) | r1


def sp_imm(word, sub):
    """sub／add sp, sp, #imm{, lsl #12} 的字节数，不是则 None。"""
    if (word & 0xFF8003FF) != (0xD10003FF if sub else 0x910003FF):
        return None
    return ((word >> 10) & 0xFFF) << (12 * ((word >> 22) & 1))


def op_matches(op, word, prolog, x15):
    name, arg = op[0], op[1]
    if name == 'alloc':
        if prolog and word == 0xCB2F73FF:
            return x15 is not None and x15 * 16 == arg
        return sp_imm(word, prolog) == arg
    if name == 'save_fplr_x':
        return word == (stp_pre(29, 30, arg) if prolog else ldp_post(29, 30, arg))
    if name == 'save_r19r20_x':
        return word == (stp_pre(19, 20, arg) if prolog else ldp_post(19, 20, arg))
    if name == 'save_regp_x':
        r, off = arg
        return word == (stp_pre(r, r + 1, off) if prolog else ldp_post(r, r + 1, off))
    if name == 'save_reg_x':
        r, off = arg
        if prolog:
            return word == (0xF8000C00 | (((-off) & 511) << 12) | (31 << 5) | r)
        return word == (0xF8400400 | ((off & 511) << 12) | (31 << 5) | r)
    if name == 'set_fp':
        return word == (0x910003FD if prolog else 0x910003BF)
    if name == 'add_fp':
        return word == ((0x910003FD if prolog else 0xD10003BF) | (arg << 10))
    if name == 'nop':
        # 栈探测的 mov／movk x15 与 bl __tx_chkstk。
        return prolog and ((word & 0xFF80001F) in (0xD280000F, 0xF280000F) or (word & 0xFC000000) == 0x94000000)
    if name == 'end':
        return not prolog and word == RET
    return False


def x15_before(p, start, at):
    """序言里 at 之前的 movz／movk x15 给出的值（栈探测），没有则 None。"""
    v = None
    for a in range(start, at, 4):
        w, = p.u('I', a)
        sh = 16 * ((w >> 21) & 3)
        if (w & 0xFF80001F) == 0xD280000F:
            v = ((w >> 5) & 0xFFFF) << sh
        elif (w & 0xFF80001F) == 0xF280000F and v is not None:
            v = (v & ~(0xFFFF << sh)) | (((w >> 5) & 0xFFFF) << sh)
    return v


class Pe:
    def __init__(self, path):
        self.b = open(path, 'rb').read()
        self.errors = []

    def fail(self, msg):
        self.errors.append(msg)

    def u(self, fmt, at):
        return struct.unpack_from('<' + fmt, self.b, at)

    def section_of(self, rva, size=1):
        for s in self.secs:
            if s['va'] <= rva and rva + size <= s['va'] + max(s['vs'], s['raw']):
                return s
        return None

    def file_at(self, rva, size=1):
        s = self.section_of(rva, size)
        if s is None or rva + size > s['va'] + s['raw']:
            return None
        return s['ptr'] + (rva - s['va'])

    def cstr(self, rva):
        at = self.file_at(rva)
        if at is None:
            return None
        end = self.b.find(b'\0', at)
        return None if end < 0 else self.b[at:end]


def check(path, need_unwind=True):
    p = Pe(path)
    b = p.b
    if b[:2] != b'MZ':
        p.fail('DOS 签名不是 MZ')
        return p.errors, ''
    pe, = p.u('I', 0x3C)
    if pe % 4 or b[pe:pe + 4] != b'PE\0\0':
        p.fail('PE 签名不对')
        return p.errors, ''
    machine, nsec, _, _, _, optsz, chars = p.u('HHIIIHH', pe + 4)
    if machine != IMAGE_FILE_MACHINE_ARM64:
        p.fail('机器号 0x%x 不是 0xAA64' % machine)
    if not chars & 0x2 or chars & 0x1 or not chars & 0x20:
        p.fail('COFF 特征 0x%x：须可执行、带重定位、大地址' % chars)
    o = pe + 24
    if optsz != 240 or p.u('H', o)[0] != 0x20B:
        p.fail('不是 240 字节的 PE32+ 可选头')
        return p.errors, ''
    entry, = p.u('I', o + 16)
    base, salign, falign = p.u('QII', o + 24)
    size_image, size_headers = p.u('II', o + 56)
    subsystem, dllch = p.u('HH', o + 68)
    ndirs, = p.u('I', o + 108)
    dirs = [p.u('II', o + 112 + 8 * i) for i in range(ndirs)]
    if salign != 0x1000 or falign < 0x200 or falign > salign or falign & (falign - 1):
        p.fail('对齐不合：节 0x%x、文件 0x%x' % (salign, falign))
    if base % 0x10000:
        p.fail('映像基址 0x%x 不是 64 KiB 的倍数' % base)
    want = DYNAMIC_BASE | NX_COMPAT | HIGH_ENTROPY_VA
    if dllch & want != want:
        p.fail('DllCharacteristics 0x%x 缺 DYNAMIC_BASE／NX_COMPAT／HIGH_ENTROPY_VA' % dllch)
    if subsystem not in (2, 3):
        p.fail('子系统 %d 不是 GUI(2) 或控制台(3)' % subsystem)
    sh = o + optsz
    p.secs = []
    for i in range(nsec):
        name, vs, va, raw, ptr = p.u('8sIIII', sh + 40 * i)
        ch, = p.u('I', sh + 40 * i + 36)
        p.secs.append({'name': name.rstrip(b'\0').decode('ascii', 'replace'), 'vs': vs, 'va': va, 'raw': raw, 'ptr': ptr, 'ch': ch})
    if sh + 40 * nsec > size_headers or size_headers % falign:
        p.fail('节表超出 SizeOfHeaders 或其未按文件对齐')
    prev_end = size_headers
    for s in p.secs:
        if s['va'] % salign or s['va'] < prev_end:
            p.fail('节 %s 的地址 0x%x 未对齐或与前节重叠' % (s['name'], s['va']))
        if s['raw'] % falign or (s['raw'] and (s['ptr'] % falign or s['ptr'] + s['raw'] > len(b))):
            p.fail('节 %s 的文件内容越界或未对齐' % s['name'])
        prev_end = s['va'] + ((max(s['vs'], s['raw']) + salign - 1) // salign) * salign
    if size_image != prev_end:
        p.fail('SizeOfImage 0x%x 不等于末节结束 0x%x' % (size_image, prev_end))
    expect = {'.text': (EXEC | READ | CODE, WRITE), '.rdata': (READ | IDATA, WRITE | EXEC), '.data': (READ | WRITE, EXEC),
              '.pdata': (READ | IDATA, WRITE | EXEC), '.reloc': (READ | DISCARD, WRITE | EXEC)}
    for s in p.secs:
        need, forbid = expect.get(s['name'], (0, 0))
        if s['name'] not in expect:
            p.fail('多出的节 %s' % s['name'])
        elif s['ch'] & need != need or s['ch'] & forbid:
            p.fail('节 %s 的属性 0x%x 不合' % (s['name'], s['ch']))
    text = next((s for s in p.secs if s['name'] == '.text'), None)
    if text is None or not text['va'] <= entry < text['va'] + text['vs']:
        p.fail('入口 0x%x 不在 .text 里' % entry)

    # 导入表。
    iat_lo = iat_hi = 0
    imp_rva, imp_size = dirs[1] if ndirs > 1 else (0, 0)
    if ndirs > 12:
        iat_lo, iat_size = dirs[12]
        iat_hi = iat_lo + iat_size
    if imp_size:
        k = 0
        while True:
            at = p.file_at(imp_rva + 20 * k, 20)
            if at is None:
                p.fail('导入描述符 %d 越界' % k)
                break
            ilt, _, _, name_rva, iat = p.u('IIIII', at)
            if ilt == 0 and name_rva == 0 and iat == 0:
                break
            dll = p.cstr(name_rva)
            if not dll or not all(32 < c < 127 for c in dll):
                p.fail('导入描述符 %d 的 DLL 名不对' % k)
            j = 0
            while True:
                a1, a2 = p.file_at(ilt + 8 * j, 8), p.file_at(iat + 8 * j, 8)
                if a1 is None or a2 is None:
                    p.fail('导入名表或 IAT 越界（%s）' % dll)
                    break
                v1, v2 = p.u('Q', a1)[0], p.u('Q', a2)[0]
                if v1 != v2:
                    p.fail('导入名表与 IAT 初值不同（%s 第 %d 项）' % (dll, j))
                if v1 == 0:
                    break
                if v1 >> 63:
                    p.fail('按序号导入（%s 第 %d 项）' % (dll, j))
                elif not p.cstr(v1 + 2):
                    p.fail('提示名项越界（%s 第 %d 项）' % (dll, j))
                if iat_hi and not (iat_lo <= iat + 8 * j < iat_hi):
                    p.fail('IAT 项不在数据目录 12 的范围里')
                j += 1
            k += 1
    else:
        p.fail('没有导入表')

    # 基址重定位。
    rel_rva, rel_size = dirs[5] if ndirs > 5 else (0, 0)
    dir64 = set()
    at = p.file_at(rel_rva, rel_size) if rel_size else None
    if at is None:
        p.fail('没有基址重定位（ARM64 映像须带 .reloc）')
    else:
        end = at + rel_size
        while at < end:
            page, size = p.u('II', at)
            if size < 8 or size % 4 or at + size > end or page % 0x1000:
                p.fail('基址重定位块头不对（页 0x%x、长 %d）' % (page, size))
                break
            for e in range(8, size, 2):
                w, = p.u('H', at + e)
                typ, off = w >> 12, w & 0xFFF
                if typ == 0:
                    continue
                if typ != 10:
                    p.fail('基址重定位类型 %d 不是 DIR64' % typ)
                    continue
                rva = page + off
                fa = p.file_at(rva, 8)
                if fa is None:
                    p.fail('DIR64 0x%x 不在有文件内容的节里' % rva)
                    continue
                v, = p.u('Q', fa)
                if not base <= v < base + size_image:
                    p.fail('DIR64 0x%x 处的值 0x%x 不在映像内' % (rva, v))
                dir64.add(rva)
            at += size

    # TLS 目录。
    if ndirs > 9 and dirs[9][1]:
        tls_rva = dirs[9][0]
        ta = p.file_at(tls_rva, 40)
        if ta is None:
            p.fail('TLS 目录越界')
        else:
            start, end_, index, callbacks = p.u('QQQQ', ta)
            for k2, name in enumerate(('StartAddressOfRawData', 'EndAddressOfRawData', 'AddressOfIndex', 'AddressOfCallBacks')):
                if tls_rva + 8 * k2 not in dir64:
                    p.fail('TLS 目录的 %s 没有 DIR64' % name)
            if not base <= start <= end_ < base + size_image:
                p.fail('TLS 模板范围不对')
            s = p.section_of(index - base, 4)
            if s is None or not s['ch'] & WRITE:
                p.fail('TLS 索引槽不在可写节里')

    # 导入桩：adrp x16 ／ ldr x16, [x16, #imm] ／ br x16。
    stubs = 0
    if text is not None and text['raw']:
        t0, tn = text['ptr'], min(text['raw'], text['vs'])
        for off in range(0, tn - 11, 4):
            w0, w1, w2 = p.u('III', t0 + off)
            if (w0 & 0x9F00001F) == 0x90000010 and (w1 & 0xFFC003FF) == 0xF9400210 and w2 == 0xD61F0200:
                site = text['va'] + off
                imm = (((w0 >> 5) & 0x7FFFF) << 2) | ((w0 >> 29) & 3)
                if imm & (1 << 20):
                    imm -= 1 << 21
                slot = ((site & ~0xFFF) + (imm << 12)) + ((w1 >> 10) & 0xFFF) * 8
                if not (iat_lo <= slot < iat_hi) or (slot - iat_lo) % 8:
                    p.fail('导入桩 0x%x 指向 0x%x，不是 IAT 的槽' % (site, slot))
                stubs += 1
    funcs = check_unwind(p, dirs, text, need_unwind)
    p.summary = 'sections %d, imports dir %d, dir64 %d, stubs %d, unwind %d' % (nsec, imp_size, len(dir64), stubs, funcs)
    return p.errors, p.summary


def check_unwind(p, dirs, text, need_unwind=True):
    ex_rva, ex_size = dirs[3] if len(dirs) > 3 else (0, 0)
    if not ex_size:
        if need_unwind:
            p.fail('没有异常目录（.pdata）')
        return 0
    ps = p.section_of(ex_rva, ex_size)
    at = p.file_at(ex_rva, ex_size)
    if ps is None or ps['name'] != '.pdata' or at is None or ex_size % 8:
        p.fail('异常目录不在 .pdata 节里或长度不是 8 的倍数')
        return 0
    if text is None:
        return 0
    t_lo, t_hi = text['va'], text['va'] + text['vs']
    starts = set()
    prev_end = t_lo
    for k in range(ex_size // 8):
        begin, x = p.u('II', at + 8 * k)
        where = '.pdata 第 %d 项（0x%x）' % (k, begin)
        if x & 3:
            p.fail(where + '用了压缩形式')
            continue
        if not t_lo <= begin < t_hi or begin % 4:
            p.fail(where + '的函数起点不在 .text 里')
            continue
        if begin < prev_end:
            p.fail(where + '未按地址升序或与前一函数重叠')
        prev_begin_end = prev_end
        xs = p.section_of(x, 8)
        xa = p.file_at(x, 8)
        if xs is None or xa is None or xs['ch'] & (WRITE | EXEC) or x % 4:
            p.fail(where + '的 .xdata 不在只读数据节里或未对齐')
            continue
        h, = p.u('I', xa)
        flen, vers, xbit, ebit, ecount, cwords = (h & 0x3FFFF) * 4, (h >> 18) & 3, (h >> 20) & 1, (h >> 21) & 1, (h >> 22) & 31, (h >> 27) & 31
        q = xa + 4
        if ecount == 0 and cwords == 0:
            h2, = p.u('I', q)
            ecount, cwords = h2 & 0xFFFF, (h2 >> 16) & 0xFF
            q += 4
        if vers or xbit or flen == 0 or begin + flen > t_hi:
            p.fail(where + '的 .xdata 头不对（版本 %d、异常处理 %d、函数长 %d）' % (vers, xbit, flen))
            continue
        prev_end = begin + flen
        starts.add(begin)
        scopes = []
        if not ebit:
            for _ in range(ecount):
                w, = p.u('I', q)
                scopes.append(((w & 0x3FFFF) * 4, w >> 22))
                q += 4
        codes = p.b[q:q + 4 * cwords]
        fa = p.file_at(begin, flen)
        if fa is None:
            p.fail(where + '的函数没有文件内容')
            continue
        prolog = unwind_ops(codes, 0)
        if prolog is None:
            p.fail(where + '的序言展开码不认识或没有 end')
            continue
        fragment = prolog[0][0] == 'end_c'
        if fragment:
            # 分片：本片没有序言，end_c 之后是幻影序言（从片里展开用），须紧接前一片。
            if begin != prev_begin_end:
                p.fail(where + '是没有序言的分片，却不紧接前一片')
            if any(op[0] == 'end_c' for op in prolog[1:]):
                p.fail(where + '的幻影序言里又有 end_c')
            prolog = []
        elif any(op[0] == 'end_c' for op in prolog):
            p.fail(where + '的序言码里有 end_c')
            continue
        n = max(len(prolog) - 1, 0)
        if 4 * n > flen:
            p.fail(where + '的序言长于函数')
            continue
        for i, op in enumerate(prolog[:-1]):
            ia = fa + 4 * (n - 1 - i)
            w, = p.u('I', ia)
            if not op_matches(op, w, True, x15_before(p, fa, ia)):
                p.fail(where + '的序言码 %s 与指令 0x%08x（+%d）不符' % (op[0], w, 4 * (n - 1 - i)))
        if ebit:
            ops = unwind_ops(codes, ecount)
            scopes = [] if ops is None or ops[0][0] == 'end_c' else [(flen - 4 * len(ops), ecount)]
            if ops is None:
                p.fail(where + '的尾声展开码不认识')
            elif ops[0][0] == 'end_c' and not (fragment and ecount == 0):
                p.fail(where + '的尾声起点指向 end_c，却不是没有序言的分片')
        for off, idx in scopes:
            ops = unwind_ops(codes, idx)
            if ops is None or any(op[0] == 'end_c' for op in ops) or off < 4 * n or off + 4 * len(ops) > flen:
                p.fail(where + '的尾声（+%d，码起点 %d）越界或展开码不认识' % (off, idx))
                continue
            for i, op in enumerate(ops):
                w, = p.u('I', fa + off + 4 * i)
                if not op_matches(op, w, False, None):
                    p.fail(where + '的尾声码 %s 与指令 0x%08x（+%d）不符' % (op[0], w, off + 4 * i))
    # 覆盖：每个建帧记录的序言首条指令都有 .pdata。
    t0, tn = text['ptr'], min(text['raw'], text['vs'])
    for off in range(0, tn - 3, 4):
        if p.u('I', t0 + off)[0] == STP_FP_LR and text['va'] + off not in starts:
            p.fail('0x%x 处建帧（stp x29, x30, [sp, #-16]!）却不是 .pdata 函数的起点' % (text['va'] + off))
    return len(starts)


def main():
    bad = 0
    paths = sys.argv[1:]
    need_unwind = '--no-unwind' not in paths
    for path in [a for a in paths if a != '--no-unwind']:
        errors, summary = check(path, need_unwind)
        if errors:
            bad = 1
            print('FAIL %s' % path)
            for e in errors:
                print('  ' + e)
        else:
            print('PASS %s (%s)' % (path, summary))
    return bad


if __name__ == '__main__':
    sys.exit(main())
