"""冻结的前端输出：bootstrap/lexer.tx 的记号流、parser.tx 的 AST（二进制与文本）、`doc --format=json` 与 spike/loads 的诊断。

来历：Zig 参照退场前，diff-lexer、diff-parser 在 tests/cases、examples、docs/design、skill/taixu/examples、bootstrap 上
逐文件对拍原生与 Zig 实现，sema 探针对拍 spike/loads 的诊断，doc 在全语料上对拍；清退（4416378）删掉参照侧时连同本体
断言一起删了（清退设计稿 §三要求保留冻结输出）。这里按文件冻结原生输出（stdout 的 SHA-256 与退出码）。

输入要稳定，冻结的输出才有意义（同 Go 的 testdata＋golden、Rust insta 的快照）：
- bootstrap 是编译器自己的活源码，每个提交都在变，所以不取工作树，而取 bootstrap/ZIG_SEED 那个提交（794d3ba，
  与 Zig 参照对拍通过的最后状态）的内容，经 git archive 现取，永不过期；
- 其余目录是夹具，取工作树。清单同时记每个输入文件的 SHA-256：输入没变而输出变了是前端回归；输入变了则报“输入
  已改”，审阅输出变化后 --update（夹具改动要连同冻结值一起提交）。两种都判失败。
lexer.tx 自 794d3ba 以来未改，parser.tx 只改了 async 箭头前瞻一处，所以 794d3ba 时已有的文件继承了与 Zig 参照
一致的结论；之后新增的文件没有独立参照，只防回归。

用法：python scripts/test-frontend-frozen.py --compiler <原生 taixu> [--update]
清单 tests/frozen/frontend-outputs.json；--update 按当前输出重写。清单外的新文件只提示。
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
CORPUS = ['tests/cases', 'tests/nrt-cases', 'examples', 'docs/design', 'skill/taixu/examples', 'spike/loads']
PINNED = 'bootstrap'
SEMA = 'spike/loads'
EXE = '.exe' if os.name == 'nt' else ''


def pinned_tree(work):
    """bootstrap/ZIG_SEED 那个提交的 bootstrap/，展开到 work/pinned；返回 (提交, 展开根)。

    公开仓 taixu-lang 只有单次提交、取不到那段历史，改用导出时由同一提交生成的 tests/frozen/pinned-bootstrap.tar
    （设计 v052-public-release §二-5）；内容是否原样由清单逐文件记的 SHA-256 核对。"""
    pin = (ROOT / 'bootstrap/ZIG_SEED').read_text(encoding='utf-8').strip()
    p = subprocess.run(['git', 'archive', '--format=tar', pin, PINNED], cwd=ROOT, capture_output=True)
    data = p.stdout
    if p.returncode != 0:
        vendored = ROOT / 'tests/frozen/pinned-bootstrap.tar'
        if not vendored.is_file():
            raise SystemExit('FAIL 取不到 %s 的 %s（需要 git 与可达的 ZIG_SEED 提交，或 tests/frozen/pinned-bootstrap.tar）：%s' % (
                pin, PINNED, p.stderr.decode('utf-8', 'replace').strip()))
        data = vendored.read_bytes()
    dest = work / 'pinned'
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(dest, filter='data')
    return pin, dest


def corpus(pinned_root):
    """(相对路径, 所在根) 的有序列表：夹具取工作树，bootstrap 取固定提交。"""
    files = {}
    for d in CORPUS:
        for p in (ROOT / d).rglob('*.tx'):
            files[p.relative_to(ROOT).as_posix()] = ROOT
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
    a = ap.parse_args()
    compiler = str(a.compiler.resolve())
    work = (a.work or Path(tempfile.mkdtemp(prefix='taixu-frozen-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    tools = {}
    for name in ('lexer', 'parser'):
        out = work / name
        shutil.rmtree(out, ignore_errors=True)
        p = subprocess.run([compiler, 'build', 'bootstrap/%s.tx' % name, '--out', str(out)], cwd=ROOT,
                           capture_output=True, timeout=900)
        if p.returncode != 0:
            print('FAIL build', name, p.stdout.decode('utf-8', 'replace')[-500:], p.stderr.decode('utf-8', 'replace')[-500:])
            return 1
        tools[name] = str(out / (name + EXE))
    pin, pinned_root = pinned_tree(work)
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
        doc = {'note': '由 scripts/test-frontend-frozen.py --update 生成；值为 "退出码:stdout 的 SHA-256"（CRLF 归一为 LF），'
                       'sema 为 dev-check 的诊断，inputs 为输入文件的 SHA-256；bootstrap/ 取 pinned 提交的内容。'
                       '改动前端或夹具的提交须说明每处变化。',
               'compiler_sha256': hashlib.sha256(Path(compiler).read_bytes()).hexdigest(),
               'pinned': {PINNED: pin}, 'inputs': inputs, **current}
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
        print('UPDATED', MANIFEST.relative_to(ROOT).as_posix(), '%d files' % len(files))
        return 0
    frozen = json.loads(MANIFEST.read_text(encoding='utf-8'))
    fails, new = [], []
    if frozen.get('pinned', {}).get(PINNED) != pin:
        fails.append('%s 的固定提交由 %s 变为 %s：确认后 --update' % (PINNED, frozen.get('pinned', {}).get(PINNED), pin))
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
