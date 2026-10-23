#!/usr/bin/env bash
# Embedded into each template. Requires Linux, Bash and GNU coreutils.
set -eu
set -f

for tool in timeout head tr awk; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'ERROR: required command unavailable: %s\n' "$tool"
        exit 2
    fi
done
if [[ $(uname -s) != Linux ]]; then
    printf 'ERROR: Linux required\n'
    exit 2
fi

run() {
    local rows=$1
    shift
    local -a codes
    # Failures are captured explicitly; head may close early with SIGPIPE.
    set +e
    timeout --kill-after=1s 6s "$@" 2>/dev/null |
        head -c 32768 |
        head -n "$rows" |
        tr -cd '\11\12\40-\176'
    codes=("${PIPESTATUS[@]}")
    set -e
    local code
    for code in "${codes[@]}"; do
        if [[ $code != 0 && $code != 141 ]]; then
            printf '\nERROR: query failed or timed out: %s (exit %s)\n' "$1" "$code"
            return 2
        fi
    done
    if [[ ${codes[0]} == 141 || ${codes[1]} == 141 ]]; then
        printf '\n[partial output: limit reached]\n'
    fi
    printf '\n'
}

php_branches() {
    case "${_SPUG_PHP_VERSION:-all}" in
        all) printf '%s\n' 54 55 56 70 71 72 73 74 80 81 82 83 84 85 ;;
        54|5.4) printf '54\n' ;; 55|5.5) printf '55\n' ;; 56|5.6) printf '56\n' ;;
        70|7.0) printf '70\n' ;; 71|7.1) printf '71\n' ;; 72|7.2) printf '72\n' ;;
        73|7.3) printf '73\n' ;; 74|7.4) printf '74\n' ;; 80|8.0) printf '80\n' ;;
        81|8.1) printf '81\n' ;; 82|8.2) printf '82\n' ;; 83|8.3) printf '83\n' ;;
        84|8.4) printf '84\n' ;; 85|8.5) printf '85\n' ;;
        *) printf 'ERROR: PHP_VERSION must be all or e.g. 7.4\n' >&2; return 2 ;;
    esac
}
