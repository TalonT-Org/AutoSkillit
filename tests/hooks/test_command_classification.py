"""Tests for the shared command classification primitive (hooks/_command_classification.py)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._command_classification import (
    has_interpreter_wrapped_command,
    has_interpreter_write,
    has_nested_shell,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def test_detects_python3_subprocess_run():
    cmd = "python3 -c \"import subprocess; subprocess.run('gh pr create', shell=True)\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh pr create"])


def test_detects_bash_c_nesting():
    assert has_nested_shell('bash -c "gh pr create --fill"')


def test_no_false_positive_simple_command():
    assert not has_interpreter_wrapped_command(
        "gh pr create --fill",
        target_commands=["gh pr create"],
    )


def test_detects_os_system_wrapping():
    cmd = "python3 -c \"import os; os.system('gh issue list')\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh issue list"])


def test_detects_python_write_text():
    cmd = "python3 -c \"Path('/tmp/x').write_text('data')\""
    assert has_interpreter_write(cmd)


def test_detects_python_open_write_mode():
    cmd = "python3 -c \"open('/tmp/x', 'w').write('data')\""
    assert has_interpreter_write(cmd)


def test_detects_python_heredoc_write():
    cmd = "python3 <<'EOF'\nopen('/tmp/x', 'w').write('hi')\nEOF"
    assert has_interpreter_write(cmd)


def test_no_false_positive_read_only_python():
    cmd = "python3 -c \"print(open('/tmp/x').read())\""
    assert not has_interpreter_write(cmd)


def test_no_false_positive_simple_gh_command():
    assert not has_nested_shell("gh pr create --fill")


def test_detects_sh_c_nesting():
    assert has_nested_shell('sh -c "gh issue list"')


def test_no_match_when_no_interpreter():
    assert not has_interpreter_wrapped_command(
        "git push origin main",
        target_commands=["git push"],
    )


def test_detects_python_os_popen():
    cmd = "python3 -c \"import os; os.popen('gh pr create')\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh pr create"])


def test_interpreter_wrapped_command_case_insensitive():
    cmd = "python3 -c \"import os; os.system('GH PR CREATE')\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh pr create"])
