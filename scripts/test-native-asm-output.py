"""Independent PE/ELF relocation checks plus native byte-buffer lifetime checks."""
import argparse
import os
from pathlib import Path
import struct
import subprocess

if not __debug__:  # 本门以 assert 判定；python -O 会去掉断言而空过
    raise SystemExit('须在未加 -O 的 Python 下运行')

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--compiler', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    a = ap.parse_args()
    work = a.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    def run(argv):
        p = subprocess.run(list(map(str,argv)),cwd=ROOT,capture_output=True,timeout=240)
        assert p.returncode == 0,(p.returncode,p.stdout,p.stderr)
        return p
    run([a.compiler.resolve(),'build','bootstrap/native_asm_bytes_probe.tx','--toolchain','--leak-check','--out',work/'probe'])
    p = run([work/'probe'/('native_asm_bytes_probe'+('.exe' if os.name=='nt' else '')),work/'image'])
    assert p.stdout.strip() == b'PASS_ASM_BYTES_OWNERSHIP_ROLLBACK_PE_ELF',p.stdout
    print('PASS native ownership, growth, reset, invalid byte and decimal rollback',flush=True)
    pe=(work/'image.exe').read_bytes()
    u16=lambda at:struct.unpack_from('<H',pe,at)[0]
    u32=lambda at:struct.unpack_from('<I',pe,at)[0]
    u64=lambda at:struct.unpack_from('<Q',pe,at)[0]
    header=u32(60); opt=header+24
    assert pe[:2]==b'MZ' and pe[header:header+4]==b'PE\0\0' and u16(opt)==0x20b
    base=u64(opt+24); entry=base+u32(opt+16)
    sections={}
    for i in range(u16(header+6)):
        at=opt+u16(header+20)+40*i
        sections[pe[at:at+8].rstrip(b'\0')]=(base+u32(at+12),u32(at+20),u32(at+8))
    def check_image(text_at,entry,ro_addr,ro_at,data_at,raw):
        assert raw[text_at:text_at+3]==b'\x48\x8d\x05' and raw[text_at+7]==0xc3
        assert entry+7+struct.unpack_from('<i',raw,text_at+3)[0]==ro_addr
        assert struct.unpack_from('<Q',raw,data_at)[0]==entry
        assert raw[ro_at:ro_at+65541]==bytes(i&255 for i in range(65541))
    text=sections[b'.text']; ro=sections[b'.rdata']; data=sections[b'.data']
    check_image(text[1]+entry-text[0],entry,ro[0],ro[1],data[1],pe)
    assert data[2]>=8200
    print('PASS PE rel32/absolute addresses, all byte values and BSS size',flush=True)
    elf=(work/'image.elf').read_bytes()
    assert elf[:6]==b'\x7fELF\x02\x01'
    entry=struct.unpack_from('<Q',elf,24)[0]
    phoff=struct.unpack_from('<Q',elf,32)[0]
    size,count=struct.unpack_from('<HH',elf,54)
    segments=[]
    for i in range(count):
        kind,flags,off,va,_,filesz,memsz,_=struct.unpack_from('<IIQQQQQQ',elf,phoff+i*size)
        if kind==1:segments.append((flags,off,va,filesz,memsz))
    text=next(s for s in segments if s[0]==5)
    ro=next(s for s in segments if s[0]==4 and s[1]!=0)
    data=next(s for s in segments if s[0]==6)
    check_image(text[1]+entry-text[2],entry,ro[2],ro[1],data[1],elf)
    assert data[4]>=data[3]+8192
    print('PASS ELF rel32/absolute addresses, all byte values and BSS size',flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
