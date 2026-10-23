def main():
    keys = ('display_errors', 'log_errors', 'expose_php', 'allow_url_include',
            'allow_url_fopen', 'cgi.fix_pathinfo', 'session.cookie_httponly',
            'session.cookie_secure', 'session.use_strict_mode', 'opcache.enable')
    emit('=== php.ini security-related settings (file values only) ===')
    emit('Not effective runtime values: extra INIs, FPM pools and site overrides may differ.')
    emit('UNSET means no value here; this is not a pass/fail security verdict.')
    for branch in installed_branches():
        content = read_text('/www/server/php/' + branch + '/etc/php.ini')
        values = {}
        for line in content.splitlines():
            match = re.match(r'^\s*([a-zA-Z0-9_.]+)\s*=\s*([^;\r\n]*)', line)
            if match and match[1] in keys:
                value = match[2].strip().strip('"\'')
                values[match[1]] = value if re.fullmatch(r'(?i:on|off|true|false|yes|no|0|1|)', value) else '[non-boolean value omitted]'
        emit('PHP ' + branch)
        for key in keys:
            emit('  ' + key + '=' + values.get(key, 'UNSET'))
