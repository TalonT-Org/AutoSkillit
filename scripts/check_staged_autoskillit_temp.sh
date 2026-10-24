#!/usr/bin/env bash
set -euo pipefail

# This pathspec intentionally mirrors _TEMP_PATHSPEC in test_temp_tracking_guard.py;
# its equivalence test guards their alignment. Keep both literals in sync.
# A plain pathspec from the top level stays valid under GIT_LITERAL_PATHSPECS.
top_level="$(git rev-parse --show-toplevel)"
cd "$top_level"

if git diff --cached --name-only -z --diff-filter=ACMRT --ignore-submodules=none -- .autoskillit/temp |
    while IFS= read -r -d '' path; do
        printf 'Refusing to stage AutoSkillit temp artifact: %s\n' "$path" >&2
        exit 1
    done
then
    exit 0
fi

exit 1
