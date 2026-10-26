"""Bind test-only C Value tags to the live Taixu/Zig ABI contract."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def compiler_defines():
    contract = (ROOT/'bootstrap/selfhost/value_abi.tx').read_text(encoding='utf-8')
    zig = (ROOT/'src/vm/interp/value.zig').read_text(encoding='utf-8')
    result = []
    for name in ['INT', 'FLOAT', 'BOOL']:
        tx = int(re.search(r'export const TAG_'+name+r': int = (\d+);', contract)[1])
        host = int(re.search(r'    '+name.lower()+r' = (\d+),', zig)[1])
        if tx != host:
            raise RuntimeError('Value tag ABI mismatch: '+name)
        result.append('-DTX_TAG_'+name+'='+str(tx))
    return result
