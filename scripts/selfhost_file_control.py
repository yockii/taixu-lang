"""Actual native read controls for generation helper-file visibility."""
import shutil
import os
from pathlib import Path

from selfhost_validation import ROOT, require_success, run, sha


SOURCE = '''function readable(path: string): int {
    const data: string = readFile(path) catch (e) {
        if (strIndexOf(e.message, "too big:") == 0) { return 1; }
        return 0;
    };
    if (len(data) == 0) { return 0; }
    return 1;
}
function main(args: []string): int {
    if (len(args) < 2 || readable(args[0]) == 0) { return 98; }
    let blocked = 0;
    let i = 1;
    while (i < len(args)) {
        if (readable(args[i]) == 0) { blocked = blocked + 1; }
        i = i + 1;
    }
    if (blocked == len(args) - 1) { print("outside helper files denied"); return 0; }
    if (blocked == 0) { print("outside helper files readable"); return 99; }
    return 97;
}
'''


def prepare(root, env, compiler, host, outside_paths):
    source = root / 'file-control.tx'
    source.write_text(SOURCE, encoding='utf-8', newline='\n')
    out = root / 'file-control-bin'; out.mkdir()
    row = run([compiler, 'build', source, '--out', out], ROOT, env, timeout=120)
    require_success(row)
    exe = out / ('file-control.exe' if os.name == 'nt' else 'file-control')
    command = [exe, host, *outside_paths]
    return command, {'build': row, 'source_sha256': sha(source), 'product_sha256': sha(exe),
                     'predicate': 'read-open permission; TX_ETOOBIG after successful open counts readable, not denied',
                     'outside_file_sha256': {str(p): sha(p) for p in outside_paths}}
