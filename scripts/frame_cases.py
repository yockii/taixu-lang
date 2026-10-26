"""LSP/DAP 共用的有界帧头回归数据；不发送文件或程序执行请求。"""
import json
import subprocess


def framing_cases(exe, service, cwd, check):
    request = ({'jsonrpc': '2.0', 'id': 7, 'method': 'initialize', 'params': {}}
               if service == 'lsp' else {'seq': 7, 'type': 'request', 'command': 'initialize', 'arguments': {}})
    body = json.dumps(request).encode()
    head = b'Content-Length: %d\r\n\r\n' % len(body)
    cases = [('ordinary', head + body, True), ('lf', head.replace(b'\r', b'') + body, True)]
    for size in (65535, 65536, 65537):
        prefix = b'X: ' + b'a' * (size - len(head) - 5) + b'\r\n'
        assert len(prefix + head) == size
        cases.append(('header-%d' % size, prefix + head + body, size <= 65536))
    cases += [
        ('short-lines-total', b'X: a\r\n' * 11000 + head + body, False),
        ('complete-long-line', b'X: ' + b'a' * 70000 + b'\r\n' + head + body, False),
        ('duplicate-same', head[:-2] + b'content-length: %d\r\n\r\n' % len(body) + body, False),
        ('duplicate-different', head[:-2] + b'Content-Length: 1\r\n\r\n' + body, False),
        ('duplicate-space-case', head[:-2] + b' CONTENT-LENGTH \t: 1\r\n\r\n' + body, False),
        ('interior-cr', head.replace(b'Length', b'Le\rngth') + body, False),
        ('non-utf8-duplicate', b'Content-Length: 5\xff\r\n' + head + body, False),
        ('non-ascii-header', b'X-Note: \xe4\xb8\xad\r\n' + head + body, False),
        ('control-byte', b'X: a\x01\r\n' + head + body, False),
        ('missing-length', b'X: a\r\n\r\n' + body, False),
        ('truncated-header', b'Content-Length: 2\r\n', False),
        ('truncated-body', head + body[:-1], False),
    ]
    for value in (b'', b'-1', b'+1', b'1e3', b'18446744073709551616', b'999999999999999999'):
        cases.append(('length-' + repr(value), b'Content-Length: ' + value + b'\r\n\r\n', False))
    for name, data, accepted in cases:
        p = subprocess.run([str(exe), service], cwd=cwd, input=data, capture_output=True, timeout=15)
        if accepted:
            marker = b'"id":7' if service == 'lsp' else b'"request_seq":7'
            good = p.returncode == (1 if service == 'lsp' else 0) and marker in p.stdout
        else:
            want = b'MessageTooLong' if name == 'length-' + repr(b'999999999999999999') else b'BadFrame'
            good = p.returncode == 3 and want in p.stderr
        check('framing:' + name, good, (p.returncode, p.stderr[-200:], p.stdout[:200]))
