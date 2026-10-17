"""Write evidence computation for headless sessions."""

from __future__ import annotations

import os
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import (
    WORKTREE_SKILLS,
    DelegatedWriteObservation,
    DelegatedWriteTarget,
    WriteEvidence,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.session._session_model import ClaudeSessionResult

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WriteEvidenceScope:
    write_tool_names: Collection[str]
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes: tuple[str, ...]
    write_watch_dirs: tuple[Path, ...]
    cwd: str
    skill_command: str
    delegated_target: DelegatedWriteTarget | None


def _is_worktree_dispatch(scope: WriteEvidenceScope) -> bool:
    extracted = extract_skill_name(scope.skill_command) if scope.skill_command else None
    return bool(
        extracted and extracted in WORKTREE_SKILLS and scope.cwd and scope.write_watch_dirs
    )


def _is_implementation_path(scope: WriteEvidenceScope, resolved: str) -> bool:
    """Return whether a resolved path is outside the worktree temp directory."""
    if not _is_worktree_dispatch(scope):
        return True
    temp_prefix = str(Path(scope.cwd).resolve()) + "/.autoskillit/temp/"
    return not resolved.startswith(temp_prefix)


def _count_worktree_write_calls(
    scope: WriteEvidenceScope,
    eligible_calls: list[dict[str, Any]],
) -> int:
    resolved_cwd = str(Path(scope.cwd).resolve())
    write_call_count = 0
    for tool_use in eligible_calls:
        file_path = tool_use.get("file_path", "")
        if not file_path:
            continue
        resolved = str((Path(resolved_cwd) / file_path).resolve())
        if _is_implementation_path(scope, resolved):
            write_call_count += 1
    return write_call_count


def _count_root_write_calls(session: ClaudeSessionResult, scope: WriteEvidenceScope) -> int:
    is_worktree = _is_worktree_dispatch(scope)
    eligible_calls: list[dict[str, Any]] = []
    for tool_use in session.tool_uses:
        if tool_use.get("name") not in scope.write_tool_names:
            continue
        tool_id = tool_use.get("id")
        if is_worktree:
            if tool_id is not None and tool_id in session.denied_tool_use_ids:
                continue
        elif tool_id in session.denied_tool_use_ids:
            continue
        eligible_calls.append(tool_use)

    if not is_worktree:
        return len(eligible_calls)
    return _count_worktree_write_calls(scope, eligible_calls)


def _is_admitted_delegated_write(
    observation: DelegatedWriteObservation,
    target: DelegatedWriteTarget,
    scope: WriteEvidenceScope,
) -> bool:
    if not observation.succeeded or observation.tool_name not in scope.write_tool_names:
        return False
    resolved = os.path.realpath(Path(scope.cwd) / observation.file_path)
    return resolved == target.path and _is_implementation_path(scope, resolved)


def _admitted_delegated_write_count(
    session: ClaudeSessionResult,
    scope: WriteEvidenceScope,
) -> int:
    observations = session.delegated_writes
    if not observations:
        return 0

    target = scope.delegated_target
    target_bound = target is not None
    target_changed = target.changed() if target is not None else False
    if target is not None and target_changed:
        admitted = sum(
            1
            for observation in observations
            if _is_admitted_delegated_write(observation, target, scope)
        )
    else:
        admitted = 0

    if admitted == 0:
        logger.warning(
            "delegated_writes_not_admitted",
            observed=len(observations),
            target_bound=target_bound,
            target_changed=target_changed,
        )
    return admitted


def _compute_write_evidence(
    session: ClaudeSessionResult,
    scope: WriteEvidenceScope,
) -> WriteEvidence:
    # Worktree implementation evidence must be outside temp; other skills count temp writes.
    write_call_count = _count_root_write_calls(session, scope)
    delegated_write_count = _admitted_delegated_write_count(session, scope)

    if write_call_count == 0 and scope.file_changes:
        if _is_worktree_dispatch(scope):
            resolved_cwd = str(Path(scope.cwd).resolve())
            file_changes_count = sum(
                1
                for file_change in scope.file_changes
                if _is_implementation_path(
                    scope,
                    str((Path(resolved_cwd) / file_change).resolve()),
                )
            )
        else:
            file_changes_count = len(scope.file_changes)
    else:
        file_changes_count = 0

    return WriteEvidence(
        write_call_count=write_call_count,
        fs_writes_detected=scope.fs_writes_detected,
        git_writes_detected=scope.git_writes_detected,
        file_changes_count=file_changes_count,
        delegated_write_count=delegated_write_count,
    )
