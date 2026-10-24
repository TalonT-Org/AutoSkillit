#!/usr/bin/env bash
set -euo pipefail

# Git's pathspec engine decides containment, the same predicate as the tracked-temp
# policy. A plain pathspec from the top level stays valid under GIT_LITERAL_PATHSPECS.
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
