def main():
    emit('=== PHP-FPM processes (all versions, no command arguments/pool names) ===')
    output = run(['ps', '-eo', 'pid,comm,pcpu,pmem'], truncate=True)
    rows = output.splitlines()
    matched = [line for line in rows[1:] if re.search(r'\bphp-fpm(?:[0-9.]*)?\b', line)]
    emit('\n'.join(rows[:1] + matched[:100]))
    if not matched:
        emit('No PHP-FPM process in this bounded process snapshot; CGI/Apache modules may differ.')
    if len(matched) > 100 or '[output truncated]' in output:
        emit('[partial snapshot: output limit reached]')
