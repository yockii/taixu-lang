"""对象与函数元数据的结构化 fuzz（安全审查攻关第 3 项，docs/design/v052-object-fuzz-v0.1-20261025.md）。

以独立构造的 ELF64／COFF 夹具（test-native-link-map.py）和编译器写出的带 .txfn 的对象（runtime_function_probe）为种子，按
固定种子做结构感知变异，每例一个新的受限进程（Windows 作业对象：进程数、提交内存、关闭即杀；Linux：RLIMIT_AS、RLIMIT_CPU、
不留 core），只解析与链接、不执行产物：

  F1 单字段（0／1／±1／上限／随机，随机字节，截断）   F2 偏移与长度成对（和溢出、越过文件末尾 1 字节、表长非项长整数倍）
  F3 重定位（加数极值、类型互换改宽度、位置贴节尾）   F4 节重叠（节、符号表与字符串表、重定位表与目标节）
  F5 对齐（0、非 2 的幂、极大；COFF 保留的对齐值）    F6 结构边界截断（头、每个节头、每张表的末尾及 ±1）
  F7 .txfn 函数元数据（经 taixu build … --map；版本、节号、长度、计数、串长与偏移，及字段边界截断）

F1–F6 经链接探针 native_link_map_probe（生产链接器的同一份代码）跑两遍：标准内核（--leak-check）与第 2 项的页堆内核，结局须
相同。每例只允许干净拒绝（退出 1、stderr 空、无部分映射，原因是解析期的拒绝）或成功（映射与写出的映像一致）；崩溃、信号、
超时、限额触发、泄漏或页堆报告、链接器内部不变式类的原因（*-OOB、AssemblerFailed、BadProgram*、Program*Overflow）都算失败。
批前批后各以新进程跑未变异的夹具，映像须逐字节相同。失败样本连同按字节回退的最小化样本存进 --work。

  python scripts/test-object-fuzz.py --compiler <taixu> --work <dir> [--cases N]

宿主为 Windows 或 Linux（x86_64）。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from importlib import import_module

linkmap = import_module('test-native-link-map')
rtf = import_module('test-runtime-functions')
products = import_module('test-nrt-products')

WINDOWS = os.name == 'nt'
EXE = '.exe' if WINDOWS else ''
SEED = 20261025
U64 = (1 << 64) - 1
LIMIT_BYTES = 1 << 30
INTERNAL = re.compile(r'-OOB|^AssemblerFailed|^BadProgram|^Program\w*Overflow|^__tx_entry')


def reasons():
    """链接器源码里解析期的拒绝原因（字面量与带后缀的前缀）。由源码提取，判据跟着源码走。"""
    found = set()
    for name in ['link_objects.tx', 'link_elf_objects.tx', 'link_macho.tx', 'link_pe.tx']:
        text = (ROOT / 'bootstrap/selfhost' / name).read_text(encoding='utf-8')
        found.update(re.findall(r'(?:lkoReject|lkFail|reject)\("([^"]+)"', text))
    return sorted(r for r in found if not INTERNAL.search(r))


def limited(argv, cwd, label, timeout=30, processes=1):
    """一个新的受限进程；返回 (退出码或 None, stdout, stderr, 说明)。"""
    argv = [str(x) for x in argv]
    if WINDOWS:
        from selfhost_windows_job import run_job
        try:
            r = run_job(argv, cwd, dict(os.environ), Path(cwd) / ('job-' + label), timeout=timeout,
                        process_limit=processes, memory_bytes=LIMIT_BYTES)
        except TimeoutError:
            return None, '', '', 'timeout'
        finally:
            for suffix in ('.stdout', '.stderr'):
                (Path(cwd) / ('job-' + label + suffix)).unlink(missing_ok=True)
        return r['exit'], r['stdout'], r['stderr'], ''

    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        # macOS 不支持按地址空间设限（setrlimit(RLIMIT_AS) 失败）：只限 CPU 与 core，内存不设限。
        if sys.platform != 'darwin':
            resource.setrlimit(resource.RLIMIT_AS, (LIMIT_BYTES * 2, LIMIT_BYTES * 2))
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, preexec_fn=limits)
    except subprocess.TimeoutExpired:
        return None, '', '', 'timeout'
    return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace'), ''


# ---------------------------------------------------------------------------------------------------------------------
# 夹具的独立结构（只读夹具自己的字节；不用编译器的解码）

def elf_layout(data):
    shoff, = struct.unpack_from('<Q', data, 0x28)
    shnum, = struct.unpack_from('<H', data, 0x3c)
    secs = []
    for i in range(shnum):
        h = shoff + 64 * i
        kind, = struct.unpack_from('<I', data, h + 4)
        off, size = struct.unpack_from('<QQ', data, h + 24)
        link, info = struct.unpack_from('<II', data, h + 40)
        entsize, = struct.unpack_from('<Q', data, h + 56)
        secs.append(dict(index=i, header=h, kind=kind, off=off, size=size, link=link, info=info, entsize=entsize))
    return shoff, secs


def coff_layout(data):
    count, = struct.unpack_from('<H', data, 2)
    symptr, nsyms = struct.unpack_from('<II', data, 8)
    secs = []
    for i in range(count):
        h = 20 + 40 * i
        size, ptr, relptr = struct.unpack_from('<III', data, h + 16)
        nrel, = struct.unpack_from('<H', data, h + 32)
        secs.append(dict(index=i, header=h, size=size, ptr=ptr, relptr=relptr, nrel=nrel))
    strings = symptr + 18 * nsyms
    return secs, symptr, nsyms, strings


def put(data, at, width, value):
    if 0 <= at and at + width <= len(data):
        data[at:at + width] = (value & ((1 << (8 * width)) - 1)).to_bytes(width, 'little')


def families(mode, source):
    """F2–F6：按夹具结构枚举的变异，每项 (族, 描述, 变异函数)。"""
    out = []
    n = len(source)
    if mode == 'elf':
        shoff, secs = elf_layout(source)
        for s in secs:
            h = s['header']
            out += [('F2', 'sec%d off+size overflows' % s['index'], lambda d, h=h, s=s: put(d, h + 24, 8, U64 - s['size'] + 1)),
                    ('F2', 'sec%d one past end' % s['index'], lambda d, h=h, s=s: put(d, h + 24, 8, n - s['size'] + 1)),
                    ('F2', 'sec%d size to end+1' % s['index'], lambda d, h=h, s=s: put(d, h + 32, 8, n - s['off'] + 1)),
                    ('F2', 'sec%d size max' % s['index'], lambda d, h=h: put(d, h + 32, 8, U64))]
            if s['entsize']:
                out.append(('F2', 'sec%d size not entry multiple' % s['index'], lambda d, h=h, s=s: put(d, h + 32, 8, s['size'] + 1)))
                out.append(('F2', 'sec%d entsize 0' % s['index'], lambda d, h=h: put(d, h + 56, 8, 0)))
            for v in [0, 3, 1 << 63, 1 << 32]:
                out.append(('F5', 'sec%d align %#x' % (s['index'], v), lambda d, h=h, v=v: put(d, h + 48, 8, v)))
            for t in secs:
                if t['index'] != s['index'] and s['kind'] != 0 and t['kind'] != 0:
                    out.append(('F4', 'sec%d overlaps sec%d' % (s['index'], t['index']),
                                lambda d, h=h, t=t: (put(d, h + 24, 8, t['off']), put(d, h + 32, 8, t['size']))))
            if s['kind'] == 4 and s['entsize'] == 24:  # SHT_RELA
                target = secs[s['info']] if s['info'] < len(secs) else None
                for r in range(s['off'], s['off'] + s['size'], 24):
                    info, = struct.unpack_from('<Q', source, r + 8)
                    for a in [(1 << 63) - 1, -(1 << 63), (1 << 31) - 1, -(1 << 31), 1 << 31, -(1 << 31) - 1]:
                        out.append(('F3', 'rela@%d addend %d' % (r, a), lambda d, r=r, a=a: put(d, r + 16, 8, a)))
                    for kind in [1, 2, 4, 9, 10, 11, 24, 42]:
                        out.append(('F3', 'rela@%d type %d' % (r, kind),
                                    lambda d, r=r, info=info, kind=kind: put(d, r + 8, 8, (info & ~0xffffffff) | kind)))
                    if target:
                        for w in [1, 4, 8]:
                            out.append(('F3', 'rela@%d site end-%d+1' % (r, w), lambda d, r=r, t=target, w=w: put(d, r, 8, t['size'] - w + 1)))
        bounds = {64, shoff} | {shoff + 64 * i for i in range(len(secs) + 1)}
        for s in secs:
            if s['kind'] != 8:  # SHT_NOBITS 不占文件
                bounds |= {s['off'], s['off'] + s['size']}
    else:
        secs, symptr, nsyms, strings = coff_layout(source)
        for s in secs:
            h = s['header']
            out += [('F2', 'sec%d ptr+size overflows u32' % s['index'], lambda d, h=h, s=s: put(d, h + 20, 4, 0xffffffff - s['size'] + 1)),
                    ('F2', 'sec%d one past end' % s['index'], lambda d, h=h, s=s: put(d, h + 20, 4, n - s['size'] + 1)),
                    ('F2', 'sec%d size to end+1' % s['index'], lambda d, h=h, s=s: put(d, h + 16, 4, n - s['ptr'] + 1)),
                    ('F2', 'sec%d relocs past end' % s['index'], lambda d, h=h, s=s: put(d, h + 24, 4, n - 10 * max(s['nrel'], 1) + 1)),
                    ('F2', 'sec%d reloc count max' % s['index'], lambda d, h=h: put(d, h + 32, 2, 0xffff))]
            flags, = struct.unpack_from('<I', source, h + 36)
            for nib in [0xF, 0xE, 0x0]:
                out.append(('F5', 'sec%d align nibble %#x' % (s['index'], nib),
                            lambda d, h=h, flags=flags, nib=nib: put(d, h + 36, 4, (flags & ~0x00F00000) | (nib << 20))))
            for t in secs:
                if t['index'] != s['index'] and t['size'] and s['size']:
                    out.append(('F4', 'sec%d overlaps sec%d' % (s['index'], t['index']),
                                lambda d, h=h, t=t: (put(d, h + 20, 4, t['ptr']), put(d, h + 16, 4, t['size']))))
            for k in range(s['nrel']):
                r = s['relptr'] + 10 * k
                site, = struct.unpack_from('<I', source, r)
                kind, = struct.unpack_from('<H', source, r + 8)
                for v in [1, 2, 3, 4, 5, 9, 10, 11, 0x10]:
                    out.append(('F3', 'reloc@%d type %d' % (r, v), lambda d, r=r, v=v: put(d, r + 8, 2, v)))
                for w in [1, 4, 8]:
                    out.append(('F3', 'reloc@%d site end-%d+1' % (r, w), lambda d, r=r, s=s, w=w: put(d, r, 4, s['size'] - w + 1)))
                for a in [(1 << 63) - 1, -(1 << 63), (1 << 31) - 1, -(1 << 31)]:
                    width = 8 if kind == 1 else 4
                    out.append(('F3', 'reloc@%d implicit addend %d' % (r, a), lambda d, at=s['ptr'] + site, a=a, width=width: put(d, at, width, a)))
            out.append(('F4', 'sec%d relocs overlap symbols' % s['index'], lambda d, h=h: put(d, h + 24, 4, symptr)))
        out += [('F2', 'symbols past end', lambda d: put(d, 8, 4, n - 18 * nsyms + 1)),
                ('F2', 'symbol count max', lambda d: put(d, 12, 4, 0xffffffff)),
                ('F2', 'symbol count to end+1', lambda d: put(d, 12, 4, (n - symptr) // 18 + 1)),
                ('F4', 'symbols overlap section 0', lambda d: put(d, 8, 4, secs[0]['ptr'] if secs else 0)),
                ('F2', 'string table size max', lambda d: put(d, strings, 4, 0xffffffff)),
                ('F2', 'string table size end+1', lambda d: put(d, strings, 4, n - strings + 1))]
        bounds = {20, symptr, strings, n} | {20 + 40 * i for i in range(len(secs) + 1)}
        for s in secs:
            bounds |= {s['ptr'], s['ptr'] + s['size'], s['relptr'] + 10 * s['nrel']}
    for b in sorted(bounds):
        for at in (b - 1, b, b + 1):
            if 0 < at < n:
                out.append(('F6', 'truncate@%d' % at, lambda d, at=at: d.__delitem__(slice(at, None))))
    return out


def minimize(source, data, fails):
    """按字节回退：把与原样本不同的字节逐个改回，仍失败就保留回退。截断的样本不缩。"""
    if len(data) != len(source):
        return bytes(data)
    cur = bytearray(data)
    for i in range(len(cur)):
        if cur[i] != source[i]:
            old = cur[i]
            cur[i] = source[i]
            if not fails(bytes(cur)):
                cur[i] = old
    return bytes(cur)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--cases', type=int, default=60, help='F1 与 F7 每格式的随机例数；F2–F6 枚举后各族至多取这么多')
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    work = a.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rows = []
    bad = 0

    def check(label, ok, detail=''):
        nonlocal bad
        rows.append({'check': label, 'ok': bool(ok), 'detail': detail if ok else str(detail)[:3000]})
        print('%s %s%s' % ('PASS' if ok else 'FAIL', label, '' if ok else ': ' + str(detail)[:600]), flush=True)
        if not ok:
            bad += 1
        return ok

    def sh(*args, cwd=ROOT, timeout=600):
        p = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode('utf-8', 'replace') + p.stderr.decode('utf-8', 'replace')

    accepted_reasons = reasons()
    check('rejection reasons extracted from the linker source', len(accepted_reasons) > 30, accepted_reasons)

    # 判据的自检（正控制）：每种该判失败的结局都须判失败，干净拒绝与成功须判通过。
    def outcome(code, out, err, note, image=None, mode='pe'):
        """返回 ('reject'|'accept'|'fail', 原因)。"""
        if note:
            return 'fail', note
        if code is None or code not in (0, 1):
            return 'fail', 'exit %s' % code
        if err.strip():
            return 'fail', 'stderr ' + err.strip()[:200]
        try:
            doc = json.loads(out)
        except ValueError:
            return 'fail', 'stdout is not JSON'
        if code == 1:
            reason = doc.get('error')
            if doc.get('ok') is not False or doc.get('map', 'missing') is not None or not isinstance(reason, str) or not reason:
                return 'fail', 'unclean reject %r' % out[:200]
            if INTERNAL.search(reason):
                return 'fail', 'internal invariant ' + reason
            if not any(reason == r or (r.endswith(':') or r.endswith(': ')) and reason.startswith(r) for r in accepted_reasons):
                if mode != 'pe':
                    return 'fail', 'unknown reason ' + reason[:120]
                return 'reject', 'name:' + reason[:60]  # PE 的未解析符号以名字本身为原因（lkFail(名字)）
            return 'reject', reason
        if doc.get('ok') is not True or image is None or not image.is_file():
            return 'fail', 'accepted without image'
        body = image.read_bytes()
        if int(doc['map']['image']['bytes']) != len(body) or doc['map']['image']['sha256'] != hashlib.sha256(body).hexdigest():
            return 'fail', 'map disagrees with image'
        return 'accept', ''

    probe_reject = json.dumps({'ok': False, 'map': None, 'error': 'BadImage'})
    for label, args, want in [
        ('clean reject passes', (1, probe_reject, '', ''), 'reject'),
        ('trap fails', (3, '', 'x.tx:1:1: [runtime:out_of_bounds]', ''), 'fail'),
        ('access violation fails', (3221225477 if WINDOWS else -11, '', '', ''), 'fail'),
        ('timeout fails', (None, '', '', 'timeout'), 'fail'),
        ('leak report fails', (1, probe_reject, '[runtime:leak] 1 object', ''), 'fail'),
        ('page heap report fails', (3, '', '[runtime:corrupt_code] heapcheck: 重复释放', ''), 'fail'),
        ('internal invariant fails', (1, json.dumps({'ok': False, 'map': None, 'error': 'reloc-OOB sec=.text'}), '', ''), 'fail'),
        ('partial map fails', (1, json.dumps({'ok': False, 'map': {}, 'error': 'BadImage'}), '', ''), 'fail'),
        ('unknown ELF reason fails', (1, json.dumps({'ok': False, 'map': None, 'error': 'whatever'}), '', '', None, 'elf'), 'fail'),
    ]:
        check('oracle: ' + label, outcome(*args)[0] == want, outcome(*args))

    # 探针：标准内核（--leak-check）与页堆内核。页堆内核由本树的内核生成器生成。
    # 生成器在本宿主上跑：Linux 显式给目标，Windows 与 macOS 取宿主缺省（同 test-heap-check；macOS 上给 linux 得 ELF，执行即 ENOEXEC）。
    tflag = ['--target=linux'] if not WINDOWS and sys.platform != 'darwin' else []
    code, text = sh(compiler, 'build', 'bootstrap/runtime_memory_gen.tx', '--runtime=native', *tflag, '--out', work / 'gen')
    if not check('kernel generator build', code == 0, text):
        return finish(work, rows, bad)
    gen = work / 'gen' / ('runtime_memory_gen' + EXE)
    # 页堆探针按本宿主构建：Apple silicon 的 macOS 上内核与探针都取 aarch64-macos（同 test-heap-check）。
    host = 'x86_64-windows' if WINDOWS else ('aarch64-macos' if sys.platform == 'darwin' else 'x86_64-linux')
    host_flag = ['--target=aarch64-macos'] if sys.platform == 'darwin' else []
    kernels = {}
    for name, target, extra in [('heapcheck', host, ['--generic=heapcheck']), ('windows', 'x86_64-windows', []), ('linux', 'x86_64-linux', [])]:
        k = work / ('kernel-%s.o' % name)
        code, text = sh(gen, '--target', target, k, *products.KERNEL_FLAGS, *extra)
        if not check('kernel ' + name, code == 0 and k.is_file(), text):
            return finish(work, rows, bad)
        kernels[name] = k
    probes = {}
    for name, extra in [('standard', ['--leak-check']), ('heapcheck', ['--link-object', kernels['heapcheck']])]:
        code, text = sh(compiler, 'build', 'bootstrap/native_link_map_probe.tx', '--toolchain', *host_flag, *extra, '--out', work / ('probe-' + name))
        if not check('link probe build ' + name, code == 0, text):
            return finish(work, rows, bad)
        probes[name] = work / ('probe-' + name) / ('native_link_map_probe' + EXE)

    runs = work / 'runs'
    runs.mkdir()
    counter = [0]

    def link(probe, mode, objects):
        counter[0] += 1
        tag = '%d' % counter[0]
        image = runs / (tag + '.image')
        paths = []
        for i, data in enumerate(objects):
            p = runs / ('%s-%d.o' % (tag, i))
            p.write_bytes(data)
            paths.append(p)
        code, out, err, note = limited([probe, mode, image, *paths], runs, tag)
        result = outcome(code, out, err, note, image, mode)
        sha = hashlib.sha256(image.read_bytes()).hexdigest() if result[0] == 'accept' else None
        for p in [image, *paths]:
            p.unlink(missing_ok=True)
        return result, sha

    fixtures = {}
    for mode, factory in [('elf', linkmap.elf_object), ('pe', linkmap.coff_object)]:
        fixtures[mode] = (factory(False)[0], factory(True)[0])

    # 新进程同结果：批前的基线。
    baseline = {}
    for mode, (first, second) in fixtures.items():
        for name, probe in probes.items():
            (kind, why), sha = link(probe, mode, [first, second])
            check('baseline %s %s links' % (mode, name), kind == 'accept', why)
            baseline[(mode, name)] = sha
        check('baseline %s: page-heap probe writes the same image' % mode, baseline[(mode, 'standard')] == baseline[(mode, 'heapcheck')])

    failures = []
    counts = {}
    for mode, (first, second) in fixtures.items():
        rng = random.Random(SEED + (mode == 'pe'))
        cases = []
        fields = linkmap.object_fields(mode, first)
        for k in range(a.cases):
            data = bytearray(first)
            how = linkmap.mutate(rng, data, fields)
            cases.append(('F1', how, bytes(data)))
        enumerated = {}
        for fam, how, fn in families(mode, first):
            enumerated.setdefault(fam, []).append((how, fn))
        for fam in sorted(enumerated):
            picked = enumerated[fam]
            if len(picked) > a.cases:
                picked = random.Random(SEED + int(fam[1:])).sample(picked, a.cases)
            for how, fn in picked:
                data = bytearray(first)
                fn(data)
                if bytes(data) != first:
                    cases.append((fam, how, bytes(data)))
        for fam, how, data in cases:
            results = {}
            for name, probe in probes.items():
                results[name] = link(probe, mode, [data, second])
            kinds = {name: r[0][0] for name, r in results.items()}
            key = (mode, fam, kinds['standard'])
            counts[key] = counts.get(key, 0) + 1
            same = results['standard'][0][0] == results['heapcheck'][0][0] and results['standard'][1] == results['heapcheck'][1]
            if 'fail' in kinds.values() or not same:
                def still(d, mode=mode):
                    rs = [link(p, mode, [d, second]) for p in probes.values()]
                    return any(r[0][0] == 'fail' for r in rs) or rs[0][0][0] != rs[1][0][0]
                index = len(failures)
                (work / ('failure-%d.o' % index)).write_bytes(data)
                (work / ('failure-%d-min.o' % index)).write_bytes(minimize(first, data, still))
                failures.append({'mode': mode, 'family': fam, 'mutation': how, 'sample': 'failure-%d.o' % index,
                                 'standard': results['standard'][0], 'heapcheck': results['heapcheck'][0]})
    for (mode, fam, kind), n in sorted(counts.items()):
        print('  %s %s %s=%d' % (mode, fam, kind, n), flush=True)
    check('structured object fuzz (seed %d): every case cleanly rejected or consistently linked, page heap agrees' % SEED,
          not failures, failures[:8])

    # 新进程同结果：批后再跑基线。
    for mode, (first, second) in fixtures.items():
        for name, probe in probes.items():
            (kind, why), sha = link(probe, mode, [first, second])
            check('after the batch, %s %s gives the baseline image' % (mode, name), kind == 'accept' and sha == baseline[(mode, name)], why)

    # F7：.txfn 函数元数据，经 taixu build … --map。夹具由 runtime_function_probe 写出（编译器自己的对象写出端）。
    code, text = sh(compiler, 'build', 'bootstrap/runtime_function_probe.tx', '--toolchain', '--out', work / 'rf-probe')
    fixture = work / 'rf-fixture'
    fixture.mkdir()
    if check('runtime function probe build', code == 0, text):
        code, text = sh(work / 'rf-probe' / ('runtime_function_probe' + EXE), fixture)
        check('runtime function fixture objects', code == 0, text[-500:])
    basic = work / 'basic.tx'
    basic.write_text('export function main(args: []string): int { print("fuzz"); return 0; }\n', encoding='utf-8', newline='\n')
    txfn_failures = []
    txfn_counts = {}
    for target, suffix in [('windows', '.obj'), ('linux', '.o')]:
        path = fixture / ('fixture' + suffix)
        if not path.is_file():
            continue
        source = path.read_bytes()
        meta, _, frows = rtf.metadata(source, target)
        base, size = meta['at'], meta['size']
        spots = [(base + 4, 4), (base + 8, 4), (base + 12, 8), (base + 84, 4)]
        for r in frows:
            spots += [(r['model_position'] - 4, 4), (r['begin_position'], 8), (r['begin_position'] + 8, 8), (r['begin_position'] + 16, 8),
                      (r['begin_position'] + 24, 4)]
            for e in r['events']:
                spots += [(e['position'], 8), (e['position'] + 16, 1), (e['position'] + 17, 1), (e['position'] + 18, 8)]
        rng = random.Random(SEED + 7 + (target == 'linux'))
        txcases = [('F7', 'baseline', source)]
        for k in range(a.cases):
            data = bytearray(source)
            at, width = rng.choice(spots)
            top = (1 << (8 * width)) - 1
            value = rng.choice([0, 1, top, top - 1, top >> 1, (top >> 1) + 1, rng.randrange(top + 1)])
            put(data, at, width, value)
            txcases.append(('F7', 'txfn@%d/%d=%#x' % (at - base, width, value), bytes(data)))
        for at in sorted({base + 4, base + 8, base + 20, base + 84, base + 88, base + size - 1} | {r['begin_position'] for r in frows}):
            # 节内截断：把 .txfn 的长度字段改小（节头里的大小），解码器须报截断而不是越界读。
            data = bytearray(source)
            hdr = [s for s in rtf.sections(source, target) if s['name'] == '.txfn'][0]['header']
            if target == 'windows':
                put(data, hdr + 16, 4, at - base)
            else:
                put(data, hdr + 32, 8, at - base)
            txcases.append(('F7', 'txfn size cut@%d' % (at - base), bytes(data)))
        for fam, how, data in txcases:
            counter[0] += 1
            tag = 'txfn%d' % counter[0]
            obj = runs / (tag + suffix)
            obj.write_bytes(data)
            out = runs / tag
            mapping = runs / (tag + '.json')
            code, so, se, note = limited([compiler, 'build', basic, '--target=' + target, '--out', out, '--link-object', kernels[target],
                                          '--link-object', obj, '--map', mapping, '--map-format=json'], runs, tag, timeout=120, processes=4)
            product = out / ('basic.exe' if target == 'windows' else 'basic')
            if note or code not in (0, 1):
                kind = 'fail'
            elif code == 1:
                kind = 'reject' if se.startswith('driver:') and se.count('\n') <= 1 and not mapping.exists() and not product.exists() else 'fail'
            else:
                try:
                    doc = json.loads(mapping.read_text(encoding='utf-8'))
                    kind = 'accept' if doc.get('ok') is True and product.is_file() else 'fail'
                except (OSError, ValueError):
                    kind = 'fail'
            if how == 'baseline':
                check('function metadata baseline links with a map: ' + target, kind == 'accept', (code, se[-300:], note))
                continue
            label = kind if kind != 'reject' else 'reject ' + re.sub(r'[\s:].*', '', se.split('失败', 1)[-1].strip())[:40]
            txfn_counts[(target, label)] = txfn_counts.get((target, label), 0) + 1
            if kind == 'fail':
                keep = work / ('txfn-failure-%d%s' % (len(txfn_failures), suffix))
                keep.write_bytes(data)
                txfn_failures.append({'target': target, 'mutation': how, 'sample': keep.name, 'exit': code, 'note': note,
                                      'stderr': se[-300:]})
            shutil.rmtree(out, ignore_errors=True)
            for p in [obj, mapping]:
                p.unlink(missing_ok=True)
    for (target, kind), n in sorted(txfn_counts.items()):
        print('  txfn %s %s=%d' % (target, kind, n), flush=True)
    check('function metadata fuzz (seed %d): every case cleanly rejected or linked with a map' % (SEED + 7),
          sum(txfn_counts.values()) > 0 and not txfn_failures, txfn_failures[:8])
    return finish(work, rows, bad)


def finish(work, rows, bad):
    (work / 'object-fuzz.json').write_text(json.dumps({'seed': SEED, 'rows': rows, 'failed': bad}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('SUMMARY %s object-fuzz: %d checks, %d failed' % ('PASS' if bad == 0 else 'FAIL', len(rows), bad), flush=True)
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
