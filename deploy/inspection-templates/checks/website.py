def main():
    domain = domain_name(os.environ.get('_SPUG_DOMAIN', ''))
    started = time.monotonic()
    with connect_tls(domain) as conn:
        conn.sendall(('HEAD / HTTP/1.1\r\nHost: ' + domain +
                      '\r\nUser-Agent: Spug-ReadOnly-Check/1.0\r\nConnection: close\r\n\r\n').encode('ascii'))
        line = bytearray()
        while not line.endswith(b'\r\n') and len(line) < 1024:
            char = conn.recv(1)
            if not char:
                break
            line.extend(char)
    match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2})(?: [^\r\n]*)?\r\n', bytes(line))
    if not match:
        raise CheckError('invalid or oversized HTTP status line')
    status = int(match[1])
    if status < 200:
        raise CheckError('HTTP interim response received; final status was not checked')
    emit('%s | HTTPS HEAD / | HTTP %s | %.2fs | TLS verified' % (
        domain, status, time.monotonic() - started))
    if 300 <= status < 400:
        emit('Redirect reported; target was not followed.')
    elif status == 405:
        emit('Server disallows HEAD; no GET fallback was made.')
    emit('This checks the root endpoint only; application/login health is not tested.')
    return 1 if status >= 400 else 0
