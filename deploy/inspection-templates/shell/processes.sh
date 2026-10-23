#!/usr/bin/env bash
main() {
    printf '=== CPU TOP20: PID / name / CPU / memory; no command arguments ===\n'
    run 21 ps -eo pid,comm,pcpu,pmem --sort=-pcpu
    printf '=== Memory TOP20 ===\n'
    run 21 ps -eo pid,comm,pcpu,pmem --sort=-pmem
    printf 'CPU is lifetime average, not an instantaneous usage sample.\n'
}
