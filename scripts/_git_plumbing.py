#!/usr/bin/env python3
# autoskillit: policy-authority -- repository acceptance policy enforcement.
"""Shared Git and Python-source plumbing for repository policy checks."""

from __future__ import annotations

import io
import subprocess
import tokenize
from collections.abc import Callable
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 30


class GitFailure(RuntimeError):
    """A required Git, filesystem, or source-decoding operation failed."""


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run Git in bytes mode without raising for a nonzero exit status."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitFailure(f"git {' '.join(args)}: {exc}") from exc


def _plumbing_text(result: subprocess.CompletedProcess[bytes]) -> str:
    """Decode Git metadata, which is not Python source."""
    return result.stdout.decode("utf-8", errors="replace")


def _plumbing_stderr(result: subprocess.CompletedProcess[bytes]) -> str:
    """Return replacement-safe Git stderr formatted for an error message."""
    text = result.stderr.decode("utf-8", errors="replace").strip()
    return f" | stderr: {text}" if text else ""


def _decode_source(data: bytes) -> str:
    """Decode Python source according to its BOM or PEP 263 encoding cookie."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as exc:
        raise GitFailure(f"Python source encoding detection failed: {exc}") from exc
    try:
        return data.decode(encoding)
    except UnicodeError as exc:
        raise GitFailure(f"Python source encoding {encoding!r} failed: {exc}") from exc


def merge_base(repo_root: Path, ref: str) -> str:
    """Return the required merge base of HEAD and *ref*."""
    result = _git(repo_root, "merge-base", "HEAD", ref)
    if result.returncode != 0:
        raise GitFailure(f"git merge-base HEAD {ref} failed{_plumbing_stderr(result)}")
    resolved = _plumbing_text(result).strip()
    if not resolved:
        raise GitFailure(f"git merge-base HEAD {ref} produced no output")
    return resolved


def _revision_required_reader(repo_root: Path, rev: str) -> Callable[[str], str | None]:
    """Build a reader for Python sources that must exist at *rev*."""

    def read(path: str) -> str:
        object_name = f"{rev}:{path}"
        result = _git(repo_root, "show", object_name)
        if result.returncode != 0:
            raise GitFailure(f"git show {object_name} failed{_plumbing_stderr(result)}")
        return _decode_source(result.stdout)

    return read


def _revision_optional_reader(repo_root: Path, rev: str) -> Callable[[str], str | None]:
    """Build a reader that returns None only for a path absent from a valid revision."""

    def read(path: str) -> str | None:
        tree_name = f"{rev}^{{tree}}"
        revision = _git(repo_root, "cat-file", "-e", tree_name)
        if revision.returncode != 0:
            raise GitFailure(f"git cat-file -e {tree_name} failed{_plumbing_stderr(revision)}")

        object_name = f"{rev}:{path}"
        present = _git(repo_root, "cat-file", "-e", object_name)
        if present.returncode != 0:
            return None

        result = _git(repo_root, "show", object_name)
        if result.returncode != 0:
            raise GitFailure(f"git show {object_name} failed{_plumbing_stderr(result)}")
        return _decode_source(result.stdout)

    return read


def _index_reader(repo_root: Path) -> Callable[[str], str | None]:
    """Build a reader that distinguishes an absent index path from Git failure."""

    def read(path: str) -> str | None:
        context = _git(repo_root, "rev-parse", "--git-dir")
        if context.returncode != 0:
            raise GitFailure(f"git rev-parse --git-dir failed{_plumbing_stderr(context)}")

        probe_args = ("ls-files", "--stage", "-z", "--", path)
        present = _git(repo_root, *probe_args)
        if present.returncode != 0:
            raise GitFailure(f"git {' '.join(probe_args)} failed{_plumbing_stderr(present)}")
        if not present.stdout:
            return None

        result = _git(repo_root, "show", f":{path}")
        if result.returncode != 0:
            raise GitFailure(f"git show :{path} failed{_plumbing_stderr(result)}")
        return _decode_source(result.stdout)

    return read


def _working_tree_reader(repo_root: Path) -> Callable[[str], str | None]:
    """Build a reader that returns None only for a missing working-tree path.

    A dangling symlink follows the same FileNotFoundError absence path as a missing file.
    """

    def read(path: str) -> str | None:
        candidate = repo_root / path
        try:
            data = candidate.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GitFailure(f"unable to read working-tree source {path}: {exc}") from exc
        return _decode_source(data)

    return read
