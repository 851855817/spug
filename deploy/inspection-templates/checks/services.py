def main():
    emit('=== Failed systemd service units ===')
    emit('Lists state only; no logs, configuration, reload or restart.')
    emit(run(['systemctl', '--failed', '--type=service', '--no-pager', '--plain', '--no-legend'],
             truncate=True) or 'No failed service units reported')
