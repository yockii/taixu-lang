#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug-build stub gate (round 6 step 6c-2, docs/design/v052-round6-debug-v0.1-20261018.md §三-5, §四-4).

Builds programs under tests/debug with `taixu build --debug`, starts each product with a private control
channel (two pipes handed over through TAIXU_DEBUG_CHANNEL=<read>,<write>; Windows handle values or Linux
file descriptors) and drives the stub with its binary protocol:
  product -> client: HELLO 1, STOP 2, TIMEOUT 3, FRAMES reply 4, READ reply 5
  client -> product: ARM 10 (fn, pc, value), RUN 11 (mode 0 continue/1 into/2 over/3 out, deadline ms),
                     FRAMES 12, READ 13 (address, length), PAUSE 14 (while running; round 6 step 6c-5a)
Every frame is a u32 little-endian length, a u8 opcode and u64 little-endian fields.

The client knows no names: it reads the program image (`__tx_nrt_image`, address from HELLO) through READ to
map modules and source lines to (function, pc) and pcs back to lines. Each scenario checks the exact stop
sequence (reason, line, depth), the program's stdout and exit code; others check FRAMES, slot values read
through READ, a READ of an invalid address (0 bytes, the product keeps running), the continue deadline
(TIMEOUT, exit 3), a closed channel (exit 3) and that threads other than main never stop.

Round 6 step 6c-3 adds two checks: a toolchain program (tests/debug/native/ctl-driver.tx, built with
--toolchain) drives a debug product through procSpawn's control channel (HELLO, RUN into, STOP, RUN continue,
exit code and stdout); and a TAIXU_DEBUG_CHANNEL naming handles or descriptors that are not pipes leaves the
product running as without a channel.

Round 6 step 6c-5a (HELLO version 2): the stub looks at the channel every 8192 new-line events while running, so an
ARM sent while the product runs takes effect at once and PAUSE stops it (reason 3). The live check arms a line in
an endless loop after RUN, pauses after the next RUN, sends a stale PAUSE while stopped (ignored) and ends with a
deadline.

Runs the products of the host platform (on Linux run it inside Linux/WSL with a Linux compiler).
Usage: python scripts/test-native-debug-stub.py --compiler <taixu> [--work <dir>] [scenario ...]
"""
import argparse
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBUG = ROOT / 'tests' / 'debug'
WIN = os.name == 'nt'
TIMEOUT_S = 30

OP_HELLO, OP_STOP, OP_TIMEOUT, OP_FRAMES_R, OP_READ_R = 1, 2, 3, 4, 5
OP_ARM, OP_RUN, OP_FRAMES, OP_READ, OP_PAUSE = 10, 11, 12, 13, 14
CONTINUE, INTO, OVER, OUT = 0, 1, 2, 3
BREAKPOINT, STEP, PAUSE = 1, 2, 3


class StubError(Exception):
    pass


class Session:
    def __init__(self, exe, channel=True, extra_env=None):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_')}
        env.update(extra_env or {})
        self.out_chunks = []
        self.err_chunks = []
        self.frames = queue.Queue()
        pass_fds = ()
        startupinfo = None
        if channel:
            c_read, p_write = os.pipe()   # client -> product
            p_read, c_write = os.pipe()   # product -> client
            if WIN:
                import msvcrt
                h_in = msvcrt.get_osfhandle(c_read)
                h_out = msvcrt.get_osfhandle(c_write)
                os.set_handle_inheritable(h_in, True)
                os.set_handle_inheritable(h_out, True)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.lpAttributeList = {'handle_list': [h_in, h_out]}
                env['TAIXU_DEBUG_CHANNEL'] = '%d,%d' % (h_in, h_out)
            else:
                pass_fds = (c_read, c_write)
                env['TAIXU_DEBUG_CHANNEL'] = '%d,%d' % (c_read, c_write)
        kw = {'startupinfo': startupinfo} if WIN else {'pass_fds': pass_fds}
        self.proc = subprocess.Popen([str(exe)], cwd=str(Path(exe).parent), env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
        # finish() 等进程退出后还要等这两个线程读完，否则刚退出的产物的输出可能还没收到。
        self.drains = [threading.Thread(target=self._drain, args=(self.proc.stdout, self.out_chunks), daemon=True),
                       threading.Thread(target=self._drain, args=(self.proc.stderr, self.err_chunks), daemon=True)]
        for t in self.drains:
            t.start()
        self.wfile = None
        if channel:
            os.close(c_read)
            os.close(c_write)
            self.wfile = os.fdopen(p_write, 'wb', buffering=0)
            self.rfile = os.fdopen(p_read, 'rb', buffering=0)
            threading.Thread(target=self._pump, daemon=True).start()

    @staticmethod
    def _drain(stream, sink):
        while True:
            b = stream.read(65536)
            if not b:
                return
            sink.append(b)

    def _pump(self):
        try:
            while True:
                hdr = self._exact(4)
                if hdr is None:
                    break
                n = struct.unpack('<I', hdr)[0]
                body = self._exact(n)
                if body is None:
                    break
                self.frames.put(body)
        finally:
            self.frames.put(None)

    def _exact(self, n):
        buf = b''
        while len(buf) < n:
            b = self.rfile.read(n - len(buf))
            if not b:
                return None
            buf += b
        return buf

    def send(self, op, *fields):
        body = bytes([op]) + b''.join(struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF) for v in fields)
        self.wfile.write(struct.pack('<I', len(body)) + body)

    def recv(self, timeout=TIMEOUT_S):
        try:
            body = self.frames.get(timeout=timeout)
        except queue.Empty:
            raise StubError('no frame within %ds' % timeout)
        if body is None:
            return None, b''
        return body[0], body[1:]

    def expect(self, op):
        got, payload = self.recv()
        if got != op:
            raise StubError('expected op %d, got %r' % (op, got))
        return payload

    def read(self, addr, n):
        self.send(OP_READ, addr, n)
        p = self.expect(OP_READ_R)
        got = struct.unpack_from('<Q', p, 0)[0]
        return p[8:8 + got]

    def u64(self, addr):
        b = self.read(addr, 8)
        if len(b) != 8:
            raise StubError('READ failed at %#x' % addr)
        return struct.unpack('<Q', b)[0]

    def stack(self):
        self.send(OP_FRAMES)
        p = self.expect(OP_FRAMES_R)
        total, n = struct.unpack_from('<QQ', p, 0)
        return total, [struct.unpack_from('<QQQ', p, 16 + 24 * i) for i in range(n)]

    def finish(self, timeout=TIMEOUT_S):
        if self.wfile is not None:
            try:
                self.wfile.close()
            except OSError:
                pass
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = 'killed'
        if code != 'killed' and code > 0x7FFFFFFF:
            code -= 1 << 32
        for t in self.drains:
            t.join(timeout)
        out = b''.join(self.out_chunks).replace(b'\r\n', b'\n').decode('utf-8', 'replace')
        err = b''.join(self.err_chunks).replace(b'\r\n', b'\n').decode('utf-8', 'replace')
        return code, out, err


class Image:
    """Program image read through READ: modules, functions, per-pc lines."""

    def __init__(self, s, img, nfuncs):
        hdr = s.read(img, 104)
        if len(hdr) != 104:
            raise StubError('image header unreadable')
        q = lambda off: struct.unpack_from('<Q', hdr, off)[0]
        self.nf, nmod, funcs, mods, strs = q(16), q(24), q(48), q(56), q(64)
        if self.nf != nfuncs:
            raise StubError('HELLO nfuncs %d != image %d' % (nfuncs, self.nf))
        rows = s.read(funcs, 64 * self.nf)
        self.fn_module = []
        self.lines = []
        for i in range(self.nf):
            reg, mod, _, nlines, lp = struct.unpack_from('<QQQQQ', rows, 64 * i)
            self.fn_module.append(mod)
            raw = s.read(lp, 8 * nlines) if nlines else b''
            self.lines.append([struct.unpack_from('<Q', raw, 8 * k)[0] & 0xFFFFFFFF for k in range(nlines)])
        mrows = s.read(mods, 32 * nmod)
        self.mod_names = []
        for m in range(nmod):
            si = struct.unpack_from('<Q', mrows, 32 * m + 24)[0]
            ln, ptr = struct.unpack('<QQ', s.read(strs + 16 * si, 16))
            self.mod_names.append(s.read(ptr, ln).decode('utf-8', 'replace'))

    def module(self, name):
        hits = [m for m, n in enumerate(self.mod_names) if n == name or n.replace('\\', '/').split('/')[-1] == name]
        if len(hits) != 1:
            raise StubError('module %r not found in %r' % (name, self.mod_names))
        return hits[0]

    def pcs_at(self, module, line):
        m = self.module(module)
        return [(f, pc) for f in range(self.nf) if self.fn_module[f] == m for pc, ln in enumerate(self.lines[f]) if ln == line]

    def line(self, fn, pc):
        return self.lines[fn][pc] if 0 <= fn < self.nf and 0 <= pc < len(self.lines[fn]) else -1


def start(exe):
    s = Session(exe)
    p = s.expect(OP_HELLO)
    version, tb, img, nf = struct.unpack_from('<QQQQ', p, 0)
    if version != 2:
        raise StubError('HELLO version %d' % version)
    return s, Image(s, img, nf), tb


def arm(s, image, module, line, value=1):
    pcs = image.pcs_at(module, line)
    if not pcs:
        raise StubError('no pc at %s:%d' % (module, line))
    for fn, pc in pcs:
        s.send(OP_ARM, fn, pc, value)


def run(s, image, mode, deadline_ms=0):
    """RUN, then the next STOP as (reason, line, depth, fn, win), or None when the product is gone."""
    s.send(OP_RUN, mode, deadline_ms)
    op, p = s.recv()
    if op is None:
        return None
    if op == OP_TIMEOUT:
        return 'timeout'
    if op != OP_STOP:
        raise StubError('expected STOP, got op %r' % op)
    reason, fn, pc, depth, win = struct.unpack_from('<QQQQQ', p, 0)
    return (reason, image.line(fn, pc), depth, fn, win)


def stops(s, image, modes):
    out = []
    for mode in modes:
        r = run(s, image, mode)
        if r is None:
            out.append('exit')
            break
        out.append(r if isinstance(r, str) else r[:3])
    return out


# —— scenarios ——————————————————————————————————————————————————————————————————————
# Each returns a list of (label, ok, detail). Expected stop sequences are (reason, line, depth); 'exit'
# means the product ended (channel EOF). They follow the new-line rule of design §四-4.

def sc_step_into(build):
    s, image, _ = start(build('basic.tx'))
    got = stops(s, image, [INTO] * 9)
    code, out, _ = s.finish()
    want = [(STEP, 2, 1), (STEP, 10, 1), (STEP, 11, 1), (STEP, 5, 2), (STEP, 6, 2), (STEP, 12, 1), (STEP, 13, 1), (STEP, 14, 1), 'exit']
    return [('step-into', got == want and code == 10 and out == 'b=2\n', 'got=%r exit=%r out=%r' % (got, code, out))]


def sc_breakpoint_frames(build):
    s, image, tb = start(build('basic.tx'))
    arm(s, image, 'basic.tx', 5)
    rows = []
    r = run(s, image, CONTINUE)
    ok = r is not None and r[:3] == (BREAKPOINT, 5, 2)
    rows.append(('bp-hit', ok, 'got=%r' % (r,)))
    total, frames = s.stack()
    ok = total == 2 and len(frames) == 2 and frames[0][0] < frames[1][0] and frames[1][0] == r[4] \
        and image.line(frames[0][1], frames[0][2]) == 11 and frames[0][0] & -268435456 == tb
    rows.append(('frames', ok, 'total=%d frames=%r' % (total, frames)))
    # twice(x) is called with x = 1: slot 1 of its window holds int (tag 1) 1.
    slot = s.read(r[4] + 16, 16)
    ok = len(slot) == 16 and slot[0] == 1 and struct.unpack_from('<q', slot, 8)[0] == 1
    rows.append(('read-slot', ok, 'slot=%r' % slot.hex()))
    bad = s.read(8, 16)
    rows.append(('read-invalid', bad == b'', 'got %d bytes' % len(bad)))
    r2 = run(s, image, CONTINUE)
    code, out, _ = s.finish()
    rows.append(('bp-continue-exit', r2 is None and code == 10 and out == 'b=2\n', 'got=%r exit=%r out=%r' % (r2, code, out)))
    return rows


def sc_over_out(build):
    s, image, _ = start(build('basic.tx'))
    arm(s, image, 'basic.tx', 11)
    got = stops(s, image, [CONTINUE, OVER, INTO, CONTINUE])
    code, _, _ = s.finish()
    want = [(BREAKPOINT, 11, 1), (STEP, 12, 1), (STEP, 13, 1), 'exit']
    rows = [('step-over', got == want and code == 10, 'got=%r exit=%r' % (got, code))]
    s, image, _ = start(build('basic.tx'))
    arm(s, image, 'basic.tx', 5)
    got = stops(s, image, [CONTINUE, OUT, CONTINUE])
    code, _, _ = s.finish()
    want = [(BREAKPOINT, 5, 2), (STEP, 12, 1), 'exit']
    rows.append(('step-out', got == want and code == 10, 'got=%r exit=%r' % (got, code)))
    return rows


def sc_recurse(build):
    s, image, _ = start(build('recurse.tx'))
    arm(s, image, 'recurse.tx', 4)
    r = run(s, image, CONTINUE)
    total, frames = s.stack()
    arm(s, image, 'recurse.tx', 4, 0)
    got = [r[:3] if r else None] + stops(s, image, [OUT, OUT, OUT, OUT, CONTINUE])
    code, out, _ = s.finish()
    want = [(BREAKPOINT, 4, 5), (STEP, 7, 4), (STEP, 7, 3), (STEP, 7, 2), (STEP, 12, 1), 'exit']
    ok = got == want and total == 5 and code == 0 and out == '24\n' \
        and all(frames[i][0] < frames[i + 1][0] for i in range(len(frames) - 1))
    return [('recurse-out', ok, 'got=%r total=%r exit=%r out=%r' % (got, total, code, out))]


def sc_async(build):
    s, image, _ = start(build('async.tx'))
    arm(s, image, 'async.tx', 3)
    arm(s, image, 'async.tx', 11)
    got = stops(s, image, [CONTINUE, CONTINUE, CONTINUE])
    code, out, _ = s.finish()
    want = [(BREAKPOINT, 3, 1), (BREAKPOINT, 11, 1), 'exit']
    return [('async', got == want and code == 0 and out == 'spawned\njob 15\ngot 15\n', 'got=%r exit=%r out=%r' % (got, code, out))]


def sc_generator(build):
    s, image, _ = start(build('native/gen.tx'))
    arm(s, image, 'gen.tx', 5)
    got = stops(s, image, [CONTINUE] * 4)
    code, out, _ = s.finish()
    want = [(BREAKPOINT, 5, 2)] * 3 + ['exit']
    rows = [('gen-breakpoint', got == want and code == 0 and out == '30\n', 'got=%r exit=%r out=%r' % (got, code, out))]
    s, image, _ = start(build('native/gen.tx'))
    arm(s, image, 'gen.tx', 13)
    first = run(s, image, CONTINUE)
    arm(s, image, 'gen.tx', 13, 0)
    got = [first[:3] if first else None] + stops(s, image, [INTO, INTO, OVER, OVER, OVER, CONTINUE])
    code, _, _ = s.finish()
    # After the body the consumer is back on the `for` line before genNext resumes the body; stepping over
    # the yield returns to the body of the loop (the rest of line 12 is the same line, not a new-line event).
    want = [(BREAKPOINT, 13, 1), (STEP, 12, 1), (STEP, 6, 2), (STEP, 4, 2), (STEP, 5, 2), (STEP, 13, 1), 'exit']
    rows.append(('gen-step', got == want and code == 0, 'got=%r exit=%r' % (got, code)))
    return rows


def sc_thread(build):
    s, image, _ = start(build('native/thread.tx'))
    arm(s, image, 'thread.tx', 9)
    got = stops(s, image, [CONTINUE, CONTINUE])
    code, out, _ = s.finish()
    want = [(BREAKPOINT, 9, 2), 'exit']
    return [('thread-main-only', got == want and code == 0 and out == '499500 499500\n', 'got=%r exit=%r out=%r' % (got, code, out))]


def sc_timeout(build):
    s, image, _ = start(build('loop.tx'))
    r = run(s, image, CONTINUE, 1000)
    code, _, _ = s.finish()
    return [('deadline', r == 'timeout' and code == 3, 'got=%r exit=%r' % (r, code))]


def sc_eof(build):
    s, image, _ = start(build('basic.tx'))
    arm(s, image, 'basic.tx', 12)
    r = run(s, image, CONTINUE)
    code, out, _ = s.finish()
    return [('channel-closed', r is not None and r[:3] == (BREAKPOINT, 12, 1) and code == 3 and out == '', 'got=%r exit=%r out=%r' % (r, code, out))]


def sc_live(build):
    s, image, _ = start(build('loop.tx'))
    rows = []
    # ARM while running: the endless loop stops on line 5 once the stub sees the frame.
    s.send(OP_RUN, CONTINUE, 0)
    time.sleep(0.3)
    arm(s, image, 'loop.tx', 5)
    op, p = s.recv()
    r = struct.unpack_from('<QQQQQ', p, 0) if op == OP_STOP else None
    ok = r is not None and r[0] == BREAKPOINT and image.line(r[1], r[2]) == 5 and r[3] == 1
    rows.append(('live-arm', ok, 'op=%r stop=%r' % (op, r)))
    # PAUSE while running: stops on a line of the loop with reason 3.
    arm(s, image, 'loop.tx', 5, 0)
    s.send(OP_RUN, CONTINUE, 0)
    time.sleep(0.3)
    s.send(OP_PAUSE)
    op, p = s.recv()
    r = struct.unpack_from('<QQQQQ', p, 0) if op == OP_STOP else None
    ok = r is not None and r[0] == PAUSE and image.line(r[1], r[2]) in (4, 5) and r[3] == 1
    rows.append(('pause', ok, 'op=%r stop=%r' % (op, r)))
    # A PAUSE that arrives while stopped is ignored; the product still answers and runs on.
    s.send(OP_PAUSE)
    total, _ = s.stack()
    r2 = run(s, image, CONTINUE, 500)
    code, _, _ = s.finish()
    rows.append(('stale-pause', total == 1 and r2 == 'timeout' and code == 3, 'total=%r got=%r exit=%r' % (total, r2, code)))
    return rows


def sc_inert(build):
    s = Session(build('basic.tx'), channel=False)
    code, out, err = s.finish()
    return [('no-channel', code == 10 and out == 'b=2\n' and err == '', 'exit=%r out=%r err=%r' % (code, out, err))]


def sc_bogus(build):
    s = Session(build('basic.tx'), channel=False, extra_env={'TAIXU_DEBUG_CHANNEL': '99,98'})
    code, out, err = s.finish()
    return [('bogus-channel', code == 10 and out == 'b=2\n' and err == '', 'exit=%r out=%r err=%r' % (code, out, err))]


def sc_driver(build):
    product = build('basic.tx')
    driver = build('native/ctl-driver.tx', '--toolchain')
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('TAIXU_')}
    p = subprocess.run([str(driver), str(product)], cwd=str(product.parent), env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=TIMEOUT_S * 2)
    out = p.stdout.replace(b'\r\n', b'\n').decode('utf-8', 'replace')
    want = ('hello: op=1 version=2\nrun into sent: 21\nstop: op=2 reason=2 depth=1\ncontinue sent: 21\n'
            'product stdout: b=2\n\nproduct exit: 10\nctl after exit: -1\n')
    return [('toolchain-driver', p.returncode == 0 and out == want, 'exit=%r out=%r err=%r' % (p.returncode, out, p.stderr[-300:]))]


SCENARIOS = {
    'step-into': sc_step_into, 'breakpoint': sc_breakpoint_frames, 'over-out': sc_over_out, 'recurse': sc_recurse,
    'async': sc_async, 'generator': sc_generator, 'thread': sc_thread, 'timeout': sc_timeout, 'eof': sc_eof,
    'live': sc_live, 'inert': sc_inert, 'bogus': sc_bogus, 'driver': sc_driver,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', required=True, type=Path)
    ap.add_argument('--work', type=Path)
    ap.add_argument('scenarios', nargs='*')
    a = ap.parse_args()
    a.compiler = a.compiler.resolve()
    work = (a.work or Path(tempfile.mkdtemp(prefix='tx-dbgstub-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    built = {}

    def build(rel, flag='--debug'):
        key = (rel, flag)
        if key not in built:
            out = work / (rel.replace('/', '_').replace('.tx', '') + flag.replace('-', '_'))
            shutil.rmtree(out, ignore_errors=True)
            p = subprocess.run([str(a.compiler), 'build', str(DEBUG / rel), flag, '--out', str(out)], capture_output=True, timeout=600)
            exe = out / (Path(rel).stem + ('.exe' if WIN else ''))
            if p.returncode != 0 or not exe.exists():
                raise StubError('build %s failed: %s' % (rel, (p.stdout + p.stderr).decode('utf-8', 'replace')[-400:]))
            built[key] = exe
        return built[key]

    names = a.scenarios or list(SCENARIOS)
    fails = 0
    total = 0
    for name in names:
        try:
            rows = SCENARIOS[name](build)
        except (StubError, OSError, struct.error) as e:
            rows = [(name, False, 'error: %s' % e)]
        for label, ok, detail in rows:
            total += 1
            fails += 0 if ok else 1
            print('%-5s %-20s %s' % ('PASS' if ok else 'FAIL', label, '' if ok else detail), flush=True)
    print('SUMMARY %s (%d checks)' % ('PASS' if fails == 0 else 'FAIL=%d' % fails, total))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
