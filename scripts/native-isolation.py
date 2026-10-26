"""原生 A→B→C→C-repeat：OS 级进程/文件限制，先验证正负控制。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build import native_compiler
from selfhost_validation import ROOT, require_success, run, sha, write_json
from selfhost_file_control import prepare


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler',type=Path,required=True)
    ap.add_argument('--probe-file',type=Path,action='append',default=[],help='existing outside SDK/helper files for positive/negative read controls')
    ap.add_argument('--deny',type=Path,action='append',default=[],help='Windows: owned SDK/helper paths denied only to this task token')
    ap.add_argument('--report',type=Path,required=True)
    a=ap.parse_args()
    compiler=native_compiler(a.compiler)
    win=os.name=='nt';exe='.exe' if win else ''
    # Existing Windows ACL helper deliberately accepts only an owned temp child.
    root=Path(tempfile.mkdtemp(prefix='v050-native-isolation-')).resolve()
    for directory in ['bootstrap','prelude']:
        for source in (ROOT/directory).rglob('*.tx'):
            out=root/source.relative_to(ROOT);out.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,out)
    initial=root/'A'/('taixu'+exe);initial.parent.mkdir();shutil.copy2(compiler,initial)
    sources={p.relative_to(root).as_posix():sha(p) for d in ['bootstrap','prelude'] for p in (root/d).rglob('*.tx')}
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('TAIXU_') and k!='GIT_DIR'}
    empty=root/'empty-path';empty.mkdir();env['PATH']=str(empty)
    temp=root/'temp';temp.mkdir();env.update(TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp))
    doc={'root':str(root),'source_sha256':sources,'A':sha(initial),'rows':[],'status':'RUNNING'}
    def save():write_json(a.report,doc)
    save()
    outside_files=[ROOT/'bootstrap/taixu.tx',*[p.resolve(strict=True) for p in a.probe_file]]
    if not all(p.is_file() and not p.is_relative_to(root) for p in outside_files):
        raise ValueError('Every outside probe must be a real file outside the private workspace')
    control,control_meta=prepare(root,env,initial,root/'bootstrap/taixu.tx',outside_files)
    outside=run(control,root,env);assert outside['exit']==99,outside
    doc['file_control']={'outside':outside,'build':control_meta}
    if win:
        from selfhost_windows_job import run_job
        from selfhost_restricted_token import RestrictedFiles
        probe=root/'child-control.py'
        probe.write_text('import subprocess,sys\ntry:\n subprocess.run([sys.executable,"-c","pass"],check=True)\nexcept OSError:\n sys.exit(0)\nsys.exit(99)\n',encoding='utf-8',newline='\n')
        outside=run([sys.executable,probe],root,env);assert outside['exit']==99,outside
        inside=run_job([sys.executable,probe],root,env,root/'child-control-log');require_success(inside)
        doc['process_control']={'outside':outside,'inside':inside}
    else:
        source=root/'fork-control.tx'
        source.write_text('declare function fork(): i32 from syscall 57;\nfunction main(args: []string): int { args; const p = fork(); if (p < 0) { return 0; } if (p == 0) { return 0; } return 99; }\n',encoding='utf-8',newline='\n')
        require_success(run([initial,'build',source,'--out',root/'fork-control-bin'],root,env))
        fork=root/'fork-control-bin/fork-control'
        outside=run([fork],root,env);assert outside['exit']==99,outside
        doc['process_control']={'outside':outside}
    spec=importlib.util.spec_from_file_location('isolation_bootstrap',ROOT/'scripts/nrt-bootstrap.py')
    bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)

    def exercise(policy):
        def restricted(name,cmd):
            print('START',name,flush=True)
            if win:
                row=run_job(cmd,root,env,root/(name+'-log'),restricted_files=policy)
            else:
                row=run([sys.executable,ROOT/'scripts/linux-isolation-exec.py','--root',root,
                         '--record',root/(name+'-policy.json'),'--',*cmd],root,env)
                row['policy']=json.loads((root/(name+'-policy.json')).read_text(encoding='utf-8'))
            row['name']=name;doc['rows'].append(row);save();require_success(row)
            print('PASS',name,flush=True)
            return row
        doc['file_control']['inside']=restricted('file-control',control)
        if not win:doc['process_control']['inside']=restricted('process-control',[fork])
        gen=root/'gen'/('runtime_memory_gen'+exe)
        restricted('generator',[initial,'build','bootstrap/runtime_memory_gen.tx','--out',root/'gen'])
        kernel=root/'bootstrap/nrt-kernel';kernel.mkdir(exist_ok=True)
        doc['kernels']={}
        for target,name in bootstrap.KERNELS:
            restricted('kernel-'+target,[gen,'--target',target,kernel/name,*bootstrap.KERNEL_FLAGS])
            digest=sha(kernel/name);assert digest==sha(ROOT/'bootstrap/nrt-kernel'/name)
            doc['kernels'][name]=digest
        previous=initial
        doc['compilers']={'A':sha(initial)}
        for name in ['B','C','C-repeat']:
            restricted(name,[previous,'build','bootstrap/taixu.tx','--toolchain','--out',root/name])
            previous=root/name/('taixu'+exe)
            doc['compilers'][name]=sha(previous);assert sha(previous)==doc['A'],doc['compilers']
        assert all(sha(root/name)==value for name,value in sources.items())

    try:
        if win:
            with RestrictedFiles() as policy:
                doc['restricting_sid']=policy.sid_string
                doc['file_policy']='known source/SDK paths denied to task-only SID; normal user access unchanged'
                save()
                policy.allow_workspace(root)
                policy.deny([ROOT.parent,*[p.resolve(strict=True) for p in a.deny]])
                exercise(policy)
            doc['acl_cleaned']=policy.cleaned;doc['acl_operations']=policy.commands
            assert policy.cleaned
        else:exercise(None)
    except BaseException as error:
        if win and 'policy' in locals():
            doc['acl_operations']=policy.commands;doc['acl_cleaned']=policy.cleaned
        doc['status']='FAIL';doc['error']=str(error);save();raise
    doc['status']='PASS';save();print('PASS native isolation',a.report,flush=True)
    return 0


if __name__=='__main__':sys.exit(main())
