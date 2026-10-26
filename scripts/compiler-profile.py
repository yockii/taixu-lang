"""编译器优化画像／消融：阶段、CPU、周期与 OS 峰值；不替代正式性能门。"""
import argparse
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def source_hashes():
    return {p.relative_to(ROOT).as_posix():sha(p) for folder in ['bootstrap','prelude']
            for p in sorted((ROOT/folder).rglob('*.tx'))}

def windows_metrics(p):
    import ctypes as c
    from ctypes import wintypes as w
    k=c.WinDLL('kernel32',use_last_error=True)
    k.GetProcessTimes.argtypes=[w.HANDLE]+[c.POINTER(w.FILETIME)]*4
    k.QueryProcessCycleTime.argtypes=[w.HANDLE,c.POINTER(c.c_ulonglong)]
    k.GetPriorityClass.argtypes=[w.HANDLE]
    ts=[w.FILETIME() for _ in range(4)]
    h=w.HANDLE(int(p._handle))
    if not k.GetProcessTimes(h,*[c.byref(t) for t in ts]):raise c.WinError(c.get_last_error())
    cycles=c.c_ulonglong()
    if not k.QueryProcessCycleTime(h,c.byref(cycles)):raise c.WinError(c.get_last_error())
    class Counters(c.Structure):
        _fields_=[('cb',w.DWORD),('PageFaultCount',w.DWORD)]+[(name,c.c_size_t) for name in
            ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    ps=c.WinDLL('psapi',use_last_error=True)
    ps.GetProcessMemoryInfo.argtypes=[w.HANDLE,c.POINTER(Counters),w.DWORD]
    counters=Counters();counters.cb=c.sizeof(counters)
    if not ps.GetProcessMemoryInfo(h,c.byref(counters),counters.cb):raise c.WinError(c.get_last_error())
    ns=lambda t:((t.dwHighDateTime<<32)|t.dwLowDateTime)*100
    return {'user_cpu_ns':ns(ts[3]),'kernel_cpu_ns':ns(ts[2]),'cycles':cycles.value,
            'peak_rss':counters.PeakWorkingSetSize,'priority':k.GetPriorityClass(h)}

# A fresh helper keeps Python's accumulated RSS out of the native child's peak.
# Native wall time is measured inside it; parent wall includes helper startup.
LINUX_HELPER = '''import json,os,sys,time
note=sys.argv[1]
start=time.perf_counter_ns()
pid=os.fork()
if pid==0:os.execv(sys.argv[2],sys.argv[2:])
_,st,ru=os.wait4(pid,0)
d={'native_wall_ns':time.perf_counter_ns()-start,'user_cpu_ns':round(ru.ru_utime*1e9),
   'kernel_cpu_ns':round(ru.ru_stime*1e9),'cycles':None,'peak_rss':ru.ru_maxrss*1024,
   'priority':os.getpriority(os.PRIO_PROCESS,0),'actual_affinity':sorted(os.sched_getaffinity(0))}
open(note,'w').write(json.dumps(d))
sys.exit(os.waitstatus_to_exitcode(st))
'''

def build(argv,env,folder,read_affinity,cpu):
    command=list(map(str,argv))
    if os.name!='nt':command=[sys.executable,'-c',LINUX_HELPER,str(folder/'metrics.json'),*command]
    start=time.perf_counter_ns()
    p=subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                       start_new_session=os.name!='nt')
    actual=None
    try:
        if read_affinity:
            actual=read_affinity(p)
            if actual!=[cpu]:raise RuntimeError('child CPU affinity mismatch: '+str(actual))
        stdout,stderr=p.communicate(timeout=1200)
        wall=time.perf_counter_ns()-start
        metrics=windows_metrics(p) if os.name=='nt' else json.loads((folder/'metrics.json').read_text())
        if os.name!='nt' and cpu is not None and metrics['actual_affinity']!=[cpu]:
            raise RuntimeError('native helper CPU affinity mismatch')
    finally:
        if p.poll() is None:
            if os.name=='nt':p.kill()
            else:os.killpg(p.pid,signal.SIGKILL)
            p.communicate()
    (folder/'stdout.log').write_bytes(stdout);(folder/'stderr.log').write_bytes(stderr)
    metrics.update({'parent_wall_ns':wall,'wall_ns':metrics.get('native_wall_ns',wall),
                    'exit':p.returncode,'actual_affinity':metrics.get('actual_affinity',actual),
                    'cpu_ns':metrics['user_cpu_ns']+metrics['kernel_cpu_ns'],
                    'phases_ms':{name:int(value) for name,value in
                        re.findall(r'\[time\] (\S+) (\d+) ms',stderr.decode('utf8','replace'))}})
    return metrics

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--arm',nargs=2,action='append',metavar=('NAME','COMPILER'),required=True)
    ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--kernel',type=Path)
    ap.add_argument('--runs',type=int,default=6)
    ap.add_argument('--cpu',type=int)
    ap.add_argument('--load',action='append',type=Path)
    a=ap.parse_args()
    if a.runs<2:ap.error('at least two diagnostic rounds are required')
    if not 1<=len(a.arm)<=3:ap.error('one to three arms are supported')
    arms={name:Path(path).resolve() for name,path in a.arm}
    if len(arms)!=len(a.arm) or any(not re.fullmatch(r'[A-Za-z0-9_-]+',name) for name in arms):
        ap.error('use distinct simple arm names')
    work=a.work.resolve()
    if work.exists():ap.error('use a fresh output directory')
    work.mkdir(parents=True)
    kernel=(a.kernel or ROOT/'bootstrap/nrt-kernel'/('kernel.obj' if os.name=='nt' else 'kernel-linux.o')).resolve()
    spec=importlib.util.spec_from_file_location('compiler_profile_affinity',ROOT/'scripts/compiler-regression.py')
    reg=importlib.util.module_from_spec(spec);spec.loader.exec_module(reg)
    policy,read_affinity=reg.select_cpu(a.cpu)
    empty=work/'empty-path';empty.mkdir()
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('TAIXU_')};env['PATH']=str(empty)
    loads=a.load or [Path('bootstrap/lexer.tx'),Path('bootstrap/parser.tx'),Path('bootstrap/semamirror.tx')]
    names=list(arms)
    orders=list(itertools.permutations(names))
    doc={'scope':'optimization profile/ablation; no acceptance verdict; no exclusive-CPU claim',
         'host':'windows' if os.name=='nt' else 'linux','N':a.runs,'cpu_policy':policy,
         'compilers':{name:sha(path) for name,path in arms.items()},'kernel_sha256':sha(kernel),
         'source_sha256':source_hashes(),'orders':orders,'samples':[],'loads':{},'status':'RUNNING'}
    def save():
        (work/'report.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    save()
    try:
        for load in loads:
            expected=None
            for iteration in range(-1,a.runs):
                order=names if iteration<0 else orders[iteration%len(orders)]
                for slot,name in enumerate(order):
                    dest=work/load.stem/(str(iteration)+'-'+str(slot)+'-'+name);dest.mkdir(parents=True)
                    argv=[arms[name],'build',load,'--time','--link-object',kernel,'--out',dest]
                    if load.resolve()==(ROOT/'bootstrap/taixu.tx').resolve():argv+=['--toolchain']
                    row=build(argv,env,dest,read_affinity,a.cpu)
                    product=dest/(load.stem+('.exe' if os.name=='nt' else ''))
                    row.update({'load':str(load),'round':iteration,'arm':name,'slot':slot,
                                'product_sha256':sha(product) if product.exists() else None})
                    doc['samples'].append(row);save()
                    if row['exit']!=0:raise RuntimeError('native build failed; see '+str(dest/'stderr.log'))
                    if row['product_sha256'] is None:raise RuntimeError('native build produced no output: '+str(product))
                    if expected is None:expected=row['product_sha256']
                    if row['product_sha256']!=expected:raise RuntimeError('different compiled products: '+str(dest))
                    print(load,iteration,name,round(row['wall_ns']/1e9,3),'s',
                          round(row['cpu_ns']/1e9,3),'CPU s',round(row['peak_rss']/1048576,1),'MiB',flush=True)
            summary={}
            for name in names:
                rows=[r for r in doc['samples'] if r['load']==str(load) and r['arm']==name and r['round']>=0]
                medians={k:statistics.median(r[k] for r in rows) if all(r[k] is not None for r in rows) else None
                         for k in ['wall_ns','cpu_ns','cycles','peak_rss']}
                medians['wall_mad_ratio']=statistics.median(abs(r['wall_ns']-medians['wall_ns']) for r in rows)/medians['wall_ns']
                medians['phase_medians_ms']={phase:statistics.median(r['phases_ms'][phase] for r in rows)
                                            for phase in rows[0]['phases_ms']}
                summary[name]=medians
            doc['loads'][str(load)]=summary;save()
            print('SUMMARY',load,json.dumps(summary),flush=True)
        if source_hashes()!=doc['source_sha256']:raise RuntimeError('source changed during profile')
        if {name:sha(path) for name,path in arms.items()}!=doc['compilers'] or sha(kernel)!=doc['kernel_sha256']:
            raise RuntimeError('compiler or kernel changed during profile')
        doc['status']='COMPLETED DIAGNOSTIC';save()
        print(doc['status'],work/'report.json',flush=True)
        return 0
    except BaseException as error:
        doc['status']='FAIL';doc['error']=str(error);save();raise

if __name__=='__main__':raise SystemExit(main())
