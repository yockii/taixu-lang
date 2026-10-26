"""完整自编译的时间/OS 峰值与泄漏检查；单列观测，不替代 D102 性能验收。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time

from build import native_compiler, sha

ROOT=Path(__file__).resolve().parent.parent


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler',type=Path,required=True)
    ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--runs',type=int,default=5)
    a=ap.parse_args()
    if a.runs<5:ap.error('at least five measured self-builds are required')
    compiler=native_compiler(a.compiler)
    work=a.work.resolve()
    if work.exists():ap.error('use a fresh work directory')
    source=work/'src';source.mkdir(parents=True)
    for directory in ['bootstrap','prelude']:
        for path in (ROOT/directory).rglob('*.tx'):
            dest=source/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,dest)
    kernels=source/'bootstrap/nrt-kernel';kernels.mkdir()
    for name in ['kernel.obj','kernel-linux.o']:
        shutil.copyfile(ROOT/'bootstrap/nrt-kernel'/name,kernels/name)
    exe='.exe' if os.name=='nt' else ''
    binary=source/'A'/('taixu'+exe);binary.parent.mkdir();shutil.copy2(compiler,binary)
    expected=sha(binary)
    spec=importlib.util.spec_from_file_location('self_profile_peak',ROOT/'scripts/test-capacity-limits.py')
    peak=importlib.util.module_from_spec(spec);spec.loader.exec_module(peak)
    empty=work/'empty-path';empty.mkdir()
    for key in list(os.environ):
        if key.upper().startswith('TAIXU_'):del os.environ[key]
    os.environ['PATH']=str(empty)
    doc={'scope':'descriptive full self-build; no exclusive-CPU claim; not D102 acceptance',
         'clock':'parent perf_counter_ns; Linux includes the small RSS helper',
         'rss':'Windows OS PeakWorkingSetSize / Linux wait4 ru_maxrss (fresh helper)',
         'compiler_sha256':expected,'N':a.runs,'source_sha256':{p.relative_to(source).as_posix():sha(p)
             for d in ['bootstrap','prelude'] for p in (source/d).rglob('*.tx')},
         'kernels':{p.name:sha(p) for p in kernels.iterdir()},'samples':[],'status':'RUNNING'}
    def save():(work/'report.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    def build(name,builder,flags,measured):
        out=source/name
        argv=[builder,'build','bootstrap/taixu.tx','--toolchain',*flags,'--out',out]
        start=time.perf_counter_ns();code,text,rss=peak.run_peak(argv,source,1200);ns=time.perf_counter_ns()-start
        (work/(name+'.log')).write_text(text,encoding='utf-8',newline='\n')
        product=out/('taixu'+exe)
        row={'name':name,'exit':code,'ns':ns,'peak_rss':rss,'sha256':sha(product) if product.exists() else None}
        if measured:doc['samples'].append(row)
        else:doc[name]=row
        save();print(name,code,ns/1e9,'s',rss/1048576,'MiB',flush=True)
        if code or rss<=0:raise RuntimeError('self-build failed: '+name)
        return product,row
    try:
        # 先核对完整编译链的退出泄漏；失败时不再耗时采性能观测样本。
        instrumented,_=build('instrumented',binary,['--leak-check'],False)
        _,check=build('instrumented-self',instrumented,[],False)
        if check['sha256']!=expected:raise RuntimeError('instrumented compiler changed output')
        _,warm=build('warmup',binary,[],False)
        if warm['sha256']!=expected:raise RuntimeError('warmup changed compiler identity')
        for i in range(a.runs):
            _,row=build('sample-'+str(i),binary,[],True)
            if row['sha256']!=expected:raise RuntimeError('self-build is not byte-identical')
        doc['median_ns']=statistics.median(r['ns'] for r in doc['samples'])
        doc['peak_rss_max']=max(r['peak_rss'] for r in doc['samples'])
        doc['status']='PASS';save();print('PASS full self-build profile',work/'report.json',flush=True)
        return 0
    except BaseException as error:
        doc['status']='FAIL';doc['error']=str(error);save();raise


if __name__=='__main__':sys.exit(main())
