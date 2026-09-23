"""Validated projection of an installed Codex bundled model catalog."""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import atomic_write, write_versioned_json
from autoskillit.execution.process._lifecycle.owned_group import (
    OwnedProcessGroup,
    spawn_owned_process,
)
from autoskillit.execution.process._process_tether import TetherSpec

_BUNDLED_TOOL_MODE = "code_mode_only"
_DIRECT_TOOL_MODE = "direct"
_BUNDLED_APPLY_PATCH_TOOL_TYPE = "freeform"
_DISABLED_APPLY_PATCH_TOOL_TYPE = None
_STREAM_CHUNK = 64 * 1024
CODEX_CATALOG_LIMIT = 2_000_000
_STDERR_LIMIT = 64 * 1024


class CodexCatalogAcquisitionError(RuntimeError):
    """A fail-closed bundled-catalog acquisition error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CodexProcessOutput:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class CodexCatalogProjection:
    """Canonical bytes and identities for one validated catalog projection."""

    canonical_projected_bytes: bytes
    bundled_sha256: str
    projected_sha256: str


def deadline_remaining(deadline: float) -> float:
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(deadline)
    ):
        raise CodexCatalogAcquisitionError("deadline_invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CodexCatalogAcquisitionError("deadline_exceeded")
    return remaining


def _drain_bounded_output(
    selector: selectors.BaseSelector,
    owner: OwnedProcessGroup,
    output: dict[str, bytearray],
    deadline: float,
    stdout_limit: int,
    *,
    remaining: Callable[[float], float],
) -> None:
    while selector.get_map() or owner.observe_exit() is None:
        available = remaining(deadline)
        if not selector.get_map():
            time.sleep(min(0.01, available))
            continue
        for key, _ in selector.select(min(0.1, available)):
            descriptor = key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
            chunk = os.read(descriptor, _STREAM_CHUNK)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            limit = stdout_limit if key.data == "stdout" else _STDERR_LIMIT
            if len(output[key.data]) + len(chunk) > limit:
                raise CodexCatalogAcquisitionError("stream_limit_exceeded")
            output[key.data].extend(chunk)


def run_owned_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline: float,
    stdout_limit: int,
    capture_dir: Path | None = None,
    spawn: Callable[..., OwnedProcessGroup] = spawn_owned_process,
    remaining: Callable[[float], float] = deadline_remaining,
    selector_factory: Callable[[], selectors.BaseSelector] = selectors.DefaultSelector,
) -> CodexProcessOutput:
    """Run one Codex command with the evidence-reader process contract."""
    try:
        owner = spawn(
            tuple(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            tether=TetherSpec(origin="evidence_reader", ceiling_seconds=3600.0),
        )
    except OSError as exc:
        raise CodexCatalogAcquisitionError("codex_unavailable") from exc
    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector: selectors.BaseSelector | None = None
    try:
        selector = selector_factory()
        assert owner.process.stdout is not None and owner.process.stderr is not None
        selector.register(owner.process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(owner.process.stderr, selectors.EVENT_READ, "stderr")
        _drain_bounded_output(
            selector,
            owner,
            output,
            deadline,
            stdout_limit,
            remaining=remaining,
        )
        returncode, cleanup = owner.settle_evidence(timeout=min(2.0, remaining(deadline)))
        if returncode is None or not cleanup.complete:
            raise CodexCatalogAcquisitionError("process_cleanup_incomplete")
        return CodexProcessOutput(
            returncode,
            bytes(output["stdout"]),
            bytes(output["stderr"]),
        )
    except BaseException as exc:
        cleanup = owner.settle_preserving(
            exc, timeout=min(2.0, max(0.0, deadline - time.monotonic()))
        )
        if not cleanup.complete:
            raise CodexCatalogAcquisitionError("process_cleanup_incomplete") from exc
        raise
    finally:
        if selector is not None:
            selector.close()
        for stream in (owner.process.stdout, owner.process.stderr):
            if stream is not None:
                stream.close()
        if capture_dir is not None:
            capture_dir.mkdir(parents=True, exist_ok=True)
            write_versioned_json(
                capture_dir / "command.json",
                {"argv": list(command), "cwd": str(cwd)},
                schema_version=1,
            )
            for name, data in output.items():
                atomic_write(capture_dir / f"{name}.txt", bytes(data))


def acquire_bundled_codex_catalog(
    codex: str,
    *,
    scratch_root: Path,
    environment: Mapping[str, str],
    deadline: float,
    runner: Callable[..., CodexProcessOutput] = run_owned_bounded,
) -> bytes:
    """Acquire bundled catalog bytes in an isolated, always-removed scratch cwd."""
    scratch: Path | None = None
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
        mode = scratch_root.lstat().st_mode
        resolved_root = scratch_root.resolve(strict=True)
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CodexCatalogAcquisitionError("scratch_invalid")
        scratch = Path(tempfile.mkdtemp(prefix="bundled-catalog-", dir=resolved_root))
        scratch.chmod(0o700)
    except (CodexCatalogAcquisitionError, OSError) as exc:
        if scratch is not None:
            try:
                shutil.rmtree(scratch)
            except OSError as cleanup_exc:
                raise CodexCatalogAcquisitionError("cleanup_incomplete") from cleanup_exc
        if isinstance(exc, CodexCatalogAcquisitionError):
            raise
        raise CodexCatalogAcquisitionError("scratch_invalid") from exc
    assert scratch is not None
    try:
        home = scratch / "home"
        codex_home = home / ".codex"
        sqlite_home = scratch / "sqlite"
        for directory in (home, codex_home, sqlite_home):
            directory.mkdir(mode=0o700)
        probe_environment = dict(environment)
        probe_environment.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "CODEX_SQLITE_HOME": str(sqlite_home),
            }
        )
        result = runner(
            (codex, "debug", "models", "--bundled"),
            cwd=scratch,
            environment=probe_environment,
            deadline=deadline,
            stdout_limit=CODEX_CATALOG_LIMIT,
        )
        if result.returncode != 0 or result.stderr:
            raise CodexCatalogAcquisitionError("catalog_probe_failed")
        return result.stdout
    finally:
        try:
            shutil.rmtree(scratch)
        except OSError as exc:
            raise CodexCatalogAcquisitionError("cleanup_incomplete") from exc
        if os.path.lexists(scratch):
            raise CodexCatalogAcquisitionError("cleanup_incomplete")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _sha256_identity(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _parse_catalog(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        models = parsed["models"]
    except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Codex bundled models catalog is malformed") from exc
    if not isinstance(parsed, dict) or not isinstance(models, list):
        raise ValueError("Codex bundled model catalog has no model list")
    if any(
        not isinstance(model, dict) or not isinstance(model.get("slug"), str) for model in models
    ):
        raise ValueError("Codex bundled model catalog has a malformed model entry")
    return parsed, models


def _selected_model(models: list[dict[str, Any]], expected_model: str) -> dict[str, Any]:
    matching = [model for model in models if model["slug"] == expected_model]
    if len(matching) != 1:
        raise ValueError(f"Codex bundled model catalog must contain exactly one {expected_model}")
    return matching[0]


def resolve_codex_catalog_effort(raw: bytes, *, expected_model: str) -> str:
    """Resolve one installed model's advertised default reasoning effort."""
    if not expected_model:
        raise ValueError("Codex catalog effort resolution requires a model")
    _, models = _parse_catalog(raw)
    model = _selected_model(models, expected_model)
    effort = model.get("default_reasoning_level")
    if not isinstance(effort, str) or not effort:
        raise ValueError(f"{expected_model} has no default reasoning level")
    levels = model.get("supported_reasoning_levels")
    if not isinstance(levels, list) or effort not in {
        entry.get("effort") for entry in levels if isinstance(entry, dict)
    }:
        raise ValueError(f"{expected_model} default reasoning level is not supported")
    return effort


def project_codex_catalog(
    raw: bytes,
    *,
    expected_model: str,
    expected_reasoning_effort: str,
) -> CodexCatalogProjection:
    """Validate and project one installed model to direct MCP-only tool dispatch.

    The bundled catalog must expose the known ``code_mode_only``/``freeform``
    surface for exactly one requested model and advertise the requested effort.
    The projection changes only that model's tool mode and built-in apply-patch
    type, preserving every other catalog byte semantically through canonical JSON.
    """
    if not expected_model or not expected_reasoning_effort:
        raise ValueError("Codex catalog projection requires a model and reasoning effort")
    parsed, models = _parse_catalog(raw)
    model = _selected_model(models, expected_model)
    efforts = model.get("supported_reasoning_levels")
    if not isinstance(efforts, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("effort"), str)
        for entry in efforts
    ):
        raise ValueError(f"{expected_model} has malformed supported reasoning levels")
    if expected_reasoning_effort not in {entry["effort"] for entry in efforts}:
        raise ValueError(
            f"{expected_model} does not advertise {expected_reasoning_effort} reasoning"
        )
    if model.get("tool_mode") != _BUNDLED_TOOL_MODE:
        raise ValueError(f"{expected_model} bundled tool_mode must be {_BUNDLED_TOOL_MODE!r}")
    if model.get("apply_patch_tool_type") != _BUNDLED_APPLY_PATCH_TOOL_TYPE:
        raise ValueError(
            f"{expected_model} bundled apply_patch_tool_type must be "
            f"{_BUNDLED_APPLY_PATCH_TOOL_TYPE!r}"
        )

    canonical_bundled = _canonical_json_bytes(parsed)
    model["tool_mode"] = _DIRECT_TOOL_MODE
    model["apply_patch_tool_type"] = _DISABLED_APPLY_PATCH_TOOL_TYPE
    canonical_projected = _canonical_json_bytes(parsed)
    return CodexCatalogProjection(
        canonical_projected_bytes=canonical_projected,
        bundled_sha256=_sha256_identity(canonical_bundled),
        projected_sha256=_sha256_identity(canonical_projected),
    )
