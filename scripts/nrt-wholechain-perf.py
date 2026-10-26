"""NRT M6 whole-chain timing: NRT compiler vs ReleaseFast Zig seed on the D102 loads.

Numerator: the NRT compiler (zero Zig host) building each load to a native exe.
Denominator: the Zig seed `aot --frontend=zig` building the same load to a native exe.
Both arms are timed from spawn to exit (load/parse/sema/codegen/emit/assemble/link/write).
Warmups are excluded; AB/BA alternation; N>=5; medians per arm; each load judged alone.
Products must behave identically before a sample counts. Results stay out of Git.
"""
import argparse
import io
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selfhost_validation import ROOT, clean_env, require_success, run, sha, write_json  # noqa: E402

LOADS = ['bootstrap/lexer.tx', 'bootstrap/parser.tx', 'bootstrap/semamirror.tx']
FIXTURE = ROOT / 'tests/perf/behavior.sein'
LIMIT = 6.0


TARGET = 'windows' if os.name == 'nt' else 'linux'


def exe_name(stem):
    return stem + ('.exe' if TARGET == 'windows' else '')


def target_flags():
    return ['--target=linux'] if TARGET == 'linux' else []


def behavior(exe, stem, directory, env):
    if stem == 'semamirror':
        seb = directory / 'behavior.seb1'
        fab = directory / 'behavior.fab1'
        row = run([exe, FIXTURE, seb, fab], ROOT, env, timeout=120)
        require_success(row)
        return {'exit': row['exit'], 'stdout': row['stdout'], 'stderr': row['stderr'],
                'seb1_sha256': sha(seb), 'fab1_sha256': sha(fab)}
    row = run([exe, 'tests/cases/10-switch.tx'], ROOT, env, timeout=60)
    require_success(row)
    return {'exit': row['exit'], 'stdout': row['stdout'], 'stderr': row['stderr']}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seed', type=Path, required=True, help='ReleaseFast Zig taixu (control arm)')
    ap.add_argument('--compiler', type=Path, required=True, help='NRT compiler (fixpoint-verified here)')
    ap.add_argument('--kernel', type=Path, required=True, help='NRT kernel object')
    ap.add_argument('--work', type=Path)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--skip-fixpoint', action='store_true')
    ap.add_argument('--target', choices=['windows', 'linux'], default=TARGET,
                    help='product target; must be runnable on this host')
    args = ap.parse_args()
    globals()['TARGET'] = args.target
    if args.runs < 5:
        ap.error('D102 requires N>=5')
    work = args.work.resolve() if args.work else Path(tempfile.mkdtemp(prefix='nrt-perf-'))
    if args.work and work.exists():
        ap.error('use a fresh --work directory')
    work.mkdir(parents=True, exist_ok=True)
    # Only this explicitly selected control arm needs archived Zig sources.
    pin = (ROOT / 'bootstrap/ZIG_SEED').read_text(encoding='utf-8').strip()
    archive = subprocess.check_output(['git', 'archive', '--format=tar', pin], cwd=ROOT)
    seed_source = work / 'seed-src'
    seed_source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
        tf.extractall(seed_source, filter='data')
    report = args.report or work / 'report.json'
    env = clean_env()
    # Windows: both arms write PE directly, so an empty PATH proves no tool is
    # spawned. Linux: the seed's aot assembles/links through `zig cc`, which is
    # part of that control arm, so PATH stays as is there.
    empty = work / 'empty-path'
    empty.mkdir()
    native_env = dict(env, PATH=str(empty))
    if TARGET == 'windows':
        env['PATH'] = str(empty)
    seed = work / 'seed' / exe_name('taixu')
    seed.parent.mkdir()
    shutil.copyfile(args.seed, seed)
    shutil.copymode(args.seed, seed)
    compiler = work / 'nrt' / exe_name('taixu')
    compiler.parent.mkdir()
    shutil.copyfile(args.compiler, compiler)
    shutil.copymode(args.compiler, compiler)
    kernel = work / 'kernel' / args.kernel.name
    kernel.parent.mkdir()
    shutil.copyfile(args.kernel, kernel)
    doc = {'schema': 'NRT M6 wholechain v1', 'N': args.runs, 'limit': LIMIT, 'target': TARGET,
           'clock': 'perf_counter_ns from spawn to exit',
           'seed_sha256': sha(seed), 'compiler_sha256': sha(compiler), 'kernel_sha256': sha(kernel),
           'seed_source_commit': pin, 'behavior_fixture_sha256': sha(FIXTURE),
           'source_sha256': {load: sha(ROOT / load) for load in LOADS},
           'environment': {k: v for k, v in env.items() if k.startswith('TAIXU_')},
           'path_policy': 'N: empty PATH; S: empty PATH on Windows, inherited PATH for zig cc on Linux',
           'warmup_policy': 'one excluded build per arm per load; AB/BA alternation per round',
           'samples': [], 'warmups': [], 'results': {}, 'status': 'RUNNING'}
    if not args.skip_fixpoint:
        dest = work / 'fixpoint'
        row = run([compiler, 'build', 'bootstrap/taixu.tx', '--toolchain', '--runtime=native',
                   *target_flags(), '--link-object', kernel, '--out', dest], ROOT, native_env)
        require_success(row)
        again = sha(dest / exe_name('taixu'))
        if again != doc['compiler_sha256']:
            raise RuntimeError('Measured compiler is not a fixpoint: ' + again)
        doc['fixpoint'] = 'self-build identical'
    # The seed's direct emission links a prebuilt host object from aot-cache/.
    # Prepare it outside the window (PATH intact for zig); this is a fixed
    # runtime input like the NRT kernel, not a compiler-result cache.
    cache = seed.parent / 'aot-cache'
    cache.mkdir()
    for load in LOADS:
        dest = work / ('host-' + Path(load).stem)
        exported = dest / 'host'
        row = run([args.seed.resolve(), 'aot', '--frontend=zig', *target_flags(), ROOT / load, '--out', dest,
                   '--emit-shim-obj=' + str(exported)], seed_source, clean_env())
        require_success(row)
        for obj in exported.iterdir():
            target = cache / obj.name
            if target.exists() and sha(target) != sha(obj):
                raise RuntimeError('Seed host objects differ between loads: ' + obj.name)
            shutil.copyfile(obj, target)
    # Linux has no direct-emission alias probe: the seed keys its host object by
    # cfg hash inside the same aot-cache/ and builds it with zig on first use.
    # One untimed build per load here fills that cache before the window.
    for load in LOADS:
        row = run([seed, 'aot', '--frontend=zig', *target_flags(), ROOT / load,
                   '--out', work / ('prime-' + Path(load).stem)], seed_source, clean_env())
        require_success(row)
    # Linux S arm assembles and links through `zig cc`, which caches results by
    # input. A cache hit would skip the work being timed (D102 forbids caches),
    # e.g. a rerun with the same paths reuses the previous run's link. Every S
    # sample gets a fresh copy of a base cache that only holds load-independent
    # toolchain pieces (libc/compiler-rt), built here from examples/hello.tx.
    zig_base = None
    if TARGET == 'linux':
        zig_base = work / 'zig-cache-base'
        base_env = clean_env()
        base_env['ZIG_GLOBAL_CACHE_DIR'] = str(zig_base)
        base_env['ZIG_LOCAL_CACHE_DIR'] = str(zig_base)
        row = run([seed, 'aot', '--frontend=zig', *target_flags(), ROOT / 'examples/hello.tx',
                   '--out', work / 'zig-base-hello'], seed_source, base_env)
        require_success(row)
        doc['zig_cache_policy'] = 'fresh per S sample, copied from a hello-only base outside the timed span'
    doc['seed_hosts'] = {p.name: sha(p) for p in sorted(cache.iterdir())}
    write_json(report, doc)
    for load_index, load in enumerate(LOADS):
        stem = Path(load).stem
        expected = None
        for i in range(-1, args.runs):
            order = ('N', 'S') if (load_index + max(i, 0)) % 2 == 0 else ('S', 'N')
            for arm in order:
                dest = work / f'{stem}-{arm}-{i + 1}'
                if arm == 'N':
                    cmd = [compiler, 'build', load, '--runtime=native', *target_flags(), '--link-object', kernel, '--out', dest]
                else:
                    cmd = [seed, 'aot', '--frontend=zig', *target_flags(), ROOT / load, '--out', dest]
                arm_env = native_env if arm == 'N' else env
                zig_cache = None
                if arm == 'S' and zig_base is not None:
                    zig_cache = work / f'zig-cache-{stem}-{i + 1}'
                    shutil.copytree(zig_base, zig_cache)
                    arm_env = dict(env)
                    arm_env['ZIG_GLOBAL_CACHE_DIR'] = str(zig_cache)
                    arm_env['ZIG_LOCAL_CACHE_DIR'] = str(zig_cache)
                row = run(cmd, ROOT if arm == 'N' else seed_source, arm_env)
                if zig_cache is not None:
                    shutil.rmtree(zig_cache)
                row.update(load=load, arm=arm, index=i)
                require_success(row)
                if arm == 'S' and {p.name: sha(p) for p in sorted(cache.iterdir())} != doc['seed_hosts']:
                    raise RuntimeError('Seed host cache changed inside the window')
                product = dest / exe_name(stem)
                row['product_sha256'] = sha(product)
                row['product_bytes'] = product.stat().st_size
                row['behavior'] = behavior(product, stem, dest, env)
                if expected is None:
                    expected = row['behavior']
                elif row['behavior'] != expected:
                    raise RuntimeError('Product behavior mismatch: ' + load + ' ' + arm)
                (doc['warmups'] if i == -1 else doc['samples']).append(row)
                write_json(report, doc)
                print(stem, arm, i, f"{row['ns'] / 1e9:.3f}s", flush=True)
        values = {arm: [s['ns'] for s in doc['samples'] if s['load'] == load and s['arm'] == arm]
                  for arm in ('N', 'S')}
        med = {arm: statistics.median(v) for arm, v in values.items()}
        ratio = med['N'] / med['S']
        doc['results'][load] = {'N_ns': values['N'], 'S_ns': values['S'], 'median_N': med['N'],
                                'median_S': med['S'], 'ratio': ratio, 'pass': ratio <= LIMIT}
        write_json(report, doc)
    doc['status'] = 'PASS' if all(r['pass'] for r in doc['results'].values()) else 'FAIL'
    write_json(report, doc)
    for load, r in doc['results'].items():
        print(f"{Path(load).stem:12s} N={r['median_N'] / 1e9:.3f}s S={r['median_S'] / 1e9:.3f}s "
              f"ratio={r['ratio']:.2f} {'<' if r['pass'] else '>='}{LIMIT:g}")
    print('STATUS', doc['status'], 'report', report)
    return 0 if doc['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
