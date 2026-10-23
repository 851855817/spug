#!/usr/bin/env bash
main() {
    local snapshot
    printf '=== PHP-FPM processes: bounded snapshot, no command arguments/pool names ===\n'
    snapshot=$(run 4000 ps -eo pid,comm,pcpu,pmem)
    printf '%s\n' "$snapshot" | awk '
        NR == 1 {print; next}
        $2 ~ /^php-fpm([0-9.]+)?$/ {if (++count <= 100) print}
        END {if (!count) print "No FPM process in this bounded snapshot; not a website health verdict."}
    '
    printf 'Only the first 32KiB of process output were inspected; results may be partial.\n'
}
