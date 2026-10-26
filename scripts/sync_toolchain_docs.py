#!/usr/bin/env python3
"""从新编译器同步派生文档；产品版本仅在 selfhost/toolchain.tx 修改。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
VERSION_SOURCE = ROOT / 'bootstrap/selfhost/toolchain.tx'
CHANGES_SOURCE = ROOT / 'bootstrap/changes.tx'
CHANGES_DOC = ROOT / 'docs/ai/toolchain-changes.md'


def product_version():
    source = VERSION_SOURCE.read_text(encoding='utf-8')
    values = re.findall(r'^export const TOOLCHAIN_VERSION: string = "([^"\r\n]+)";$',
                        source, re.M)
    if len(values) != 1:
        raise ValueError('toolchain.tx 必须只有一个字面量 TOOLCHAIN_VERSION 定义')
    version = values[0]
    number = r'(?:0|[1-9][0-9]*)'
    identifier = r'(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
    if not re.fullmatch(number + r'\.' + number + r'\.' + number +
                        r'(?:-' + identifier + r'(?:\.' + identifier + r')*)?'
                        r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', version):
        raise ValueError('TOOLCHAIN_VERSION 不是有效的 SemVer：' + version)
    return version


def generated_changes(compiler):
    def run(*args):
        return subprocess.run([str(compiler), *args], cwd=ROOT, capture_output=True,
                              timeout=30, check=True).stdout

    version = product_version()
    cap = json.loads(run('capabilities', '--format=json'))
    data = cap.get('capabilities', {})
    if (cap.get('schema') != 'capabilities/1' or data.get('compiler') != 'native-tx'
            or data.get('toolchain_version') != version):
        raise ValueError('所选编译器与当前源码产品版本不一致；请先构建当前源码')
    if data.get('change_registry', {}).get('sha256') != hashlib.sha256(CHANGES_SOURCE.read_bytes()).hexdigest():
        raise ValueError('所选编译器内嵌变更注册表与当前源码不一致；请先重新构建')
    changes = json.loads(run('changes', '--format=json'))
    if changes.get('schema') != 'changes/1' or changes.get('toolchain_version') != version:
        raise ValueError('编译器 changes 与 capabilities 的产品版本不一致')
    # 内容只由太虚渲染；Python 仅检查 UTF-8 并统一文档行尾。
    text = run('changes').decode('utf-8').replace('\r\n', '\n')
    if '\r' in text or not text.startswith('# 太虚工具链兼容变更\n'):
        raise ValueError('changes 文档格式无效')
    return (text.rstrip('\n') + '\n').encode('utf-8')


def sync_docs(compiler, *, check=False):
    generated = generated_changes(compiler)
    current = CHANGES_DOC.read_bytes() if CHANGES_DOC.is_file() else None
    if current == generated:
        return False
    if check:
        raise ValueError('派生文档未同步；运行 python scripts/sync_toolchain_docs.py --compiler <新编译器>')
    CHANGES_DOC.write_bytes(generated)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True, help='由当前源码构建的原生编译器')
    ap.add_argument('--check', action='store_true', help='只检查，不写入文档')
    args = ap.parse_args()
    changed = sync_docs(args.compiler.resolve(), check=args.check)
    print('PASS', '已同步' if changed else '已一致', CHANGES_DOC.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('同步失败：', error, file=sys.stderr)
        sys.exit(1)
