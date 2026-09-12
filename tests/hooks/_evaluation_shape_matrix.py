"""Shared evaluation-shape matrix for command-classification tests.

Centralizes the delivery shapes a heredoc/herestring/pipe/`-c`/substitution
payload can take, so the tokenizer-binding tests, the payload-authority
tests, the blocklist coverage matrix, and each guard's sampling tests all
draw from one definition instead of hand-rolling their own subset (rectify
#4941 Part A -- see "How Tests Missed This" § 6). Sibling of
``_flag_form_matrix.py``; no tests live in this module.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass


def PY(inner: str) -> str:  # noqa: N802 - matches the plan's PY(inner) notation
    """Render a Python program that runs *inner* via a literal-argv subprocess call."""
    argv = shlex.split(inner)
    return f"import subprocess; subprocess.run({argv!r})"


@dataclass(frozen=True, slots=True)
class EvaluationShape:
    """One way a command's inner text can be delivered to (or hidden from) execution.

    `executes` is True when the wrapped inner text runs as shell/Python
    somewhere. `consumer` is `"outer"` (the outer shell itself evaluates it,
    independent of who reads the resulting text), `"shell"`, `"python"`, or
    `"inert"`. `build` renders the full Bash command given the inner text.
    """

    id: str
    executes: bool
    consumer: str
    build: Callable[[str], str]


def wrap_git_op(shape: EvaluationShape, op: tuple[str, ...]) -> str:
    """Render *op* (a RISKY_GIT_OPERATIONS tuple) as `git <op> origin main` through *shape*."""
    inner = " ".join(("git", *op, "origin", "main"))
    return shape.build(inner)


EVALUATION_SHAPE_MATRIX: tuple[EvaluationShape, ...] = (
    EvaluationShape("direct", True, "outer", lambda inner: inner),
    EvaluationShape("env-assignment-prefix", True, "outer", lambda inner: f"VAR=1 {inner}"),
    EvaluationShape("sudo-direct", True, "outer", lambda inner: f"sudo {inner}"),
    EvaluationShape(
        "function-body-bash-heredoc",
        True,
        "shell",
        lambda inner: f"f() {{ bash <<'EOF'\n{inner}\nEOF\n}}; f",
    ),
    EvaluationShape(
        "loop-body-bash-heredoc",
        True,
        "shell",
        lambda inner: f"for i in 1; do bash <<'EOF'\n{inner}\nEOF\ndone",
    ),
    EvaluationShape("bash-c", True, "shell", lambda inner: f"bash -c '{inner}'"),
    EvaluationShape("sh-c", True, "shell", lambda inner: f"sh -c '{inner}'"),
    EvaluationShape("sudo-bash-c", True, "shell", lambda inner: f"sudo bash -c '{inner}'"),
    EvaluationShape("pipe-bash-c", True, "shell", lambda inner: f"true | bash -c '{inner}'"),
    EvaluationShape("eval", True, "shell", lambda inner: f"eval '{inner}'"),
    EvaluationShape("dollar-substitution", True, "outer", lambda inner: f"echo $({inner})"),
    EvaluationShape("backtick", True, "outer", lambda inner: f"echo `{inner}`"),
    EvaluationShape(
        "bash-heredoc-quoted", True, "shell", lambda inner: f"bash <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "bash-heredoc-unquoted", True, "shell", lambda inner: f"bash <<EOF\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "bash-heredoc-tab", True, "shell", lambda inner: f"bash <<-'EOF'\n\t{inner}\n\tEOF\n"
    ),
    EvaluationShape("sh-heredoc", True, "shell", lambda inner: f"sh <<'EOF'\n{inner}\nEOF\n"),
    EvaluationShape("zsh-heredoc", True, "shell", lambda inner: f"zsh <<'EOF'\n{inner}\nEOF\n"),
    EvaluationShape("dash-heredoc", True, "shell", lambda inner: f"dash <<'EOF'\n{inner}\nEOF\n"),
    EvaluationShape(
        "bash-s-heredoc", True, "shell", lambda inner: f"bash -s <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "bash-dash-heredoc", True, "shell", lambda inner: f"bash - <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "bash-dev-stdin-heredoc",
        True,
        "shell",
        lambda inner: f"bash /dev/stdin <<'EOF'\n{inner}\nEOF\n",
    ),
    EvaluationShape(
        "bash-flags-heredoc",
        True,
        "shell",
        lambda inner: f"bash -e -o pipefail <<'EOF'\n{inner}\nEOF\n",
    ),
    EvaluationShape(
        "env-bash-heredoc", True, "shell", lambda inner: f"env bash <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "abs-bash-heredoc", True, "shell", lambda inner: f"/bin/bash <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "sudo-bash-heredoc", True, "shell", lambda inner: f"sudo bash <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "timeout-bash-heredoc",
        True,
        "shell",
        lambda inner: f"timeout 30 bash <<'EOF'\n{inner}\nEOF\n",
    ),
    EvaluationShape("bash-herestring-double", True, "shell", lambda inner: f'bash <<< "{inner}"'),
    EvaluationShape("bash-herestring-single", True, "shell", lambda inner: f"bash <<< '{inner}'"),
    EvaluationShape(
        "cat-heredoc-pipe-bash",
        True,
        "shell",
        lambda inner: f"cat <<'EOF' | bash\n{inner}\nEOF\n",
    ),
    EvaluationShape("echo-pipe-sh", True, "shell", lambda inner: f"echo '{inner}' | sh"),
    EvaluationShape(
        "printf-pipe-bash", True, "shell", lambda inner: f"printf '%s\\n' '{inner}' | bash"
    ),
    EvaluationShape(
        "python-c-subprocess", True, "python", lambda inner: f'python3 -c "{PY(inner)}"'
    ),
    EvaluationShape(
        "python-dash-heredoc-subprocess",
        True,
        "python",
        lambda inner: f"python3 - <<'EOF'\n{PY(inner)}\nEOF\n",
    ),
    EvaluationShape(
        "python-heredoc-subprocess",
        True,
        "python",
        lambda inner: f"python3 <<'EOF'\n{PY(inner)}\nEOF\n",
    ),
    EvaluationShape(
        "cat-heredoc-pipe-python",
        True,
        "python",
        lambda inner: f"cat <<'EOF' | python3 -\n{PY(inner)}\nEOF\n",
    ),
    EvaluationShape(
        "dollar-in-bash-heredoc",
        True,
        "shell",
        lambda inner: f"bash <<'EOF'\necho $({inner})\nEOF\n",
    ),
    EvaluationShape(
        "dollar-in-unquoted-cat-heredoc",
        True,
        "outer",
        lambda inner: f"cat > f.md <<EOF\nvalue is $({inner})\nEOF\n",
    ),
    EvaluationShape(
        "cat-redirect-heredoc-fenced",
        False,
        "inert",
        lambda inner: f"cat > notes.md <<'EOF'\n```\n{inner}\n```\nEOF\n",
    ),
    EvaluationShape(
        "cat-redirect-heredoc-inline-backtick",
        False,
        "inert",
        lambda inner: f"cat > f.md <<'EOF'\nrun `{inner}` here\nEOF\n",
    ),
    EvaluationShape(
        "cat-redirect-heredoc-dollar",
        False,
        "inert",
        lambda inner: f"cat > f.md <<'EOF'\nvalue is $({inner})\nEOF\n",
    ),
    EvaluationShape(
        "cat-heredoc-dquote-delim",
        False,
        "inert",
        lambda inner: f'cat > f.md <<"EOF"\n$({inner})\nEOF\n',
    ),
    EvaluationShape(
        "tee-heredoc-dollar",
        False,
        "inert",
        lambda inner: f"tee f.md <<'EOF'\n$({inner})\nEOF\n",
    ),
    EvaluationShape(
        "cat-heredoc-stdout", False, "inert", lambda inner: f"cat <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "cat-heredoc-tab-inert",
        False,
        "inert",
        lambda inner: f"cat <<-'EOF'\n\t{inner}\n\tEOF\n",
    ),
    EvaluationShape(
        "python-script-heredoc-data",
        False,
        "inert",
        lambda inner: f"python3 script.py <<'EOF'\n{PY(inner)}\nEOF\n",
    ),
    EvaluationShape(
        "python-c-heredoc-data",
        False,
        "inert",
        lambda inner: f"python3 -c 'import sys; print(sys.stdin.read())' <<'EOF'\n{inner}\nEOF\n",
    ),
    EvaluationShape(
        "bash-c-heredoc-data",
        False,
        "inert",
        lambda inner: f"bash -c 'cat' <<'EOF'\n{inner}\nEOF\n",
    ),
    EvaluationShape(
        "git-apply-heredoc", False, "inert", lambda inner: f"git apply <<'EOF'\n{inner}\nEOF\n"
    ),
    EvaluationShape(
        "gh-body-file-stdin-heredoc",
        False,
        "inert",
        lambda inner: f"gh pr create --body-file - <<'EOF'\n{inner}\nEOF\n",
    ),
    EvaluationShape(
        "cat-heredoc-pipe-tee",
        False,
        "inert",
        lambda inner: f"cat <<'EOF' | tee f\n{inner}\nEOF\n",
    ),
)


# Deferred shapes: regex-level limitations of _HEREDOC_BODY_RE that are not
# fixed by this rectify because each would change strip_heredoc_bodies's
# matching and therefore the byte-for-byte parity lock against
# core/git/bash_write_targets.py. Each is an inert `cat` consumer whose
# intended-behavior builder is documented here for the strict-XFAIL
# regression pinned in tests/arch/test_hook_raw_command_scan_inventory.py.
DEFERRED_SHAPES: dict[str, Callable[[str], str]] = {
    "two-heredocs-one-line": lambda inner: f"cat <<'A' <<'B'\nignored\nA\n{inner}\nB\n",
    "backslash-quoted-delimiter": lambda inner: f"cat <<\\EOF\n{inner}\nEOF\n",
    "partially-quoted-delimiter": lambda inner: f'cat <<E"OF"\n{inner}\nEOF\n',
    "unterminated-heredoc": lambda inner: f"cat <<'EOF'\n{inner}\n",
    "non-word-delimiter": lambda inner: f"cat <<'END-DOC'\n{inner}\nEND-DOC\n",
}
