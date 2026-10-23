#!/usr/bin/env bash
main() {
    printf '=== TCP / UDP listeners: up to 100 rows, no remote probing ===\n'
    run 100 ss -H -lntu
}
