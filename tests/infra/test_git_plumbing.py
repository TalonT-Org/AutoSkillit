"""Focused contracts for the shared stdlib-only Git plumbing helper."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_git_plumbing():
    previous_path = list(sys.path)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        return importlib.import_module("_git_plumbing")
    finally:
        sys.path[:] = previous_path


git_plumbing = _load_git_plumbing()


def _completed(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(["git"], returncode, stdout, stderr)


def test_git_runs_in_bytes_mode_with_shared_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        return _completed()

    monkeypatch.setattr(git_plumbing.subprocess, "run", fake_run)

    assert git_plumbing._git(Path("/repo"), "status", "--short").returncode == 0
    assert calls == [
        (
            ["git", "status", "--short"],
            {
                "cwd": "/repo",
                "capture_output": True,
                "timeout": 30,
                "check": False,
            },
        )
    ]
    assert git_plumbing._GIT_TIMEOUT_SECONDS == 30


@pytest.mark.parametrize(
    "failure",
    [
        OSError("git executable unavailable"),
        subprocess.TimeoutExpired(cmd="git", timeout=30),
    ],
)
def test_git_wraps_launch_and_timeout_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(git_plumbing.subprocess, "run", fail)

    with pytest.raises(git_plumbing.GitFailure, match="git merge-base") as exc_info:
        git_plumbing._git(Path("/repo"), "merge-base", "HEAD", "main")

    assert exc_info.value.__cause__ is failure


def test_working_tree_reader_distinguishes_absence_from_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = git_plumbing._working_tree_reader(tmp_path)
    assert reader("missing.py") is None

    source_path = tmp_path / "unreadable.py"
    source_path.write_bytes(b"pass\n")
    original_read_bytes = Path.read_bytes
    failure = PermissionError("denied")

    def fail_target(path: Path) -> bytes:
        if path == source_path:
            raise failure
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_target)
    with pytest.raises(git_plumbing.GitFailure, match="unreadable.py") as exc_info:
        reader("unreadable.py")
    assert exc_info.value.__cause__ is failure


@pytest.mark.parametrize(
    "data",
    [
        b"# coding: definitely-not-an-encoding\npass\n",
        b"# coding: utf-8\nvalue = '\xff'\n",
    ],
)
def test_source_decoding_errors_are_chained_git_failures(data: bytes) -> None:
    with pytest.raises(git_plumbing.GitFailure, match="encoding") as exc_info:
        git_plumbing._decode_source(data)
    assert isinstance(exc_info.value.__cause__, (SyntaxError, UnicodeError))


def test_merge_base_failure_surfaces_replacement_safe_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git_plumbing,
        "_git",
        lambda *_args: _completed(1, stderr=b"fatal: bad ref \xff"),
    )

    with pytest.raises(git_plumbing.GitFailure) as exc_info:
        git_plumbing.merge_base(Path("/repo"), "bad-ref")

    message = str(exc_info.value)
    assert "merge-base" in message
    assert "fatal: bad ref" in message
    assert "\ufffd" in message


def test_required_revision_reader_fails_on_object_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git_plumbing,
        "_git",
        lambda *_args: _completed(1, stderr=b"fatal: object is unreadable"),
    )

    reader = git_plumbing._required_revision_reader(Path("/repo"), "abc123")
    with pytest.raises(git_plumbing.GitFailure, match="object is unreadable"):
        reader("src/a.py")


def test_optional_revision_reader_returns_none_only_after_exact_path_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if args == ("cat-file", "-e", "abc123^{tree}"):
            return _completed()
        if args == ("cat-file", "-e", "abc123:src/missing.py"):
            return _completed(1)
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(git_plumbing, "_git", fake_git)

    reader = git_plumbing._optional_revision_reader(Path("/repo"), "abc123")
    assert reader("src/missing.py") is None
    assert calls == [
        ("cat-file", "-e", "abc123^{tree}"),
        ("cat-file", "-e", "abc123:src/missing.py"),
    ]


def test_optional_revision_reader_rejects_invalid_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if args == ("cat-file", "-e", "bad-ref^{tree}"):
            return _completed(1, stderr=b"fatal: invalid object name")
        raise AssertionError(f"revision validation must precede path probe; got {args}")

    monkeypatch.setattr(git_plumbing, "_git", fake_git)

    reader = git_plumbing._optional_revision_reader(Path("/repo"), "bad-ref")
    with pytest.raises(git_plumbing.GitFailure, match="invalid object name"):
        reader("src/a.py")
    assert calls == [("cat-file", "-e", "bad-ref^{tree}")]


def test_optional_revision_reader_fails_if_existing_object_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if args[0] == "cat-file":
            return _completed()
        return _completed(1, stderr=b"fatal: object read failed")

    monkeypatch.setattr(git_plumbing, "_git", fake_git)

    reader = git_plumbing._optional_revision_reader(Path("/repo"), "abc123")
    with pytest.raises(git_plumbing.GitFailure, match="object read failed"):
        reader("src/a.py")
    assert calls == [
        ("cat-file", "-e", "abc123^{tree}"),
        ("cat-file", "-e", "abc123:src/a.py"),
        ("show", "abc123:src/a.py"),
    ]


def test_index_reader_validates_context_and_proves_exact_path_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if args == ("rev-parse", "--git-dir"):
            return _completed(stdout=b".git\n")
        if args == ("ls-files", "--stage", "-z", "--", "src/missing.py"):
            return _completed(stdout=b"")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(git_plumbing, "_git", fake_git)

    reader = git_plumbing._index_reader(Path("/repo"))
    assert reader("src/missing.py") is None
    assert calls == [
        ("rev-parse", "--git-dir"),
        ("ls-files", "--stage", "-z", "--", "src/missing.py"),
    ]


@pytest.mark.parametrize(
    ("failure_at", "stderr", "expected_command"),
    [
        ("context", b"fatal: not a git repository", "rev-parse --git-dir"),
        ("probe", b"fatal: index unavailable", "ls-files --stage"),
        ("read", b"fatal: index object unreadable", "show :"),
    ],
)
def test_index_reader_rejects_context_probe_and_read_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
    stderr: bytes,
    expected_command: str,
) -> None:
    def fake_git(_repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "rev-parse":
            return _completed(1, stderr=stderr) if failure_at == "context" else _completed()
        if args[0] == "ls-files":
            if failure_at == "probe":
                return _completed(1, stderr=stderr)
            return _completed(stdout=b"100644 deadbeef 0\tsrc/a.py\0")
        return _completed(1, stderr=stderr)

    monkeypatch.setattr(git_plumbing, "_git", fake_git)

    reader = git_plumbing._index_reader(Path("/repo"))
    with pytest.raises(git_plumbing.GitFailure, match=expected_command):
        reader("src/a.py")


@pytest.mark.parametrize("reader_kind", ["working", "index", "revision"])
def test_readers_decode_pep263_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader_kind: str,
) -> None:
    source = "# -*- coding: latin-1 -*-\nvalue = 'caf\xe9'\n"
    data = source.encode("latin-1")
    path = "src/a.py"

    if reader_kind == "working":
        (tmp_path / "src").mkdir()
        (tmp_path / path).write_bytes(data)
        reader = git_plumbing._working_tree_reader(tmp_path)
    else:

        def fake_git(_repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
            if args[0] == "rev-parse":
                return _completed(stdout=b".git\n")
            if args[:2] == ("ls-files", "--stage"):
                return _completed(stdout=b"100644 deadbeef 0\tsrc/a.py\0")
            if args[:2] == ("cat-file", "-e"):
                return _completed()
            if args[0] == "show":
                return _completed(stdout=data)
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(git_plumbing, "_git", fake_git)
        if reader_kind == "index":
            reader = git_plumbing._index_reader(tmp_path)
        else:
            reader = git_plumbing._optional_revision_reader(tmp_path, "abc123")

    assert reader(path) == source
