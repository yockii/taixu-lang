#!/usr/bin/env python3
"""标准库原生验证：格式、test/run、冻结产物输出、泄漏检查与卫生门。"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler',type=Path)
    # 公开仓 taixu-lang 里标准库在 stdlib/、核心即上级目录；开发布局里核心仓是同级的 taixu-project。
    ap.add_argument('--core',type=Path,default=ROOT.parent if (ROOT.parent/'bootstrap/taixu.tx').is_file() else ROOT.parent/'taixu-project')
    ap.add_argument('--report',type=Path)
    a=ap.parse_args()
    core=a.core.resolve()
    spec=importlib.util.spec_from_file_location('stdlib_native_build',core/'scripts/build.py')
    build=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    compiler=build.native_compiler(a.compiler)
    env={**os.environ,'PYTHONUTF8':'1','TAIXU_STDLIB':str(ROOT/'src')}
    rows=[]
    temp_root=ROOT/'.tmp'
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native-verify-',dir=temp_root) as temp:
        work=Path(temp).resolve()
        assert work.parent==temp_root.resolve()
        sandbox=work/'run'
        sandbox.mkdir()
        shutil.copytree(ROOT/'tests',sandbox/'tests')

        def record(name,ok,exit_code=None,detail=''):
            rows.append({'name':name,'ok':ok,'exit':exit_code,'detail':'' if ok else detail[-1000:]})
            print('PASS' if ok else 'FAIL',name,rows[-1]['detail'],flush=True)
            return ok

        def check(name,argv,want=None,tests=False):
            try:
                p=subprocess.run(list(map(str,argv)),cwd=sandbox,env=env,capture_output=True,timeout=600)
            except subprocess.TimeoutExpired as e:
                return record(name,False,None,'超时 600 s：'+((e.stdout or b'')+(e.stderr or b'')).decode('utf-8','replace'))
            ok=p.returncode==0
            if want is not None:
                ok=ok and not p.stderr and p.stdout.replace(b'\r\n',b'\n')==want
            if tests:
                # 末行须恰为汇总行，且无失败、无泄漏报告（子串 '0 failed' 会把 '10 failed' 当通过）。
                lines=p.stdout.replace(b'\r\n',b'\n').rstrip(b'\n').split(b'\n')
                summary=re.fullmatch(rb'(\d+) passed, (\d+) failed',lines[-1])
                ok=ok and summary is not None and summary.group(2)==b'0' and b'[runtime:leak]' not in p.stderr
            return record(name,ok,p.returncode,(p.stdout+p.stderr).decode('utf-8','replace'))

        check('fmt',[compiler,'fmt','--check',ROOT/'src',ROOT/'tests'])
        for source in sorted((ROOT/'tests').glob('*.tx')):
            if source.name.startswith('aot-'):
                continue
            # -toolchain.tx 结尾的用例用到工具链内建（runProcess 等），按工具链构建（test 与 run 都是）。
            tc=['--toolchain'] if source.name.endswith('-toolchain.tx') else []
            if not source.name.endswith('-async.tx'):
                for flag in [[],['--leak-check']]:
                    check('test '+source.name+(' leak' if flag else ''),[compiler,'test',*tc,*flag,source],tests=True)
            out=source.with_suffix('.out')
            if out.exists():
                expected=out.read_bytes().replace(b'\r\n',b'\n')
                check('run '+source.name,[compiler,'run',*tc,source],expected)
                check('run '+source.name+' leak',[compiler,'run',*tc,'--leak-check',source],expected)
            elif source.name.endswith(('-async.tx','-verify.tx')):
                record('run '+source.name,False,None,'缺少伴文件 '+out.name)
        products=json.loads((ROOT/'tests/native-products.json').read_text(encoding='utf-8'))
        listed=sorted(case['file'] for case in products['cases'])
        present=sorted(p.name for p in (ROOT/'tests').glob('aot-*.tx'))
        record('native-products 覆盖全部 aot-*.tx',listed==present,None,'清单 %s，实有 %s'%(listed,present))
        exe='.exe' if os.name=='nt' else ''
        for case in products['cases']:
            source=ROOT/'tests'/case['file']
            expected=case['stdout'].encode('utf-8')
            check('CLI '+source.name,[compiler,'run',source],expected)
            for name,flags in [('native',[]),('leak',['--leak-check'])]:
                out=work/(source.stem+'-'+name)
                if check('build '+source.name+' '+name,[compiler,'build',source,*flags,'--out',out]):
                    check('product '+source.name+' '+name,[out/(source.stem+exe)],expected)
    spec=importlib.util.spec_from_file_location('stdlib_hygiene',core/'scripts/repo_hygiene.py')
    hygiene=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hygiene)
    files=hygiene.source_files(ROOT)
    lf=hygiene.check_eol(ROOT,files)
    rows.append({'name':'LF','ok':lf})
    print('PASS' if lf else 'FAIL','LF',flush=True)
    counts=sorted(((p.read_bytes().count(b'\n'),p.name) for p in (ROOT/'src').glob('*.tx')),reverse=True)
    print('linecount report-only:',len(counts),'files;',sum(n>1000 for n,_ in counts),'over soft limit; top',counts[:10],flush=True)
    failed=sum(not row['ok'] for row in rows)
    print('SUMMARY','PASS' if not failed else 'FAIL','checks=%d failures=%d'%(len(rows),failed),flush=True)
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256(compiler.read_bytes()).hexdigest()
        a.report.write_text(json.dumps({'compiler':str(compiler),'compiler_sha256':digest,'rows':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return int(bool(failed))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
