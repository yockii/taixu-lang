"""冻结的前端输出：bootstrap/lexer.tx 的记号流、parser.tx 的 AST（二进制与文本）、`doc --format=json` 与 spike/loads 的诊断。

按文件冻结原生输出（stdout 的 SHA-256 与退出码），防前端回归。输入要稳定，冻结的输出才有意义（同 Go 的 testdata＋golden、
Rust insta 的快照）：
- 夹具（tests/cases、examples 等）取工作树，清单 tests/frozen/frontend-outputs.json。清单同时记每个输入文件的 SHA-256：
  输入没变而输出变了是前端回归；输入变了则报“输入已改”，审阅输出变化后 --update（夹具改动要连同冻结值一起提交）。两种都判失败。
- 编译器自己的源码 bootstrap/ 每个提交都在变，开发仓另以 tests/frozen/frontend-pinned.json 固定一个提交，经 git archive
  取那时的 bootstrap/ 作输入。公开仓没有这份清单（也没有那段提交历史），只查夹具；开发仓的门带 --require-pinned，缺了即失败。

用法：python scripts/test-frontend-frozen.py --compiler <原生 taixu> [--update] [--require-pinned]
--update 按当前输出重写清单（有固定提交清单时一并重写）。清单外的新文件只提示。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tests/frozen/frontend-outputs.json'
PINNED_MANIFEST = ROOT / 'tests/frozen/frontend-pinned.json'
CORPUS = ['tests/cases', 'tests/nrt-cases', 'examples', 'docs/design', 'skill/taixu/examples', 'spike/loads']
PINNED = 'bootstrap'
SEMA = 'spike/loads'
EXE = '.exe' if os.name == 'nt' else ''


def pinned_tree(work, commit):
    """固定提交的 bootstrap/，展开到 work/pinned；返回展开根。"""
    p = subprocess.run(['git', 'archive', '--format=tar', commit, PINNED], cwd=ROOT, capture_output=True)
    if p.returncode != 0:
        raise SystemExit('FAIL 取不到 %s 的 %s（需要 git 与可达的提交）：%s' % (commit, PINNED, p.stderr.decode('utf-8', 'replace').strip()))
    dest = work / 'pinned'
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as archive:
        archive.extractall(dest, filter='data')
    return dest


def corpus(pinned_root):
    """(相对路径, 所在根) 的有序列表：夹具取工作树，bootstrap 取固定提交（没有固定提交清单时不取）。"""
    files = {}
    for d in CORPUS:
        for p in (ROOT / d).rglob('*.tx'):
            files[p.relative_to(ROOT).as_posix()] = ROOT
    if pinned_root is not None:
        for p in (pinned_root / PINNED).rglob('*.tx'):
            files[p.relative_to(pinned_root).as_posix()] = pinned_root
    return sorted(files.items())


def digest(argv, cwd):
    p = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=300)
    return '%d:%s' % (p.returncode, hashlib.sha256(p.stdout.replace(b'\r\n', b'\n')).hexdigest())


def diagnostics(compiler, rel):
    """dev-check 的诊断，去掉随宿主变化的 path 与渲染文本。"""
    p = subprocess.run([compiler, 'dev-check', rel], cwd=ROOT, capture_output=True, timeout=300)
    out = []
    for line in p.stdout.decode('utf-8', 'replace').splitlines():
        row = json.loads(line)
        if 'code' in row:
            out.append({k: row.get(k) for k in ('code', 'module', 'line', 'col', 'msg')})
    return {'exit': p.returncode, 'diags': out}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path)
    ap.add_argument('--update', action='store_true', help='按当前输出重写清单')
    ap.add_argument('--require-pinned', action='store_true', help='固定提交的清单必须存在（开发仓的门）')
    a = ap.parse_args()
    compiler = str(a.compiler.resolve())
    work = (a.work or Path(tempfile.mkdtemp(prefix='taixu-frozen-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    tools = {}
    for name in ('lexer', 'parser'):
        out = work / name
        shutil.rmtree(out, ignore_errors=True)
        p = subprocess.run([compiler, 'build', 'bootstrap/%s.tx' % name, '--toolchain', '--out', str(out)], cwd=ROOT,
                           capture_output=True, timeout=900)
        if p.returncode != 0:
            print('FAIL build', name, p.stdout.decode('utf-8', 'replace')[-500:], p.stderr.decode('utf-8', 'replace')[-500:])
            return 1
        tools[name] = str(out / (name + EXE))
    pinned_doc = None
    if PINNED_MANIFEST.is_file():
        pinned_doc = json.loads(PINNED_MANIFEST.read_text(encoding='utf-8'))
    elif a.require_pinned:
        print('FAIL 缺 %s（开发仓的门要求固定提交的输入）' % PINNED_MANIFEST.relative_to(ROOT).as_posix())
        return 1
    else:
        print('NOTE 没有 %s：只查夹具，不查 %s/ 的固定提交输入' % (PINNED_MANIFEST.relative_to(ROOT).as_posix(), PINNED))
    pinned_root = pinned_tree(work, pinned_doc['commit']) if pinned_doc else None
    files = corpus(pinned_root)
    inputs = {rel: hashlib.sha256((base / rel).read_bytes()).hexdigest() for rel, base in files}
    jobs = {}
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        for rel, base in files:
            jobs[('lexer', rel)] = pool.submit(digest, [tools['lexer'], rel], base)
            jobs[('parser-bin', rel)] = pool.submit(digest, [tools['parser'], rel, '--bin'], base)
            jobs[('parser-text', rel)] = pool.submit(digest, [tools['parser'], rel], base)
            jobs[('doc', rel)] = pool.submit(digest, [compiler, 'doc', '--format=json', rel], base)
            if rel.startswith(SEMA + '/'):
                jobs[('sema', rel)] = pool.submit(diagnostics, compiler, rel)
        current = {}
        for (kind, rel), job in jobs.items():
            current.setdefault(kind, {})[rel] = job.result()
    if a.update:
        compiler_sha = hashlib.sha256(Path(compiler).read_bytes()).hexdigest()
        outputs = [(MANIFEST, json.loads(MANIFEST.read_text(encoding='utf-8'))['note'] if MANIFEST.is_file() else '', {}, False)]
        if pinned_doc:
            outputs.append((PINNED_MANIFEST, pinned_doc['note'], {'commit': pinned_doc['commit']}, True))
        for path, note, extra, mine in outputs:
            doc = {'note': note, 'compiler_sha256': compiler_sha, **extra,
                   'inputs': {r: v for r, v in inputs.items() if r.startswith(PINNED + '/') == mine}}
            for kind, rows in current.items():
                part = {r: v for r, v in rows.items() if r.startswith(PINNED + '/') == mine}
                if part:
                    doc[kind] = part
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
            print('UPDATED', path.relative_to(ROOT).as_posix(), '%d files' % len(doc['inputs']))
        return 0
    frozen = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if pinned_doc:
        for kind, rows in pinned_doc.items():
            if isinstance(rows, dict):
                frozen.setdefault(kind, {}).update(rows)
    fails, new = [], []
    want_inputs = frozen.get('inputs', {})
    changed = sorted(rel for rel, value in inputs.items() if rel in want_inputs and want_inputs[rel] != value)
    for rel in changed:
        fails.append('输入已改 %s：审阅它的输出变化后 --update，连同夹具一起提交' % rel)
    for kind, rows in current.items():
        want = frozen.get(kind, {})
        for rel, value in rows.items():
            if rel not in want:
                new.append('%s %s' % (kind, rel))
            elif want[rel] != value and rel not in changed:
                fails.append('%s %s: frozen %s, now %s' % (kind, rel, json.dumps(want[rel], ensure_ascii=False)[:200],
                                                            json.dumps(value, ensure_ascii=False)[:200]))
        for rel in sorted(set(want) - set(rows)):
            print('NOTE', kind, rel, '已不在语料中（清单可用 --update 更新）')
    for line in fails:
        print('FAIL', line)
    for line in new[:20]:
        print('NEW ', line, '（未冻结；确认后 --update）')
    checked = sum(len(v) for v in current.values())
    print('SUMMARY %s checked=%d mismatched=%d new=%d' % ('FAIL' if fails else 'PASS', checked, len(fails), len(new)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
