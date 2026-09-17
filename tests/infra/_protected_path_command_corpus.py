"""Shared protected-path command vectors for hook and server guard tests."""

from __future__ import annotations

ADMITTED_COMMANDS = (
    "git add -- src/autoskillit/recipes/remediation.yaml",
    "git diff --stat -- src/autoskillit/recipes/remediation.yaml",
    "git diff --name-only -- src/autoskillit/recipes/remediation.yaml",
    "git status -- src/autoskillit/recipes/remediation.yaml",
    "wc -l src/autoskillit/recipes/remediation.yaml",
    "git -C /repo status -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore -v src/autoskillit/recipes/remediation.yaml",
    "git check-ignore --verbose --no-index -- src/autoskillit/recipes/remediation.yaml",
    (
        "git check-ignore -v src/autoskillit/recipes/remediation.yaml "
        ".autoskillit/recipes/remediation.yaml"
    ),
    "git check-ignore -v src/autoskillit/recipes/remediation.yaml || true",
    (
        "git check-ignore -v src/autoskillit/recipes/remediation.yaml "
        "&& git check-ignore --verbose --no-index -- .autoskillit/recipes/remediation.yaml"
    ),
)


DENIED_COMMANDS = (
    "git diff -- src/autoskillit/recipes/remediation.yaml",
    "git status -v -- src/autoskillit/recipes/remediation.yaml",
    "git add -p -- src/autoskillit/recipes/remediation.yaml",
    "git add --pathspec-from-file=src/autoskillit/recipes/remediation.yaml",
    "git add --pathspec-from-file src/autoskillit/recipes/remediation.yaml",
    "git diff --stat --patch-with-stat -- src/autoskillit/recipes/remediation.yaml",
    "git diff --stat --patch-with-raw -- src/autoskillit/recipes/remediation.yaml",
    "git diff --stat --binary -- src/autoskillit/recipes/remediation.yaml",
    "git diff --check -- src/autoskillit/recipes/remediation.yaml",
    "python3 <<'PY'\nprint(open('src/autoskillit/recipes/remediation.yaml').read())\nPY",
    (
        "git add -- src/autoskillit/recipes/remediation.yaml\n"
        "cat src/autoskillit/recipes/remediation.yaml"
    ),
    (
        "git add -- src/autoskillit/recipes/remediation.yaml "
        "& cat src/autoskillit/recipes/remediation.yaml"
    ),
    'git add -- "$(cat src/autoskillit/recipes/remediation.yaml)"',
    "git add -- $(cat src/autoskillit/recipes/remediation.yaml)",
    "git add -- src/autoskillit/recipes/remediation.yaml && cat $_",
    (
        "git add -- src/autoskillit/recipes/remediation.yaml"
        "&&cat src/autoskillit/recipes/remediation.yaml"
    ),
    "cat < src/autoskillit/recipes/remediation.yaml",
    "git show HEAD:src/autoskillit/recipes/remediation.yaml",
    "git log -p -- src/autoskillit/recipes/remediation.yaml",
    "git blame src/autoskillit/recipes/remediation.yaml",
    "git grep pattern -- src/autoskillit/recipes/remediation.yaml",
    "git add -A -- src/autoskillit/recipes/remediation.yaml",
    "git add --all -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore -v src/autoskillit/recipes/remediation.yaml > out.txt",
    "git check-ignore -v src/autoskillit/recipes/remediation.yaml 2>/dev/null",
    "git status -- src/autoskillit/recipes/remediation.yaml > out.txt",
    "git status -- src/autoskillit/recipes/remediation.yaml 2>&1",
    "git add -- src/autoskillit/recipes/remediation.yaml > out.txt",
    "git -c core.fsmonitor=./evil.sh status -- src/autoskillit/recipes/remediation.yaml",
    (
        "git -c core.excludesFile=/tmp/evil check-ignore -v "
        "src/autoskillit/recipes/remediation.yaml"
    ),
    ("git --config-env=core.fsmonitor=EVIL status -- src/autoskillit/recipes/remediation.yaml"),
    "git --git-dir=/tmp/repo/.git status -- src/autoskillit/recipes/remediation.yaml",
    "git --work-tree=/tmp/repo status -- src/autoskillit/recipes/remediation.yaml",
    "git --bare status -- src/autoskillit/recipes/remediation.yaml",
    "git --namespace=evil status -- src/autoskillit/recipes/remediation.yaml",
    "git --exec-path=/tmp/evil status -- src/autoskillit/recipes/remediation.yaml",
    "GIT_CONFIG_GLOBAL=/tmp/evil git status -- src/autoskillit/recipes/remediation.yaml",
    "env GIT_CONFIG_GLOBAL=/tmp/evil git status -- src/autoskillit/recipes/remediation.yaml",
    "env X=1 git status -- src/autoskillit/recipes/remediation.yaml",
    "sudo git status -- src/autoskillit/recipes/remediation.yaml",
    "timeout 5 git status -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore --stdin -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore src/autoskillit/recipes/remediation.yaml",
    "git check-ignore -z -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore -q -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore -n -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore --non-matching -- src/autoskillit/recipes/remediation.yaml",
    "git check-ignore --index -- src/autoskillit/recipes/remediation.yaml",
    "git status -- ${P:-src/autoskillit/recipes/remediation.yaml}",
    "P=src/autoskillit/recipes/remediation.yaml git status -- $P",
    (
        "git check-ignore -v src/autoskillit/recipes/remediation.yaml "
        "&& cat src/autoskillit/recipes/remediation.yaml"
    ),
    "git -C /repo check-ignore -v src/autoskillit/recipes/remediation.yaml",
)
