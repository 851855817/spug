#!/usr/bin/env bash
main() {
    printf '=== Local filesystem space (up to 100 rows) ===\n'
    run 101 df -l -h -P
    printf '=== Local filesystem inodes (up to 100 rows) ===\n'
    run 101 df -l -i -P
}
