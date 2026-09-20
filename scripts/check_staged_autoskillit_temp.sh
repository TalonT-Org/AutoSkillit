#!/usr/bin/env bash
set -euo pipefail

if git diff --cached --name-only -z --diff-filter=ACMRT |
    while IFS= read -r -d '' path; do
        case "$path" in
            .autoskillit/temp/*)
                printf 'Refusing to stage AutoSkillit temp artifact: %s\n' "$path" >&2
                exit 1
                ;;
        esac
    done
then
    exit 0
fi

exit 1
