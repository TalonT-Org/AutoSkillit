"""Hard semantic-conformance gate for the shell capture harness.

Runs a corpus of shell commands both raw (``bash -c <command>``) and wrapped
(``bash -c <harness(command)>``), and asserts the harness is byte-exact and
exit-code-exact with the raw execution — the harness must never change what a
command does or what output the agent ultimately sees, only how much of that
output lands inline vs. in an artifact file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoskillit.hooks.shell_capture_hook import _build_harness

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_INLINE_BYTES = 12_000
_CAPTURE_SUBDIR = ".autoskillit/temp/shell_capture"
_TIMEOUT = 30

_NESTED_WRAP_INNER = "echo hi"

_CORPUS = [
    (
        "pipe_wc",
        "find . -path ./.autoskillit -prune -o -type f -print 2>&1 | wc -l | head -c 4000",
    ),
    ("ls_wc", "ls . 2>&1 | wc -l"),
    (
        "heredoc_append",
        "cat >> .autoskillit/temp/investigate/report.md <<'MARKER'\nsome content\nMARKER",
    ),
    ("multi_stmt", "cd /tmp && echo done # comment"),
    ("exit_3", "exit 3"),
    ("false_cmd", "false"),
    ("mid_exit", "echo pre; exit 7; echo post"),
    ("errexit", "set -e; false; echo unreachable"),
    ("stderr_only", "echo err >&2"),
    ("true_cmd", "true"),
    ("self_bg", "{ sleep 0.2; echo late; } & echo started"),
    ("large_output", "seq 1 200000"),
    ("rg_sort", "rg pat . 2>&1 | sort | uniq -c | head -c 3000"),
    ("jq_keys", "jq -c 'keys' x.jsonl 2>&1 | head -1 | head -c 1000"),
    ("heredoc_no_newline", "cat <<'END'\nsome text\nEND"),
    ("trailing_backslash", "echo one \\"),
    ("self_signal", "echo pre; kill -TERM $$"),
    (
        "unicode_heavy",
        "python3 -c \"import sys; sys.stdout.buffer.write(b'\\xc3\\xa9' * 8000)\"",
    ),
    ("nested_wrap", None),  # filled in below once _build_harness is available
]


def _make_project_dirs(tmp_path: Path) -> None:
    (tmp_path / _CAPTURE_SUBDIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "temp" / "investigate").mkdir(parents=True, exist_ok=True)
    (tmp_path / "x.jsonl").write_text('{"a":1}\n{"b":2}\n')


def _capture_dir(tmp_path: Path) -> Path:
    return tmp_path / _CAPTURE_SUBDIR


def _artifact_files(tmp_path: Path) -> list[Path]:
    return sorted(_capture_dir(tmp_path).glob("shell_*.log"))


def _run_raw(command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
    )


def _run_wrapped(command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    wrapped = _build_harness(command, str(tmp_path), _INLINE_BYTES)
    return subprocess.run(
        ["bash", "-c", wrapped],
        capture_output=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
    )


# nested_wrap wraps a harness-of-"echo hi" and feeds it back through the
# harness a second time, proving the sentinel/idempotency path is also
# byte-safe under double-wrapping.
_CORPUS[-1] = ("nested_wrap", _build_harness(_NESTED_WRAP_INNER, "/tmp", _INLINE_BYTES))


@pytest.mark.parametrize("label,command", _CORPUS, ids=[row[0] for row in _CORPUS])
def test_capture_conformance(label: str, command: str, tmp_path: Path) -> None:
    if label == "rg_sort" and shutil.which("rg") is None:
        pytest.skip("rg not available")
    if label == "jq_keys" and shutil.which("jq") is None:
        pytest.skip("jq not available")

    _make_project_dirs(tmp_path)

    raw = _run_raw(command, tmp_path)
    raw_combined = raw.stdout + raw.stderr

    if label == "heredoc_append":
        # raw run already appended once; reset the target file so the
        # wrapped run's append produces byte-identical content to compare.
        report = tmp_path / ".autoskillit" / "temp" / "investigate" / "report.md"
        report.unlink(missing_ok=True)

    wrapped = _run_wrapped(command, tmp_path)

    assert wrapped.returncode == raw.returncode, (
        f"[{label}] exit code mismatch: raw={raw.returncode} wrapped={wrapped.returncode}\n"
        f"raw stderr={raw.stderr!r}\nwrapped stderr={wrapped.stderr!r}"
    )

    artifacts = _artifact_files(tmp_path)

    if label == "true_cmd":
        assert raw_combined == b""
        assert wrapped.stdout == b""
        assert raw.returncode == 0
        assert not artifacts
        return

    if label == "self_bg":
        assert b"started" in wrapped.stdout
        assert b"late" in wrapped.stdout
        assert wrapped.returncode == 0
        return

    if label == "heredoc_append":
        report = tmp_path / ".autoskillit" / "temp" / "investigate" / "report.md"
        assert report.exists()
        assert "some content" in report.read_text()
        return

    if label == "nested_wrap":
        assert wrapped.returncode == 0
        assert b"hi" in wrapped.stdout
        return

    if label == "mid_exit":
        assert raw.returncode == 7
        assert wrapped.returncode == 7

    if label == "errexit":
        assert raw.returncode == 1
        assert wrapped.returncode == 1

    if label == "self_signal":
        # subprocess.run reports signal-terminated processes as -signum
        # (SIGTERM -> -15) rather than the 128+signum shells report via $?.
        assert raw.returncode == -15
        assert wrapped.returncode == -15
        if artifacts:
            assert b"pre" in artifacts[0].read_bytes()
        return

    if label == "trailing_backslash":
        return

    if label == "unicode_heavy":
        assert artifacts, f"[{label}] expected an artifact for large unicode output"
        assert len(artifacts) == 1, f"[{label}] expected exactly one artifact, found {artifacts}"
        artifact_bytes = artifacts[0].read_bytes()
        assert artifact_bytes == raw_combined, (
            f"[{label}] artifact content mismatch with raw combined output"
        )
        artifact_bytes.decode("utf-8")
        return

    if len(raw_combined) <= _INLINE_BYTES:
        assert wrapped.stdout == raw_combined, (
            f"[{label}] inline output mismatch.\nraw={raw_combined!r}\nwrapped={wrapped.stdout!r}"
        )
        assert not artifacts, f"[{label}] expected no artifact for small output, found {artifacts}"
    else:
        assert artifacts, f"[{label}] expected an artifact for large output, found none"
        assert len(artifacts) == 1, f"[{label}] expected exactly one artifact, found {artifacts}"
        artifact_bytes = artifacts[0].read_bytes()
        assert artifact_bytes == raw_combined, (
            f"[{label}] artifact content mismatch with raw combined output"
        )


def test_capture_dir_uncreatable_fail_stops(tmp_path: Path) -> None:
    blocking_dir = tmp_path / ".autoskillit" / "temp"
    blocking_dir.mkdir(parents=True)
    (blocking_dir / "shell_capture").write_text("not a directory")

    command = "echo should_not_run"
    wrapped = _run_wrapped(command, tmp_path)

    assert wrapped.returncode == 1
    combined = (wrapped.stdout + wrapped.stderr).decode(errors="replace")
    assert "capture_failed" in combined.lower() or "CAPTURE_FAILED" in combined
    assert "should_not_run" not in combined
