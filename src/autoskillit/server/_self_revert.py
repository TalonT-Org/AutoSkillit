"""Bounded, conservative first-parent self-revert detection."""

from __future__ import annotations

import dataclasses
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

from autoskillit.core import SubprocessRunner, TerminationReason
from autoskillit.server._subprocess import _process_runner_result

__all__ = ["detect_self_reverts"]

_SCAN_TIMEOUT_SECONDS = 10.0
_SCAN_MAX_COMMITS = 256
_SCAN_MAX_OUTPUT_BYTES = 128 * 1024


@dataclasses.dataclass(frozen=True, slots=True)
class _PatchHunk:
    """One textual, single-file unified-diff hunk used for inverse matching."""

    path: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    before_context: str | None
    after_context: str | None


_ScanGit = Callable[[list[str]], Awaitable[tuple[str | None, str | None]]]


def _boundary_error(line: str) -> str | None:
    if line.startswith(
        (
            "Binary files ",
            "GIT binary patch",
            "copy from ",
            "copy to ",
            "rename from ",
            "rename to ",
            "similarity index ",
        )
    ):
        return "self-revert scan encountered a binary, copy, or rename boundary"
    return None


def _diff_path(line: str) -> tuple[str | None, str | None]:
    if not line.startswith("diff --git "):
        return None, None
    try:
        old_path, new_path = shlex.split(line.removeprefix("diff --git "))
    except ValueError:
        return None, "self-revert scan encountered an unparseable diff header"
    if not old_path.startswith("a/") or not new_path.startswith("b/"):
        return None, "self-revert scan encountered an unparseable diff header"
    return new_path.removeprefix("b/"), None


def _finish_hunk(
    path: str | None,
    hunk_lines: list[str] | None,
    hunks: list[_PatchHunk],
) -> None:
    if hunk_lines is None or path is None:
        return
    added = tuple(line[1:] for line in hunk_lines if line.startswith("+"))
    removed = tuple(line[1:] for line in hunk_lines if line.startswith("-"))
    if added or removed:
        changed = [index for index, line in enumerate(hunk_lines) if line.startswith(("+", "-"))]
        first_change, last_change = changed[0], changed[-1]
        before_context = next(
            (
                line[1:]
                for line in reversed(hunk_lines[:first_change])
                if line.startswith(" ") and line[1:].strip()
            ),
            None,
        )
        after_context = next(
            (
                line[1:]
                for line in hunk_lines[last_change + 1 :]
                if line.startswith(" ") and line[1:].strip()
            ),
            None,
        )
        hunks.append(
            _PatchHunk(
                path=path,
                added=added,
                removed=removed,
                before_context=before_context,
                after_context=after_context,
            )
        )


def _parse_text_hunks(patch: str) -> tuple[list[_PatchHunk], str | None]:
    """Parse a textual patch, refusing boundaries that make matching ambiguous."""
    hunks: list[_PatchHunk] = []
    path: str | None = None
    hunk_lines: list[str] | None = None

    for line in patch.splitlines():
        if boundary_error := _boundary_error(line):
            return [], boundary_error
        diff_path, header_error = _diff_path(line)
        if header_error is not None:
            return [], header_error
        if diff_path is not None:
            _finish_hunk(path, hunk_lines, hunks)
            hunk_lines = None
            path = diff_path
            continue
        if line.startswith("@@ "):
            if path is None:
                return [], "self-revert scan encountered a hunk without a file header"
            _finish_hunk(path, hunk_lines, hunks)
            hunk_lines = []
            continue
        if hunk_lines is not None and line[:1] in {" ", "+", "-"}:
            hunk_lines.append(line)
    _finish_hunk(path, hunk_lines, hunks)
    return hunks, None


def _are_inverse_hunks(earlier: _PatchHunk, later: _PatchHunk) -> bool:
    return (
        earlier.path == later.path
        and earlier.added == later.removed
        and earlier.removed == later.added
        and earlier.before_context == later.before_context
        and earlier.after_context == later.after_context
        and (earlier.before_context is not None or earlier.after_context is not None)
    )


async def _scan_commit(
    scan_git: _ScanGit,
    cwd: str,
    commit_sha: str,
) -> tuple[list[_PatchHunk] | None, str | None]:
    parents_output, scan_error = await scan_git(
        ["git", "-C", cwd, "rev-list", "--parents", "-n", "1", commit_sha]
    )
    if scan_error is not None:
        return None, scan_error
    if len((parents_output or "").split()) != 2:
        return None, "self-revert scan encountered a merge or root boundary"
    patch, scan_error = await scan_git(
        [
            "git",
            "-C",
            cwd,
            "show",
            "--format=",
            "--find-renames",
            "--no-ext-diff",
            "--unified=3",
            commit_sha,
        ]
    )
    if scan_error is not None:
        return None, scan_error
    return _parse_text_hunks(patch or "")


def _record_inverse_hunks(
    commit_sha: str,
    hunks: list[_PatchHunk],
    prior_hunks: list[tuple[str, _PatchHunk]],
    reverted_hunks_by_pair: dict[tuple[str, str], list[dict[str, str]]],
) -> None:
    matched_prior: set[int] = set()
    for hunk in hunks:
        for index, (earlier_sha, earlier_hunk) in enumerate(prior_hunks):
            if index in matched_prior or not _are_inverse_hunks(earlier_hunk, hunk):
                continue
            reverted_hunks_by_pair.setdefault((earlier_sha, commit_sha), []).append(
                {"path": hunk.path}
            )
            matched_prior.add(index)
            break
    prior_hunks.extend((commit_sha, hunk) for hunk in hunks)


@dataclasses.dataclass(slots=True)
class _ScanState:
    runner: SubprocessRunner
    cwd: str
    output_used: int = 0

    async def run(self, cmd: list[str]) -> tuple[str | None, str | None]:
        result = await self.runner(
            cmd,
            cwd=Path(self.cwd),
            timeout=_SCAN_TIMEOUT_SECONDS,
            max_combined_output_bytes=_SCAN_MAX_OUTPUT_BYTES,
        )
        if result.termination is TerminationReason.OUTPUT_LIMIT:
            return None, "self-revert scan reached its output limit"
        rc, stdout, stderr = _process_runner_result(result, _SCAN_TIMEOUT_SECONDS)
        stdout = stdout or ""
        stderr = stderr or ""
        self.output_used += len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        if self.output_used > _SCAN_MAX_OUTPUT_BYTES:
            return None, "self-revert scan reached its output limit"
        if rc == -1:
            return None, "self-revert scan timed out"
        if rc != 0:
            return None, f"self-revert scan git command failed with status {rc}"
        return stdout, None


async def detect_self_reverts(
    runner: SubprocessRunner,
    cwd: str,
    base_ref: str,
    head_ref: str,
) -> dict[str, object]:
    """Find inverse-hunk pairs on a bounded first-parent path.

    Unsupported topology or diff boundaries make the result incomplete rather
    than silently claiming that the complete history was scanned.
    """
    scan_state = _ScanState(runner, cwd)
    commits_output, scan_error = await scan_state.run(
        [
            "git",
            "-C",
            cwd,
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{base_ref}..{head_ref}",
        ]
    )
    if scan_error is not None:
        return {"pairs": [], "complete": False, "scan_error": scan_error}

    commit_shas = [line for line in (commits_output or "").splitlines() if line]
    if len(commit_shas) > _SCAN_MAX_COMMITS:
        return {
            "pairs": [],
            "complete": False,
            "scan_error": "self-revert scan reached its commit limit",
        }

    prior_hunks: list[tuple[str, _PatchHunk]] = []
    reverted_hunks_by_pair: dict[tuple[str, str], list[dict[str, str]]] = {}

    def pairs() -> list[dict[str, object]]:
        return [
            {
                "earlier_sha": earlier_sha,
                "later_sha": later_sha,
                "reverted_hunks": reverted_hunks,
            }
            for (earlier_sha, later_sha), reverted_hunks in reverted_hunks_by_pair.items()
        ]

    for commit_sha in commit_shas:
        hunks, scan_error = await _scan_commit(scan_state.run, cwd, commit_sha)
        if scan_error is not None or hunks is None:
            return {"pairs": pairs(), "complete": False, "scan_error": scan_error or ""}
        _record_inverse_hunks(commit_sha, hunks, prior_hunks, reverted_hunks_by_pair)

    return {"pairs": pairs(), "complete": True}
