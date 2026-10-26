"""Independent C/assembly checks called by the existing runtime-functions gate.

TAIXU_TEST_CC selects a GCC/Clang compatible test compiler. It is a verification
dependency only; neither the language compiler nor generated programs use it.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/runtime-security'


def security_checks(compiler, parent, check):
    work = Path(parent) / 'independent-security'
    work.mkdir(parents=True, exist_ok=True)
    windows = os.name == 'nt'
    macos = sys.platform == 'darwin'
    target = 'windows' if windows else ('macos' if macos else 'linux')
    # 直调内核例程的 C 驱动在 macOS 上经 Mach-O 目标文件执行（v052-macho-object）：arm64 原生跑可移植版，x86_64 经
    # Rosetta 跑手写与可移植版。try-io 的 macOS 探针对象带 SIGPIPE 处置（__tx_tool_nopipe），libSystem 调用由 try-io.c 提供。
    suffix = '.exe' if windows else ''
    cc = os.environ.get('TAIXU_TEST_CC') or next((p for n in ['cc', 'gcc', 'clang'] if (p := shutil.which(n))), None)
    check('independent-security: C verifier available', bool(cc), 'set TAIXU_TEST_CC to a GCC/Clang compatible compiler')
    if not cc:
        return
    rows = []

    def save():
        report = {'compiler_sha256': hashlib.sha256(Path(compiler).read_bytes()).hexdigest(),
                  'target': target, 'cc': cc, 'rows': rows}
        (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')

    def execute(exe, args=(), label='run', processes=1, extra_env=None):
        env = os.environ.copy()
        env['LD_LIBRARY_PATH'] = str(work)
        env.update(extra_env or {})
        if windows:
            from selfhost_windows_job import run_job
            row = run_job([exe, *args], work, env, work / ('job-'+label), timeout=15, process_limit=processes, memory_bytes=2*1024**3)
        else:
            def limits():
                import resource
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
                if not macos:  # macOS 没有可用的 RLIMIT_AS（setrlimit 即失败）
                    resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))
            p = subprocess.run([str(exe), *map(str, args)], cwd=work, env=env, capture_output=True, timeout=15, preexec_fn=limits)
            row = dict(exit=p.returncode, stdout=p.stdout.decode('utf-8', 'replace'), stderr=p.stderr.decode('utf-8', 'replace'))
        row['label'] = label
        rows.append(row)
        save()
        return row

    def cbuild(name, sources, flags=()):
        for header in FIXTURES.glob('*.h'):  # 驱动共用的独立分配器（ledger.h）
            shutil.copyfile(header, work / header.name)
        for source in sources:
            if (FIXTURES / source).exists():
                shutil.copyfile(FIXTURES / source, work / source)
        p = subprocess.run([cc, '-O2', '-Wall', '-Wextra', *flags, *sources, '-o', name], cwd=work, capture_output=True, timeout=60)
        check('independent-security: C build '+name, p.returncode == 0, p.stderr)
        return p.returncode == 0

    def txbuild(source, name, flags=()):
        out = work / name
        p = subprocess.run([str(compiler), 'build', str(source), *flags, '--out', str(out)], cwd=ROOT, capture_output=True, timeout=300)
        check('independent-security: TX build '+name, p.returncode == 0, p.stderr)
        return out / (Path(source).stem+suffix) if p.returncode == 0 else None

    # (目标三元组, cc 的架构参数, 名字后缀)：macOS 宿主为 Apple silicon，arm64 原生、x86_64 经 Rosetta（目标文件为 Mach-O）。
    arches = [('aarch64-macos', [], '-aarch64'), ('x86_64-macos', ['-arch', 'x86_64'], '-x86_64')] if macos else [('x86_64-'+target, [], '')]
    # 访问无权限页：Windows 0xC0000005；Linux SIGSEGV；macOS 为 SIGBUS 或 SIGSEGV。
    fault = [3221225477] if windows else ([-10, 138, -11, 139] if macos else [-11, 139])
    generators = {}

    def generator(producer):
        # 测试专用的对象生成器（bootstrap/native_*_probe.tx）属工具链程序：读 TAIXU_PROBE_FORMAT、写文件。每个只编一次。
        if producer not in generators:
            generators[producer] = txbuild(ROOT/'bootstrap'/(producer+'.tx'), producer, ['--toolchain'])
        return generators[producer]
    for triple, archflags, tag in arches:
        a64 = triple.startswith('aarch64')
        for producer, cname, modes in [
            # arm64 内核全为可移植版（rtfn），没有手写的 plain／nrt。
            ('native_rc_boundary_probe', 'rc-boundary.c', ['generic'] if a64 else ['plain', 'nrt', 'generic']),
            # quarantine：页堆内核的对象号隔离（检测形态，设计 v052-heap-check-tool）。
            ('native_store_slots_probe', 'slot-faults.c', ['', 'quarantine']),
            ('native_try_io_probe', 'try-io.c', ['']),
            ('native_weak_table_probe', 'weak-faults.c', ['']),
            *([('native_windows_argv_probe', 'argv-split.c', [''])] if windows else []),
        ]:
            gen = generator(producer)
            if not gen:
                continue
            for mode in modes:
                name = producer + ('-'+mode if mode else '') + tag
                obj = work / (name+('.obj' if windows else '.o'))
                generated = execute(gen, ['--target', triple, obj, *([mode] if mode else [])], name+'-object')
                check('independent-security: object '+name, generated['exit'] == 0 and obj.is_file(), generated)
                if generated['exit'] != 0:
                    continue
                binary = 'check-'+name+suffix
                if not cbuild(binary, [cname, obj.name], [*archflags, *{'nrt': ['-DRC_NRT'], 'generic': ['-DRC_NRT', '-DRC_GENERIC'], 'quarantine': ['-DSLOTS_QUARANTINE']}.get(mode, [])]):
                    continue
                row = execute(work/binary, label=name, processes=2 if cname == 'argv-split.c' else 1)  # argv 驱动逐个起子进程走完整取参路径
                marker = {'rc-boundary.c':'PASS RC', 'slot-faults.c':'PASS slots:', 'try-io.c':'PASS try-write', 'weak-faults.c':'PASS weak:', 'argv-split.c':'PASS argv:'}[cname]
                check('independent-security: '+name, row['exit'] == 0 and marker in row['stdout'], row)
                if cname == 'rc-boundary.c' and mode in ('nrt', 'generic'):
                    # 过度释放（运行时加固 H1）：计数已为 0 再释放，快路径与三个通用臂都以 4012 终止、不返回。
                    for arm in range(4):
                        row = execute(work/binary, ['over-release', arm], '%s%s-over-release-%d' % (mode, tag, arm))
                        check('independent-security: %s%s over-release arm %d' % (mode, tag, arm), row['exit'] == 88 and
                              'TRAP code=4012 fn=ffffffff pc=0' in row['stdout'] and '引用计数下溢' in row['stdout'], row)
                if cname == 'slot-faults.c' and mode == 'quarantine':
                    # 负对照：检测形态的检查对标准形态的对象必须失败（标准槽表立即复用号码、状态只有 64 字节）。
                    plain = work / ('native_store_slots_probe' + tag + ('.obj' if windows else '.o'))
                    if plain.is_file() and cbuild(binary + '-negative', [cname, plain.name], [*archflags, '-DSLOTS_QUARANTINE']):
                        row = execute(work/(binary + '-negative'), label=name + '-negative')
                        check('independent-security: '+name+' negative (standard slots)', row['exit'] != 0 and 'PASS slots:' not in row['stdout'], row)
                if cname == 'slot-faults.c':
                    for control, expected in [('invalid-free', [84]), ('double-free', [85]), ('left-canary', [86]),
                                              ('right-bound', fault), ('use-after-free', fault)]:
                        row = execute(work/binary, [control], control+tag)
                        check('independent-security: detector control '+control+tag, row['exit'] in expected, row)

    # Mach-O 目标文件对照同一映像的 ELF（独立解析，scripts/macho_object_check.py）：任何宿主都可交叉生成，三端都执行。
    sys.path.insert(0, str(ROOT / 'scripts'))
    import macho_object_check
    for triple, cases in [('aarch64-macos', [('native_rc_boundary_probe', 'generic'), ('native_store_slots_probe', ''), ('native_store_slots_probe', 'quarantine'),
                                             ('native_weak_table_probe', '')]),
                          ('x86_64-macos', [('native_rc_boundary_probe', 'plain'), ('native_rc_boundary_probe', 'nrt'), ('native_rc_boundary_probe', 'generic'),
                                            ('native_store_slots_probe', ''), ('native_store_slots_probe', 'quarantine'), ('native_weak_table_probe', '')])]:
        for producer, mode in cases:
            gen = generator(producer)
            if not gen:
                continue
            name = 'pair-' + producer + ('-'+mode if mode else '') + '-' + triple
            pair = []
            for fmt in ['elf', 'macho']:
                obj = work / (name+'.'+fmt+'.o')
                row = execute(gen, ['--target', triple, obj, *([mode] if mode else [])], name+'-'+fmt, extra_env={'TAIXU_PROBE_FORMAT': fmt})
                pair.append(obj.read_bytes() if row['exit'] == 0 and obj.is_file() else None)
            try:
                problems = macho_object_check.check(*pair) if None not in pair else ['generation failed']
            except (AssertionError, ValueError, IndexError, KeyError, struct.error) as e:  # 解析不了即不一致
                problems = ['unparseable: %r' % e]
            check('independent-security: Mach-O object vs ELF '+name[len('pair-'):], not problems, problems[:8])

    if not windows and not macos and cbuild('reaper-layout', ['reaper-layout.c', 'reaper-signal-layout.c']):
        row = execute(work / 'reaper-layout', label='reaper-layout')
        check('independent-security: Linux reaper ABI', row['exit'] == 0 and 'PASS independent Linux reaper ABI headers' in row['stdout'], row)

    library = 'ffi_safety_probe.dll' if windows else ('libffi_safety_probe.dylib' if macos else 'libffi_safety_probe.so')
    # macOS 按 LC_LOAD_DYLIB 绑定库路径（D3 外部库写法：绝对路径、@executable_path/、@loader_path/），给绝对路径；其余宿主
    # 产物旁边的库即可（同 test-heap-check.py）。
    named = str(work / library) if macos else library
    # 回调前后被调方保存的向量寄存器：Windows xmm6–15、Apple arm64 d8–d15（宿主为 Apple silicon，产物为 aarch64-macos）。
    simd = ['xmm-windows.S'] if windows else (['simd-arm64.S'] if macos else [])
    if not cbuild(library, ['ffi-probe.c', *simd], ['-shared', *(['-static-libgcc'] if windows else ['-fPIC'])]):
        return
    for mode, flags in [('normal', []), ('leak', ['--leak-check']), ('debug', ['--debug'])]:
        for fixture in ['ffi-bytes', *(['ffi-xmm'] if simd else [])]:
            source = work / (fixture+'.tx')
            source.write_text((FIXTURES/(fixture+'.tx')).read_text(encoding='utf-8').replace('from "ffi_safety_probe"', 'from "'+named+'"'), encoding='utf-8', newline='\n')
            product = txbuild(source, fixture+'-'+mode, flags)
            if not product:
                continue
            shutil.copyfile(work/library, product.parent/library)
            if fixture == 'ffi-xmm':
                row = execute(product, label=fixture+'-'+mode)
                check('independent-security: '+('XMM128 ' if windows else 'A64 d8-d15 ')+mode, row['exit'] == 0 and 'PASS_XMM128' in row['stdout'], row)
            else:
                # D3 v0.34：借用期内任何改变长度的操作 trap 4024，与容量无关；*-spare 先留出空余容量（v0.33 的按容量规则
                # 放行这几种），list-push-spare 走内核快路径。
                for action in ['safe', 'push', 'empty-push', 'append', 'list-push', 'list-insert', 'after-nested',
                               'push-spare', 'append-spare', 'list-push-spare', 'list-pop', 'list-remove']:
                    row = execute(product, [action], fixture+'-'+mode+'-'+action)
                    # 4024 须由内建按调用处报出（带源码位置）：落到运行时内部不带位置的兜底，说明有快路径漏查借用位。
                    if action == 'safe':
                        expected = row['exit'] == 0 and 'PASS_FFI_BYTES_ALIAS' in row['stdout']
                    else:
                        expected = row['exit'] == 3 and '[runtime:ffi_buffer_borrow]' in row['stderr'] and 'ffi-bytes.tx:' in row['stderr']
                    check('independent-security: Bytes '+mode+':'+action, expected, row)


def oom_sweep_checks(compiler, parent, check):
    """内存上限扫描（安全复审 v0.2 攻关第 1、2 项）：tests/runtime-security/oom-sweep.tx 在逐级收紧的进程内存上限下
    运行，每次只允许三种结局——跑完且校验和正确；致命 OOM（退出码 3、“taixu: 内存不足”、无别的输出）；进程在太虚
    代码运行之前就起不来（Windows 加载器的 0xC0000142／0xC0000017、Linux execve 失败，均无任何输出）。先找出能跑完
    的最小上限，再在其三分之一到它之间均匀取点，不按宿主写死数字。Windows 为 Job 提交上限，Linux 为 RLIMIT_AS。"""
    if sys.platform == 'darwin':
        # 未执行，不记通过：macOS 不执行 RLIMIT_AS（setrlimit 即失败），内存上限由工具链的看守线程按物理足迹事后执行，
        # 扫描另需按那套机制设计。
        print('SKIP oom-sweep: macOS 宿主上不执行（没有可用的 RLIMIT_AS）；取页失败路径由门步 oom-fault 注入扫描覆盖', flush=True)
        return
    work = Path(parent) / 'oom-sweep'
    work.mkdir(parents=True, exist_ok=True)
    windows = os.name == 'nt'
    out = work / 'product'
    p = subprocess.run([str(compiler), 'build', str(FIXTURES / 'oom-sweep.tx'), '--out', str(out)], cwd=ROOT, capture_output=True, timeout=300)
    check('oom-sweep: build', p.returncode == 0, (p.stdout + p.stderr)[-800:])
    if p.returncode:
        return
    exe = out / ('oom-sweep' + ('.exe' if windows else ''))
    rows = []

    def capped(limit):
        if windows:
            from selfhost_windows_job import run_job
            row = run_job([str(exe)], str(work), os.environ.copy(), work / ('job-%d' % limit), timeout=30, memory_bytes=limit)
        else:
            def limits():
                import resource
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            try:
                q = subprocess.run([str(exe)], cwd=work, capture_output=True, timeout=30, preexec_fn=limits)
                row = dict(exit=q.returncode, stdout=q.stdout.decode('utf-8', 'replace'), stderr=q.stderr.decode('utf-8', 'replace'))
            except OSError as e:
                row = dict(exit=None, stdout='', stderr='', exec_error=str(e))
        row['limit'] = limit
        rows.append(row)
        return row

    full = capped(4 * 1024 ** 3)
    expected = full.get('stdout', '').strip()
    check('oom-sweep: unlimited run', full.get('exit') == 0 and expected.startswith('PASS_OOM_SWEEP '), full)
    if full.get('exit') != 0:
        return

    def kind(row):
        out, err, code = row.get('stdout', ''), row.get('stderr', ''), row.get('exit')
        lines = out.splitlines()
        if code == 0 and not err and lines and lines[-1] == expected and set(lines[:-1]) <= {'JOIN_FAILED'}:
            return 'pass'
        if code == 3 and err == 'taixu: 内存不足\n' and set(lines) <= {'JOIN_FAILED'}:
            return 'oom'
        if not out and not err and (code is None if not windows else code in (0xC0000142, 0xC0000017)):
            return 'loader'
        return 'other'

    low, high = 1024 * 1024, 4 * 1024 ** 3
    while high - low > 64 * 1024:  # 能跑完的最小上限（近似：线程调度可能让边界附近不单调，扫描本身不依赖它精确）
        mid = (low + high) // 2
        if kind(capped(mid)) == 'pass':
            high = mid
        else:
            low = mid
    points = 96
    seen = {}
    for i in range(points + 1):
        limit = high // 3 + (high - high // 3) * i // points
        k = kind(capped(limit))
        seen.setdefault(k, []).append(limit)
    joins = sum(r.get('stdout', '').count('JOIN_FAILED') for r in rows if kind(r) == 'pass')
    (work / 'report.json').write_text(json.dumps({'minimal_pass': high, 'join_failed_runs': joins,
                                                  'outcomes': {k: len(v) for k, v in seen.items()},
                                                  'rows': rows}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    others = [r for r in rows if kind(r) == 'other']
    check('oom-sweep: every capped run passes or ends in the defined fatal OOM', not others, others[:5])
    check('oom-sweep: the sweep reaches allocation failures', len(seen.get('oom', [])) >= 8, {k: len(v) for k, v in seen.items()})
