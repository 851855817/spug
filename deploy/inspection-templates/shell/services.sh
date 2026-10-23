#!/usr/bin/env bash
main() {
    printf '=== Failed systemd services: up to 100 rows, no logs or restart ===\n'
    run 100 systemctl --failed --type=service --no-pager --plain --no-legend
    printf 'Empty list means no failed units reported; non-systemd hosts may be unavailable.\n'
}
