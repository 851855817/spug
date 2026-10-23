def main():
    emit('=== Extensions declared in Baota PHP php.ini ===')
    emit('Configured entries only, NOT proof modules are installed/loaded. Extra INI directories excluded.')
    for branch in installed_branches():
        names = set()
        content = read_text('/www/server/php/' + branch + '/etc/php.ini')
        for line in content.splitlines():
            match = re.match(r'^\s*(?:zend_extension|extension)\s*=\s*([^;\r\n]+)', line)
            if match:
                name = match[1].strip().strip('"\'').rsplit('/', 1)[-1]
                if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name):
                    names.add(name)
        emit('PHP ' + branch + ': ' + (', '.join(sorted(names)[:100]) or 'no declared entries'))
        if len(names) > 100:
            emit('[additional entries omitted]')
