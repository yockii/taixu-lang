"""Producer function ranges and frame events, checked against executable bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

from importlib.util import module_from_spec, spec_from_file_location

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parents[1]
layout_spec = spec_from_file_location('map_test_layout', ROOT/'scripts/test-native-link-map.py')
layout_tools = module_from_spec(layout_spec)
layout_spec.loader.exec_module(layout_tools)


def frame_bytes(event, code):
    """Decode the limited x86-64 frame instructions independently of Taixu."""
    kind = event['kind']; reg = event['register']; size = int(event['bytes'])
    if kind in ['push-nonvolatile', 'pop-nonvolatile']:
        assert reg in [3, 5, 6, 7, 12, 13, 14, 15] and size == 8
        expected = (b'\x41' if reg >= 8 else b'') + bytes([(0x50 if kind.startswith('push') else 0x58)+(reg & 7)])
        assert code == expected, (event, code.hex())
        return -8 if kind.startswith('push') else 8
    if kind == 'set-frame-pointer':
        assert reg == 5 and size == 0 and code in [b'\x48\x89\xe5', b'\x48\x8b\xec'], (event, code.hex())
        return 0
    if kind in ['allocate-stack', 'release-stack']:
        modrm = 0xec if kind.startswith('allocate') else 0xc4
        assert reg == 4 and size >= 0 and size % 8 == 0 and code[:1] == b'\x48'
        if len(code) == 4:
            assert code[1:3] == bytes([0x83, modrm]); amount = struct.unpack('<b', code[3:])[0]
        else:
            assert len(code) == 7 and code[1:3] == bytes([0x81, modrm]); amount = struct.unpack('<i', code[3:])[0]
        assert amount == size, (event, code.hex())
        return -size if kind.startswith('allocate') else size
    assert kind == 'return' and reg is None and size == 8 and code == b'\xc3', (event, code.hex())
    return 8


def a64_frame(code, sp, fp):
    """Decode the fixed A64 frame forms independently of Taixu: [(kind, register, bytes, sp_delta)] for one instruction,
    in effect order. sp is the stack offset before it; fp the offset x29 was set to (releases through x29 use it)."""
    assert len(code) == 4, code.hex()
    w = struct.unpack('<I', code)[0]; rt, rn, rt2 = w & 31, (w >> 5) & 31, (w >> 10) & 31
    imm7 = (((w >> 15) & 127) ^ 64)-64; imm9 = (((w >> 12) & 511) ^ 256)-256
    if w & 0xFFC00000 == 0xA9800000 and rn == 31 and imm7 == -2:  # stp xa, xb, [sp, #-16]!
        return [('push-nonvolatile', rt2, 8, -8), ('push-nonvolatile', rt, 8, -8)]
    if w & 0xFFE00C00 == 0xF8000C00 and rn == 31 and imm9 == -16:  # str x, [sp, #-16]!
        return [('push-nonvolatile', rt, 16, -16)]
    if w & 0xFFC00000 == 0xA8C00000 and rn == 31 and imm7 == 2:  # ldp xa, xb, [sp], #16
        return [('pop-nonvolatile', rt, 8, 8), ('pop-nonvolatile', rt2, 8, 8)]
    if w & 0xFFE00C00 == 0xF8400400 and rn == 31 and imm9 == 16:  # ldr x, [sp], #16
        return [('pop-nonvolatile', rt, 16, 16)]
    if w == 0x910003FD:  # mov x29, sp
        return [('set-frame-pointer', 29, 0, 0)]
    if w & 0xFF8003FF == 0xD10003FF:  # sub sp, sp, #imm{, lsl #12}
        size = ((w >> 10) & 4095) << (12*((w >> 22) & 1)); return [('allocate-stack', 31, size, -size)]
    if w & 0xFFC003FF in (0x910003BF, 0xD10003BF) and fp is not None:  # mov sp, x29 / sub sp, x29, #k
        k = (w >> 10) & 4095; target = fp-k if w & 0x40000000 else fp+k
        return [('release-stack', 31, target-sp, target-sp)]
    assert w == 0xD65F03C0, hex(w)  # ret
    return [('return', None, 0, 0)]


def la64_words(code):
    assert code and len(code) % 4 == 0, code.hex()
    return struct.unpack('<%dI' % (len(code)//4), code)


def la64_si12(w): return (((w >> 10) & 4095) ^ 2048)-2048


def la64_movimm(words, rd):
    """Value a lu12i.w/ori/lu32i.d/lu52i.d/addi.d sequence leaves in rd (each word writes rd), independently of Taixu."""
    v = 0
    for w in words:
        assert w & 31 == rd, hex(w)
        src = 0 if (w >> 5) & 31 == 0 else v
        if w & 0xFFC00000 == 0x02C00000: assert (w >> 5) & 31 == 0; v = la64_si12(w)  # addi.d rd, zero, si12
        elif w & 0xFFC00000 == 0x03800000: v = src | ((w >> 10) & 4095)  # ori
        elif w & 0xFE000000 == 0x14000000: v = (((w >> 5) & 0xFFFFF) ^ 0x80000)-0x80000 << 12  # lu12i.w
        elif w & 0xFE000000 == 0x16000000: v = (v & 0xFFFFFFFF) | ((((w >> 5) & 0xFFFFF) ^ 0x80000)-0x80000 << 32)  # lu32i.d
        else: assert w & 0xFFC00000 == 0x03000000, hex(w); v = (src & ((1 << 52)-1)) | ((((w >> 10) & 4095) ^ 2048)-2048 << 52)  # lu52i.d
    return v


def la64_frame(code, sp, fp):
    """Decode the fixed loongarch64 frame forms independently of Taixu: [(kind, register, bytes, sp_delta)] for the
    instructions of one event range, in effect order. A push group is addi.d sp, sp, -N then st.d of each saved register
    inside those N bytes; registers are recorded from the highest slot down, each reaching up to the next higher slot (the
    first to N), as pushing them one by one would lay them out. A pop group is ld.d from the lowest slot up, then
    addi.d sp, sp, N. sp is the stack offset before the range; fp the offset r22 was set to (releases through r22 use it).
    The frame pointer is addi.d fp, sp, 16: the address just above the frame record, as the psABI and perf expect."""
    w = la64_words(code)
    addi = lambda x, rd, rj: x & 0xFFC00000 == 0x02C00000 and x & 31 == rd and (x >> 5) & 31 == rj
    if len(w) > 1 and addi(w[0], 3, 3) and la64_si12(w[0]) < 0 and all(x & 0xFFC003E0 == 0x29C00060 for x in w[1:]):
        n = -la64_si12(w[0]); slots = sorted(((la64_si12(x), x & 31) for x in w[1:]), reverse=True)
        tops = [n]+[o for o, _ in slots[:-1]]
        assert slots[-1][0] == 0 and len({o for o, _ in slots}) == len(slots), [hex(x) for x in w]
        out = [('push-nonvolatile', r, t-o, o-t) for (o, r), t in zip(slots, tops)]
        assert all(b in (8, 16) for _, _, b, _ in out), out
        return out
    if len(w) > 1 and addi(w[-1], 3, 3) and la64_si12(w[-1]) > 0 and all(x & 0xFFC003E0 == 0x28C00060 for x in w[:-1]):
        n = la64_si12(w[-1]); slots = sorted((la64_si12(x), x & 31) for x in w[:-1])
        tops = [o for o, _ in slots[1:]]+[n]
        assert slots[0][0] == 0 and len({o for o, _ in slots}) == len(slots), [hex(x) for x in w]
        out = [('pop-nonvolatile', r, t-o, t-o) for (o, r), t in zip(slots, tops)]
        assert all(b in (8, 16) for _, _, b, _ in out), out
        return out
    if len(w) > 1:  # sp -= N 的长形：N 进 t8（r20），再 sub.d sp, sp, t8
        assert w[-1] == 0x0011D063, hex(w[-1])
        size = la64_movimm(w[:-1], 20); assert size > 2048; return [('allocate-stack', 3, size, -size)]
    x = w[0]
    if x == 0x02C04076:  # addi.d fp, sp, 16
        return [('set-frame-pointer', 22, 16, 0)]
    if addi(x, 3, 3) and la64_si12(x) < 0:  # addi.d sp, sp, -N
        return [('allocate-stack', 3, -la64_si12(x), la64_si12(x))]
    if addi(x, 3, 22) and fp is not None:  # addi.d sp, fp, -k
        target = fp+la64_si12(x); return [('release-stack', 3, target-sp, target-sp)]
    assert x == 0x4C000020, hex(x)  # jirl zero, ra, 0
    return [('return', None, 0, 0)]


def rv64_words(code):
    assert code and len(code) % 4 == 0, code.hex()
    return struct.unpack('<%dI' % (len(code)//4), code)


def rv64_i12(w): return (((w >> 20) & 4095) ^ 2048)-2048


def rv64_s12(w): return ((((w >> 25) << 5) | ((w >> 7) & 31)) ^ 2048)-2048


def rv64_movimm(words, rd):
    """Value a lui/addiw/addi/slli sequence leaves in rd (each word writes rd), independently of Taixu."""
    v = 0
    wrap = lambda x: ((x + (1 << 63)) % (1 << 64)) - (1 << 63)
    sext32 = lambda x: ((x & 0xFFFFFFFF) ^ 0x80000000)-0x80000000
    for w in words:
        assert (w >> 7) & 31 == rd, hex(w)
        src = 0 if (w >> 15) & 31 == 0 else v
        if w & 0x7F == 0x37: v = sext32(w & 0xFFFFF000)  # lui
        elif w & 0x707F == 0x1B: v = sext32(src + rv64_i12(w))  # addiw
        elif w & 0x707F == 0x13: v = wrap(src + rv64_i12(w))  # addi
        else: assert w & 0xFC00707F == 0x1013, hex(w); v = wrap(src << ((w >> 20) & 63))  # slli
    return v


def rv64_frame(code, sp, fp):
    """Decode the fixed riscv64 frame forms independently of Taixu, the same shapes as loongarch64: a push group is
    addi sp, sp, -N then sd of each saved register inside those N bytes (recorded from the highest slot down, each reaching
    up to the next higher slot, the first to N); a pop group is ld from the lowest slot up, then addi sp, sp, N. The frame
    pointer is addi s0, sp, 16 (the psABI's fp = CFA); a long allocation puts N in t6 (x31) and subtracts it."""
    w = rv64_words(code)
    addi = lambda x, rd, rs: x & 0x707F == 0x13 and (x >> 7) & 31 == rd and (x >> 15) & 31 == rs
    if len(w) > 1 and addi(w[0], 2, 2) and rv64_i12(w[0]) < 0 and all(x & 0x000FF07F == 0x00013023 for x in w[1:]):
        n = -rv64_i12(w[0]); slots = sorted(((rv64_s12(x), (x >> 20) & 31) for x in w[1:]), reverse=True)
        tops = [n]+[o for o, _ in slots[:-1]]
        assert slots[-1][0] == 0 and len({o for o, _ in slots}) == len(slots), [hex(x) for x in w]
        out = [('push-nonvolatile', r, t-o, o-t) for (o, r), t in zip(slots, tops)]
        assert all(b in (8, 16) for _, _, b, _ in out), out
        return out
    if len(w) > 1 and addi(w[-1], 2, 2) and rv64_i12(w[-1]) > 0 and all(x & 0x000FF07F == 0x00013003 for x in w[:-1]):
        n = rv64_i12(w[-1]); slots = sorted((rv64_i12(x), (x >> 7) & 31) for x in w[:-1])
        tops = [o for o, _ in slots[1:]]+[n]
        assert slots[0][0] == 0 and len({o for o, _ in slots}) == len(slots), [hex(x) for x in w]
        out = [('pop-nonvolatile', r, t-o, t-o) for (o, r), t in zip(slots, tops)]
        assert all(b in (8, 16) for _, _, b, _ in out), out
        return out
    if len(w) > 1:  # sp -= N 的长形：N 进 t6（x31），再 sub sp, sp, t6
        assert w[-1] == 0x41F10133, hex(w[-1])
        size = rv64_movimm(w[:-1], 31); assert size > 2048; return [('allocate-stack', 2, size, -size)]
    x = w[0]
    if x == 0x01010413:  # addi s0, sp, 16
        return [('set-frame-pointer', 8, 16, 0)]
    if addi(x, 2, 2) and rv64_i12(x) < 0:  # addi sp, sp, -N
        return [('allocate-stack', 2, -rv64_i12(x), rv64_i12(x))]
    if addi(x, 2, 8) and fp is not None:  # addi sp, s0, -k
        target = fp+rv64_i12(x); return [('release-stack', 2, target-sp, target-sp)]
    assert x == 0x00008067, hex(x)  # jalr zero, 0(ra)
    return [('return', None, 0, 0)]


def verify_functions(doc, image, sections):
    x = doc['map']; functions = x['functions']; input_ends = {}; saves_seen = set(); ranges = []
    assert doc['schema'] == 'link-map/6' and doc['ok'] is True
    assert x['image']['sha256'] == hashlib.sha256(image).hexdigest() and int(x['image']['bytes']) == len(image)
    assert int(x['functions_total']) == len(functions) > 0
    boundary_count = sum(f['stack_model'] != 'fixed-call' for f in functions)
    assert x['coverage']['function_ranges'] == 'producer-declared'
    assert x['coverage']['frame_events'] == ('partial-producer-declared' if boundary_count else 'producer-declared')
    assert int(x['coverage']['boundary_functions']) == boundary_count
    assert int(x['coverage']['frame_functions']) == len(functions)-boundary_count
    assert x['coverage']['unwind'] is None and x['coverage']['cpu_profile'] is None and not x['coverage']['truncated']
    assert x['image']['runtime_load_bias'] is None
    # x86_64-windows 内核的手写例程已全部登记（PE 规范化 P3 第 7 项），别的目标仍有未登记的手写例程。
    assert set(x['coverage']['excluded_function_origins']) == {'linker-thunks', 'external-objects-without-metadata'} | (set() if x['target'] == 'x86_64-windows' else {'runtime-manual'})
    assert set(x['coverage']['included_function_origins']) == {f['origin'] for f in functions}
    assert x['coverage']['object_metadata']['schema'] == 'txfn/3'
    assert 'sp_delta=decimal-string/i64' in x['numeric_encoding']
    a64 = x['target'].startswith('aarch64-'); la64 = x['target'] == 'loongarch64-linux'; rv64 = x['target'] == 'riscv64-linux'
    risc = a64 or la64 or rv64
    for expected_id, f in enumerate(functions):
        va = int(f['link_va'], 16); stop = int(f['link_va_end'], 16); size = int(f['bytes']); offset = int(f['file_offset'])
        assert re.fullmatch('0x[0-9a-f]{16}', f['link_va']) and re.fullmatch('0x[0-9a-f]{16}', f['link_va_end'])
        assert f['id'] == expected_id
        tx = f['origin'] == 'tx-generated'
        if tx: assert f['source_id'] is not None and f['link_name'] == 'f_'+str(f['source_id'])
        else: assert f['source_id'] is None and f['link_name'] == f['name']
        key = (f['object_index'], f['input_section_index'])
        assert va >= input_ends.get(key, -1) and stop == va+size and size > 0; input_ends[key] = stop
        ranges.append((va, stop))
        assert int(f['input_section_end'])-int(f['input_section_begin']) == size
        assert offset == layout_tools.file_at(sections, va) and offset+size <= len(image)
        if f['stack_model'] != 'fixed-call':
            assert f['origin'] == 'process-thread-entry' and f['stack_model'] in {
                'process-entry','thread-entry','stack-transition','stack-disposal'}
            assert f['frame'] is None and f['prologue_end_offset'] is None and f['epilogues'] == []
            continue
        prologue = int(f['prologue_end_offset']); eps = [(int(e['start_offset']), int(e['end_offset'])) for e in f['epilogues']]
        assert 0 <= prologue <= size
        previous = prologue
        for start, end in eps: assert previous <= start < end <= size; previous = end
        if tx: assert len(eps) == 1 and eps[0][1] == size
        frame = f['frame']; assert frame['effect_at'] == 'instruction-end' and frame['offset_basis'] == 'function-start'
        assert frame['entry_sp'] == ('call-site-sp' if risc else 'return-address-slot')
        assert frame['register_numbering'] == ('aarch64-gpr' if a64 else 'loongarch64-gpr' if la64 else 'riscv64-gpr' if rv64 else 'x86-64-gpr')
        assert frame['body_sp'] == 'fixed' and frame['register_recovery'] == ('saved-gpr-events' if tx or f['origin'] == 'ffi-callback-shell' else 'not-provided')
        # sp is relative to entry; saved holds (register, bytes). One A64 instruction (stp/ldp) or one loongarch64 / riscv64
        # group of instructions (addi.d sp with st.d/ld.d, addi sp with sd/ld) may carry several events with the same range.
        position = 0; sp = 0; saved = []; original = []; allocation = None; returned = False; ep_index = -1; pro_end = 0
        body_sp = None; fp = None; events = frame['events']; i = 0
        while i < len(events):
            before = int(events[i]['instruction_start_offset']); after = int(events[i]['instruction_end_offset'])
            group = [events[i]]; i += 1
            while risc and i < len(events) and (int(events[i]['instruction_start_offset']), int(events[i]['instruction_end_offset'])) == (before, after):
                group.append(events[i]); i += 1
            assert position <= before < after <= size
            pro = before < prologue
            if pro:
                assert before == position and after <= prologue and ep_index == -1
                pro_end = after
            else:
                if body_sp is None: body_sp = sp
                if ep_index == -1 or before >= eps[ep_index][1]:
                    if ep_index >= 0: assert returned
                    ep_index += 1
                    assert ep_index < len(eps)
                    position = eps[ep_index][0]; sp = body_sp; saved = original[:]; returned = False
                assert not returned and before == position and after <= eps[ep_index][1]
            position = after
            code = image[offset+before:offset+after]
            decoded = a64_frame(code, sp, fp) if a64 else la64_frame(code, sp, fp) if la64 else rv64_frame(code, sp, fp) if rv64 else \
                [(group[0]['kind'], group[0]['register'], int(group[0]['bytes']), frame_bytes(group[0], code))]
            assert [(e['kind'], e['register'], int(e['bytes']), int(e['sp_delta'])) for e in group] == decoded, (f['name'], before, group, code.hex())
            for e in group:
                kind = e['kind']; reg = e['register']; size_ = int(e['bytes'])
                if kind == 'push-nonvolatile':
                    # x86 上压栈只许 tx 生成函数、回调外壳与按平台约定调用的入口（x86_64-windows 的线程入口，PE 规范化 P3）。
                    assert pro and (tx or risc or f['origin'] in ('ffi-callback-shell', 'process-thread-entry', 'runtime-manual')); saved.append((reg, size_)); original.append((reg, size_))
                if kind == 'set-frame-pointer':
                    assert (saved[-2:] == [(30, 8), (29, 8)]) if a64 else (saved == [(1, 8), (22, 8)]) if la64 else \
                        (saved == [(1, 8), (8, 8)]) if rv64 else (saved == [(5, 8)]); fp = sp+size_
                if kind == 'allocate-stack': assert pro and (allocation is None or a64); allocation = (allocation or 0)+size_
                if kind == 'release-stack': assert not pro and size_ == allocation
                if kind == 'pop-nonvolatile': assert saved.pop() == (reg, size_)
                if kind == 'return': assert not pro and sp == 0 and not saved and after == eps[ep_index][1]; returned = True
                sp += int(e['sp_delta'])
            if risc: assert sp % 16 == 0, (f['name'], before, sp)
        assert pro_end == prologue and ep_index+1 == len(eps)
        if eps: assert returned and sp == (0 if risc else 8)
        if tx: saves_seen.add(tuple(r for r, _ in original))
    for left, right in zip(sorted(ranges), sorted(ranges)[1:]): assert left[1] <= right[0], (left, right)
    return saves_seen


def a64_call_target(f, words, k):
    # 第 k 条是 bl：返回它的目标地址。
    assert words[k] >> 26 == 0x25
    return int(f['link_va'],16)+4*k+4*(((words[k] & 0x3FFFFFF) ^ 0x2000000)-0x2000000)


def verify_boundaries(doc, image, target):
    # target：windows、linux（同 x86_64-linux）、x86_64-linux、aarch64-linux、loongarch64-linux、riscv64-linux、x86_64-macos、
    # aarch64-macos。
    x86_linux = target in ('linux', 'x86_64-linux')
    rows = {f['link_name']: f for f in doc['map']['functions']}
    special = {n:f for n,f in rows.items() if f['origin'] == 'process-thread-entry'}
    code = lambda f: image[int(f['file_offset']):int(f['file_offset'])+int(f['bytes'])]
    if target == 'windows':
        # x86_64-windows 的进程入口与线程入口在内核里，是按平台约定调用、带帧事件的普通函数（PE 规范化 P3，同 aarch64-windows）：
        # __tx_entry：sub rsp, 40；call __tx_native_entry；add rsp, 40；ret。
        # __tx_thread_entry：push rbx；sub rsp, 32；call __tx_thread_main；xor eax, eax；add rsp, 32；pop rbx；ret。
        assert {n:f['stack_model'] for n,f in special.items()} == {'__tx_entry':'fixed-call','__tx_thread_entry':'fixed-call'}
        for f in special.values():
            assert f['object_index'] == 0 and f['frame'] is not None and len(f['epilogues']) == 1
        call = lambda f, at: int(f['link_va'],16)+at+5+struct.unpack_from('<i',code(f),at+1)[0]
        f = special['__tx_entry']; body = code(f)
        assert body[:4] == b'\x48\x83\xec\x28' and body[4] == 0xe8 and body[9:] == b'\x48\x83\xc4\x28\xc3'
        if '__tx_native_entry' in rows: assert call(f, 4) == int(rows['__tx_native_entry']['link_va'],16)
        f = special['__tx_thread_entry']; body = code(f)
        assert body[:5] == b'\x53\x48\x83\xec\x20' and body[5] == 0xe8 and call(f, 5) == int(rows['__tx_thread_main']['link_va'],16)
        assert body[10:] in [b'\x31\xc0\x48\x83\xc4\x20\x5b\xc3', b'\x33\xc0\x48\x83\xc4\x20\x5b\xc3']
        return
    expected = {'__tx_thread_entry':'thread-entry'}
    if x86_linux or target in ('aarch64-linux', 'loongarch64-linux', 'riscv64-linux'): expected.update({'_start':'process-entry','__tx_thread_clone':'stack-transition','__tx_thread_unmap_exit':'stack-disposal'})
    assert {n:f['stack_model'] for n,f in special.items()} == expected
    for f in special.values():
        assert f['object_index'] == 0 and f['frame'] is None and f['prologue_end_offset'] is None and f['epilogues'] == []
    if target == 'aarch64-linux':
        # bootstrap/selfhost/linux_a64.tx 的手写入口（号按 aarch64：exit_group 94、clone 220、munmap 215、exit 93）。
        words = lambda f: struct.unpack('<%dI' % (int(f['bytes'])//4), code(f))
        f = special['_start']; w = words(f)
        # movz x29/x30, #0；ldr x0, [sp]；add x1, sp, #8 …… 取 TPIDR_EL0、为 0 时设成静态 TLS 块 …… bl __tx_native_entry；
        # movz x8, #94；svc #0；brk #1。
        assert w[:4] == (0xD280001D, 0xD280001E, 0xF94003E0, 0x910023E1) and w[-3:] == (0xD2800BC8, 0xD4000001, 0xD4200020)
        assert w.count(0xD53BD050) == 1 and w.count(0xD51BD050) == 1 and w.count(0xD4000001) == 1
        assert a64_call_target(f, w, len(w)-4) == int(rows['__tx_native_entry']['link_va'],16)
        f = special['__tx_thread_clone']; w = words(f)
        # 实参按 x86_64 的次序收、交换 ctid 与 tls（x3↔x4）后 svc clone；父线程 cbz 不跳、ret；子线程清帧链、ldr x0, [sp]、
        # bl __tx_thread_main（不返回）、brk。
        assert len(w) == 12 and w[:7] == (0xAA0303F0, 0xAA0403E3, 0xAA1003E4, 0xD2801B88, 0xD4000001, 0xB4000040, 0xD65F03C0)
        assert w[7:10] == (0xD280001D, 0xD280001E, 0xF94003E0) and w[11] == 0xD4200020
        assert a64_call_target(f, w, 10) == int(rows['__tx_thread_main']['link_va'],16)
        # munmap(base, size) 后只用寄存器：movz x0, #0；exit；brk。
        assert words(special['__tx_thread_unmap_exit']) == (0xD2801AE8, 0xD4000001, 0xD2800000, 0xD2800BA8, 0xD4000001, 0xD4200020)
    if target == 'loongarch64-linux':
        # bootstrap/selfhost/linux_la64.tx 与 nrt_spawn.tx 的手写入口（号按 loongarch64：exit_group 94、clone 220、munmap 215、
        # exit 93）。move 即 or rd, rj, zero；ret 即 jirl zero, ra, 0；syscall 0；break 1。
        words = lambda f: la64_words(code(f))
        def call(f, w, k):
            assert w[k] & 0xFC000000 == 0x54000000  # bl
            off = ((w[k] >> 10) & 0xFFFF) | ((w[k] & 0x3FF) << 16)
            return int(f['link_va'],16)+4*k+4*((off ^ 0x2000000)-0x2000000)
        f = special['_start']; w = words(f)
        # fp＝0、ra＝0；ld.d a0, [sp]；addi.d a1, sp, 8；slli.d t0, a0, 3；add.d a2, a1, t0；addi.d a2, a2, 8；经 t7 存 envp；
        # bnez tp 跳过下两条（静态产物设 tp）；bl __tx_native_entry；exit_group；break。
        assert len(w) == 16 and w[:7] == (0x00150016, 0x00150001, 0x28C00064, 0x02C02065, 0x00410C8C, 0x0010B0A6, 0x02C020C6)
        assert w[7] & 0xFE00001F == 0x1A000013 and w[8] & 0xFFC003FF == 0x29C00266
        assert w[9] == 0x44000C40 and w[10] & 0xFE00001F == 0x1A000002 and w[11] & 0xFFC003FF == 0x02C00042
        assert w[13:] == (0x02C1780B, 0x002B0000, 0x002A0001) and call(f, w, 12) == int(rows['__tx_native_entry']['link_va'],16)
        f = special['__tx_thread_clone']; w = words(f)
        # 实参原样 syscall clone；父线程 beqz 不跳、ret；子线程清帧链、ld.d a0, [sp]、bl __tx_thread_main（不返回）、break。
        assert len(w) == 9 and w[:7] == (0x02C3700B, 0x002B0000, 0x40000880, 0x4C000020, 0x00150016, 0x00150001, 0x28C00064)
        assert w[8] == 0x002A0001 and call(f, w, 7) == int(rows['__tx_thread_main']['link_va'],16)
        # munmap(base, size) 后只用寄存器：a0＝0；exit；break。
        assert words(special['__tx_thread_unmap_exit']) == (0x02C35C0B, 0x002B0000, 0x02C00004, 0x02C1740B, 0x002B0000, 0x002A0001)
        # addi.d sp, sp, -32；st.d ra, [sp+24]；st.d fp, [sp+16]；addi.d fp, sp, 32（进入时的 sp）；st.d s0, [sp+8]；bl __tx_thread_main；
        # ld.d s0、fp、ra；addi.d sp, sp, 32；a0＝0；ret。
        f = special['__tx_thread_entry']; w = words(f)
        assert len(w) == 12 and w[:5] == (0x02FF8063, 0x29C06061, 0x29C04076, 0x02C08076, 0x29C02077)
        assert call(f, w, 5) == int(rows['__tx_thread_main']['link_va'],16)
        assert w[6:] == (0x28C02077, 0x28C04076, 0x28C06061, 0x02C08063, 0x02C00004, 0x4C000020)
        return
    if target == 'riscv64-linux':
        # bootstrap/selfhost/linux_rv64.tx 与 nrt_spawn.tx 的手写入口（号按 riscv64：exit_group 94、clone 220、munmap 215、exit 93）。
        # mv 即 addi rd, rs, 0；ret 即 jalr zero, 0(ra)；调用为 auipc ra＋jalr ra（R_RISCV_CALL_PLT）；ecall；ebreak。期望的字由
        # GNU as 2.42 汇编核对。
        words = lambda f: rv64_words(code(f))
        def call(f, w, k):
            assert w[k] & 0xFFF == 0x097 and w[k+1] & 0xFFFFF == 0x080E7  # auipc ra；jalr ra, lo(ra)
            hi = ((w[k] & 0xFFFFF000) ^ 0x80000000)-0x80000000
            return int(f['link_va'],16)+4*k+hi+rv64_i12(w[k+1])
        f = special['_start']; w = words(f)
        # s0＝0、ra＝0；ld a0, 0(sp)；addi a1, sp, 8；slli t0, a0, 3；add a2, a1, t0；addi a2, a2, 8；经 t5 存 envp；读
        # __tx_dynamic_image 进 t0，不为 0 跳过下两条（静态产物设 tp）；call __tx_native_entry；exit_group；ebreak。
        assert len(w) == 19 and w[:7] == (0x00000413, 0x00000093, 0x00013503, 0x00810593, 0x00351293, 0x00558633, 0x00860613)
        assert w[7] & 0xFFF == 0xF17 and w[8] & 0x01FFF07F == 0x00CF3023
        assert w[9] & 0xFFF == 0x297 and w[10] & 0xFFFFF == 0x2B283 and w[11] == 0x00029663
        assert w[12] & 0xFFF == 0x217 and w[13] & 0xFFFFF == 0x20213
        assert w[16:] == (0x05E00893, 0x00000073, 0x00100073) and call(f, w, 14) == int(rows['__tx_native_entry']['link_va'],16)
        f = special['__tx_thread_clone']; w = words(f)
        # 实参按 x86_64 的次序收、交换 ctid 与 tls（a3↔a4，经 t5）后 ecall clone；父线程 beqz 不跳、ret；子线程清帧链、
        # ld a0, 0(sp)、call __tx_thread_main（不返回）、ebreak。
        assert len(w) == 13 and w[:7] == (0x00068F13, 0x00070693, 0x000F0713, 0x0DC00893, 0x00000073, 0x00050463, 0x00008067)
        assert w[7:10] == (0x00000413, 0x00000093, 0x00013503) and w[12] == 0x00100073
        assert call(f, w, 10) == int(rows['__tx_thread_main']['link_va'],16)
        # munmap(base, size) 后只用寄存器：a0＝0；exit；ebreak。
        assert words(special['__tx_thread_unmap_exit']) == (0x0D700893, 0x00000073, 0x00000513, 0x05D00893, 0x00000073, 0x00100073)
        # addi sp, sp, -32；sd ra, 24(sp)；sd s0, 16(sp)；addi s0, sp, 32（进入时的 sp）；sd s1, 8(sp)；call __tx_thread_main；
        # ld s1、s0、ra；addi sp, sp, 32；a0＝0；ret。
        f = special['__tx_thread_entry']; w = words(f)
        assert len(w) == 13 and w[:5] == (0xFE010113, 0x00113C23, 0x00813823, 0x02010413, 0x00913423)
        assert call(f, w, 5) == int(rows['__tx_thread_main']['link_va'],16)
        assert w[7:] == (0x00813483, 0x01013403, 0x01813083, 0x02010113, 0x00000513, 0x00008067)
        return
    f = special['__tx_thread_entry']; body = code(f); at = 1
    if target.startswith('aarch64-'):
        # stp x29, x30, [sp, #-32]!; mov x29, sp; str x19, [sp, #16]; bl __tx_thread_main; ldr x19, [sp, #16];
        # ldp x29, x30, [sp], #32; movz x0, #0; ret
        words = struct.unpack('<%dI' % (len(body)//4), body); call = words[3]
        assert len(body) == 32 and words[:3] == (0xA9BE7BFD, 0x910003FD, 0xF9000BF3) and call >> 26 == 0x25
        assert int(f['link_va'],16)+12+4*(((call & 0x3FFFFFF) ^ 0x2000000)-0x2000000) == int(rows['__tx_thread_main']['link_va'],16)
        assert words[4:] == (0xF9400BF3, 0xA8C27BFD, 0xD2800000, 0xD65F03C0)
        return
    assert body[:1] == b'\x53'
    if x86_linux or target == 'x86_64-macos': assert body[at:at+3] in [b'\x48\x89\xf9',b'\x48\x8b\xcf']; at += 3
    size = 4 if body[at:at+2] == b'\x48\x83' else 7
    frame_bytes({'kind':'allocate-stack','register':4,'bytes':32},body[at:at+size]); at += size
    assert body[at] == 0xe8 and int(f['link_va'],16)+at+5+struct.unpack_from('<i',body,at+1)[0] == int(rows['__tx_thread_main']['link_va'],16)
    at += 5; size = 4 if body[at:at+2] == b'\x48\x83' else 7
    frame_bytes({'kind':'release-stack','register':4,'bytes':32},body[at:at+size]); at += size
    assert body[at:] in [b'\x5b\x31\xc0\xc3',b'\x5b\x33\xc0\xc3']
    if x86_linux:
        root = code(special['_start'])
        assert root[:2] in [b'\x31\xed',b'\x33\xed'] and root[2:6] == b'\x48\x8b\x3c\x24'
        assert root[-9:] in [b'\x89\xc7\xb8\xe7\0\0\0\x0f\x05',b'\x8b\xf8\xb8\xe7\0\0\0\x0f\x05']
        clone = code(special['__tx_thread_clone'])
        assert clone.count(b'\x0f\x05') == 1 and clone[-7] == 0xe8 and clone[-2:] == b'\x0f\x0b'
        assert int(special['__tx_thread_clone']['link_va'],16)+len(clone)-2+struct.unpack_from('<i',clone,len(clone)-6)[0] == int(rows['__tx_thread_main']['link_va'],16)
        disposal = code(special['__tx_thread_unmap_exit'])
        assert len(disposal) == 24 and disposal[6:13] == b'\xb8\x0b\0\0\0\x0f\x05'
        assert disposal[13:15] in [b'\x31\xff',b'\x33\xff'] and disposal[15:] == b'\xb8\x3c\0\0\0\x0f\x05\x0f\x0b'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--baseline-compiler', type=Path, help='optional previous compiler for unchanged client byte identity')
    a = ap.parse_args(); compiler = a.compiler.resolve(); work = a.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok)})
        print('PASS' if ok else 'FAIL', name, '' if ok else str(detail)[:800], flush=True)

    def run(*args, timeout=300):
        return subprocess.run([compiler, *map(str, args)], cwd=ROOT, capture_output=True, timeout=timeout)

    def validate(name, doc, image, sections):
        try: saves = verify_functions(doc, image, sections)
        except (AssertionError, KeyError, ValueError, IndexError, struct.error) as error:
            check(name, False, repr(error)); return set()
        check(name, True); return saves

    cap = json.loads(run('capabilities', '--format=json').stdout)['capabilities']
    check('discover scopes and partial coverage', cap.get('link_map_schema') == 'link-map/6'
          and cap.get('link_map_scopes') == 'functions,all' and cap.get('link_map_default_scope') == 'functions'
          and cap.get('link_map_function_ranges') == 'producer-declared'
          and cap.get('link_map_frame_events') == 'partial-producer-declared'
          and cap.get('link_map_stack_models') == 'fixed-call,process-entry,thread-entry,stack-transition,stack-disposal'
          and cap.get('link_map_object_metadata') == 'txfn/3'
          and 'link-map/6' in cap['schemas'] and 'link-map/5' not in cap['schemas'] and 'link-map/2' not in cap['schemas'] and 'link-map/1' not in cap['schemas'])
    built = run('build', ROOT/'bootstrap/native_function_probe.tx', '--toolchain', '--leak-check', '--out', work/'probe')
    check('producer ownership probe build', built.returncode == 0, built.stderr)
    if built.returncode == 0:
        p = subprocess.run([work/'probe'/('native_function_probe'+('.exe' if os.name == 'nt' else '')), work/'fixture.bin'],
                           cwd=ROOT, capture_output=True, timeout=60)
        (work/'probe.stdout').write_bytes(p.stdout); (work/'probe.stderr').write_bytes(p.stderr)
        check('producer negative checks, ownership, reentry, no leaks', p.returncode == 0 and not p.stderr, (p.returncode, p.stdout[:400], p.stderr))
        if p.returncode == 0:
            doc = json.loads(p.stdout); image = (work/'fixture.bin').read_bytes(); x = doc['map']
            saves = validate('independent producer frame bytes and near-int64-limit address', doc, image, [(9223372036854775697, 512, len(image)-512, len(image)-512)])
            check('both save sets', saves == {(5, 3, 14, 15), (5, 3, 14, 15, 12, 13)}, saves)
            check('padding excluded and internal labels not boundaries', int(x['functions'][0]['input_section_begin']) == 6
                  and int(x['functions'][1]['input_section_begin'])-int(x['functions'][0]['input_section_end']) == 9
                  and all(int(f['epilogues'][0]['start_offset'])-int(f['prologue_end_offset']) == 9 for f in x['functions']))
            check('escaping and human summary', x['functions'][0]['module'] == '库/引用"\\\n.tx'
                  and '太虚函数' in (work/'fixture.bin.txt').read_text(encoding='utf8'))
    built = run('build', ROOT/'bootstrap/native_function_driver_probe.tx', '--toolchain', '--leak-check', '--out', work/'driver-probe')
    check('same process driver probe build', built.returncode == 0, built.stderr)
    cases = [('basic', ROOT/'tests/debug/basic.tx', []), ('closure', ROOT/'tests/cases/22-closure-fnarg.tx', []),
             ('generator', ROOT/'tests/nrt-cases/gen-basic.tx', []), ('async', ROOT/'tests/cases/async-await-any.tx', []),
             ('debug', ROOT/'tests/debug/basic.tx', ['--debug']), ('leak', ROOT/'tests/debug/basic.tx', ['--leak-check']),
             ('thread', ROOT/'tests/nrt-cases/thread-basic.tx', []), ('tool', ROOT/'tests/debug/basic.tx', ['--toolchain'])]
    all_saves = set()
    # 三种 Linux 与两种 macOS 在任何宿主上交叉构建、只做静态核对（产物字节与映射）。目标写三元组：不带架构的 linux 在 arm64
    # 宿主上取 aarch64-linux，windows 同理取 aarch64-windows（A6）。
    libraries = {'windows': 'ucrtbase', 'x86_64-linux': 'libc.so.6', 'aarch64-linux': 'libc.so.6', 'loongarch64-linux': 'libc.so.6',
                 'riscv64-linux': 'libc.so.6',
                 'x86_64-macos': 'libSystem', 'aarch64-macos': 'libSystem'}
    callbacks = {'windows': 'ffi-win-callback.tx', 'x86_64-linux': 'ffi-linux-callback-outside.tx', 'aarch64-linux': 'ffi-linux-callback-outside.tx',
                 'loongarch64-linux': 'ffi-linux-callback-outside.tx', 'riscv64-linux': 'ffi-linux-callback-outside.tx',
                 'x86_64-macos': 'ffi-macos-lib.tx', 'aarch64-macos': 'ffi-macos-lib.tx'}
    for target in ['windows', 'x86_64-linux', 'aarch64-linux', 'loongarch64-linux', 'riscv64-linux', 'x86_64-macos', 'aarch64-macos']:
        a64 = target.startswith('aarch64-'); la64 = target == 'loongarch64-linux'; rv64 = target == 'riscv64-linux'
        triple = 'x86_64-windows' if target == 'windows' else target
        ffi = work/('ffi-'+target+'.tx')
        ffi.write_text('declare function strlen(s: string): usize from '+json.dumps(libraries[target])
                       +';\nexport function main(args: []string): int { print(toStr(strlen("abc"))); return 0; }\n', encoding='utf8', newline='\n')
        callback = ROOT/'tests/nrt-cases'/callbacks[target]
        for flavor, source, flags in [*cases, ('ffi', ffi, []), ('callback', callback, [])]:
            dest = work/(target+'-'+flavor); mapping = work/(target+'-'+flavor+'.json')
            args = ['build', source, '--target='+triple, '--out', dest, *flags]
            p = run(*args, '--map', mapping, '--map-format=json')
            check('map:'+target+':'+flavor, p.returncode == 0 and mapping.is_file(), p.stderr)
            if p.returncode: continue
            doc = json.loads(mapping.read_bytes()); x = doc['map']; product = dest/(source.stem+('.exe' if target == 'windows' else '')); image = product.read_bytes()
            _, _, sections = layout_tools.image_layout(image)
            all_saves.update(validate('independent executable frames:'+target+':'+flavor, doc, image, sections))
            try: verify_boundaries(doc,image,target); boundaries_ok = True
            except (AssertionError, KeyError, ValueError, IndexError, struct.error): boundaries_ok = False
            check('actual special entry bytes and unknown frames:'+target+':'+flavor,boundaries_ok)
            if flavor == 'callback':
                shells = [f for f in x['functions'] if f['origin'] == 'ffi-callback-shell']
                check('callback shell declared coverage:'+target, bool(shells)
                      and 'ffi-callback-shell' in x['coverage']['included_function_origins'])
                pushes = [30,29,20,19,22,21,24,23,26,25,28,27] if a64 else [1,22,*range(23,32)] if la64 else [1,8,9,*range(18,28)] if rv64 \
                    else [3,5,6,7,12,13,14,15]
                pops = [27,28,25,26,23,24,21,22,19,20,29,30] if a64 else [*range(31,22,-1),22,1] if la64 else [*range(27,17,-1),9,8,1] if rv64 \
                    else [15,14,13,12,7,6,5,3]
                check('callback GPR saves and reverse restores:'+target, bool(shells) and all(
                    [e['register'] for e in f['frame']['events'] if e['kind'] == 'push-nonvolatile'] == pushes
                    and [e['register'] for e in f['frame']['events'] if e['kind'] == 'pop-nonvolatile'] == pops
                    and f['frame']['register_recovery'] == 'saved-gpr-events' and len(f['epilogues']) == 1
                    and int(f['epilogues'][0]['end_offset']) == int(f['bytes']) for f in shells))
            check('default skips raw symbol collection:'+target+':'+flavor, x['scope'] == 'functions' and x['symbols_total'] is None
                  and x['symbols'] == [] and x['coverage']['symbols'] == 'omitted-by-scope')
            check('explicit program object provenance:'+target+':'+flavor, all(f['object_index'] == (None if target == 'windows' else 1)
                  and f['input_section_index'] == (0 if target == 'windows' else 1) for f in x['functions'] if f['origin'] not in ['runtime-rtfn','process-thread-entry','runtime-manual']))
            ordinary = run(*args)
            check('ordinary identity:'+target+':'+flavor, ordinary.returncode == 0 and product.read_bytes() == image, ordinary.stderr)
            full = work/(target+'-'+flavor+'-all.json'); p = run(*args, '--map', full, '--map-format=json', '--map-scope=all')
            check('all scope same functions and product:'+target+':'+flavor, p.returncode == 0 and product.read_bytes() == image
                  and json.loads(full.read_bytes())['map']['functions'] == x['functions']
                  and int(json.loads(full.read_bytes())['map']['symbols_total']) > 0, p.stderr)
            if a.baseline_compiler:
                p = subprocess.run([a.baseline_compiler.resolve(), *map(str, args)], cwd=ROOT, capture_output=True, timeout=300)
                check('previous compiler client identity:'+target+':'+flavor, p.returncode == 0 and product.read_bytes() == image, p.stderr)
            if flavor == 'basic':
                for command in ['profile', 'disasm']:
                    cmd = ['profile', 'compile'] if command == 'profile' else ['disasm']
                    extra = ['--top', '0'] if command == 'profile' else []
                    p = run(*cmd, source, '--target='+triple, '--format=json', *extra)
                    other = json.loads(p.stdout); rows = other['profile']['functions'] if command == 'profile' else other['functions']
                    check('identity vs '+command+':'+target, p.returncode == 0 and sorted((f['source_id'], f['module'], f['name']) for f in x['functions'] if f['origin'] == 'tx-generated')
                          == sorted((f['id'] if command == 'profile' else f['index'], f['module'], f['name']) for f in rows))
                text = work/(target+'.txt'); p = run(*args, '--map', text)
                human = text.read_text(encoding='utf8') if text.is_file() else ''
                check('human function summary:'+target, p.returncode == 0 and 'link-map/6' in human and '右端排他' in human
                      and ('aarch64-gpr' if a64 else 'loongarch64-gpr' if la64 else 'riscv64-gpr' if rv64 else 'x86-64-gpr') in human
                      and '停止展开' in human and 'thread-entry' in human
                      and x['image']['sha256'] in human and all(json.dumps(f['name'], ensure_ascii=False) in human for f in x['functions']))
                explicit = work/(target+'-explicit.json'); p = run(*args, '--map', explicit, '--map-format=json', '--map-scope=functions')
                check('explicit functions equals default:'+target, p.returncode == 0 and json.loads(explicit.read_bytes()) == doc)
        if built.returncode == 0:
            p = subprocess.run([work/'driver-probe'/('native_function_driver_probe'+('.exe' if os.name == 'nt' else '')),
                                ROOT/'tests/debug/basic.tx', target], cwd=ROOT, capture_output=True, timeout=120)
            check('driver failure/listing/scope/reentry/no leaks:'+target, p.returncode == 0 and not p.stderr
                  and p.stdout.strip() == b'PASS_FUNCTION_DRIVER_REENTRY_FAILURE_LISTING_SCOPES', (p.returncode, p.stdout, p.stderr))
    check('production save register set valid', all_saves and all(s in {(5, 3, 14, 15), (5, 3, 14, 15, 12, 13), (30, 29, 20, 19, 21),
                                                                          (1, 22, 23, 24, 25), (1, 8, 9, 18, 19)} for s in all_saves), all_saves)
    for flags in [['--map-scope=functions'], ['--map-scope=all'], ['--map-scope=bogus', '--map', work/'bad.json']]:
        p = run('build', ROOT/'tests/debug/basic.tx', '--out', work/'usage', *flags)
        check('scope usage:'+str(flags), p.returncode == 64, (p.returncode, p.stderr))
    failed = sum(not c['ok'] for c in checks)
    report = {'status': 'PASS' if not failed else 'FAIL', 'total': len(checks), 'failed': failed, 'checks': checks,
              'baseline_comparison': 'run' if a.baseline_compiler else 'not requested'}
    (work/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print(f"functions: {len(checks)-failed}/{len(checks)} PASS", flush=True)
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
