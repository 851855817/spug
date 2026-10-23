def main():
    emit('=== Baota PHP CLI versions; -n skips php.ini and dynamic extensions ===')
    emit('CLI version does not prove a website or FPM uses this version.')
    failed = False
    for branch in installed_branches():
        binary = '/www/server/php/' + branch + '/bin/php'
        if os.path.realpath(binary) != binary or not os.path.isfile(binary):
            emit('PHP ' + branch + ': trusted regular binary not found (symlink refused)')
            failed = True
            continue
        try:
            emit('PHP ' + branch + ': ' + '\n'.join(run([binary, '-n', '-v'], timeout=3).splitlines()[:2]))
        except CheckError as error:
            emit('PHP ' + branch + ': ' + str(error))
            failed = True
    return 2 if failed else 0
