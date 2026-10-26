#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON parser differential gate (round 6 step 6c-4a, docs/design/v052-round6-debug-v0.1-20261018.md §六).

bootstrap/selfhost/json_parse.tx follows Zig std.json's defaults (the VM debug protocol parses commands with
it): duplicate keys, trailing content, invalid UTF-8 in strings, raw control characters and lone \\u surrogates
are errors; a number is an integer when it has no . e E, is not -0 and fits i64, otherwise its text is kept.

The reference is Python's json module with those rules added: strict UTF-8 decoding of the input bytes, an
object_pairs_hook that rejects duplicate keys, parse_constant rejecting NaN/Infinity, parse_int/parse_float
keeping the literal for non-i64 numbers, and strings holding surrogates rejected.

Cases: a fixed list (valid and invalid inputs of every class), seeded random documents with random whitespace
and escapes, seeded random byte mutations of them, and nesting deeper than Python's recursion limit (expected
results built directly). bootstrap/selfhost/json-probe.tx prints one canonical line per case; every line must
match.

Usage: python scripts/test-native-json.py --compiler <taixu> [--work <dir>] [--random N]
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = os.name == 'nt'
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
SEED = 6403


class Reject(Exception):
    pass


def hexs(s):
    try:
        return s.encode('utf-8').hex()
    except UnicodeEncodeError:
        raise Reject()


def canon(v):
    if v is None:
        return 'n'
    if v is True:
        return 't'
    if v is False:
        return 'f'
    if isinstance(v, str):
        return 's' + hexs(v)
    if isinstance(v, list):
        return '[' + ','.join(canon(x) for x in v) + ']'
    tag = v[0]
    if tag == 'int':
        return 'i%d' % v[1]
    if tag == 'num':
        return 'd' + v[1]
    if tag == 'obj':
        return '{' + ','.join(hexs(k) + ':' + canon(x) for k, x in v[1]) + '}'
    raise AssertionError(v)


def reference(data):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return 'ERR'

    def pairs(ps):
        seen = set()
        for k, _ in ps:
            if k in seen:
                raise Reject()
            seen.add(k)
        return ('obj', ps)

    def pint(s):
        v = int(s)
        if s == '-0' or v < I64_MIN or v > I64_MAX:
            return ('num', s)
        return ('int', v)

    def pconst(s):
        raise Reject()

    try:
        v = json.loads(text, object_pairs_hook=pairs, parse_int=pint, parse_float=lambda s: ('num', s), parse_constant=pconst)
        return canon(v)
    except (ValueError, Reject, RecursionError):
        return 'ERR'


FIXED = [
    # 合法
    b'{}', b'[]', b'null', b'true', b'false', b'0', b'-0', b'1', b'-1', b'123456789',
    b'9223372036854775807', b'-9223372036854775808', b'9223372036854775808', b'-9223372036854775809',
    b'99999999999999999999999', b'1.5', b'-0.0', b'1e5', b'1E+5', b'1e-5', b'2.5E-3', b'1e400', b'0.1',
    b'"a"', b'""', b'"\\u00e9"', b'"\\u00E9"', b'"\\ud83d\\ude00"', b'"\\uD83D\\uDE00"', '"é太😀"'.encode('utf-8'),
    b'"\\u0000"', b'"\\/\\"\\\\\\b\\f\\n\\r\\t"', b'"\x7f"', b' \t\n\r{} \n\r\t ', b'  1  ',
    b'{"a":1,"b":[1,2,{"c":null}],"d":"x"}', b'{"cmd":"continue","timeout_s":5}',
    b'{"cmd":"breakpoint_set","module":"basic.tx","line":12}', b'[1,[2,[3,[4]]]]', b'[[],{},[[]],{"a":{}}]',
    b'{"a":1,"A":2}', b'{"":1}', b'{"a":{"a":{"a":1}}}', b'{"x":[{"x":1},{"x":2}]}',
    ('{' + ','.join('"k%d":%d' % (i, i) for i in range(40)) + '}').encode(),
    ('{' + ','.join('"%s":%d' % (chr(97 + i % 26) * (1 + i // 26), i) for i in range(200)) + '}').encode(),
    # 非法
    b'', b' ', b'{', b'}', b'[', b']', b'[1,]', b'{"a":1,}', b'{"a"}', b'{"a":}', b'{1:2}', b'{a:1}', b'01', b'-01',
    b'1.', b'.5', b'1e', b'1e+', b'-', b'+1', b'0x10', b'NaN', b'Infinity', b'-Infinity', b'nul', b'truex',
    b'True', b'"abc', b'"a\\qb"', b'"\\u12"', b'"\\u12g4"', b'"\\ud800"', b'"\\udc00"', b'"\\ud800\\u0041"',
    b'"\\ud800x"', b'"\\ud800\\ud800"', b'"\\udfff\\ud800"', b'"a\x01b"', b'"a\tb"', b'"a\nb"', b'"\x00"',
    b'"\xff"', b'"\xc0\x80"', b'"\xc1\xbf"', b'"\xed\xa0\x80"', b'"\xf4\x90\x80\x80"', b'"\xf5\x80\x80\x80"',
    b'"\xe2\x82"', b'"\xe2\x82"x', b'"\x80"', b'\xef\xbb\xbf{}', b'{} {}', b'[1] x', b'{"a":1}}', b'[1 2]',
    b'"a" "b"', b"'a'", b'// x\n1', b'[1,,2]', b'{"a"::1}', b'\x00', b'[[[', b']]]', b'{"a":1,"a":2}',
    b'{"a":1,"\\u0061":2}', b'{"b":1,"c":{"x":1,"x":2}}', b'[1]\x00', b'1 2', b'tru', b'nulll', b'[-]', b'["a",]',
    ('{' + ','.join('"k%d":%d' % (i, i) for i in range(40)) + ',"k17":0}').encode(),
    ('{' + ','.join('"%s":%d' % (chr(97 + i % 26) * (1 + i // 26), i) for i in range(200)) + ',"zzzz":1,"aaa":9}').encode(),
    b'[' * 300 + b']' * 300, b'{"a":' * 300 + b'1' + b'}' * 300, b'[' * 300 + b']' * 299,
]


def rand_str(rng):
    pool = 'abcxyz \t"\\/é太😀\x01\x1f\x7f\u2028'
    return ''.join(rng.choice(pool) for _ in range(rng.randint(0, 8)))


def rand_value(rng, depth):
    r = rng.random()
    if depth > 5 or r < 0.45:
        k = rng.randint(0, 6)
        if k == 0:
            return None
        if k == 1:
            return rng.choice([True, False])
        if k == 2:
            return rng.choice([0, 1, -1, 42, I64_MAX, I64_MIN, I64_MAX + 1, I64_MIN - 1, rng.randint(-10 ** 6, 10 ** 6)])
        if k == 3:
            return rng.choice([1.5, -0.0, 1e300, 2.5e-7, 0.1])
        return rand_str(rng)
    if r < 0.72:
        return [rand_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    keys = [rand_str(rng) for _ in range(rng.randint(0, 4))]
    return {k: rand_value(rng, depth + 1) for k in keys}


def rand_ws(rng):
    return ''.join(rng.choice(' \t\n\r') for _ in range(rng.choice([0, 0, 0, 1, 2])))


def dump(rng, v):
    """json.dumps with random whitespace between tokens and random escaping of string characters."""
    if isinstance(v, (list, dict)):
        if isinstance(v, list):
            parts = [dump(rng, x) for x in v]
            return '[' + rand_ws(rng) + (',' + rand_ws(rng)).join(parts) + rand_ws(rng) + ']'
        parts = [dump(rng, k) + rand_ws(rng) + ':' + rand_ws(rng) + dump(rng, x) for k, x in v.items()]
        return '{' + rand_ws(rng) + (',' + rand_ws(rng)).join(parts) + rand_ws(rng) + '}'
    if isinstance(v, str):
        out = []
        for ch in v:
            if ord(ch) < 0x20 or ch in '"\\' or rng.random() < 0.3:
                c = ord(ch)
                if c > 0xFFFF:
                    c -= 0x10000
                    hi, lo = 0xD800 + (c >> 10), 0xDC00 + (c & 0x3FF)
                    out.append(rng.choice(['\\u%04x\\u%04x', '\\u%04X\\u%04X']) % (hi, lo))
                elif ch == '"':
                    out.append(rng.choice(['\\"', '\\u0022']))
                elif ch == '\\':
                    out.append(rng.choice(['\\\\', '\\u005c']))
                elif ch == '/':
                    out.append(rng.choice(['\\/', '/']))
                else:
                    out.append(rng.choice(['\\u%04x', '\\u%04X']) % c)
            else:
                out.append(ch)
        return '"' + ''.join(out) + '"'
    return json.dumps(v)


MUT_POOL = b'{}[],:"\\ue.-+019az\x00\x1f\x7f\x80\xc3\xa9\xed\xa0\xff\t\n'


def mutate(rng, data):
    b = bytearray(data)
    for _ in range(rng.randint(1, 3)):
        op = rng.randint(0, 2)
        at = rng.randint(0, len(b))
        if op == 0:
            b[at:at] = bytes([rng.choice(MUT_POOL)])
        elif op == 1 and b:
            del b[min(at, len(b) - 1)]
        elif b:
            b[min(at, len(b) - 1)] = rng.choice(MUT_POOL)
    return bytes(b)


def build_cases(n_random):
    cases = [(c, reference(c)) for c in FIXED]
    rng = random.Random(SEED)
    for _ in range(n_random):
        text = rand_ws(rng) + dump(rng, rand_value(rng, 0)) + rand_ws(rng)
        data = text.encode('utf-8', 'surrogatepass')
        cases.append((data, reference(data)))
        bad = mutate(rng, data)
        cases.append((bad, reference(bad)))
    # Python 的递归上限之外：期望直接构造。
    for depth in (5000, 100000):
        cases.append((b'[' * depth + b']' * depth, '[' * depth + ']' * depth))
        cases.append((b'[' * depth + b']' * (depth - 1), 'ERR'))
        key = '61'
        cases.append((b'{"a":' * depth + b'null' + b'}' * depth, ('{' + key + ':') * depth + 'n' + '}' * depth))
    return cases


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, type=Path)
    ap.add_argument('--work', type=Path)
    ap.add_argument('--random', type=int, default=3000, help='random documents (each also mutated once)')
    a = ap.parse_args()
    compiler = a.compiler.resolve()
    work = (a.work or Path(tempfile.mkdtemp(prefix='tx-json-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out = work / 'probe'
    shutil.rmtree(out, ignore_errors=True)
    p = subprocess.run([str(compiler), 'build', 'bootstrap/selfhost/json-probe.tx', '--out', str(out)],
                       cwd=str(ROOT), capture_output=True, timeout=1200)
    probe = out / ('json-probe.exe' if WIN else 'json-probe')
    if p.returncode != 0 or not probe.exists():
        print('FAIL  probe-build  %s' % (p.stdout + p.stderr).decode('utf-8', 'replace')[-600:])
        print('SUMMARY FAIL=1')
        return 1
    cases = build_cases(a.random)
    case_file = work / 'cases.hex'
    case_file.write_text(''.join('x' + data.hex() + '\n' for data, _ in cases), encoding='ascii', newline='\n')
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_')}
    r = subprocess.run([str(probe), str(case_file)], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=1200)
    got = r.stdout.decode('utf-8', 'replace').replace('\r\n', '\n').split('\n')
    if got and got[-1] == '':
        got.pop()
    fails = 0
    accepted = sum(1 for _, want in cases if want != 'ERR')
    if r.returncode != 0 or len(got) != len(cases):
        print('FAIL  probe-run    exit=%d lines=%d cases=%d stderr=%r' % (r.returncode, len(got), len(cases), r.stderr[-400:]))
        fails += 1
    for i, (data, want) in enumerate(cases):
        have = got[i] if i < len(got) else '<missing>'
        if have != want:
            fails += 1
            if fails <= 12:
                print('FAIL  case %-5d input=%r want=%s got=%s' % (i, data[:80], want[:80], have[:80]))
    print('cases=%d accepted=%d rejected=%d' % (len(cases), accepted, len(cases) - accepted))
    print('SUMMARY %s' % ('PASS' if fails == 0 else 'FAIL=%d' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
