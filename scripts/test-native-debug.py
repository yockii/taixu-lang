#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native debug session gate (round 6 step 6c-4b, docs/design/v052-round6-debug-v0.1-20261018.md §六).

Runs every proto case of tests/debug/cases.json against the native `taixu run --debug` with the same driver
that recorded the VM transcripts (scripts/debug-baseline.py run_proto), then checks two things:

1. Native golden: the transcript, normalized (pc and object numbers blanked, reg_count dropped, only named
   slots kept in frames), equals tests/debug/native/proto/<case>.proto byte for byte. `--update` rewrites the
   goldens (review the diff before committing).
2. VM cross-check against tests/debug/vm/<case>.proto (the comparison rules of §六; intended differences are
   the rows of §五):
   - ready: the same function names in the same order and the same module count; the entry module path equal
     (imported modules: the VM gives the import specifier, native the absolute path);
   - the concatenated stdout text and the terminal event (exited / trapped / compile_failed) equal;
   - error messages equal, except the documented ones (negative line; a module named by its file name);
   - cases driven only by continue: the stopped (function, line) sequence equals the VM's after merging
     consecutive stops in the same frame on the same line; frames compare function names per frame, and every
     named native slot holding a value other than none has the same brief in the VM's frame (object numbers
     blanked; a variable past its last use may already be released natively and shows none).

inspect replies for the native named slots that are not none are compared node by node with the VM's reply for
the same slot (section 五 differences allowed by name).

DAP cases (round 6 step 6c-5b): the `dap` cases of tests/debug/cases.json run against the native `taixu dap`; the
transcript is compared with tests/debug/native/dap/<case>.dap (normalized as above) and with the VM transcript for
the program's stdout text, the exit code (when both report one) and where the first stop lands (function and line);
everything else differs
by design (docs/design/v052-round6-debug-v0.1-20261018.md §四-8, §五).

MCP cases (round 6 step 6c-6): the `mcp` cases of tests/debug/cases.json run against the native `taixu mcp`; the
transcript is compared with tests/debug/native/mcp/<case>.mcp (normalized as above) and with the VM transcript:
initialize's result apart from the added serverInfo.version; the sequence of replies other than stopped (consecutive
equal ones merged): JSON-RPC error messages, `ok:false` texts, the reply event names and debug_start's start event;
debug_start's breakpoint receipts (native adds an error receipt for an unknown module, §五); the stdout text carried in
events; the exited / trapped / compile_failed replies; where the first stop lands (function and line). Stop
positions after a step differ by design (§五).

Native-only cases (tests/debug/native/cases.json: generator, threads, named variables, stepping from the
loading state) have no VM transcript and are checked against their native goldens only.

Usage: python scripts/test-native-debug.py --compiler <taixu> [--update] [case ...]
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBUG = ROOT / 'tests' / 'debug'
GOLDEN = DEBUG / 'native' / 'proto'

spec = importlib.util.spec_from_file_location('debug_baseline', ROOT / 'scripts' / 'debug-baseline.py')
dbl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dbl)

OBJ = re.compile(r'#\d+')
# 有意差别（调试实施设计 §五）：VM 报的错、原生改正后的回执。
ALLOWED_ERRORS = {
    'multi-bp': {'未知模块（见 ready 事件的 modules 清单）'},
}
NATIVE_ONLY_ERRORS = {
    'errors-negative-line': {'breakpoint_set 的 line 需要非负整数'},
}
# VM 进程 panic（没有终结事件）、原生照常收尾的用例。
VM_PANICS = {'errors-negative-line'}


def same_brief(native, vm):
    """Briefs agree with object numbers blanked; a VM string brief cut inside a UTF-8 sequence (U+FFFD tail)
    agrees with the native one cut at the character boundary before it."""
    a, b = OBJ.sub('#', native), OBJ.sub('#', vm)
    if a == b:
        return True
    return '\ufffd' in b and b.rstrip('\ufffd') == a


def events(text):
    out = []
    for line in text.splitlines():
        if line.startswith('< '):
            msg = json.loads(line[2:])
            if 'folded' in msg:
                out += [msg['first']] + [{'event': 'stdout', 'text': None}] * (msg['folded'] - 2) + [msg['last']]
            else:
                out.append(msg)
    return out


def norm_native(text):
    """Golden form: pc and object numbers blanked, reg_count dropped, frames keep named slots only."""
    lines = []
    for line in text.splitlines():
        if line.startswith('< '):
            msg = json.loads(line[2:])
            # TOOL-98: product versions evolve independently of debug behavior.
            # Actual CLI/LSP/MCP agreement is checked by test-toolchain-contract.
            result = msg.get('result', {}) if isinstance(msg, dict) else {}
            if isinstance(result, dict) and 'protocolVersion' in result:
                info = result.get('serverInfo', {})
                if isinstance(info, dict) and 'version' in info:
                    info['version'] = '<toolchain-version>'
            msg = blank(msg)
            line = '< ' + json.dumps(msg, ensure_ascii=False, separators=(',', ':'))
        lines.append(line)
    return '\n'.join(lines) + '\n'


def blank(v, key=None):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k == 'reg_count':
                continue
            if k == 'pc':
                out[k] = 0
            elif k == 'slots':
                out[k] = [blank(s) for s in x if 'name' in s]
            else:
                out[k] = blank(x, k)
        return out
    if isinstance(v, list):
        return [blank(x) for x in v]
    if isinstance(v, str):
        return OBJ.sub('#', v)
    return v


def merged_stops(evs):
    out = []
    for e in evs:
        if e.get('event') == 'stopped':
            key = (e['fn'], e['line'], e['depth'])
            if not out or out[-1] != key:
                out.append(key)
    return out


def frames_by_stop(evs):
    """The first non-empty frames event after each merged stop, keyed by the merged stop's ordinal."""
    out = {}
    last = None
    k = -1
    for e in evs:
        if e.get('event') == 'stopped':
            key = (e['fn'], e['line'], e['depth'])
            if key != last:
                k += 1
                last = key
        elif e.get('event') == 'frames' and e['frames'] and k >= 0 and k not in out:
            out[k] = e['frames']
    return out


def stdout_text(evs):
    return '\n'.join(e['text'] for e in evs if e.get('event') == 'stdout' and e['text'] is not None)


def terminal(evs):
    for e in evs:
        if e.get('event') in ('exited', 'trapped', 'compile_failed'):
            return e
    return None


def inspect_pairs(text):
    """inspect replies keyed by (frame, slot), first reply per key, each with the slot's entry in the frames event
    that preceded the request (None when there was none)."""
    out = {}
    pending = None
    frames = None
    for line in text.splitlines():
        if line.startswith('> '):
            try:
                c = json.loads(line[2:])
            except ValueError:
                c = None
            pending = None
            if isinstance(c, dict) and c.get('cmd') == 'inspect':
                pending = (c.get('frame'), c.get('slot'))
        elif line.startswith('< '):
            try:
                e = json.loads(line[2:])
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if e.get('event') == 'frames':
                frames = e.get('frames')
            elif pending is not None and e.get('event') in ('inspect', 'error'):
                if e.get('event') == 'inspect' and pending not in out:
                    slot = None
                    f, i = pending
                    if frames and isinstance(f, int) and 0 <= f < len(frames):
                        slot = next((x for x in frames[f]['slots'] if x['idx'] == i), None)
                    out[pending] = (e['value'], slot)
                pending = None
    return out


def same_node(n, v, path, issues):
    """An inspect node agrees with the VM's, allowing the differences listed in docs/design/v052-round6-debug-v0.1-20261018.md
    section 五 (object numbers, the VM's broken UTF-8 tail, type and variant names the VM lacks for enums made by `new`,
    the kind name, payload and cause native gives error enum values)."""
    if n.get('tag') != v.get('tag') or n.get('name') != v.get('name'):
        issues.append('%s: tag/name %r/%r vs VM %r/%r' % (path, n.get('tag'), n.get('name'), v.get('tag'), v.get('name')))
        return
    vm_lacks_names = n.get('tag') == 'enum' and 'type' not in v and str(v.get('text', '')).startswith('variant ')
    rich_err = n.get('tag') == 'err' and 'type' not in v
    if n.get('type') != v.get('type') and not vm_lacks_names and not rich_err:
        issues.append('%s: type %r vs VM %r' % (path, n.get('type'), v.get('type')))
    if not vm_lacks_names and not same_brief(str(n.get('text')), str(v.get('text'))):
        issues.append('%s: text %r vs VM %r' % (path, n.get('text'), v.get('text')))
    if n.get('rc') != v.get('rc') or n.get('truncated') != v.get('truncated'):
        issues.append('%s: rc/truncated %r/%r vs VM %r/%r' % (path, n.get('rc'), n.get('truncated'), v.get('rc'), v.get('truncated')))
    kn, kv = n.get('children', []), v.get('children', [])
    if rich_err:
        kn = [k for k in kn if k.get('name') not in ('payload', 'cause')]
    if len(kn) != len(kv):
        issues.append('%s: %d children vs VM %d' % (path, len(kn), len(kv)))
        return
    for i, (a, b) in enumerate(zip(kn, kv)):
        same_node(a, b, '%s.%s' % (path, a.get('name', i)), issues)


def cross_check_inspect(nat_text, vm_text, issues):
    """inspect of the native named, not-none slots agrees with the VM's inspect of the same slot."""
    vm = inspect_pairs(vm_text)
    for key, (node, slot) in inspect_pairs(nat_text).items():
        if slot is None or 'name' not in slot or node.get('tag') == 'none' or key not in vm:
            continue
        same_node(node, vm[key][0], '%s' % slot['name'], issues)


def dap_stdout(evs):
    out = []
    for e in evs:
        b = e.get('body') if e.get('event') == 'output' else (e if 'category' in e else None)
        if isinstance(b, dict) and b.get('category') == 'stdout' and b.get('output') is not None:
            out.append(b['output'])
    return ''.join(out).replace('\n', '')


def dap_first(evs, name):
    return next((e.get('body', {}) for e in evs if e.get('type') == 'event' and e.get('event') == name), None)


def cross_check_dap(nat, vm, issues):
    if dap_stdout(nat) != dap_stdout(vm):
        issues.append('stdout differs: %r vs %r' % (dap_stdout(nat), dap_stdout(vm)))
    # The VM sends no exited on a trap and reports the kill at disconnect; native does the opposite (§五, §四-8).
    en, ev = dap_first(nat, 'exited'), dap_first(vm, 'exited')
    if en is not None and ev is not None and en.get('exitCode') != ev.get('exitCode'):
        issues.append('exit code %r vs %r' % (en, ev))
    sn, sv = dap_first(nat, 'stopped'), dap_first(vm, 'stopped')
    where = lambda b: b and b.get('description', '').rsplit(':', 1)[0]
    if where(sn) != where(sv):
        issues.append('first stop %r vs %r' % (sn, sv))


UNKNOWN_MODULE = '未知模块（见 ready 事件的 modules 清单）'


def mcp_replies(text):
    """(request, reply) pairs of an MCP transcript."""
    out = []
    req = None
    for line in text.splitlines():
        if line.startswith('> '):
            req = json.loads(line[2:])
        elif line.startswith('< ') and req is not None:
            out.append((req, json.loads(line[2:])))
            req = None
    return out


def mcp_kind(reply):
    if 'error' in reply:
        return ('error', reply['error'].get('message'))
    if 'result' in reply:
        # 6d-4a: the same tool-argument validation text now travels in an isError result.
        # Golden comparison checks the actual new shape; this VM cross-check compares only its message.
        r = reply['result']
        content = r.get('content', []) if isinstance(r, dict) else []
        if r.get('isError') is True and 'structuredContent' not in r and len(content) == 1 \
                and content[0].get('type') == 'text' and content[0].get('text', '').startswith('Invalid params: '):
            return ('error', content[0]['text'])
        return ('result',)
    s = reply.get('structured', {})
    if 'start' in s:
        return ('start', s['start'].get('event'))
    if s.get('ok') is False:
        return ('fail', s.get('error'))
    if 'killed' in s:
        return ('killed', (s.get('exited') or {}).get('code'))
    return ('event', s.get('event'))


def cross_check_mcp(nat_text, vm_text, issues):
    nat, vm = mcp_replies(nat_text), mcp_replies(vm_text)

    def init(rs):
        for q, r in rs:
            if q.get('method') == 'initialize':
                res = dict(r.get('result', {}))
                info = dict(res.get('serverInfo', {}))
                info.pop('version', None)
                res['serverInfo'] = info
                return res
        return None

    def kinds(rs):
        out = []
        for _, r in rs:
            k = mcp_kind(r)
            if k != ('event', 'stopped') and (not out or out[-1] != k):
                out.append(k)
        return out

    def stdout(rs):
        out = []
        for _, r in rs:
            s = r.get('structured', {})
            for e in s.get('events', []):
                if 'elided' in e:
                    out.append('<elided %d>' % e['elided'])
                elif e.get('event') == 'stdout':
                    out.append(e.get('text'))
        return out

    def finals(rs):
        out = []
        for _, r in rs:
            s = r.get('structured', {})
            e = s.get('start') if 'start' in s else s
            if isinstance(e, dict) and e.get('event') in ('exited', 'trapped', 'compile_failed'):
                out.append({k: v for k, v in e.items() if k not in ('session', 'events', 'isError')})
        return out

    def receipts(rs, native):
        out = []
        for _, r in rs:
            s = r.get('structured', {})
            if 'start' in s:
                out.append([b.get('event') for b in s.get('breakpoints', [])
                            if not (native and b.get('event') == 'error' and b.get('message') == UNKNOWN_MODULE)])
        return out

    def first_stop(rs):
        for _, r in rs:
            s = r.get('structured', {})
            if s.get('event') == 'stopped':
                return (s.get('fn'), s.get('line'))
        return None

    for name, f in (('initialize', init), ('replies', kinds), ('stdout', stdout), ('final events', finals), ('first stop', first_stop)):
        a, b = f(nat), f(vm)
        if a != b:
            issues.append('%s differ: %r vs %r' % (name, a, b))
    if receipts(nat, True) != receipts(vm, False):
        issues.append('breakpoint receipts differ: %r vs %r' % (receipts(nat, True), receipts(vm, False)))


def cross_check(case, nat, vm, issues):
    script_ops = [(i.get('cmd') or i.get('@')) for i in case.get('script', []) if isinstance(i, dict)]
    continue_only = not any(op in ('step', 'step_until_exit') for op in script_ops)
    rn = next((e for e in nat if e.get('event') == 'ready'), None)
    rv = next((e for e in vm if e.get('event') == 'ready'), None)
    if (rn is None) != (rv is None):
        issues.append('ready presence differs')
    elif rn is not None:
        if [f['name'] for f in rn['functions']] != [f['name'] for f in rv['functions']]:
            issues.append('function names differ: %r vs %r' % ([f['name'] for f in rn['functions']], [f['name'] for f in rv['functions']]))
        if len(rn['modules']) != len(rv['modules']) or rn['modules'][-1]['path'] != rv['modules'][-1]['path']:
            issues.append('modules differ: %r vs %r' % (rn['modules'], rv['modules']))
    if stdout_text(nat) != stdout_text(vm):
        issues.append('stdout differs')
    tn, tv = terminal(nat), terminal(vm)
    if tn != tv and not (case['name'] in VM_PANICS and tv is None):
        issues.append('terminal event differs: %r vs %r' % (tn, tv))
    en = [e['message'] for e in nat if e.get('event') == 'error']
    ev = [e['message'] for e in vm if e.get('event') == 'error']
    en = [m for m in en if m not in NATIVE_ONLY_ERRORS.get(case['name'], set())]
    ev = [m for m in ev if m not in ALLOWED_ERRORS.get(case['name'], set())]
    if en != ev:
        issues.append('error messages differ: %r vs %r' % (en, ev))
    if not continue_only:
        return
    sn, sv = merged_stops(nat), merged_stops(vm)
    if sn != sv:
        issues.append('stops differ: %r vs %r' % (sn, sv))
        return
    fn_ = frames_by_stop(nat)
    fv = frames_by_stop(vm)
    for k in sorted(set(fn_) & set(fv)):
        a, b = fn_[k], fv[k]
        if [f['fn'] for f in a] != [f['fn'] for f in b]:
            issues.append('stack shape differs: %r vs %r' % ([f['fn'] for f in a], [f['fn'] for f in b]))
            continue
        if a and a[-1]['line'] != b[-1]['line']:
            issues.append('top frame line %r vs %r' % (a[-1]['line'], b[-1]['line']))
        for x, y in zip(a, b):
            vm_slots = {s['idx']: s for s in y['slots']}
            for s in x['slots']:
                if 'name' not in s or s['brief'] == 'none':
                    continue
                t = vm_slots.get(s['idx'])
                if t is None or not same_brief(s['brief'], t['brief']):
                    issues.append('%s %s=%r vs VM slot %r' % (x['fn'], s['name'], s['brief'], t and t['brief']))


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, type=Path)
    ap.add_argument('--update', action='store_true', help='rewrite the native goldens')
    ap.add_argument('cases', nargs='*')
    a = ap.parse_args()
    taixu = str(a.compiler.resolve())
    cases = json.loads((DEBUG / 'cases.json').read_text(encoding='utf-8'))
    native_only = json.loads((DEBUG / 'native' / 'cases.json').read_text(encoding='utf-8'))
    for c in native_only:
        c['native_only'] = True
    cases += native_only
    norm = dbl.Normalizer(dbl.DEBUG_DIR)
    fails = 0
    total = 0
    for case in cases:
        if case['kind'] not in ('proto', 'dap', 'mcp') or (a.cases and case['name'] not in a.cases):
            continue
        total += 1
        dap = case['kind'] == 'dap'
        mcp = case['kind'] == 'mcp'
        if dap:
            text = dbl.run_dap(taixu, case, norm)
            golden = DEBUG / 'native' / 'dap' / (case['name'] + '.dap')
        elif mcp:
            text = dbl.run_mcp(taixu, case, norm)
            golden = DEBUG / 'native' / 'mcp' / (case['name'] + '.mcp')
        else:
            text = dbl.run_proto(taixu, case, norm)
            golden = GOLDEN / (case['name'] + '.proto')
        issues = []
        got = norm_native(text)
        if a.update:
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(got, encoding='utf-8', newline='\n')
        elif not golden.exists() or golden.read_text(encoding='utf-8') != got:
            want = golden.read_text(encoding='utf-8').splitlines() if golden.exists() else []
            have = got.splitlines()
            i = 0
            while i < min(len(want), len(have)) and want[i] == have[i]:
                i += 1
            issues.append('golden line %d: got %r want %r' % (i + 1, have[i][:160] if i < len(have) else None, want[i][:160] if i < len(want) else None))
        if dap:
            vm = (DEBUG / 'vm' / (case['name'] + '.dap')).read_text(encoding='utf-8')
            cross_check_dap(events(text), events(vm), issues)
        elif mcp:
            vm = (DEBUG / 'vm' / (case['name'] + '.mcp')).read_text(encoding='utf-8')
            cross_check_mcp(norm_native(text), vm, issues)
        elif not case.get('native_only'):
            vm = (DEBUG / 'vm' / (case['name'] + '.proto')).read_text(encoding='utf-8')
            cross_check(case, events(text), events(vm), issues)
            cross_check_inspect(text, vm, issues)
        if issues:
            fails += 1
            print('FAIL  %-22s %s' % (case['name'], ' | '.join(issues)[:600]), flush=True)
        else:
            print('PASS  %s' % case['name'], flush=True)
    print('SUMMARY %s (%d cases)' % ('PASS' if fails == 0 else 'FAIL=%d' % fails, total))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
