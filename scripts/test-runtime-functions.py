"""Runtime producer/object facts; independent format, bytes and corruption checks."""
import argparse
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frame_validation', ROOT/'scripts/test-native-functions.py')
frames = importlib.util.module_from_spec(spec); spec.loader.exec_module(frames)


def loaded(image):
    """装载的内容：入口与各装载段（ELF 的 PT_LOAD、Mach-O 的生产者四节、PE 的节）的字节；ELF 头的 e_shoff、e_shentsize、
    e_shnum、e_shstrndx 不算（节头在全部 PT_LOAD 之后）。PE 不带函数符号，仍比整个文件。"""
    if image[:2] == b'MZ':
        return image
    _, entry, layout = frames.layout_tools.image_layout(image)
    body = bytearray(image)
    if image[:4] == b'\x7fELF':
        body[40:48] = bytes(8); body[58:64] = bytes(6)
    return entry, [(va, None if off is None else bytes(body[off:off+size]), memory) for va, off, size, memory in layout]


def sections(data, target):
    rows = []
    if target == 'windows':
        count = struct.unpack_from('<H', data, 2)[0]
        sym, nsyms = struct.unpack_from('<II', data, 8); strings = sym+nsyms*18
        for i in range(count):
            h = 20+i*40; name = data[h:h+8].split(b'\0')[0]
            if name.startswith(b'/'): name = data[strings+int(name[1:]):].split(b'\0')[0]
            size, at = struct.unpack_from('<II', data, h+16)
            rows.append(dict(index=i, header=h, name=name.decode('utf8'), at=at, size=size,
                             flags=struct.unpack_from('<I', data, h+36)[0]))
    else:
        headers = struct.unpack_from('<Q', data, 40)[0]; count, names_index = struct.unpack_from('<HH', data, 60)
        names_at, names_size = struct.unpack_from('<QQ', data, headers+names_index*64+24)
        names = data[names_at:names_at+names_size]
        for i in range(count):
            h = headers+i*64; name = struct.unpack_from('<I', data, h)[0]
            at, size = struct.unpack_from('<QQ', data, h+24)
            rows.append(dict(index=i, header=h, name=names[name:].split(b'\0')[0].decode('utf8'), at=at, size=size,
                             flags=struct.unpack_from('<Q', data, h+8)[0], name_at=names_at+name))
    return rows


def metadata(data, target):
    layout = sections(data, target); meta, = [s for s in layout if s['name'] == '.txfn']
    blob = data[meta['at']:meta['at']+meta['size']]
    assert blob[:4] == b'TXFN' and struct.unpack_from('<I', blob, 4)[0] == 3
    text_index, size = struct.unpack_from('<IQ', blob, 8); text = layout[text_index]
    code = data[text['at']:text['at']+text['size']]
    assert size == len(code) and blob[20:84].decode('ascii') == hashlib.sha256(code).hexdigest()
    assert meta['flags'] == (0x100a00 if target == 'windows' else 0)
    count = struct.unpack_from('<I', blob, 84)[0]; at = 88; rows = []

    def read(fmt):
        nonlocal at
        value = struct.unpack_from('<'+fmt, blob, at); at += struct.calcsize('<'+fmt)
        return value[0] if len(value) == 1 else value

    def text_value():
        nonlocal at
        n = read('I'); value = blob[at:at+n].decode('utf8'); at += n; return value

    for i in range(count):
        module, name, origin = text_value(), text_value(), text_value()
        model_position = meta['at']+at+4; model = text_value()
        begin_position = meta['at']+at; begin, end, prologue = read('QQQ')
        eps = [read('QQ') for _ in range(read('I'))]; events = []
        for _ in range(read('I')):
            position = meta['at']+at
            before, after, kind, register, amount = read('QQBBQ')
            events.append(dict(before=before, after=after, kind={1:'allocate-stack',2:'release-stack',3:'return',4:'push-nonvolatile',5:'pop-nonvolatile',6:'set-frame-pointer'}[kind],
                               reg=register-1, bytes=amount, position=position))
        assert 0 <= begin < end <= len(code) and begin <= prologue <= end
        if model != 'fixed-call': assert prologue == begin and not eps and not events
        rows.append(dict(module=module, name=name, origin=origin, begin=begin, end=end, prologue=prologue,
                         epilogues=eps, events=events, begin_position=begin_position,model=model,model_position=model_position))
    assert at == len(blob)
    return meta, text, rows


def absent(data, target):
    out = bytearray(data); meta, = [s for s in sections(data, target) if s['name'] == '.txfn']
    at = meta['header'] if target == 'windows' else meta['name_at']
    out[at:at+5] = b'.none'; return bytes(out)


def duplicate(data, target):
    layout = sections(data, target); meta, = [s for s in layout if s['name'] == '.txfn']
    if target == 'windows':
        boundary = 20+40*len(layout); out = bytearray(data[:boundary]+data[meta['header']:meta['header']+40]+data[boundary:])
        struct.pack_into('<H', out, 2, len(layout)+1)
        struct.pack_into('<I', out, 8, struct.unpack_from('<I', data, 8)[0]+40)
        for i in range(len(layout)+1):
            for offset in [20,24,28]:
                at = 20+i*40+offset; old = struct.unpack_from('<I',out,at)[0]
                if old: struct.pack_into('<I',out,at,old+40)
        struct.pack_into('<I',out,boundary+20,len(out))
    else:
        assert len(data) == struct.unpack_from('<Q',data,40)[0]+64*len(layout)
        out = bytearray(data+data[meta['header']:meta['header']+64])
        struct.pack_into('<H',out,60,len(layout)+1)
        struct.pack_into('<Q',out,len(data)+24,len(out))
    out.extend(data[meta['at']:meta['at']+meta['size']]); return bytes(out)


def long_coff_name(data):
    out = bytearray(data); meta, = [s for s in sections(data,'windows') if s['name'] == '.txfn']
    strings = struct.unpack_from('<I',data,8)[0]+18*struct.unpack_from('<I',data,12)[0]
    size = struct.unpack_from('<I',data,strings)[0]; assert strings+size == len(data)
    out.extend(b'.txfn\0'); struct.pack_into('<I',out,strings,size+6)
    out[meta['header']:meta['header']+8] = ('/'+str(size)).encode().ljust(8,b'\0'); return bytes(out)


def compare_rows(rows, actual):
    assert len(rows) == len(actual)
    for raw, f in zip(rows, actual):
        assert raw['model'] == f['stack_model']
        assert (raw['module'],raw['name'],raw['origin'],raw['begin'],raw['end'],raw['prologue']) == (
            f['module'],f['name'],f['origin'],int(f['input_section_begin']),int(f['input_section_end']),
            int(f['input_section_begin'])+(int(f['prologue_end_offset']) if f['prologue_end_offset'] is not None else 0))
        base = raw['begin']
        assert raw['epilogues'] == [(base+int(e['start_offset']),base+int(e['end_offset'])) for e in f['epilogues']]
        assert [(e['before'],e['after'],e['kind'],e['reg'],e['bytes']) for e in raw['events']] == [
            (base+int(e['instruction_start_offset']),base+int(e['instruction_end_offset']),e['kind'],
             -1 if e['register'] is None else e['register'],int(e['bytes'])) for e in (f['frame']['events'] if f['frame'] else [])]


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('--compiler',type=Path,required=True); ap.add_argument('--work',type=Path,required=True)
    a = ap.parse_args(); compiler = a.compiler.resolve(); work = a.work.resolve(); work.mkdir(parents=True,exist_ok=True); checks = []

    def check(name, ok, detail=''):
        checks.append(dict(name=name,ok=bool(ok))); print('PASS' if ok else 'FAIL',name,'' if ok else str(detail)[:1000],flush=True)

    def run(*args, timeout=300):
        return subprocess.run([compiler,*map(str,args)],cwd=ROOT,capture_output=True,timeout=timeout)

    built = run('build',ROOT/'bootstrap/runtime_function_probe.tx','--toolchain','--leak-check','--out',work/'probe')
    check('runtime ownership probe build',built.returncode == 0,built.stderr)
    fixture = work/'fixture'; fixture.mkdir(exist_ok=True)
    if built.returncode == 0:
        p = subprocess.run([work/'probe'/('runtime_function_probe'+('.exe' if os.name == 'nt' else '')),fixture],cwd=ROOT,capture_output=True,timeout=120)
        (work/'probe.stdout').write_bytes(p.stdout); (work/'probe.stderr').write_bytes(p.stderr)
        check('multiple/zero returns, malformed payload, ownership, missing/duplicate end, no leaks',p.returncode == 0 and not p.stderr,(p.returncode,p.stdout[:200],p.stderr))
        if p.returncode == 0:
            doc = json.loads(p.stdout); code = (fixture/'fixture.bin').read_bytes()
            frames.verify_functions(doc,code,[(5242880,512,len(code)-512,len(code)-512)])
            check('independent multi-epilogue bytes and explicit end',len(doc['map']['functions'][0]['epilogues']) == 2
                  and doc['map']['functions'][1]['epilogues'] == [])
            for target, suffix in [('windows','.obj'),('linux','.o')]:
                fixture_data = (fixture/('fixture'+suffix)).read_bytes()
                fixture_meta, _, rows = metadata(fixture_data,target)
                compare_rows(rows,doc['map']['functions']); check('independent object codec:'+target,True)
                shell = rows[2]; check('saved-GPR multiple epilogue object codec:'+target,
                    shell['origin'] == 'ffi-callback-shell' and len(shell['epilogues']) == 2
                    and [e['reg'] for e in shell['events'] if e['kind'] == 'push-nonvolatile'] == [3,12]
                    and [e['reg'] for e in shell['events'] if e['kind'] == 'pop-nonvolatile'] == [12,3,12,3])
                check('four boundary models survive object codec:'+target,
                    [r['model'] for r in rows[3:]] == ['process-entry','thread-entry','stack-transition','stack-disposal']
                    and all(r['origin'] == 'process-thread-entry' and not r['events'] and not r['epilogues'] for r in rows[3:]))
                for name, offset, value, width in [
                    ('unknown-stack-model',rows[3]['model_position'],ord('x'),1),
                    ('invented-boundary-prologue',rows[3]['begin_position']+16,rows[3]['begin']+1,8),
                    ('old-version-two',fixture_meta['at']+4,2,4)]:
                    corrupted = bytearray(fixture_data)
                    struct.pack_into({1:'<B',4:'<I',8:'<Q'}[width],corrupted,offset,value)
                    path = work/(target+'-'+name+suffix); path.write_bytes(corrupted)
                    output = work/(target+'-'+name); mapping = work/(target+'-'+name+'.json')
                    p = run('build',ROOT/'tests/debug/basic.tx','--target='+target,'--out',output,
                            '--link-object',ROOT/'bootstrap/nrt-kernel'/('kernel.obj' if target == 'windows' else 'kernel-linux.o'),
                            '--link-object',path,'--map',mapping,'--map-format=json')
                    check('reject boundary object:'+target+':'+name,p.returncode == 1 and not mapping.exists()
                          and not (output/('basic.exe' if target == 'windows' else 'basic')).exists()
                          and any(e in p.stderr for e in [b'UnknownStackModel',b'BadBoundaryFrame',b'UnknownFunctionMetadataVersion']),(p.returncode,p.stderr))
                for name, event_index, field_offset, value, width in [
                    ('invalid-save-register',0,17,1,1),('duplicate-save',1,17,4,1),
                    ('wrong-save-size',0,18,16,8),('second-epilogue-register',8,17,6,1)]:
                    corrupted = bytearray(fixture_data)
                    struct.pack_into('<B' if width == 1 else '<Q',corrupted,shell['events'][event_index]['position']+field_offset,value)
                    path = work/(target+'-'+name+suffix); path.write_bytes(corrupted)
                    output = work/(target+'-'+name); mapping = work/(target+'-'+name+'.json')
                    p = run('build',ROOT/'tests/debug/basic.tx','--target='+target,'--out',output,
                            '--link-object',ROOT/'bootstrap/nrt-kernel'/('kernel.obj' if target == 'windows' else 'kernel-linux.o'),
                            '--link-object',path,'--map',mapping,'--map-format=json')
                    check('reject saved-GPR object:'+target+':'+name,p.returncode == 1 and not mapping.exists()
                          and not (output/('basic.exe' if target == 'windows' else 'basic')).exists()
                          and b'BadFrame' in p.stderr,(p.returncode,p.stderr))

    # macOS 的两个内核同为 ELF 目标文件（机器号 62／183），产物交叉构建、只做静态核对。
    for target, kernel_name in [('windows','kernel.obj'),('linux','kernel-linux.o'),('x86_64-macos','kernel-macos.o'),('aarch64-macos','kernel-macos-aarch64.o')]:
        kernel = ROOT/'bootstrap/nrt-kernel'/kernel_name; data = kernel.read_bytes(); meta, text, rows = metadata(data,target)
        check('kernel has real multi-return functions:'+target,len(rows) > 200 and any(len(r['epilogues']) > 1 for r in rows))
        if target != 'windows':
            check('kernel object machine names the register numbering:'+target,
                  struct.unpack_from('<H',data,18)[0] == (183 if target == 'aarch64-macos' else 62))
        dest = work/target; mapping = work/(target+'.json')
        args = ['build',ROOT/'tests/debug/basic.tx','--target='+target,'--out',dest]
        p = run(*args,'--map',mapping,'--map-format=json','--link-object',kernel)
        check('kernel map:'+target,p.returncode == 0,p.stderr)
        if p.returncode: continue
        doc = json.loads(mapping.read_bytes()); product = dest/('basic'+('.exe' if target == 'windows' else '')); image = product.read_bytes()
        frames.verify_functions(doc,image,frames.layout_tools.image_layout(image)[2])
        frames.verify_boundaries(doc,image,target); check('kernel boundary ranges and real instruction endpoints:'+target,True)
        actual = [f for f in doc['map']['functions'] if f['object_index'] == 0]
        compare_rows(rows,actual)
        check('every kernel fact relocated and independently decoded:'+target,all(f['object_index'] == 0 and f['input_section_index'] == text['index'] for f in actual))
        stripped = work/('without-metadata-'+kernel_name); stripped.write_bytes(absent(data,target))
        p = run(*args,'--map',mapping,'--map-format=json','--link-object',stripped)
        bare = json.loads(mapping.read_bytes()) if p.returncode == 0 else {}
        # 目录不改程序：装载的内容（各段字节与入口）不变。ELF／Mach-O 的函数符号表随目录而定（有目录时含内核内部例程，
        # v052-image-symbols），不在装载范围里；ELF 头的节头字段也不属装载内容。
        check('absent directory is unknown and loaded image unchanged:'+target,p.returncode == 0 and loaded(product.read_bytes()) == loaded(image)
              and bare['map']['coverage']['object_metadata'] == {'schema':'txfn/3','present_objects':0,'absent_objects':1}
              and {f['origin'] for f in bare['map']['functions']} == {'tx-generated'},p.stderr)
        p = run(*args,'--link-object',kernel)
        check('metadata on/off identical product:'+target,p.returncode == 0 and product.read_bytes() == image,p.stderr)
        if target == 'windows':
            long_name = work/'long-name.obj'; long_name.write_bytes(long_coff_name(data))
            p = run(*args,'--map',mapping,'--map-format=json','--link-object',long_name)
            check('COFF long section name metadata:'+target,p.returncode == 0 and product.read_bytes() == image
                  and json.loads(mapping.read_bytes())['map']['functions'] == doc['map']['functions'],p.stderr)
        variants = {}
        for name, offset, value, width in [('version',meta['at']+4,1,4),('text-section',meta['at']+8,0xffffffff,4),
             ('text-size',meta['at']+12,0xffffffffffffffff,8),('count',meta['at']+84,0xffffffff,4),
             ('integer-overflow',rows[0]['begin_position'],0xffffffffffffffff,8),
             ('frame-kind',next(r for r in rows if r['events'])['events'][0]['position']+16,255,1),('code-identity',text['at'],data[text['at']]^1,1)]:
            bad = bytearray(data); struct.pack_into({1:'<B',4:'<I',8:'<Q'}[width],bad,offset,value); variants[name] = bad
        multi = next(r for r in rows if len(r['epilogues']) > 1)
        releases = [e for e in multi['events'] if e['kind'] == 'release-stack']
        bad = bytearray(data); struct.pack_into('<Q',bad,releases[1]['position']+18,releases[1]['bytes']+8); variants['second-epilogue-balance'] = bad
        variants['duplicate-section'] = duplicate(data,target)
        expected_errors = {'version':'UnknownFunctionMetadataVersion','text-section':'InvalidFunctionMetadataTextSection',
            'text-size':'FunctionMetadataTextMismatch','count':'InvalidFunctionMetadataCount','integer-overflow':'FunctionMetadataIntegerOverflow',
            'frame-kind':'UnknownFrameEvent','code-identity':'FunctionMetadataTextMismatch','second-epilogue-balance':'BadFrameRelease',
            'duplicate-section':'DuplicateFunctionMetadataSection'}
        for name, corrupted in variants.items():
            path = work/(target+'-bad-'+name+('.obj' if target == 'windows' else '.o')); path.write_bytes(corrupted)
            failed_map = work/(target+'-bad-'+name+'.json'); bad_dest = work/(target+'-bad-'+name)
            p = run('build',ROOT/'tests/debug/basic.tx','--target='+target,'--out',bad_dest,'--link-object',path,'--map',failed_map,'--map-format=json')
            check('reject invalid metadata:'+target+':'+name,p.returncode == 1 and not failed_map.exists()
                  and not (bad_dest/('basic'+('.exe' if target == 'windows' else ''))).exists()
                  and expected_errors[name].encode() in p.stderr,(p.returncode,p.stderr))
            if target != 'windows' and name in ('version','text-section','text-size','count','integer-overflow','duplicate-section'):
                # 产物函数符号（v052-image-symbols）每次构建都读内核目录的名字与起止：结构不合规的目录不带 --map 也拒绝，
                # 原因同名；代码摘要与帧事实只在 --map 时核对。
                p = run('build',ROOT/'tests/debug/basic.tx','--target='+target,'--out',bad_dest,'--link-object',path)
                check('reject invalid metadata without map:'+target+':'+name,p.returncode == 1
                      and not (bad_dest/'basic').exists() and expected_errors[name].encode() in p.stderr,(p.returncode,p.stderr))
        if built.returncode == 0 and (fixture/'fixture.o').exists() and target in ['windows', 'linux']:
            extra = fixture/('fixture.obj' if target == 'windows' else 'fixture.o')
            p = run(*args,'--link-object',kernel,'--link-object',extra,'--map',mapping,'--map-format=json')
            check('additional object directory:'+target,p.returncode == 0,p.stderr)
            if p.returncode == 0:
                mapped = json.loads(mapping.read_bytes()); extra_rows = [f for f in mapped['map']['functions'] if f['object_index'] == 1]
                check('discarded COFF / placed ELF preserve actual object identity:'+target,
                      len(extra_rows) == (0 if target == 'windows' else 7) and mapped['map']['coverage']['object_metadata']['present_objects'] == 2)
                new_image = product.read_bytes(); frames.verify_functions(mapped,new_image,frames.layout_tools.image_layout(new_image)[2])
    from runtime_security_checks import security_checks, oom_sweep_checks
    security_checks(compiler, work, check)
    oom_sweep_checks(compiler, work, check)
    failed = sum(not c['ok'] for c in checks)
    report = dict(status='FAIL' if failed else 'PASS',total=len(checks),failed=failed,checks=checks)
    (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print('runtime functions:',len(checks)-failed,'/',len(checks),'PASS',flush=True); return int(bool(failed))


if __name__ == '__main__': raise SystemExit(main())
