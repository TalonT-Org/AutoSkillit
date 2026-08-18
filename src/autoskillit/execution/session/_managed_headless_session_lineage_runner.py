"""Runner observation helpers for the managed headless session lineage store."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from autoskillit.core import (
    MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureObservation,
)
from autoskillit.execution.session._managed_headless_session_lineage import (
    _MAX_RUNNER_MARKER_BYTES,
    _MAX_RUNNER_MARKERS,
    _RUNNER_OBSERVATIONS_DIR,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    canonical_json as _canonical_json,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    strict_json_load as _strict_json_load,
)


def _read_runner_markers(
    root: Path,
    reference: ManagedHeadlessSessionLineageRef,
    lineage: ManagedHeadlessSessionLineage,
) -> tuple[NativeShellCaptureObservation, ...]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, directory_flags | nofollow)
    observations_fd = -1
    launch_fd = -1
    try:
        try:
            observations_fd = os.open(
                _RUNNER_OBSERVATIONS_DIR,
                directory_flags | nofollow,
                dir_fd=root_fd,
            )
            launch_fd = os.open(
                reference.launch_id,
                directory_flags | nofollow,
                dir_fd=observations_fd,
            )
        except FileNotFoundError:
            return ()
        parsed: list[NativeShellCaptureObservation] = []
        for name in sorted(os.listdir(launch_fd))[:_MAX_RUNNER_MARKERS]:
            if not name.endswith(".json") or "/" in name or name in {".", ".."}:
                continue
            marker_fd = -1
            try:
                marker_fd = os.open(
                    name,
                    os.O_RDONLY | nofollow,
                    dir_fd=launch_fd,
                )
                metadata = os.fstat(marker_fd)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_size > _MAX_RUNNER_MARKER_BYTES
                ):
                    continue
                raw = os.read(marker_fd, _MAX_RUNNER_MARKER_BYTES + 1)
            except OSError:
                continue
            finally:
                if marker_fd >= 0:
                    os.close(marker_fd)
            try:
                marker = _strict_json_load(raw)
                if _canonical_json(marker).encode("utf-8") != raw:
                    continue
                if not isinstance(marker, dict) or set(marker) != {
                    "schema_version",
                    "launch_id",
                    "lineage_digest",
                    "observation",
                }:
                    continue
                if (
                    marker["schema_version"] != MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION
                    or marker["launch_id"] != reference.launch_id
                    or marker["lineage_digest"] != reference.lineage_digest
                ):
                    continue
                observation = NativeShellCaptureObservation.from_dict(marker["observation"])
                if observation.attempt_id not in lineage.attempt_ids:
                    continue
            except (TypeError, ValueError):
                continue
            parsed.append(observation)
        return tuple(dict.fromkeys(parsed))
    finally:
        if launch_fd >= 0:
            os.close(launch_fd)
        if observations_fd >= 0:
            os.close(observations_fd)
        os.close(root_fd)


def _settle_runner_observation(
    root: Path,
    reference: ManagedHeadlessSessionLineageRef,
    observation: NativeShellCaptureObservation,
) -> None:
    """Durably consume one marker after its lineage mutation has settled."""
    marker = {
        "schema_version": MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
        "launch_id": reference.launch_id,
        "lineage_digest": reference.lineage_digest,
        "observation": observation.to_dict(),
    }
    marker_name = f"{hashlib.sha256(_canonical_json(marker).encode('utf-8')).hexdigest()}.json"
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, directory_flags | nofollow)
    observations_fd = -1
    launch_fd = -1
    try:
        try:
            observations_fd = os.open(
                _RUNNER_OBSERVATIONS_DIR,
                directory_flags | nofollow,
                dir_fd=root_fd,
            )
            launch_fd = os.open(
                reference.launch_id,
                directory_flags | nofollow,
                dir_fd=observations_fd,
            )
            os.unlink(marker_name, dir_fd=launch_fd)
            os.fsync(launch_fd)
        except FileNotFoundError:
            return
    finally:
        if launch_fd >= 0:
            os.close(launch_fd)
        if observations_fd >= 0:
            os.close(observations_fd)
        os.close(root_fd)
