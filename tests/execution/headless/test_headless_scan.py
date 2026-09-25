"""Tests for _headless_scan parameterized tool-name dispatch."""

from __future__ import annotations

import pytest

from autoskillit.execution.headless import _scan_jsonl_write_paths
from tests.execution.conftest import _make_tool_use_line

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestScanJsonlCustomToolNames:
    CWD = "/clone/worktree"

    def test_accepts_custom_write_tool_names(self):
        line = _make_tool_use_line(
            "apply_patch", {"file_path": "/other/repo/file.py", "content": "x"}
        )
        warnings = _scan_jsonl_write_paths(
            line,
            self.CWD,
            write_tool_names=frozenset({"apply_patch"}),
        )
        assert len(warnings) == 1
        assert "apply_patch" in warnings[0]

    def test_default_write_tool_names_still_work(self):
        line = _make_tool_use_line("Write", {"file_path": "/other/repo/file.py", "content": "x"})
        warnings = _scan_jsonl_write_paths(line, self.CWD)
        assert len(warnings) == 1

    def test_custom_names_ignore_default_names(self):
        line = _make_tool_use_line("Write", {"file_path": "/other/repo/file.py", "content": "x"})
        warnings = _scan_jsonl_write_paths(
            line,
            self.CWD,
            write_tool_names=frozenset({"apply_patch"}),
        )
        assert warnings == []

    def test_accepts_custom_bash_tool_name(self):
        line = _make_tool_use_line("Shell", {"command": "echo hello > /other/repo/file.py"})
        warnings = _scan_jsonl_write_paths(
            line,
            self.CWD,
            bash_tool_name="Shell",
        )
        assert len(warnings) >= 1


class TestScanJsonlWriteWarnings:
    GENERIC_UNRESOLVED_WARNING = (
        "Bash command contained a write target that could not be statically resolved"
    )

    @pytest.mark.parametrize(
        ("command", "expected_warning"),
        [
            pytest.param(
                'echo x > "$F"',
                GENERIC_UNRESOLVED_WARNING,
                id="unresolved-variable-target",
            ),
            pytest.param(
                "echo 'unterminated",
                GENERIC_UNRESOLVED_WARNING,
                id="unparseable-command",
            ),
            pytest.param(
                "echo x > /outside/y",
                "Bash command contained write target '/outside/y' outside session cwd '{cwd}'",
                id="outside-cwd-target",
            ),
            pytest.param("printf '> q' > inside.txt", None, id="quoted-redirect-operator"),
        ],
    )
    def test_bash_jsonl_write_warnings(self, tmp_path, monkeypatch, command, expected_warning):
        monkeypatch.delenv("F", raising=False)
        cwd = str(tmp_path)
        line = _make_tool_use_line("Bash", {"command": command})

        warnings = _scan_jsonl_write_paths(line, cwd)

        if expected_warning is None:
            assert warnings == []
        else:
            assert warnings == [expected_warning.format(cwd=cwd)]
