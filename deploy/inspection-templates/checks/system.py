def main():
    emit('=== OS / kernel / uptime / UTC time ===')
    # Parse data only. Never source /etc/os-release as shell code.
    try:
        content = read_text('/etc/os-release', 32768)
    except OSError:
        content = read_text('/usr/lib/os-release', 32768)
    for line in content.splitlines():
        if line.startswith(('PRETTY_NAME=', 'ID=', 'VERSION_ID=')):
            emit(line)
    emit(run(['uname', '-sr']))
    emit(run(['uptime']))
    emit('UTC: ' + datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
    emit('Local timezone: ' + time.strftime('%Z %z'))
