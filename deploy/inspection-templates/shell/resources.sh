#!/usr/bin/env bash
main() {
    printf '=== Logical CPU count / load / memory ===\n'
    run 1 getconf _NPROCESSORS_ONLN
    run 2 uptime
    run 10 free -m
    printf 'Snapshot only; load is not CPU usage. Memory units: MiB.\n'
}
