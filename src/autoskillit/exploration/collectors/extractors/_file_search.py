"""File-search collectors: ``collect_artifact``, ``collect_file_list``, ``collect_search``.

Decomposed from the original ``collectors/extractors.py`` per #4836.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    EvidenceRecord,
)

from .._bounded import (
    CollectorLimits,
    CollectorSafetyError,
    read_contained_file,
)
from ._evidence import (
    _evidence,
    _invalid_rg_json_diagnostic,
    _report,
)

# Capture the facade module so ``collect_search`` looks up ``run_bounded_rg``
# through the package attribute. ``test_bounded_collectors.py`` monkeypatches
# ``extractors_module.run_bounded_rg`` (lines 408, 655) and expects the patch
# to propagate; without late-binding through the facade, a local import in
# this shard would capture a separate binding the patch cannot reach.
_extractors_facade = sys.modules[__package__ or "autoskillit.exploration.collectors.extractors"]

__all__ = [
    "collect_artifact",
    "collect_file_list",
    "collect_search",
    "_normalise_scope",
    "_scoped_paths",
]


def _normalise_scope(scope: str) -> str:
    """Accept only a repository-relative path prefix for scoped observations."""

    if not scope or scope == ".":
        return ""
    candidate = PurePosixPath(scope)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
        or any(any(character in part for character in "*?[]!{}\\") for part in candidate.parts)
    ):
        raise CollectorSafetyError("collector scope must be a contained literal path")
    return candidate.as_posix()


def _scoped_paths(root: Path, scope: str, limits: CollectorLimits) -> tuple[str, ...]:
    from .._bounded import list_contained_files  # local to keep top-of-file imports lean

    prefix = _normalise_scope(scope)
    paths = list_contained_files(root, limits)
    if not prefix:
        return paths
    return tuple(path for path in paths if path == prefix or path.startswith(f"{prefix}/"))


def collect_artifact(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "contained-artifact"
    try:
        payload = read_contained_file(root, path, limits)
    except CollectorSafetyError as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    return _report(
        collector_id,
        snapshot_digest,
        CollectorStatus.SUCCEEDED,
        evidence=(
            _evidence(
                collector_id,
                snapshot_digest,
                path,
                1,
                payload.decode("utf-8", "replace")[: limits.max_output_bytes],
            ),
        ),
    )


def collect_file_list(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "contained-list"
    try:
        paths = _scoped_paths(root, scope, limits)
    except CollectorSafetyError as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    evidence = tuple(_evidence(collector_id, snapshot_digest, path, 1, path) for path in paths)
    return _report(collector_id, snapshot_digest, CollectorStatus.SUCCEEDED, evidence=evidence)


def collect_search(
    root: Path,
    snapshot_digest: str,
    pattern: str,
    limits: CollectorLimits,
    *,
    scopes: tuple[str, ...] = (),
) -> CollectorReport:
    collector_id = "bounded-rg-search"
    try:
        normalized_scopes = tuple(_normalise_scope(scope) for scope in scopes)
    except CollectorSafetyError as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    globs = tuple(
        scope if (root / scope).is_file() else f"{scope}/**"
        for scope in normalized_scopes
        if scope
    )
    result = _extractors_facade.run_bounded_rg(root, pattern, globs=globs, limits=limits)
    if result.failure is not None:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (result.failure,))
    evidence: list[EvidenceRecord] = []
    for raw_line in result.stdout.splitlines():
        if len(evidence) >= limits.max_matches:
            return _report(
                collector_id,
                snapshot_digest,
                CollectorStatus.TRUNCATED,
                ("match limit exceeded",),
                tuple(evidence),
            )
        try:
            event = json.loads(raw_line)
            data = event["data"]
            if event["type"] != "match":
                continue
            path = data["path"]["text"]
            line = data["line_number"]
            text = data["lines"]["text"].rstrip("\n")
        except (KeyError, TypeError, ValueError) as exc:
            return _report(
                collector_id,
                snapshot_digest,
                CollectorStatus.FAILED,
                (_invalid_rg_json_diagnostic(raw_line, exc),),
                tuple(evidence),
            )
        evidence.append(
            replace(
                _evidence(collector_id, snapshot_digest, path, line, text),
                searched_scope=normalized_scopes or (".",),
            )
        )
    status = CollectorStatus.SUCCEEDED if result.returncode in (0, 1) else CollectorStatus.FAILED
    return _report(
        collector_id,
        snapshot_digest,
        status,
        () if status is CollectorStatus.SUCCEEDED else ("rg failed",),
        tuple(evidence),
    )
