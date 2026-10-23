def main():
    emit('=== TCP / UDP listening sockets (up to 100 rows) ===')
    emit('No process arguments, connection termination or remote probes.')
    output = run(['ss', '-H', '-lntu'], truncate=True)
    emit('\n'.join(output.splitlines()[:100]) or 'No listening sockets reported')
    if len(output.splitlines()) > 100:
        emit('[additional rows omitted]')
