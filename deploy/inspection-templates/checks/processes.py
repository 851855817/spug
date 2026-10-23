def main():
    emit('=== Top 20 processes by lifetime CPU / current memory ===')
    emit('Only PID, executable name and percentages; command arguments excluded.')
    for sort in ('-pcpu', '-pmem'):
        emit('Sort: ' + sort)
        output = run(['ps', '-eo', 'pid,comm,pcpu,pmem', '--sort=' + sort], truncate=True)
        emit('\n'.join(output.splitlines()[:21]))
