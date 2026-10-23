#!/usr/bin/env bash
main() {
    printf '=== OS / kernel / uptime / time (bounded query output) ===\n'
    run 8 awk -F= '/^(PRETTY_NAME|ID|VERSION_ID)=/ {print}' /etc/os-release
    run 2 uname -sr
    run 2 uptime
    run 1 date -u '+UTC: %Y-%m-%d %H:%M:%S'
    run 1 date '+Local timezone: %Z %z'
}
