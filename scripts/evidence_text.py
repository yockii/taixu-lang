"""Import UTF-8 evidence text as LF, retaining its exact original bytes.

Usage: python scripts/evidence_text.py INPUT OUTPUT --raw-dir DIRECTORY
Existing outputs must already match; unrelated files are never rewritten.
"""
import argparse
import hashlib
import json
from pathlib import Path


def normalize(raw):
    raw.decode('utf-8-sig')  # Validate text without removing a possible BOM.
    result = raw.replace(b'\r\n', b'\n')
    if b'\r' in result:
        raise ValueError('Standalone CR requires explicit review; original retained unchanged')
    return result


def save_exact(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError('Refuse to overwrite different existing bytes: ' + str(path))
        return
    with path.open('xb') as output:
        output.write(data)


def write_text_lf(path, text):
    """Create new evidence text without platform newline translation."""
    save_exact(path, normalize(text.encode('utf-8')))


def import_tree(source, target, raw_dir):
    """Import a dedicated text-only evidence directory; never rewrite sources."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Source and destination directories must be disjoint')
    files = sorted(source.rglob('*'))
    # Validate the complete input before publishing any views.
    for path in files:
        if path.is_symlink():
            raise ValueError('Symlinks are not evidence inputs: ' + str(path))
        if path.is_file():
            normalize(path.read_bytes())
    return [import_text(path, target / path.relative_to(source), raw_dir)
            for path in files if path.is_file()]


def import_text(source, target, raw_dir):
    source, target = Path(source), Path(target)
    raw = source.read_bytes()
    view = normalize(raw)
    original_hash = hashlib.sha256(raw).hexdigest()
    view_hash = hashlib.sha256(view).hexdigest()
    original = Path(raw_dir) / (original_hash + '.bin')
    # Keep hashes of historical originals valid; LF is a distinct display view.
    save_exact(original, raw)
    save_exact(target, view)
    receipt = {'source': str(source.resolve()), 'view': str(target.resolve()),
               'raw': str(original.resolve()), 'raw_sha256': original_hash,
               'view_sha256': view_hash, 'raw_bytes': len(raw), 'view_bytes': len(view),
               'normalized': raw != view}
    save_exact(target.with_name(target.name + '.eol.json'),
               (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--tree', action='store_true',
                        help='Import a dedicated UTF-8 text-only directory')
    args = parser.parse_args()
    action = import_tree if args.tree else import_text
    print(json.dumps(action(args.input, args.output, args.raw_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
