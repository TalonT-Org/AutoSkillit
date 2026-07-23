"""Hard semantic-conformance gate for the shell capture harness.

Runs a corpus of shell commands both raw (``bash -c <command>``) and wrapped
(``bash -c <harness(command)>``), and asserts the harness is byte-exact and
exit-code-exact with the raw execution — the harness must never change what a
command does or what output the agent ultimately sees, only how much of that
output lands inline vs. in an artifact file.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import autoskillit.hooks._capture_artifacts as capture_artifacts
import autoskillit.hooks.shell_capture_hook as shell_capture_hook
from autoskillit.hooks._capture_artifacts import (
    _CAPTURE_FILENAME_RE,
    _open_stale_candidate,
    classify_stale_captures,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks.shell_capture_hook import _build_harness

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_INLINE_BYTES = 12_000
_CAPTURE_SUBDIR = ".autoskillit/temp/shell_capture"
_TIMEOUT = 30
_HARNESS_FORBIDDEN_VERBS: frozenset[str] = frozenset(
    {
        "rm",
        "unlink",
        "shred",
        "truncate",
        "rmdir",
        "mv",  # moving the capture file would break concurrent reads
    }
)

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
    ("nested_wrap", None),
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
    wrapped = _build_harness(command, str(tmp_path), uuid4().hex[:16])
    return subprocess.run(
        ["bash", "-c", wrapped],
        capture_output=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
    )


def _main_generated_wrapper(command: str, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    event = {"cwd": str(cwd), "tool_input": {"command": command}}
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    output = io.StringIO()
    with pytest.raises(SystemExit), redirect_stdout(output):
        shell_capture_hook.main()
    payload = json.loads(output.getvalue())
    return payload["hookSpecificOutput"]["updatedInput"]["command"]


@pytest.mark.parametrize("label,command", _CORPUS, ids=[row[0] for row in _CORPUS])
def test_capture_conformance(label: str, command: str, tmp_path: Path) -> None:
    if label == "rg_sort" and shutil.which("rg") is None:
        pytest.skip("rg not available")
    if label == "jq_keys" and shutil.which("jq") is None:
        pytest.skip("jq not available")

    _make_project_dirs(tmp_path)
    if label == "nested_wrap":
        command = _build_harness(_NESTED_WRAP_INNER, str(tmp_path), uuid4().hex[:16])

    raw = _run_raw(command, tmp_path)
    raw_combined = raw.stdout + raw.stderr

    if label == "heredoc_append":
        # raw run already appended once; reset the target file so the
        # wrapped run's append produces byte-identical content to compare.
        report = tmp_path / ".autoskillit" / "temp" / "investigate" / "report.md"
        report.unlink(missing_ok=True)
    if label == "nested_wrap":
        for artifact in _artifact_files(tmp_path):
            artifact.unlink()

    wrapped = _run_wrapped(command, tmp_path)

    expected_wrapped_returncode = 128 + (-raw.returncode) if raw.returncode < 0 else raw.returncode
    assert wrapped.returncode == expected_wrapped_returncode, (
        f"[{label}] exit code mismatch: raw={raw.returncode} wrapped={wrapped.returncode}\n"
        f"raw stderr={raw.stderr!r}\nwrapped stderr={wrapped.stderr!r}"
    )

    artifacts = _artifact_files(tmp_path)

    if label == "true_cmd":
        assert raw_combined == b""
        assert wrapped.stdout == b""
        assert raw.returncode == 0
        assert artifacts, "[true_cmd] expected artifact retained (Python-side cleanup)"
        assert len(artifacts) == 1
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
        # The isolated runner deliberately translates a child signal to the
        # shell-compatible 128+signal status.
        assert raw.returncode == -15
        assert wrapped.returncode == 143
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


@pytest.mark.parametrize("component", [".autoskillit", "temp", "shell_capture"])
def test_main_generated_wrapper_rejects_symlinked_capture_components(
    component: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    secret = external / "secret"
    secret.write_text("must-not-be-read")

    parent = project
    for name in (".autoskillit", "temp", "shell_capture"):
        candidate = parent / name
        if name == component:
            candidate.symlink_to(external, target_is_directory=True)
            break
        candidate.mkdir()
        parent = candidate
    if component == "temp":
        (external / ".hook_config.json").write_text(
            json.dumps({"output_budget_policy": {"disabled": True}})
        )

    wrapper = _main_generated_wrapper(
        "printf ran > command_ran",
        project,
        monkeypatch,
    )
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 1
    assert "CAPTURE_FAILED" in completed.stdout + completed.stderr
    assert not (project / "command_ran").exists()
    assert not list(external.glob("shell_*.log"))
    assert secret.read_text() == "must-not-be-read"
    assert "must-not-be-read" not in completed.stdout + completed.stderr


def test_main_generated_wrapper_accepts_symlinked_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)

    wrapper = _main_generated_wrapper("printf anchored", supplied_cwd, monkeypatch)
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=supplied_cwd,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    assert completed.stdout == b"anchored"
    assert len(_artifact_files(project)) == 1


@pytest.mark.parametrize("collision", ["symlink", "hardlink", "regular"])
def test_main_generated_wrapper_rejects_final_artifact_collisions(
    collision: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _make_project_dirs(project)
    capture_id = "a1b2c3d4e5f60718"
    monkeypatch.setattr(
        shell_capture_hook,
        "uuid4",
        lambda: SimpleNamespace(hex=capture_id + "0" * 16),
    )
    artifact = _capture_dir(project) / f"shell_{capture_id}.log"
    external = tmp_path / "external-secret"
    external.write_bytes(b"must-survive")
    if collision == "symlink":
        artifact.symlink_to(external)
    elif collision == "hardlink":
        try:
            os.link(external, artifact)
        except OSError:
            pytest.skip("hardlinks unavailable")
    else:
        artifact.write_bytes(b"existing")

    wrapper = _main_generated_wrapper(
        "printf ran > command_ran",
        project,
        monkeypatch,
    )
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 1
    combined = (completed.stdout + completed.stderr).decode()
    assert "CAPTURE_FAILED" in combined
    assert "must-survive" not in combined
    assert not (project / "command_ran").exists()
    assert external.read_bytes() == b"must-survive"
    if collision == "regular":
        assert artifact.read_bytes() == b"existing"


def test_capture_directory_replacement_uses_open_fds_and_hides_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _make_project_dirs(project)
    config = project / ".autoskillit" / "temp" / ".hook_config.json"
    config.write_text(json.dumps({"output_budget_policy": {"shell_max_inline_bytes": 8}}))
    external = tmp_path / "replacement-target"
    external.mkdir()
    command = (
        "mv .autoskillit/temp/shell_capture "
        ".autoskillit/temp/shell_capture-original; "
        f"ln -s {shlex.quote(str(external))} .autoskillit/temp/shell_capture; "
        "printf 0123456789abcdef"
    )

    wrapper = _main_generated_wrapper(command, project, monkeypatch)
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    assert b"-> unavailable " in completed.stdout
    assert b"SHELL_OUTPUT_CAPTURED" in completed.stdout
    expected = b"0123456789abcdef"
    assert f"full output {len(expected)} bytes".encode() in completed.stdout
    assert f"sha256={hashlib.sha256(expected).hexdigest()}".encode() in completed.stdout
    assert completed.stdout.startswith(expected[:5])
    assert completed.stdout.endswith(expected[-3:])
    displaced = project / ".autoskillit" / "temp" / "shell_capture-original"
    artifacts = sorted(displaced.glob("shell_*.log"))
    assert len(artifacts) == 1
    assert artifacts[0].read_bytes() == expected
    assert not list(external.iterdir())


def test_capture_artifact_replacement_uses_open_fd_and_hides_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _make_project_dirs(project)
    config = project / ".autoskillit" / "temp" / ".hook_config.json"
    config.write_text(json.dumps({"output_budget_policy": {"shell_max_inline_bytes": 8}}))
    capture_id = "1029384756abcdef"
    monkeypatch.setattr(
        shell_capture_hook,
        "uuid4",
        lambda: SimpleNamespace(hex=capture_id + "0" * 16),
    )
    capture_dir = _capture_dir(project)
    artifact = capture_dir / f"shell_{capture_id}.log"
    displaced = capture_dir / "opened-artifact.log"
    external = tmp_path / "external-target"
    external.write_bytes(b"must-survive")
    command = (
        f"mv {shlex.quote(str(artifact))} {shlex.quote(str(displaced))}; "
        f"ln -s {shlex.quote(str(external))} {shlex.quote(str(artifact))}; "
        "printf fedcba9876543210"
    )

    wrapper = _main_generated_wrapper(command, project, monkeypatch)
    completed = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    assert b"-> unavailable " in completed.stdout
    expected = b"fedcba9876543210"
    assert f"full output {len(expected)} bytes".encode() in completed.stdout
    assert f"sha256={hashlib.sha256(expected).hexdigest()}".encode() in completed.stdout
    assert completed.stdout.startswith(expected[:5])
    assert completed.stdout.endswith(expected[-3:])
    assert displaced.read_bytes() == expected
    assert external.read_bytes() == b"must-survive"
    assert artifact.is_symlink()


@pytest.mark.parametrize(
    "cmd",
    ["echo hello", "ls -la", "cat /dev/null", "python3 -c 'print(1)'", ""],
)
def test_harness_contains_no_destructive_verbs(cmd: str) -> None:
    """Arch guard: hook-generated shell must not contain destructive verbs.

    Codex's exec-policy engine evaluates the full rewritten command, including
    hook-injected scaffolding. Destructive verbs (rm, unlink, etc.) are forbidden
    by Codex's built-in policy. This test ensures the harness never introduces them.
    """
    harness = _build_harness(cmd, "/tmp/test", uuid4().hex[:16])
    for raw_line in harness.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        first = (
            line.split(";", 1)[0].split("&&", 1)[0].split("||", 1)[0].strip().split(maxsplit=1)[0]
        )
        first = first.removeprefix("{").removeprefix("(")
        first = first.strip("\"'")
        assert first not in _HARNESS_FORBIDDEN_VERBS, (
            f"forbidden verb {first!r} found in harness line: {raw_line!r}\n"
            f"Generated from command: {cmd!r}"
        )


def test_small_output_artifact_safely_retained_by_sweep(tmp_path: Path) -> None:
    """Portable cleanup retains stale captures without identity-conditioned unlink."""
    _make_project_dirs(tmp_path)
    command = "echo hello_small"
    wrapped = _run_wrapped(command, tmp_path)
    assert wrapped.returncode == 0
    artifacts = _artifact_files(tmp_path)
    assert artifacts, "small-output harness must leave capture file on disk for sweep cleanup"
    assert len(artifacts) == 1

    target = artifacts[0]
    os.utime(target, (0, 0))
    deleted = classify_stale_captures(tmp_path, max_age_seconds=0)
    assert deleted == 0
    assert target.exists()


def test_sweep_rejects_symlinks_and_traversals(tmp_path: Path) -> None:
    """Sweep must not follow symlinks or operate outside the capture directory."""
    _make_project_dirs(tmp_path)
    capture = _capture_dir(tmp_path)

    outside = tmp_path / "outside_target.log"
    outside.write_text("must-survive")
    symlink = capture / f"shell_{uuid4().hex[:16]}.log"
    symlink.symlink_to(outside)
    assert symlink.is_symlink()

    deleted = classify_stale_captures(tmp_path, max_age_seconds=0)
    assert deleted == 0
    assert outside.exists(), "outside target file must be untouched by sweep"
    assert outside.read_text() == "must-survive"
    assert symlink.is_symlink(), (
        "symlink must remain after sweep (sweep must not follow into the target)"
    )


def test_stale_candidate_classifier_enforces_filename_allowlist(tmp_path: Path) -> None:
    """Only strict ``shell_<16hex>.log`` names reach stale-candidate classification."""
    _make_project_dirs(tmp_path)
    capture = _capture_dir(tmp_path)

    valid = capture / f"shell_{uuid4().hex[:16]}.log"
    valid.write_text("x")
    short_uid = capture / "shell_abcd1234.log"  # 8-char format — legacy, must NOT be swept
    short_uid.write_text("x")
    invalid_ext = capture / "evil.sh"
    invalid_ext.write_text("x")

    anchor = open_project_anchor(str(tmp_path))
    root = open_capture_root(anchor, create=False)
    try:
        candidate = _open_stale_candidate(
            root,
            valid.name,
            mtime_threshold=time.time() + 1,
        )
        assert candidate is not None
        candidate.close()
        assert (
            _open_stale_candidate(
                root,
                short_uid.name,
                mtime_threshold=time.time() + 1,
            )
            is None
        )
        assert (
            _open_stale_candidate(
                root,
                invalid_ext.name,
                mtime_threshold=time.time() + 1,
            )
            is None
        )
    finally:
        root.close()
        anchor.close()


def test_stale_capture_classifier_uses_wall_clock_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project_dirs(tmp_path)
    capture = _capture_dir(tmp_path)
    stale = capture / f"shell_{uuid4().hex[:16]}.log"
    stale.write_text("x")
    os.utime(stale, (300, 300))
    os.utime(capture, (499, 499))

    observed_thresholds: list[float] = []
    real_classifier = capture_artifacts._open_stale_candidate

    def observe_threshold(root, name, *, mtime_threshold):
        observed_thresholds.append(mtime_threshold)
        return real_classifier(root, name, mtime_threshold=mtime_threshold)

    monkeypatch.setattr(capture_artifacts.time, "time", lambda: 500)
    monkeypatch.setattr(capture_artifacts, "_open_stale_candidate", observe_threshold)

    assert classify_stale_captures(tmp_path, max_age_seconds=100) == 0
    assert observed_thresholds == [400]


def test_capture_filename_regex_consistency() -> None:
    """The canonical helper accepts only the current capture filename contract."""
    sample = f"shell_{uuid4().hex[:16]}.log"
    assert _CAPTURE_FILENAME_RE.fullmatch(sample) is not None
    assert _CAPTURE_FILENAME_RE.fullmatch("shell_deadbeef.log") is None
