"""Production PE/ELF map contract, checked against independent binary fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
U64 = (1 << 64) - 1


def object_fields(mode, data):
    """独立解码出的可变异字段：(偏移, 字节宽)。ELF64 节头／符号／重定位，COFF 文件头／节头／符号／重定位。"""
    fields = []
    if mode == 'elf':
        fields += [(0x28, 8), (0x3a, 2), (0x3c, 2), (0x3e, 2)]
        shoff, shnum = struct.unpack_from('<Q', data, 0x28)[0], struct.unpack_from('<H', data, 0x3c)[0]
        for i in range(shnum):
            sh = shoff + 64 * i
            fields += [(sh + o, w) for o, w in [(0, 4), (4, 4), (8, 8), (24, 8), (32, 8), (40, 4), (44, 4), (48, 8), (56, 8)]]
            kind, off, size, entsize = struct.unpack_from('<I', data, sh + 4)[0], *struct.unpack_from('<QQ', data, sh + 24), \
                struct.unpack_from('<Q', data, sh + 56)[0]
            if kind == 2 and entsize == 24:  # SHT_SYMTAB
                for s in range(off, off + size, 24):
                    fields += [(s + o, w) for o, w in [(0, 4), (4, 1), (6, 2), (8, 8), (16, 8)]]
            if kind == 4 and entsize == 24:  # SHT_RELA
                for r in range(off, off + size, 24):
                    fields += [(r, 8), (r + 8, 8), (r + 16, 8)]
    else:
        fields += [(2, 2), (8, 4), (12, 4), (16, 2)]
        count, symptr, nsyms = struct.unpack_from('<H', data, 2)[0], *struct.unpack_from('<II', data, 8)
        for i in range(count):
            sec = 20 + 40 * i
            fields += [(sec + o, w) for o, w in [(8, 4), (12, 4), (16, 4), (20, 4), (24, 4), (32, 2), (36, 4)]]
            relptr, nrel = struct.unpack_from('<I', data, sec + 24)[0], struct.unpack_from('<H', data, sec + 32)[0]
            for r in range(relptr, relptr + 10 * nrel, 10):
                fields += [(r, 4), (r + 4, 4), (r + 8, 2)]
        for s in range(symptr, symptr + 18 * nsyms, 18):
            fields += [(s, 8), (s + 8, 4), (s + 12, 2), (s + 14, 2), (s + 16, 1), (s + 17, 1)]
    return [(o, w) for o, w in fields if o + w <= len(data)]


def mutate(rng, data, fields):
    """一处变异，返回可复现的描述（scripts/test-object-fuzz.py 的 F1）。"""
    pick = rng.random()
    if pick < 0.7:
        offset, width = rng.choice(fields)
        top = (1 << (8 * width)) - 1
        old = int.from_bytes(data[offset:offset + width], 'little')
        value = rng.choice([0, 1, top, top - 1, (old + 1) & top, (old - 1) & top, top >> 1, (top >> 1) + 1, rng.randrange(top + 1)])
        data[offset:offset + width] = value.to_bytes(width, 'little')
        return 'field@%d/%d=%#x' % (offset, width, value)
    if pick < 0.9:
        offset = rng.randrange(len(data))
        value = rng.randrange(256)
        data[offset] = value
        return 'byte@%d=%#x' % (offset, value)
    size = rng.randrange(len(data))
    del data[size:]
    return 'truncate@%d' % size
UNICODE = '太虚符号'.encode()
ESCAPED = b'quote"\\\nname'


def elf_object(second=False):
    # ELF64 relocatable, independent of the compiler's object writer.
    symbols = [(b'dup', 0, 1, 0, 0), (b'meta', 0, 5, 1, 0)]
    if second:
        symbols += [(b'edge', 0x10, 0, 0, 0)]
        rels = [(0, b'edge')]
    else:
        symbols += [(b'_start', 0x12, 1, 0, 1), (b'\x80', 0x12, 1, 0, 1),
                    (b'\x81', 0x12, 1, 1, 1), (UNICODE, 0x11, 2, 2, 1),
                    (ESCAPED, 0x11, 2, 3, 1), (b'zero', 0x10, 0xfff1, 0, 0),
                    (b'edge', 0x10, 0xfff1, U64, U64), (b'slots', 0x11, 3, 0, 24),
                    (b'zeros', 0x11, 4, 0, 37), (b'unused_undefined', 0x10, 0, 0, 0)]
        rels = [(0, b'\x80'), (8, b'edge'), (16, UNICODE)]
    strings = bytearray(b'\0'); table = bytearray(24); indices = {}
    for i, (name, info, section, value, size) in enumerate(symbols, 1):
        indices[name] = i; at = len(strings); strings.extend(name+b'\0')
        table.extend(struct.pack('<IBBHQQ', at, info, 0, section, value, size))
    names = [b'', b'.text', b'.rodata', b'.data', b'.bss', b'.meta',
             b'.strtab', b'.symtab', b'.shstrtab', b'.rela.data']
    shstrings = bytearray(); no = []
    for name in names:
        no.append(len(shstrings)); shstrings.extend(name+b'\0')
    payloads = [b'', b'\xc3\x90', bytes(range(16)), bytes(24), b'', b'ab', strings,
                table, shstrings, b''.join(struct.pack('<QQq', at, (indices[n] << 32) | 1, 0) for at, n in rels)]
    data = bytearray(64); headers = []; offsets = []
    for i, payload in enumerate(payloads):
        while len(data) % 8:
            data.append(0)
        at = len(data); data.extend(payload); offsets.append(at)
        kind = {0: 0, 4: 8, 6: 3, 7: 2, 8: 3, 9: 4}.get(i, 1)
        flags = {1: 6, 2: 2, 3: 3, 4: 3}.get(i, 0)
        headers.append((no[i], kind, flags, 0, at, 37 if i == 4 else len(payload),
                        {7: 6, 9: 7}.get(i, 0), {7: 3, 9: 3}.get(i, 0), 8, 24 if i in [7, 9] else 0))
    shoff = len(data)
    for header in headers:
        data.extend(struct.pack('<IIQQQQIIQQ', *header))
    data[:64] = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01'+bytes(9),
                            1, 62, 1, 0, 0, shoff, 0, 64, 0, 0, 64, len(headers), 8)
    return data, {'shoff': shoff, 'offsets': offsets, 'symbols': indices}


def coff_object(second=False):
    sections = [(b'.text', b'\xc3\x90', 0), (b'.rdata', bytes(range(16)), 0),
                (b'.data', bytes(32), 0), (b'.bss', b'', 37)]
    symbols = [(b'.file', 0, -2, 103, 1), (b'dup', 0, 1, 3, 0)]
    if second:
        sections = [(b'.text$x', b'\xc3\x90', 0), (b'.data$x', bytes(8), 0)]
        symbols += [(b'co_tail', 1, 1, 2, 0), (b'dead', 0, 2, 3, 0)]
        rels = []
    else:
        symbols += [(b'co_root', 0, 1, 2, 0), (b'\x80', 0, 1, 2, 0), (b'\x81', 1, 1, 2, 0),
                    (UNICODE, 2, 2, 2, 0), (ESCAPED, 3, 2, 2, 0), (b'absolute', 0xffffffff, -1, 2, 0),
                    (b'slots', 0, 3, 2, 0), (b'zeros', 0, 4, 2, 0), (b'co_tail', 0, 0, 2, 0),
                    (b'unused_undefined', 0, 0, 2, 0)]
        rels = [(0, b'\x80'), (8, UNICODE), (16, b'dup'), (24, b'co_tail')]
    strings = bytearray(bytes(4)); table = bytearray(); indices = {}
    for name, value, section, storage, aux in symbols:
        indices[name] = len(table)//18
        if len(name) > 8:
            encoded = struct.pack('<II', 0, len(strings)); strings.extend(name+b'\0')
        else:
            encoded = name.ljust(8, b'\0')
        table.extend(struct.pack('<8sIhHBB', encoded, value, section, 0, storage, aux))
        table.extend(bytes(18*aux))
    strings[:4] = struct.pack('<I', len(strings))
    data = bytearray(20+40*len(sections)); headers = []; offsets = []
    for i, (name, payload, bss) in enumerate(sections):
        at = len(data); data.extend(payload); offsets.append(at)
        reloc_at = len(data)
        relocations = rels if i == 2 else []
        for where, target in relocations:
            data.extend(struct.pack('<IIH', where, indices[target], 1))
        headers.append(struct.pack('<8sIIIIIIHHI', name.ljust(8, b'\0'), bss, 0, len(payload), at,
                                   reloc_at, 0, len(relocations), 0, 0x00500000))
    symptr = len(data); data.extend(table); data.extend(strings)
    data[:20] = struct.pack('<HHIIIHH', 0x8664, len(sections), 0, symptr, len(table)//18, 0, 0)
    data[20:20+40*len(sections)] = b''.join(headers)
    return data, {'symptr': symptr, 'symbols': indices, 'offsets': offsets}


def image_layout(image):
    """Read the actual output headers, not the map's own section descriptions."""
    if image[:4] == b'\xcf\xfa\xed\xfe':
        # Mach-O 64: the four producer sections (zero-fill ones have no file bytes) and LC_MAIN.
        ncmds = struct.unpack_from('<I', image, 16)[0]; at = 32; base = entry = None; found = {}
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', image, at)
            if cmd == 0x19:
                if image[at+8:at+24].rstrip(b'\0') == b'__TEXT': base = struct.unpack_from('<Q', image, at+24)[0]
                for k in range(struct.unpack_from('<I', image, at+64)[0]):
                    h = at+72+80*k; addr, length, off = struct.unpack_from('<QQI', image, h+32)
                    zero = struct.unpack_from('<I', image, h+64)[0] & 0xff in (1, 0x12)
                    found[image[h:h+16].rstrip(b'\0')] = (addr, None if zero else off, 0 if zero else length, length)
            if cmd == 0x80000028: entry = struct.unpack_from('<Q', image, at+8)[0]
            at += size
        return base, base+entry, [found[n] for n in [b'__text', b'__const', b'__data', b'__bss']]
    if image[:4] == b'\x7fELF':
        entry, phoff = struct.unpack_from('<QQ', image, 24)
        count = struct.unpack_from('<H', image, 56)[0]; sections = []
        for i in range(count):
            kind, flags, off, va, _, file, memory, _ = struct.unpack_from('<IIQQQQQQ', image, phoff+56*i)
            if kind == 1:
                sections.append((va, off, file, memory))
        return 0x400000, entry, sections
    pe = struct.unpack_from('<I', image, 60)[0]; opt = pe+24
    base = struct.unpack_from('<Q', image, opt+24)[0]
    entry = base+struct.unpack_from('<I', image, opt+16)[0]
    count = struct.unpack_from('<H', image, pe+6)[0]
    at = opt+struct.unpack_from('<H', image, pe+20)[0]; sections = []
    for i in range(count):
        memory, rva, file, off = struct.unpack_from('<IIII', image, at+40*i+8)
        sections.append((base+rva, off, file, memory))
    return base, entry, sections


def file_at(sections, va):
    return next(off+va-start for start, off, file, _ in sections if start <= va < start+file)


def symbol_name(f):
    """产物函数符号的期望名（v052-image-symbols）：程序函数 <模块>.<函数>，模块去掉开头的 ./ 与结尾的 .tx；其余为链接名。"""
    if f['origin'] != 'tx-generated':
        return f['link_name']
    module = f['module']
    if module == '<prelude>':
        return 'prelude.' + f['name']
    while module.startswith('./'):
        module = module[2:]
    if len(module) > 3 and module.endswith('.tx'):
        module = module[:-3]
    return module + '.' + f['name']


def elf_symbol_check(image, stripped, functions):
    """独立解析 ELF 节头与 .symtab：节头与符号表在全部 PT_LOAD 之后；每个函数恰有一个同名、同址、同大小的本地 STT_FUNC
    （大小为 0 的是函数表以外的代码标号，不计）；
    --strip 的产物没有节头，可装载部分与带符号的逐字节相同（只差 ELF 头的 e_shoff／e_shentsize／e_shnum／e_shstrndx）。"""
    phoff, shoff = struct.unpack_from('<QQ', image, 32)
    phnum, shentsize, shnum, shstrndx = struct.unpack_from('<HHHH', image, 56)
    loads = [struct.unpack_from('<IIQQQQQQ', image, phoff+56*i) for i in range(phnum)]
    end = max(off+file for kind, _, off, _, _, file, _, _ in loads if kind == 1)
    if shentsize != 64 or shnum == 0 or shoff < end:
        return False, ('section headers', shoff, shentsize, shnum, end)
    secs = [struct.unpack_from('<IIQQQQIIQQ', image, shoff+64*i) for i in range(shnum)]
    def text_at(off, at):
        return image[off+at:image.index(b'\0', off+at)].decode('utf8')
    names = [text_at(secs[shstrndx][4], h[0]) for h in secs]
    for name, (_, kind, flags, addr, off, size, *_rest) in zip(names, secs):
        if flags & 2 and kind != 8 and (addr != 0x400000+off or not any(o <= off and off+size <= o+f for k, _, o, _, _, f, _, _ in loads if k == 1)):
            return False, ('allocated section outside its load segment', name)
        if not flags & 2 and kind and off < end:
            return False, ('symbol tables overlap the loaded image', name)
    symtab = names.index('.symtab'); text = names.index('.text')
    _, _, _, _, off, size, link, info, _, entsize = secs[symtab]
    strings = secs[link][4]
    syms = [struct.unpack_from('<IBBHQQ', image, off+24*i) for i in range(size//24)]
    if entsize != 24 or info != len(syms) or syms[0] != (0, 0, 0, 0, 0, 0):
        return False, ('symtab header', entsize, info, len(syms))
    tlo, tsize = secs[text][3], secs[text][5]
    funcs = {}
    for name, sinfo, _, shndx, value, size in syms[1:]:
        if sinfo >> 4 != 0:
            return False, ('non-local symbol', text_at(strings, name))
        if sinfo & 15 == 2:
            if shndx != text or not (tlo <= value and value+size <= tlo+tsize):
                return False, ('function symbol outside .text', text_at(strings, name))
            if size == 0:
                continue
            funcs.setdefault((text_at(strings, name), value, size), 0)
            funcs[(text_at(strings, name), value, size)] += 1
    want = [(symbol_name(f), int(f['link_va'], 16), int(f['bytes'])) for f in functions]
    missing = [w for w in want if funcs.get(w) != 1]
    if missing or len(funcs) != len(set(want)) or sum(funcs.values()) != len(want):
        return False, ('functions', missing[:5], len(funcs), len(want))
    if not stripped:
        return False, 'no --strip product'
    s_shoff = struct.unpack_from('<Q', stripped, 40)[0]; s_shnum = struct.unpack_from('<H', stripped, 60)[0]
    masked = lambda b: b[:40] + b[48:58] + b[64:end]
    if s_shoff or s_shnum or len(stripped) != end or masked(stripped) != masked(image):
        return False, ('--strip product', s_shoff, s_shnum, len(stripped), end)
    return True, len(want)


def macho_symbol_check(image, stripped, functions):
    """独立解析 Mach-O 的本地符号：每个函数有一个同名同址的 __text 本地符号；--strip 的产物没有本地符号，__text、__const、
    __data 的地址与内容与带符号的相同。"""
    def parse(data):
        ncmds = struct.unpack_from('<I', data, 16)[0]; at = 32; secs = {}; symtab = dysym = None; n = 0
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd == 0x19:
                for k in range(struct.unpack_from('<I', data, at+64)[0]):
                    h = at+72+80*k; n += 1
                    addr, length, off = struct.unpack_from('<QQI', data, h+32)
                    secs[data[h:h+16].rstrip(b'\0').decode()] = (n, addr, data[off:off+length] if off else b'')
            if cmd == 0x2: symtab = struct.unpack_from('<IIII', data, at+8)
            if cmd == 0xB: dysym = struct.unpack_from('<6I', data, at+8)
            at += size
        symoff, nsyms, stroff, strsize = symtab
        locals_ = []
        for i in range(dysym[1]):
            strx, ntype, nsect, _, value = struct.unpack_from('<IBBHQ', data, symoff+16*(dysym[0]+i))
            locals_.append((data[stroff+strx:data.index(b'\0', stroff+strx)].decode('utf8'), ntype, nsect, value))
        return secs, locals_
    secs, locals_ = parse(image)
    text = secs['__text'][0]
    have = {}
    for name, ntype, nsect, value in locals_:
        if ntype & 1:
            return False, ('external symbol among locals', name)
        if nsect == text:
            have[(name, value)] = have.get((name, value), 0) + 1
    want = [(symbol_name(f), int(f['link_va'], 16)) for f in functions]
    missing = [w for w in want if have.get(w) != 1]
    if missing:
        return False, ('functions', missing[:5], len(want))
    if not stripped:
        return False, 'no --strip product'
    s_secs, s_locals = parse(stripped)
    same = all(s_secs[k][1:] == secs[k][1:] for k in ['__text', '__const', '__data'])
    if s_locals or not same:
        return False, ('--strip product', len(s_locals), same)
    return True, len(want)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', required=True, type=Path)
    ap.add_argument('--work', required=True, type=Path)
    a = ap.parse_args(); compiler = a.compiler.resolve(); work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True); rows = []

    def check(name, ok, detail=''):
        rows.append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:1500]})
        print('PASS' if ok else 'FAIL', name, '' if ok else str(detail)[:500], flush=True)

    def run(*args, timeout=300):
        return subprocess.run([compiler, *map(str, args)], cwd=work, capture_output=True, timeout=timeout)

    cap = json.loads(run('capabilities', '--format=json').stdout)['capabilities']
    check('discovery', cap.get('link_map') == 'text,json' and cap.get('link_map_schema') == 'link-map/6'
          and 'link-map/6' in cap.get('schemas', []), cap)
    built = run('build', ROOT/'bootstrap/native_link_map_probe.tx', '--toolchain', '--leak-check', '--out', work/'probe')
    check('owned map probe build', built.returncode == 0, built.stderr)
    probe = work/'probe'/('native_link_map_probe'+('.exe' if os.name == 'nt' else ''))
    fixtures = {}
    for mode, factory in [('elf', elf_object), ('pe', coff_object)]:
        pair = []
        for second in [False, True]:
            data, meta = factory(second); path = work/(mode+str(int(second))+'.o'); path.write_bytes(data)
            pair.append((path, data, meta))
        fixtures[mode] = pair
    if built.returncode == 0:
        for mode in ['elf', 'elf-dynamic', 'pe']:
            pair = fixtures['elf' if mode.startswith('elf') else 'pe']; output = work/(mode+'.image')
            p = subprocess.run([probe, mode, output, pair[0][0], pair[1][0]], cwd=work, capture_output=True, timeout=120)
            (work/(mode+'.stdout')).write_bytes(p.stdout); (work/(mode+'.stderr')).write_bytes(p.stderr)
            check('native ownership/reentry/leak:'+mode, p.returncode == 0 and not p.stderr, (p.returncode, p.stderr, p.stdout[:500]))
            if p.returncode:
                continue
            doc = json.loads(p.stdout); x = doc['map']; syms = x['symbols']; image = output.read_bytes()
            base, entry, sections = image_layout(image)
            check('image identity and headers:'+mode, doc['schema'] == 'link-map/6' and doc['ok'] is True
                  and int(x['image']['bytes']) == len(image) and x['image']['sha256'] == hashlib.sha256(image).hexdigest()
                  and int(x['image']['entry'], 16) == entry and int(x['image']['preferred_base'], 16) == base)
            check('explicit unknowns/full rows:'+mode, x['image']['runtime_load_bias'] is None
                  and all(x['coverage'][k] is None for k in ['function_ranges', 'unwind', 'cpu_profile'])
                  and not x['coverage']['truncated'] and int(x['symbols_total']) == len(syms)
                  and [s['id'] for s in syms] == list(range(len(syms))))
            def named(name, obj=None):
                found = [s for s in syms if s['name_bytes'] == '0x'+name.hex() and (obj is None or s['object_index'] == obj)]
                return found[0] if found else {}
            check('lossless opaque identities:'+mode, named(b'\x80').get('name_utf8') is None
                  and named(b'\x81').get('name_utf8') is None and named(b'\x80')['value'] != named(b'\x81')['value'])
            check('unicode/control escaping:'+mode, named(UNICODE).get('name_utf8') == UNICODE.decode()
                  and named(ESCAPED).get('name_utf8') == ESCAPED.decode())
            dup = [s for s in syms if s['name_bytes'] == '0x'+b'dup'.hex()]
            check('local object provenance:'+mode, len(dup) == 2 and {s['object_index'] for s in dup} == {0, 1}
                  and all(s['binding'] == 'local' for s in dup) and dup[0]['value'] != dup[1]['value'])
            text = Path(str(output)+'.txt').read_text(encoding='utf8')
            check('human text same snapshot:'+mode, x['image']['sha256'] in text and '未截断' in text
                  and all(f"{s['id']}  {s['value_kind']}  {s['value'] or '-'}  {s['state']}  " in text for s in syms)
                  and '0x80' in text and '\\nname' in text)
            check('exact addresses and separate RVA:'+mode, all(re.fullmatch('0x[0-9a-f]{16}', s['value']) for s in syms if s['value'] is not None)
                  and all((s['image_relative_va'] is None if s['value_kind'] != 'link-va'
                           else int(s['image_relative_va'], 16) == (int(s['value'], 16)-base) & U64) for s in syms))
            slots = int(named(b'slots', 0)['value'], 16); at = file_at(sections, slots)
            targets = [b'\x80', b'edge', UNICODE] if mode.startswith('elf') else [b'\x80', UNICODE, b'dup', b'co_tail']
            check('independent relocation bytes:'+mode, all(struct.unpack_from('<Q', image, at+8*i)[0] == int(named(n)['value'], 16) for i, n in enumerate(targets)))
            zeros = int(named(b'zeros')['value'], 16)
            check('BSS virtual extent:'+mode, any(va <= zeros and zeros+37 <= va+memory for va, _, _, memory in sections)
                  and named(b'zeros')['input_section_offset'] == '0')
            check('undefined stays unknown:'+mode, named(b'unused_undefined')['value'] is None
                  and named(b'unused_undefined')['value_kind'] == 'undefined')
            if mode.startswith('elf'):
                edge = named(b'edge', 0); reference = named(b'edge', 1)
                check('all 64 bits and resolved absolute:'+mode, edge['value'] == reference['value'] == '0xffffffffffffffff'
                      and edge['declared_size'] == str(U64) and reference['value_kind'] == 'absolute'
                      and reference['state'] == 'resolved-reference' and named(b'zero')['value'] == '0x0000000000000000')
                check('nonplaced metadata:'+mode, named(b'meta')['value_kind'] == 'section-relative'
                      and named(b'meta')['state'] == 'not-placed' and named(b'meta')['output_section'] is None)
                check('ELF load groups:'+mode, x['coverage']['layout'] == 'elf-load-groups-with-separate-bss-tail'
                      and len(x['sections']) == 4 and all(int(s['link_va'], 16) == va and int(s['file_offset']) == off
                          for s, (va, off, _, _) in zip(x['sections'][:3], sections[1:]))
                      and x['sections'][3]['file_offset'] is None and x['sections'][3]['file_bytes'] == '0')
                if mode == 'elf-dynamic':
                    check('synthetic GOT slot', named(b'__imp_getenv')['symbol_type'] == 'got-slot'
                          and named(b'__imp_getenv')['origin'] == 'synthetic')
            else:
                check('COFF aux/provenance/metadata', named(b'.file')['state'] == 'metadata'
                      and named(b'dup', 0)['symbol_index'] == 2 and named(b'absolute')['value_kind'] == 'absolute'
                      and named(b'absolute')['declared_size'] is None and named(b'dead')['state'] == 'discarded')
                check('unused program labels retained', named('未引用标签'.encode())['origin'] == 'program')
                # 两个 2 字节的 .text 输入节都是 IMAGE_SCN_ALIGN_16BYTES（半字节 5 即 2^4）：相邻放置恰差 16（此前按 2^5 解成 32）。
                root, tail = int(named(b'co_root', 0)['value'], 16), int(named(b'co_tail', 1)['value'], 16) - 1
                check('COFF input section alignment is 2^(n-1)', abs(root - tail) == 16, (hex(root), hex(tail)))
                check('PE actual section headers', x['coverage']['layout'] == 'pe-sections'
                      and [(int(s['link_va'], 16), int(s['file_offset']), int(s['file_bytes']), int(s['memory_bytes']))
                           for s in x['sections']] == sections)

        # Corrupt a single independent field per fixture. Failure must not expose a partial map.
        # 这几条另核对拒绝原因：名字的边界检查只比较偏移，前提是字符串表非空且末字节为 NUL。
        reasons = {('elf', 'string'): 'BadSymbolName', ('elf', 'strtab-unterminated'): 'BadStringTable',
                   ('elf', 'shstrtab-unterminated'): 'BadSectionNames', ('elf', 'section-name'): 'BadSectionName',
                   ('elf', 'align-npot'): 'UnsupportedSectionAlignment', ('pe', 'align-reserved'): 'UnsupportedSectionAlignment', ('pe', 'align-large'): 'UnsupportedSectionAlignment'}
        for mode, mutations in [('elf', ['header', 'string', 'strtab-unterminated', 'shstrtab-unterminated', 'section-name',
                                         'section', 'tls', 'tls-symbol', 'rel-symbol', 'rel-site', 'align-npot']),
                                ('pe', ['header', 'string', 'section', 'aux', 'rel-symbol', 'rel-site', 'align-reserved', 'align-large'])]:
            path, source, meta = fixtures[mode][0]
            for change in mutations:
                data = bytearray(source)
                if change == 'header':
                    data = data[:17]
                elif mode == 'elf':
                    sh = meta['shoff']; offsets = meta['offsets']
                    if change == 'string': struct.pack_into('<I', data, offsets[7]+24, 0xffffffff)
                    if change == 'strtab-unterminated':  # .strtab 末字节改为非 NUL
                        data[offsets[6]+struct.unpack_from('<Q', data, sh+64*6+32)[0]-1] = 0x41
                    if change == 'shstrtab-unterminated':
                        data[offsets[8]+struct.unpack_from('<Q', data, sh+64*8+32)[0]-1] = 0x41
                    if change == 'section-name': struct.pack_into('<I', data, sh+64*1, 0xffff)
                    if change == 'section': struct.pack_into('<Q', data, sh+64+32, U64)
                    if change == 'tls': struct.pack_into('<Q', data, sh+64+8, 1024+6)
                    if change == 'tls-symbol': data[offsets[7]+24+4] = 6
                    if change == 'rel-symbol': struct.pack_into('<Q', data, offsets[9]+8, (0xffffffff << 32) | 1)
                    if change == 'rel-site': struct.pack_into('<Q', data, offsets[9], 24)
                    if change == 'align-npot': struct.pack_into('<Q', data, sh+64+48, 3)  # gABI：只能是 0 或 2 的幂
                else:
                    sym = meta['symptr']
                    if change == 'string': struct.pack_into('<I', data, sym+18*(struct.unpack_from('<I', data, 12)[0]), U64 & 0xffffffff)
                    if change == 'section': struct.pack_into('<h', data, sym+18*2+12, 32000)
                    if change == 'aux': data[sym+17] = 255
                    rel = struct.unpack_from('<I', data, 20+40*2+24)[0]
                    if change == 'rel-symbol': struct.pack_into('<I', data, rel+4, 1)  # auxiliary record
                    if change == 'rel-site': struct.pack_into('<I', data, rel, 32)
                    # IMAGE_SCN_ALIGN_*：15 保留；14 即 8192 字节，超过映像节对齐 4096（对象 fuzz F5 查出此前都被接受）。
                    flags = struct.unpack_from('<I', data, 20+36)[0] & ~0x00F00000
                    if change == 'align-reserved': struct.pack_into('<I', data, 20+36, flags | 0x00F00000)
                    if change == 'align-large': struct.pack_into('<I', data, 20+36, flags | 0x00E00000)
                bad = work/(mode+'-bad-'+change+'.o'); bad.write_bytes(data)
                p = subprocess.run([probe, mode, work/'bad.image', bad], cwd=work, capture_output=True, timeout=30)
                try: doc = json.loads(p.stdout)
                except (ValueError, UnicodeError): doc = {}
                check('reject:'+mode+':'+change, p.returncode == 1 and not p.stderr and doc.get('ok') is False
                      and doc.get('map', 'missing') is None and bool(doc.get('error'))
                      and reasons.get((mode, change), '') in json.dumps(doc.get('error'), ensure_ascii=False),
                      (p.returncode, p.stdout, p.stderr))

        # COFF 重定位的隐式加数（对象 fuzz F3 查出）：REL32 的位移须落在 i32 内——此前截断写回，越界即成错误产物；ADDR64 按模
        # 2^64 回绕（同 link.exe 与 ELF 侧）——此前加数近 2^63 时整数溢出 trap。.data 首个重定位的目标 \x80 在 .text（更低地址）。
        path, source, meta = fixtures['pe'][0]
        rel = struct.unpack_from('<I', source, 20+40*2+24)[0]; site = meta['offsets'][2]
        data = bytearray(source); struct.pack_into('<H', data, rel+8, 4); struct.pack_into('<I', data, site, 0x80000000)
        bad = work/'pe-rel32-range.o'; bad.write_bytes(data)
        p = subprocess.run([probe, 'pe', work/'bad.image', bad, fixtures['pe'][1][0]], cwd=work, capture_output=True, timeout=30)
        check('reject:pe:rel32-range', p.returncode == 1 and not p.stderr and b'RelativeRelocationOutOfRange' in p.stdout, (p.returncode, p.stdout, p.stderr))
        data = bytearray(source); struct.pack_into('<Q', data, site, (1 << 63) - 1)
        bad = work/'pe-addr64-wrap.o'; bad.write_bytes(data); image = work/'wrap.image'
        p = subprocess.run([probe, 'pe', image, bad, fixtures['pe'][1][0]], cwd=work, capture_output=True, timeout=30)
        ok = p.returncode == 0 and not p.stderr
        if ok:
            doc = json.loads(p.stdout); body = image.read_bytes(); _, _, layout = image_layout(body)
            values = {bytes.fromhex(s['name_bytes'][2:]): int(s['value'], 16) for s in doc['map']['symbols']
                      if s['object_index'] == 0 and s['state'] == 'defined' and s['name_bytes'].startswith('0x')}
            va = values[b'slots']  # .data 节首，即该重定位的位置
            at = [off + va - lva for lva, off, size, _ in layout if lva <= va < lva + size][0]
            ok = struct.unpack_from('<Q', body, at)[0] == ((1 << 63) - 1 + values[b'\x80']) & U64
        check('ADDR64 implicit addend wraps modulo 2^64', ok, (p.returncode, p.stderr))

        # 固定种子的结构化变异在 scripts/test-object-fuzz.py（安全审查攻关第 3 项：受限进程、字段组合、页堆探针、函数元数据）。

    source = work/'映射.tx'; source.write_text('export function main(args: []string): int { print("map"); return 0; }\n', encoding='utf8', newline='\n')
    # macOS 两个目标在任何宿主上交叉构建，映射对照实际 Mach-O 头。
    for target in ['windows', 'linux', 'x86_64-macos', 'aarch64-macos']:
        triple = target if target.endswith('-macos') else 'x86_64-'+target
        ffi = work/('ffi-'+target+'.tx')
        ffi.write_text('declare function strlen(s: string): usize from '+json.dumps({'windows': 'ucrtbase', 'linux': 'libc.so.6'}.get(target, 'libSystem'))
                       +';\nexport function main(args: []string): int { print(toStr(strlen("abc"))); return 0; }\n', encoding='utf8', newline='\n')
        for flavor, entry, flags in [('normal', source, []), ('debug', ROOT/'tests/debug/basic.tx', ['--debug']),
                                      ('leak', source, ['--leak-check']), ('ffi', ffi, []),
                                      ('thread', ROOT/'tests/nrt-cases/thread-basic.tx', [])]:
            dest = work/(target+'-'+flavor); mapping = work/(target+'-'+flavor+'.json')
            args = ['build', entry, '--target='+target, '--out', dest, *flags]
            p = run(*args, '--map', mapping, '--map-format=json', '--map-scope=all')
            check('CLI map:'+target+':'+flavor, p.returncode == 0 and mapping.is_file(), p.stderr)
            if p.returncode: continue
            doc = json.loads(mapping.read_bytes()); product = dest/(entry.stem+('.exe' if target == 'windows' else ''))
            image = product.read_bytes(); ordinary = run(*args)
            check('CLI map preserves product:'+target+':'+flavor, ordinary.returncode == 0 and product.read_bytes() == image
                  and doc['map']['image']['sha256'] == hashlib.sha256(image).hexdigest()
                  and doc['map']['target'] == triple)
            if target != 'windows':
                # 产物函数符号（v052-image-symbols）对照映射的函数表与 --strip 产物。
                bare = work/(target+'-'+flavor+'-strip')
                s = run('build', entry, '--target='+target, '--out', bare, *flags, '--strip')
                stripped = (bare/product.name).read_bytes() if s.returncode == 0 else b''
                symbols_ok = (elf_symbol_check if target == 'linux' else macho_symbol_check)(image, stripped, doc['map']['functions'])
                check('image symbols:'+target+':'+flavor, symbols_ok[0], symbols_ok[1])
            if target.endswith('-macos'):
                base, entry_va, sections = image_layout(image); x = doc['map']
                check('CLI Mach-O producer sections and entry:'+target+':'+flavor, x['coverage']['layout'] == 'macho-producer-sections'
                      and int(x['image']['preferred_base'], 16) == base and int(x['image']['entry'], 16) == entry_va
                      and [(int(s['link_va'], 16), None if s['file_offset'] is None else int(s['file_offset']), int(s['file_bytes']),
                            int(s['memory_bytes'])) for s in x['sections']] == sections, (x['sections'], sections))
            if flavor == 'ffi':
                check('CLI generated import slots:'+target, any(s['origin'] == 'synthetic' and s['value_kind'] == 'link-va'
                      and s['symbol_type'] in ['got-slot', 'iat-slot'] for s in doc['map']['symbols']))
            if flavor == 'normal':
                text = run(*args, '--map', work/(target+'.txt'))
                check('CLI default human:'+target, text.returncode == 0 and 'link-map/6' in (work/(target+'.txt')).read_text(encoding='utf8'))
                failed = run(*args, '--map', work/'missing-parent'/'map.json', '--map-format=json')
                check('CLI write failure:'+target, failed.returncode == 1 and b'map.json' in failed.stderr)
                before = product.read_bytes()
                alias = dest/'..'/dest.name/product.name
                for name, path in [('exact', product), ('normalized', alias)]:
                    collision = run(*args, '--map', path)
                    check('CLI collision:'+target+':'+name, collision.returncode == 64 and product.read_bytes() == before)
                if os.name == 'nt':
                    collision = run(*args, '--map', str(product).upper())
                    check('CLI case collision:'+target, collision.returncode == 64 and product.read_bytes() == before)
    for flags in [['--map-format=json'], ['--map-format=bogus'], ['--map'], ['--map', '--debug']]:
        p = run('build', source, '--out', work/'usage', *flags)
        check('CLI usage:'+' '.join(flags), p.returncode == 64, p.stderr)
    failures = sum(not r['ok'] for r in rows)
    (work/'report.json').write_text(json.dumps({'rows': rows, 'failures': failures}, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print('SUMMARY', 'PASS' if not failures else 'FAIL', len(rows), 'checks', failures, 'failures', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
