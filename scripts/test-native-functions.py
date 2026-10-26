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
    assert set(x['coverage']['excluded_function_origins']) == {'runtime-manual', 'linker-thunks', 'external-objects-without-metadata'}
    assert set(x['coverage']['included_function_origins']) == {f['origin'] for f in functions}
    assert x['coverage']['object_metadata']['schema'] == 'txfn/3'
    assert 'sp_delta=decimal-string/i64' in x['numeric_encoding']
    a64 = x['target'].startswith('aarch64-')
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
        assert frame['entry_sp'] == ('call-site-sp' if a64 else 'return-address-slot')
        assert frame['register_numbering'] == ('aarch64-gpr' if a64 else 'x86-64-gpr')
        assert frame['body_sp'] == 'fixed' and frame['register_recovery'] == ('saved-gpr-events' if tx or f['origin'] == 'ffi-callback-shell' else 'not-provided')
        # sp is relative to entry; saved holds (register, bytes). One A64 instruction may carry several events (stp/ldp).
        position = 0; sp = 0; saved = []; original = []; allocation = None; returned = False; ep_index = -1; pro_end = 0
        body_sp = None; fp = None; events = frame['events']; i = 0
        while i < len(events):
            before = int(events[i]['instruction_start_offset']); after = int(events[i]['instruction_end_offset'])
            group = [events[i]]; i += 1
            while a64 and i < len(events) and (int(events[i]['instruction_start_offset']), int(events[i]['instruction_end_offset'])) == (before, after):
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
            decoded = a64_frame(code, sp, fp) if a64 else [(group[0]['kind'], group[0]['register'], int(group[0]['bytes']), frame_bytes(group[0], code))]
            assert [(e['kind'], e['register'], int(e['bytes']), int(e['sp_delta'])) for e in group] == decoded, (f['name'], before, group, code.hex())
            for e in group:
                kind = e['kind']; reg = e['register']; size_ = int(e['bytes'])
                if kind == 'push-nonvolatile':
                    assert pro and (tx or a64 or f['origin'] == 'ffi-callback-shell'); saved.append((reg, size_)); original.append((reg, size_))
                if kind == 'set-frame-pointer':
                    assert (saved[-2:] == [(30, 8), (29, 8)]) if a64 else (saved == [(5, 8)]); fp = sp
                if kind == 'allocate-stack': assert pro and (allocation is None or a64); allocation = (allocation or 0)+size_
                if kind == 'release-stack': assert not pro and size_ == allocation
                if kind == 'pop-nonvolatile': assert saved.pop() == (reg, size_)
                if kind == 'return': assert not pro and sp == 0 and not saved and after == eps[ep_index][1]; returned = True
                sp += int(e['sp_delta'])
            if a64: assert sp % 16 == 0, (f['name'], before, sp)
        assert pro_end == prologue and ep_index+1 == len(eps)
        if eps: assert returned and sp == (0 if a64 else 8)
        if tx: saves_seen.add(tuple(r for r, _ in original))
    for left, right in zip(sorted(ranges), sorted(ranges)[1:]): assert left[1] <= right[0], (left, right)
    return saves_seen


def verify_boundaries(doc, image, target):
    rows = {f['link_name']: f for f in doc['map']['functions']}
    special = {n:f for n,f in rows.items() if f['origin'] == 'process-thread-entry'}
    expected = {'__tx_thread_entry':'thread-entry'}
    if target == 'linux': expected.update({'_start':'process-entry','__tx_thread_clone':'stack-transition','__tx_thread_unmap_exit':'stack-disposal'})
    assert {n:f['stack_model'] for n,f in special.items()} == expected
    code = lambda f: image[int(f['file_offset']):int(f['file_offset'])+int(f['bytes'])]
    for f in special.values():
        assert f['object_index'] == 0 and f['frame'] is None and f['prologue_end_offset'] is None and f['epilogues'] == []
    f = special['__tx_thread_entry']; body = code(f); at = 1
    if target == 'aarch64-macos':
        # stp x29, x30, [sp, #-32]!; mov x29, sp; str x19, [sp, #16]; bl __tx_thread_main; ldr x19, [sp, #16];
        # ldp x29, x30, [sp], #32; movz x0, #0; ret
        words = struct.unpack('<%dI' % (len(body)//4), body); call = words[3]
        assert len(body) == 32 and words[:3] == (0xA9BE7BFD, 0x910003FD, 0xF9000BF3) and call >> 26 == 0x25
        assert int(f['link_va'],16)+12+4*(((call & 0x3FFFFFF) ^ 0x2000000)-0x2000000) == int(rows['__tx_thread_main']['link_va'],16)
        assert words[4:] == (0xF9400BF3, 0xA8C27BFD, 0xD2800000, 0xD65F03C0)
        return
    assert body[:1] == b'\x53'
    if target in ['linux', 'x86_64-macos']: assert body[at:at+3] in [b'\x48\x89\xf9',b'\x48\x8b\xcf']; at += 3
    size = 4 if body[at:at+2] == b'\x48\x83' else 7
    frame_bytes({'kind':'allocate-stack','register':4,'bytes':32},body[at:at+size]); at += size
    assert body[at] == 0xe8 and int(f['link_va'],16)+at+5+struct.unpack_from('<i',body,at+1)[0] == int(rows['__tx_thread_main']['link_va'],16)
    at += 5; size = 4 if body[at:at+2] == b'\x48\x83' else 7
    frame_bytes({'kind':'release-stack','register':4,'bytes':32},body[at:at+size]); at += size
    assert body[at:] in [b'\x5b\x31\xc0\xc3',b'\x5b\x33\xc0\xc3']
    if target == 'linux':
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
    # macOS 两个目标在任何宿主上交叉构建、只做静态核对（产物字节与映射）。
    libraries = {'windows': 'ucrtbase', 'linux': 'libc.so.6', 'x86_64-macos': 'libSystem', 'aarch64-macos': 'libSystem'}
    callbacks = {'windows': 'ffi-win-callback.tx', 'linux': 'ffi-linux-callback-outside.tx',
                 'x86_64-macos': 'ffi-macos-lib.tx', 'aarch64-macos': 'ffi-macos-lib.tx'}
    for target in ['windows', 'linux', 'x86_64-macos', 'aarch64-macos']:
        a64 = target == 'aarch64-macos'
        ffi = work/('ffi-'+target+'.tx')
        ffi.write_text('declare function strlen(s: string): usize from '+json.dumps(libraries[target])
                       +';\nexport function main(args: []string): int { print(toStr(strlen("abc"))); return 0; }\n', encoding='utf8', newline='\n')
        callback = ROOT/'tests/nrt-cases'/callbacks[target]
        for flavor, source, flags in [*cases, ('ffi', ffi, []), ('callback', callback, [])]:
            dest = work/(target+'-'+flavor); mapping = work/(target+'-'+flavor+'.json')
            args = ['build', source, '--target='+target, '--out', dest, *flags]
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
                pushes = [30,29,20,19,22,21,24,23,26,25,28,27] if a64 else [3,5,6,7,12,13,14,15]
                pops = [27,28,25,26,23,24,21,22,19,20,29,30] if a64 else [15,14,13,12,7,6,5,3]
                check('callback GPR saves and reverse restores:'+target, bool(shells) and all(
                    [e['register'] for e in f['frame']['events'] if e['kind'] == 'push-nonvolatile'] == pushes
                    and [e['register'] for e in f['frame']['events'] if e['kind'] == 'pop-nonvolatile'] == pops
                    and f['frame']['register_recovery'] == 'saved-gpr-events' and len(f['epilogues']) == 1
                    and int(f['epilogues'][0]['end_offset']) == int(f['bytes']) for f in shells))
            check('default skips raw symbol collection:'+target+':'+flavor, x['scope'] == 'functions' and x['symbols_total'] is None
                  and x['symbols'] == [] and x['coverage']['symbols'] == 'omitted-by-scope')
            check('explicit program object provenance:'+target+':'+flavor, all(f['object_index'] == (None if target == 'windows' else 1)
                  and f['input_section_index'] == (0 if target == 'windows' else 1) for f in x['functions'] if f['origin'] not in ['runtime-rtfn','process-thread-entry']))
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
                    p = run(*cmd, source, '--target='+target, '--format=json', *extra)
                    other = json.loads(p.stdout); rows = other['profile']['functions'] if command == 'profile' else other['functions']
                    check('identity vs '+command+':'+target, p.returncode == 0 and sorted((f['source_id'], f['module'], f['name']) for f in x['functions'] if f['origin'] == 'tx-generated')
                          == sorted((f['id'] if command == 'profile' else f['index'], f['module'], f['name']) for f in rows))
                text = work/(target+'.txt'); p = run(*args, '--map', text)
                human = text.read_text(encoding='utf8') if text.is_file() else ''
                check('human function summary:'+target, p.returncode == 0 and 'link-map/6' in human and '右端排他' in human and ('aarch64-gpr' if a64 else 'x86-64-gpr') in human
                      and '停止展开' in human and 'thread-entry' in human
                      and x['image']['sha256'] in human and all(json.dumps(f['name'], ensure_ascii=False) in human for f in x['functions']))
                explicit = work/(target+'-explicit.json'); p = run(*args, '--map', explicit, '--map-format=json', '--map-scope=functions')
                check('explicit functions equals default:'+target, p.returncode == 0 and json.loads(explicit.read_bytes()) == doc)
        if built.returncode == 0:
            p = subprocess.run([work/'driver-probe'/('native_function_driver_probe'+('.exe' if os.name == 'nt' else '')),
                                ROOT/'tests/debug/basic.tx', target], cwd=ROOT, capture_output=True, timeout=120)
            check('driver failure/listing/scope/reentry/no leaks:'+target, p.returncode == 0 and not p.stderr
                  and p.stdout.strip() == b'PASS_FUNCTION_DRIVER_REENTRY_FAILURE_LISTING_SCOPES', (p.returncode, p.stdout, p.stderr))
    check('production save register set valid', all_saves and all(s in {(5, 3, 14, 15), (5, 3, 14, 15, 12, 13), (30, 29, 20, 19, 21)} for s in all_saves), all_saves)
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
