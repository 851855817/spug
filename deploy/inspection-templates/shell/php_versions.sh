#!/usr/bin/env bash
main() {
    local branches branch binary parent found=0
    # Parse before inspecting binaries. Parameters are never shell commands.
    if ! branches=$(php_branches); then
        printf 'ERROR: invalid PHP_VERSION\n'
        return 2
    fi
    printf '=== Baota CLI version; -n skips INI and dynamic extensions ===\n'
    printf 'CLI version does not prove website/FPM version or health.\n'
    for parent in /www /www/server /www/server/php; do
        if [[ -L $parent ]]; then
            printf 'ERROR: linked PHP parent directory refused\n'
            return 2
        fi
    done
    for branch in $branches; do
        binary="/www/server/php/$branch/bin/php"
        [[ -d /www/server/php/$branch ]] || continue
        if [[ -L /www/server/php/$branch || -L /www/server/php/$branch/bin ||
              -L $binary || ! -f $binary || ! -x $binary ]]; then
            printf 'ERROR: untrusted/missing PHP binary for branch %s\n' "$branch"
            return 2
        fi
        found=1
        printf 'PHP %s: ' "$branch"
        run 2 "$binary" -n -v
    done
    if [[ $found == 0 ]]; then
        printf 'ERROR: no selected Baota PHP installation found\n'
        return 2
    fi
}
