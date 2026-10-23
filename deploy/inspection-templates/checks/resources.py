def main():
    emit('=== CPU count / load / memory (snapshot, not CPU usage sample) ===')
    emit('Logical CPU: ' + str(os.cpu_count()))
    emit('Load 1/5/15 min: ' + ' / '.join('%.2f' % x for x in os.getloadavg()))
    allowed = {'MemTotal', 'MemFree', 'MemAvailable', 'Buffers', 'Cached', 'SwapTotal', 'SwapFree'}
    for line in read_text('/proc/meminfo', 32768).splitlines():
        if line.partition(':')[0] in allowed:
            emit(line)
