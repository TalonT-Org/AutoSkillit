"""Tests for the unsafe_install_guard PreToolUse hook."""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _run_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Run the guard's main() in-process and return captured stdout."""
    from autoskillit.hooks.guards.unsafe_install_guard import main

    tool_input = {"cmd": cmd, "cwd": "/some/path"}
    stdin_content = (
        raw_stdin
        if raw_stdin is not None
        else json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": tool_input,
            }
        )
    )
    buf = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_content)):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


def _run_bash_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Run the guard's main() in-process with Bash tool format, return captured stdout."""
    from autoskillit.hooks.guards.unsafe_install_guard import main

    tool_input = {"command": cmd, "cwd": "/some/path"}
    stdin_content = (
        raw_stdin
        if raw_stdin is not None
        else json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": tool_input,
            }
        )
    )
    buf = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_content)):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestBashToolDenyPath:
    """Bash tool sends command via 'command' key — guard must handle this format."""

    def test_pip_install_editable_via_bash_tool(self):
        assert _is_denied(_run_bash_guard("pip install -e ."))

    def test_uv_pip_install_editable_via_bash_tool(self):
        assert _is_denied(_run_bash_guard("uv pip install -e ."))

    def test_pip_install_editable_long_flag_via_bash_tool(self):
        assert _is_denied(_run_bash_guard("pip install --editable ."))

    def test_uv_pip_install_editable_long_flag_via_bash_tool(self):
        assert _is_denied(_run_bash_guard("uv pip install --editable ."))


class TestMaturinDevelopDenied:
    """maturin develop performs editable installs and must be blocked."""

    def test_maturin_develop_denied(self):
        assert _is_denied(_run_guard("maturin develop"))

    def test_maturin_develop_with_args_denied(self):
        assert _is_denied(_run_guard("maturin develop --release"))

    def test_maturin_develop_via_bash_tool(self):
        assert _is_denied(_run_bash_guard("maturin develop"))


class TestSystemFlagDenied:
    """--system flag installs into global environment — must be blocked for pip/uv."""

    def test_uv_pip_install_system_flag_denied(self):
        assert _is_denied(_run_guard("uv pip install foo --system"))

    def test_pip_install_system_flag_denied(self):
        assert _is_denied(_run_guard("pip install foo --system"))

    def test_uv_pip_install_editable_with_system_denied(self):
        assert _is_denied(_run_guard("uv pip install -e . --system"))

    def test_system_flag_via_bash_tool(self):
        assert _is_denied(_run_bash_guard("uv pip install foo --system"))


class TestUnsafeInstallGuardDenied:
    """Commands that should be blocked."""

    def test_pip_install_editable_without_python_venv(self):
        assert _is_denied(_run_guard("pip install -e ."))

    def test_pip_install_editable_long_flag(self):
        assert _is_denied(_run_guard("pip install --editable ."))

    def test_uv_pip_install_editable_without_python_venv(self):
        assert _is_denied(_run_guard("uv pip install -e ."))

    def test_uv_pip_install_editable_without_python_venv_subdir(self):
        assert _is_denied(_run_guard("uv pip install -e '.[dev]'"))

    def test_uv_pip_install_editable_with_wrong_python_target(self):
        """--python pointing at system Python (not .venv) is still blocked."""
        assert _is_denied(_run_guard("uv pip install -e '.[dev]' --python /usr/bin/python3"))

    def test_pip_install_editable_with_python_system(self):
        """Explicit system Python target is blocked."""
        assert _is_denied(
            _run_guard("pip install -e . --python /usr/local/micromamba/bin/python3.13")
        )


class TestUnsafeInstallGuardAllowed:
    """Commands that should be allowed through."""

    def test_uv_pip_install_editable_with_venv_python(self):
        """Editable install targeting .venv is safe — allowed."""
        assert not _is_denied(_run_guard("uv pip install -e '.[dev]' --python .venv/bin/python"))

    def test_uv_pip_install_editable_with_venv_python_absolute(self):
        """Editable install targeting .venv (absolute path) is safe — allowed."""
        assert not _is_denied(
            _run_guard("uv pip install -e '.[dev]' --python /some/worktree/.venv/bin/python")
        )

    def test_pip_install_non_editable_allowed(self):
        """Non-editable pip install does not create dangling entry points — allowed."""
        assert not _is_denied(_run_guard("pip install requests"))

    def test_task_install_worktree_allowed(self):
        """task install-worktree always uses --python .venv — allowed."""
        assert not _is_denied(_run_guard("task install-worktree"))

    def test_uv_sync_allowed(self):
        assert not _is_denied(_run_guard("uv sync --all-extras"))

    def test_unrelated_command_allowed(self):
        assert not _is_denied(_run_guard("pytest tests/"))


class TestUnsafeInstallGuardEdgeCases:
    def test_malformed_json_fail_open(self):
        """Malformed stdin → fail-open (no output, no denial)."""
        output = _run_guard("irrelevant", raw_stdin="not-json{{{")
        assert output == ""

    def test_missing_cmd_field_fail_open(self):
        """Missing cmd in tool_input → fail-open."""
        stdin = json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": {},
            }
        )
        output = _run_guard("irrelevant", raw_stdin=stdin)
        assert output == ""


# --- Quoted/read-only false-positive cases ---
# Each entry exercises a quoted reader/search that mentions an install pattern
# without actually invoking one. Both _run_guard and _run_bash_guard must allow.


_QUOTED_ALLOWED: list[tuple[str, str]] = [
    ("rg 'pip install -e' docs/", "rg-quoted-single"),
    ('rg "pip install -e" docs/', "rg-quoted-double"),
    ('grep -r "pip install --editable" .', "grep-double-quoted"),
    ('grep -r "pip install --editable" .', "grep-single-quoted-search"),
    ("git log --grep='pip install -e'", "git-log-single-quoted"),
    ('git log --grep="pip install -e"', "git-log-double-quoted"),
    ('echo "Run: pip install -e ."', "echo-double-quoted"),
    ("echo 'Run: pip install -e .'", "echo-single-quoted"),
    ("gh issue list --search '\"pip install -e\" guard'", "gh-issue-search"),
    ('cat "pip install -e"', "cat-double-quoted"),
    ("cat 'pip install -e'", "cat-single-quoted"),
    (
        'git commit -m "docs: pip install -e example"',
        "git-commit-message",
    ),
    (
        "rg '$(pip install -e .)' docs/",
        "rg-single-quoted-substitution-inert",
    ),
    (
        'echo "\\$(pip install -e .)"',
        "echo-escaped-substitution-inert",
    ),
]


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _QUOTED_ALLOWED],
    ids=[c[1] for c in _QUOTED_ALLOWED],
)
def test_quoted_read_only_run_guard_allowed(cmd: str) -> None:
    assert not _is_denied(_run_guard(cmd)), f"run_cmd should allow: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _QUOTED_ALLOWED],
    ids=[c[1] for c in _QUOTED_ALLOWED],
)
def test_quoted_read_only_bash_guard_allowed(cmd: str) -> None:
    assert not _is_denied(_run_bash_guard(cmd)), f"Bash tool should allow: {cmd!r}"


# --- System-install false-positive cases ---
# Each has --system as a real token in argument position but no actual pip install.

_SYSTEM_FALSE_POSITIVES: list[tuple[str, str]] = [
    ('rg "pip install" -- --system', "rg-double-dash-system"),
    ('echo "avoid pip install" --system', "echo-system-as-arg"),
    ('grep "pip install" -- --system', "grep-double-dash-system"),
    ("uv run -- echo install --system", "uv-run-echo-system"),
    ("uv tool install demo --system", "uv-tool-install-system"),
]


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _SYSTEM_FALSE_POSITIVES],
    ids=[c[1] for c in _SYSTEM_FALSE_POSITIVES],
)
def test_system_install_false_positive_run_guard_allowed(cmd: str) -> None:
    assert not _is_denied(_run_guard(cmd)), f"run_cmd should allow: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _SYSTEM_FALSE_POSITIVES],
    ids=[c[1] for c in _SYSTEM_FALSE_POSITIVES],
)
def test_system_install_false_positive_bash_guard_allowed(cmd: str) -> None:
    assert not _is_denied(_run_bash_guard(cmd)), f"Bash tool should allow: {cmd!r}"


# --- Direct and wrapped deny regressions ---


_DIRECT_DENIED: list[tuple[str, str]] = [
    ("pip install -e .", "pip-install-e-dot"),
    ("pip install --editable .", "pip-install-editable-dot"),
    ("uv pip install -e .", "uv-pip-install-e-dot"),
    ("maturin develop", "maturin-develop"),
    ("/usr/bin/pip install -e .", "absolute-pip-install-e"),
    ("/usr/local/bin/uv pip install --editable .", "absolute-uv-pip-install-editable"),
    ("python -m pip install -e .", "python-m-pip-install-e"),
    ("python3 -m pip install --editable .", "python3-m-pip-install-editable"),
    ("pip3 install -e .", "pip3-install-e-dot"),
    ("FOO=bar pip install -e .", "foo-assignment-pip-install-e"),
    ("env PIP_CACHE_DIR=/tmp pip install -e .", "env-pip-cache-dir"),
    ("sudo -u root pip install -e .", "sudo-pip-install-e"),
    ("nice -n 5 pip install -e .", "nice-pip-install-e"),
    ("timeout 30 pip install -e .", "timeout-pip-install-e"),
    ("stdbuf -o0 pip install -e .", "stdbuf-pip-install-e"),
    # Attached --python=<path> form: must remain unsafe when path is not .venv.
    ("pip install -e . --python=/usr/bin/python3", "attached-system-python"),
]


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _DIRECT_DENIED],
    ids=[c[1] for c in _DIRECT_DENIED],
)
def test_direct_denied_run_guard(cmd: str) -> None:
    assert _is_denied(_run_guard(cmd)), f"run_cmd should deny: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _DIRECT_DENIED],
    ids=[c[1] for c in _DIRECT_DENIED],
)
def test_direct_denied_bash_guard(cmd: str) -> None:
    assert _is_denied(_run_bash_guard(cmd)), f"Bash tool should deny: {cmd!r}"


# --- Compound-command deny regressions ---


_COMPOUND_DENIED: list[tuple[str, str]] = [
    ("pip install -e . && echo done", "pip-then-echo-spaced"),
    ("echo ok&&pip install -e .", "echo-then-pip-adjacent"),
    ("echo ok;pip install -e .", "echo-then-pip-semicolon-adjacent"),
    ("echo ok || pip install -e .", "echo-or-pip"),
    ("echo ok | pip install -e .", "echo-pipe-pip"),
    ("echo ok\npip install -e .", "echo-newline-pip"),
    ("pip install -e . > /dev/null", "pip-with-redirect"),
    (
        "uv pip install -e . --python .venv/bin/python && pip install -e .",
        "safe-then-unsafe-compound",
    ),
    (
        "rg docs/ && pip install -e .",
        "reader-then-install",
    ),
    (
        "pip install -e . && rg 'pip install' docs/",
        "install-then-reader",
    ),
]


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _COMPOUND_DENIED],
    ids=[c[1] for c in _COMPOUND_DENIED],
)
def test_compound_denied_run_guard(cmd: str) -> None:
    assert _is_denied(_run_guard(cmd)), f"run_cmd should deny compound: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _COMPOUND_DENIED],
    ids=[c[1] for c in _COMPOUND_DENIED],
)
def test_compound_denied_bash_guard(cmd: str) -> None:
    assert _is_denied(_run_bash_guard(cmd)), f"Bash tool should deny compound: {cmd!r}"


# --- Ordered-grammar allow cases ---
# A token mentioning 'install' must not be confused with a subcommand argument.


_GRAMMAR_ALLOWED: list[tuple[str, str]] = [
    ("pip help install -e", "pip-help-install-e"),
    ("uv run echo install --system", "uv-run-echo-install-system"),
    ("uv tool install demo --system", "uv-tool-install-demo"),
    ("maturin --help develop", "maturin-help-develop"),
]


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _GRAMMAR_ALLOWED],
    ids=[c[1] for c in _GRAMMAR_ALLOWED],
)
def test_grammar_allowed_run_guard(cmd: str) -> None:
    assert not _is_denied(_run_guard(cmd)), f"run_cmd should allow: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _GRAMMAR_ALLOWED],
    ids=[c[1] for c in _GRAMMAR_ALLOWED],
)
def test_grammar_allowed_bash_guard(cmd: str) -> None:
    assert not _is_denied(_run_bash_guard(cmd)), f"Bash tool should allow: {cmd!r}"


# --- Nested shell payload cases ---


class TestNestedShellDeny:
    @pytest.mark.parametrize(
        "cmd",
        [
            'bash -c "pip install -e ."',
            'sh -c "pip install -e ."',
            'zsh -c "uv pip install --editable ."',
            'dash -c "pip install --editable ."',
            'bash -c "maturin develop"',
            'eval "pip install -e ."',
        ],
        ids=[
            "bash-c-pip",
            "sh-c-pip",
            "zsh-c-uv-pip",
            "dash-c-pip-editable",
            "bash-c-maturin",
            "eval-pip",
        ],
    )
    def test_run_guard_denies_nested_shell(self, cmd: str) -> None:
        assert _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            'bash -c "pip install -e ."',
            'sh -c "pip install -e ."',
            'zsh -c "uv pip install --editable ."',
            'dash -c "pip install --editable ."',
            'bash -c "maturin develop"',
            'eval "pip install -e ."',
        ],
        ids=[
            "bash-c-pip",
            "sh-c-pip",
            "zsh-c-uv-pip",
            "dash-c-pip-editable",
            "bash-c-maturin",
            "eval-pip",
        ],
    )
    def test_bash_guard_denies_nested_shell(self, cmd: str) -> None:
        assert _is_denied(_run_bash_guard(cmd))


class TestNestedShellAllow:
    @pytest.mark.parametrize(
        "cmd",
        [
            "bash -c \"rg 'pip install -e' docs/\"",
            'sh -c "rg docs/"',
            'eval "rg docs/"',
        ],
        ids=[
            "bash-rg-quoted",
            "sh-rg",
            "eval-rg",
        ],
    )
    def test_run_guard_allows_inert_nested_shell(self, cmd: str) -> None:
        assert not _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            "bash -c \"rg 'pip install -e' docs/\"",
            'sh -c "rg docs/"',
            'eval "rg docs/"',
        ],
        ids=[
            "bash-rg-quoted",
            "sh-rg",
            "eval-rg",
        ],
    )
    def test_bash_guard_allows_inert_nested_shell(self, cmd: str) -> None:
        assert not _is_denied(_run_bash_guard(cmd))


# --- Command substitution cases ---


class TestCommandSubstitutionDeny:
    @pytest.mark.parametrize(
        "cmd",
        [
            "echo $(pip install -e .)",
            'echo "$(pip install -e .)"',
            "echo `pip install -e .`",
            'echo "`pip install -e .`"',
        ],
        ids=[
            "dollar-paren",
            "dollar-paren-in-double",
            "backtick",
            "backtick-in-double",
        ],
    )
    def test_run_guard_denies_active_substitution(self, cmd: str) -> None:
        assert _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            "echo $(pip install -e .)",
            'echo "$(pip install -e .)"',
            "echo `pip install -e .`",
            'echo "`pip install -e .`"',
        ],
        ids=[
            "dollar-paren",
            "dollar-paren-in-double",
            "backtick",
            "backtick-in-double",
        ],
    )
    def test_bash_guard_denies_active_substitution(self, cmd: str) -> None:
        assert _is_denied(_run_bash_guard(cmd))


class TestCommandSubstitutionAllow:
    @pytest.mark.parametrize(
        "cmd",
        [
            "echo '$(pip install -e .)'",
            'echo "\\$(pip install -e .)"',
        ],
        ids=[
            "single-quoted-inert",
            "escaped-inert",
        ],
    )
    def test_run_guard_allows_inert_substitution(self, cmd: str) -> None:
        assert not _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            "echo '$(pip install -e .)'",
            'echo "\\$(pip install -e .)"',
        ],
        ids=[
            "single-quoted-inert",
            "escaped-inert",
        ],
    )
    def test_bash_guard_allows_inert_substitution(self, cmd: str) -> None:
        assert not _is_denied(_run_bash_guard(cmd))


# --- Interpreter subprocess cases ---


class TestInterpreterSubprocessDeny:
    @pytest.mark.parametrize(
        "cmd",
        [
            "python -c \"import subprocess; subprocess.run('pip install -e .', shell=True)\"",
            "python3 -c \"import subprocess; subprocess.run(['pip','install','-e','.'])\"",
            "python3 -c \"import subprocess; subprocess.run(('pip','install','--editable','.'))\"",
            "python3 -c \"import os; os.system('pip install -e .')\"",
        ],
        ids=[
            "shell-string-pip",
            "argv-list-pip",
            "argv-tuple-pip",
            "os-system-pip",
        ],
    )
    def test_run_guard_denies_interpreter_subprocess(self, cmd: str) -> None:
        assert _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            "python -c \"import subprocess; subprocess.run('pip install -e .', shell=True)\"",
            "python3 -c \"import subprocess; subprocess.run(['pip','install','-e','.'])\"",
            "python3 -c \"import subprocess; subprocess.run(('pip','install','--editable','.'))\"",
            "python3 -c \"import os; os.system('pip install -e .')\"",
        ],
        ids=[
            "shell-string-pip",
            "argv-list-pip",
            "argv-tuple-pip",
            "os-system-pip",
        ],
    )
    def test_bash_guard_denies_interpreter_subprocess(self, cmd: str) -> None:
        assert _is_denied(_run_bash_guard(cmd))


class TestInterpreterSubprocessAllow:
    @pytest.mark.parametrize(
        "cmd",
        [
            "python3 -c \"import subprocess; subprocess.run(['rg', 'pip install -e', 'docs/'])\"",
        ],
        ids=["argv-reader-rg"],
    )
    def test_run_guard_allows_interpreter_reader(self, cmd: str) -> None:
        assert not _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            "python3 -c \"import subprocess; subprocess.run(['rg', 'pip install -e', 'docs/'])\"",
        ],
        ids=["argv-reader-rg"],
    )
    def test_bash_guard_allows_interpreter_reader(self, cmd: str) -> None:
        assert not _is_denied(_run_bash_guard(cmd))


# --- .venv exemption binding ---


class TestVenvExemptionBinding:
    def test_attached_python_venv_allowed_run_guard(self) -> None:
        """Attached --python=.venv/bin/python is an explicit .venv exemption."""
        assert not _is_denied(_run_guard("pip install -e . --python=.venv/bin/python"))

    def test_attached_python_venv_allowed_bash_guard(self) -> None:
        assert not _is_denied(_run_bash_guard("pip install -e . --python=.venv/bin/python"))

    def test_venv_poison_substring_not_exempt_run_guard(self) -> None:
        """.venv-poison is NOT a .venv path component."""
        assert _is_denied(_run_guard("pip install -e . --python .venv-poison/bin/python"))

    def test_venv_poison_substring_not_exempt_bash_guard(self) -> None:
        assert _is_denied(_run_bash_guard("pip install -e . --python .venv-poison/bin/python"))

    def test_later_unsafe_compound_after_safe_run_guard(self) -> None:
        """Safe install followed by unsafe install must deny."""
        assert _is_denied(
            _run_guard("pip install -e . --python .venv/bin/python && pip install -e .")
        )

    def test_later_unsafe_compound_after_safe_bash_guard(self) -> None:
        assert _is_denied(
            _run_bash_guard("pip install -e . --python .venv/bin/python && pip install -e .")
        )


# --- System install scope ---


class TestSystemInstallScope:
    @pytest.mark.parametrize(
        "cmd",
        [
            "pip install --system requests",
            "pip install foo --system",
            "uv pip install --system foo",
            "uv pip install foo --system",
        ],
        ids=[
            "pip-system-leading",
            "pip-system-trailing",
            "uv-pip-system-leading",
            "uv-pip-system-trailing",
        ],
    )
    def test_run_guard_denies_scoped_system(self, cmd: str) -> None:
        assert _is_denied(_run_guard(cmd))

    @pytest.mark.parametrize(
        "cmd",
        [
            "pip install --system requests",
            "pip install foo --system",
            "uv pip install --system foo",
            "uv pip install foo --system",
        ],
        ids=[
            "pip-system-leading",
            "pip-system-trailing",
            "uv-pip-system-leading",
            "uv-pip-system-trailing",
        ],
    )
    def test_bash_guard_denies_scoped_system(self, cmd: str) -> None:
        assert _is_denied(_run_bash_guard(cmd))
