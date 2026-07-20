"""Hard semantic-conformance gate for the shell capture harness.

Runs a corpus of shell commands both raw (``bash -c <command>``) and wrapped
(``bash -c <harness(command)>``), and asserts the harness is byte-exact and
exit-code-exact with the raw execution — the harness must never change what a
command does or what output the agent ultimately sees, only how much of that
output lands inline vs. in an artifact file.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from autoskillit.hooks._capture_cleanup import (
    _CAPTURE_FILENAME_RE,
    _is_safe_capture_file,
    sweep_stale_captures,
)
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


def _run_raw_merged(command: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
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
        assert artifacts, (
            f"[{label}] expected artifact retained for small output (Python-side cleanup)"
        )
        assert len(artifacts) == 1
    else:
        assert artifacts, f"[{label}] expected an artifact for large output, found none"
        assert len(artifacts) == 1, f"[{label}] expected exactly one artifact, found {artifacts}"
        artifact_bytes = artifacts[0].read_bytes()
        assert artifact_bytes == raw_combined, (
            f"[{label}] artifact content mismatch with raw combined output"
        )


def test_interleaved_stdout_stderr_ordering(tmp_path: Path) -> None:
    """Verify harness preserves interleaved stdout+stderr ordering via 2>&1."""
    command = "echo out1; echo err1 >&2; echo out2; echo err2 >&2; echo out3"
    _make_project_dirs(tmp_path)

    raw_merged = _run_raw_merged(command, tmp_path)
    wrapped = _run_wrapped(command, tmp_path)

    assert wrapped.returncode == raw_merged.returncode == 0

    artifacts = _artifact_files(tmp_path)
    if artifacts:
        actual = artifacts[0].read_bytes()
    else:
        actual = wrapped.stdout

    assert actual == raw_merged.stdout, (
        f"Interleaved ordering mismatch.\n  raw_merged={raw_merged.stdout!r}\n  actual={actual!r}"
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


def test_harness_contains_no_destructive_verbs() -> None:
    """Generated harness must not embed destructive shell verbs (Codex exec-policy rejection)."""
    harness = _build_harness("echo hello", "/tmp/test", _INLINE_BYTES)
    destructive = {"rm", "unlink", "shred", "truncate", "mv"}
    for raw_line in harness.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        first = (
            line.split(";", 1)[0].split("&&", 1)[0].split("||", 1)[0].strip().split(maxsplit=1)[0]
        )
        first = first.removeprefix("{").removeprefix("(")
        first = first.strip("\"'")
        assert first not in destructive, (
            f"destructive verb {first!r} found in harness line: {raw_line!r}"
        )


def test_small_output_artifact_cleaned_by_sweep(tmp_path: Path) -> None:
    """Small-output captures are retained on disk but reaped by the Python-side sweep."""
    _make_project_dirs(tmp_path)
    command = "echo hello_small"
    wrapped = _run_wrapped(command, tmp_path)
    assert wrapped.returncode == 0
    artifacts = _artifact_files(tmp_path)
    assert artifacts, "small-output harness must leave capture file on disk for sweep cleanup"
    assert len(artifacts) == 1

    target = artifacts[0]
    # Force mtime into the past so max_age_seconds=0 sweeps it.
    os.utime(target, (0, 0))
    deleted = sweep_stale_captures(_capture_dir(tmp_path), max_age_seconds=0)
    assert deleted == 1
    assert not target.exists()


def test_sweep_rejects_symlinks_and_traversals(tmp_path: Path) -> None:
    """Sweep must not follow symlinks or operate outside the capture directory."""
    _make_project_dirs(tmp_path)
    capture = _capture_dir(tmp_path)

    outside = tmp_path / "outside_target.log"
    outside.write_text("must-survive")
    symlink = capture / f"shell_{uuid4().hex[:16]}.log"
    symlink.symlink_to(outside)
    assert symlink.is_symlink()

    deleted = sweep_stale_captures(capture, max_age_seconds=0)
    assert deleted == 0
    assert outside.exists(), "outside target file must be untouched by sweep"
    assert outside.read_text() == "must-survive"
    assert not symlink.exists(), "symlink should remain (sweep must not follow into the target)"


def test_sweep_filename_allowlist(tmp_path: Path) -> None:
    """Sweep only deletes files matching the strict ``shell_<16hex>.log`` allowlist."""
    _make_project_dirs(tmp_path)
    capture = _capture_dir(tmp_path)

    valid = capture / f"shell_{uuid4().hex[:16]}.log"
    valid.write_text("x")
    short_uid = capture / "shell_abcd1234.log"  # 8-char format — legacy, must NOT be swept
    short_uid.write_text("x")
    invalid_ext = capture / "evil.sh"
    invalid_ext.write_text("x")

    deleted = sweep_stale_captures(capture, max_age_seconds=0)
    assert deleted == 1
    assert not valid.exists()
    assert short_uid.exists(), "old 8-char uid format files must not be deleted"
    assert invalid_ext.exists(), "files outside the shell_*.log allowlist must not be deleted"


def test_capture_cleanup_containment_parity(tmp_path: Path) -> None:
    """_is_safe_capture_file rejects the same attack vectors as core.path_containment."""
    from autoskillit.core.path_containment import ContainmentError, resolve_contained_path

    _make_project_dirs(tmp_path)
    capture = _capture_dir(tmp_path)

    # Valid file — both should accept.
    good = capture / f"shell_{uuid4().hex[:16]}.log"
    good.write_text("ok")
    assert _is_safe_capture_file(good, capture) is True
    try:
        resolve_contained_path(good, capture)
    except ContainmentError as exc:
        pytest.fail(f"core containment rejected a valid capture file: {exc}")

    # Symlink — both should reject.
    sym = capture / f"shell_{uuid4().hex[:16]}.log"
    sym.symlink_to(good)
    assert _is_safe_capture_file(sym, capture) is False
    with pytest.raises(ContainmentError):
        resolve_contained_path(sym, capture)

    # Traversal-shaped filename — allowlist rejects outright.
    traversal = capture / "../../escape.log"
    traversal.write_text("x")
    assert _is_safe_capture_file(traversal, capture) is False


def test_capture_filename_regex_consistency() -> None:
    """Hook module and sweep module must agree on the shell_*.log regex."""
    sample = f"shell_{uuid4().hex[:16]}.log"
    assert _CAPTURE_FILENAME_RE.match(sample) is not None

    # The inline literal in session_start_hook.py must accept the same filename.
    inline_pattern = r"^shell_[0-9a-f]{16}\.log$"
    assert re.match(inline_pattern, sample) is not None
