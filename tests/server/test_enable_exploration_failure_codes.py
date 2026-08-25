"""Precondition-code matrix for enable_exploration (#4684 Fix A).

Each test monkeypatches exactly one precondition and asserts the tool
returns its own distinct ExplorationFailureCode. Before this matrix, the
catch-all at tools_exploration.py's enable_exploration collapsed every
downstream failure into the single opaque "exploration_provisioning_failed"
code; this file pins one test per named code so a future contributor who
re-introduces a catch-all breaks a test in the right direction.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.core import SessionType
from autoskillit.exploration import SnapshotCaptureLimits, SnapshotCaptureReason
from autoskillit.hooks._exploration_request_record import write_exploration_request_record
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore
from autoskillit.server import _exploration_service
from autoskillit.server._exploration_service import DefaultExplorationService
from autoskillit.server.tools.tools_exploration import enable_exploration

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _skill_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )


def _bound_store(
    tool_ctx, exploration_snapshot_service: MagicMock
) -> OwnerBoundExplorationContextStore[object]:
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx.project_dir,
        service=exploration_snapshot_service,
    )
    tool_ctx.exploration_context_store = store
    (tool_ctx.project_dir / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    return store


async def _bind_raising(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    exploration_snapshot_service: MagicMock,
    *,
    exc: BaseException,
    expected_code: str,
) -> None:
    _skill_session(monkeypatch)
    store = _bound_store(tool_ctx, exploration_snapshot_service)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )
    monkeypatch.setattr(store, "bind_session_scoped", MagicMock(side_effect=exc))
    result = json.loads(await enable_exploration(_autoskillit_exploration_request_token=token))
    assert result == {"status": "error", "code": expected_code}


@pytest.mark.asyncio
async def test_session_type_ineligible_returns_own_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.ORCHESTRATOR,
    )
    result = json.loads(await enable_exploration())
    assert result == {"status": "error", "code": "session_type_ineligible"}


@pytest.mark.asyncio
async def test_store_unavailable_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx
) -> None:
    _skill_session(monkeypatch)
    tool_ctx.exploration_context_store = MagicMock(spec=[])
    result = json.loads(await enable_exploration())
    assert result == {"status": "error", "code": "exploration_store_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "invalid", "A" * 43])
async def test_no_session_id_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, token: str
) -> None:
    _skill_session(monkeypatch)
    result = json.loads(await enable_exploration(_autoskillit_exploration_request_token=token))
    assert result == {"status": "error", "code": "no_session_id"}


@pytest.mark.asyncio
async def test_trusted_root_mismatch_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.TrustedRootMismatch("mismatch"),
        expected_code="trusted_root_mismatch",
    )


@pytest.mark.asyncio
async def test_invalid_source_identity_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.InvalidSourceIdentity("invalid"),
        expected_code="invalid_source_identity",
    )


@pytest.mark.asyncio
async def test_session_id_invalid_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.InvalidSessionBinding(
            "session_id must be a non-empty bounded string"
        ),
        expected_code="session_id_invalid",
    )


@pytest.mark.asyncio
async def test_empty_resolved_session_id_surfaces_session_id_invalid_not_bind_failed(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    """The _validate_binding promotion (Step 3), driven end-to-end and unmocked past
    session-id resolution: enable_exploration's own `if session_id is None` guard only
    rejects None, not an empty string, so a resolved-but-empty session_id reaches the
    real store's _validate_binding and must surface as session_id_invalid — the exact
    distinction that degraded to generic bind_failed before this promotion."""
    _skill_session(monkeypatch)
    _bound_store(tool_ctx, exploration_snapshot_service)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_request_session",
        MagicMock(return_value=""),
    )

    result = json.loads(await enable_exploration())

    assert result == {"status": "error", "code": "session_id_invalid"}


@pytest.mark.asyncio
async def test_service_not_configured_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.ServiceNotConfigured("no service"),
        expected_code="service_not_configured",
    )


@pytest.mark.asyncio
async def test_snapshot_stale_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.SnapshotStale(
            SnapshotCaptureReason.IDENTITY_DRIFT, "stale"
        ),
        expected_code="snapshot_stale",
    )


@pytest.mark.asyncio
async def test_snapshot_truncated_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.SnapshotTruncated(
            SnapshotCaptureReason.FILE_BYTES_EXCEEDED, "truncated"
        ),
        expected_code="snapshot_truncated",
    )


@pytest.mark.asyncio
async def test_snapshot_capture_failed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.SnapshotCaptureFailed(
            SnapshotCaptureReason.GIT_TIMEOUT, "capture failed"
        ),
        expected_code="snapshot_capture_failed",
    )


@pytest.mark.asyncio
async def test_store_closed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.StoreClosed("closed"),
        expected_code="store_closed",
    )


@pytest.mark.asyncio
async def test_capacity_exceeded_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.CapacityExceeded("full"),
        expected_code="capacity_exceeded",
    )


@pytest.mark.asyncio
async def test_bind_failed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    """An unclassified bind_session_scoped failure gets its own code, distinct

    from any of the five named store exceptions above and from the opaque
    catch-all this matrix replaces.
    """
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=RuntimeError("unclassified bind failure"),
        expected_code="bind_failed",
    )


@pytest.mark.asyncio
async def test_enable_components_failed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    _skill_session(monkeypatch)
    store = _bound_store(tool_ctx, exploration_snapshot_service)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )
    cleanup = MagicMock(wraps=store.cleanup_session)
    monkeypatch.setattr(store, "cleanup_session", cleanup)
    request_ctx = MagicMock()
    request_ctx.enable_components = AsyncMock(side_effect=RuntimeError("enable failed"))
    request_ctx.disable_components = AsyncMock()

    result = json.loads(
        await enable_exploration(
            _autoskillit_exploration_request_token=token,
            ctx=request_ctx,
        )
    )

    assert result == {"status": "error", "code": "enable_components_failed"}
    assert store.session_scoped_capability("test-session") is None
    cleanup.assert_called_once_with("test-session")
    request_ctx.disable_components.assert_awaited_once_with(tags={"exploration"})


@pytest.mark.asyncio
async def test_unexpected_internal_error_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx
) -> None:
    """A failure outside the bind/enable calls still fails closed with its own code."""
    _skill_session(monkeypatch)
    assert isinstance(tool_ctx.exploration_context_store, OwnerBoundExplorationContextStore)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_request_session",
        MagicMock(side_effect=RuntimeError("unclassified")),
    )
    result = json.loads(await enable_exploration())
    assert result == {"status": "error", "code": "unexpected_internal_error"}


def _seed_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "module.py").write_text("def needle() -> str:\n    return 'needle'\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "module.py"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AutoSkillit Test",
            "-c",
            "user.email=autoskillit@example.invalid",
            "commit",
            "-qm",
            "seed",
        ],
        cwd=root,
        check=True,
    )


@pytest.mark.asyncio
async def test_truncating_repository_surfaces_snapshot_truncated_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx
) -> None:
    """A real capture against a real repository names its own truncation code.

    Every other test in this module drives enable_exploration through a
    MagicMock service and a synthetic exception. This one runs the real
    DefaultExplorationService against a real fixture repository with limits
    that actually truncate, proving the code reaches the tool boundary
    without a mocked shortcut — 'bind_failed' before this part, per #4756.
    """
    _skill_session(monkeypatch)
    repository_root = tool_ctx.project_dir
    _seed_repository(repository_root)
    real_capture = _exploration_service.capture_repository_snapshot

    def truncating_capture(root, *, collector_manifest_digest, limits=None):
        return real_capture(
            root,
            collector_manifest_digest=collector_manifest_digest,
            limits=SnapshotCaptureLimits(max_file_bytes=4),
        )

    monkeypatch.setattr(_exploration_service, "capture_repository_snapshot", truncating_capture)
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=repository_root,
        service=DefaultExplorationService(),
    )
    tool_ctx.exploration_context_store = store
    (tool_ctx.project_dir / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )

    result = json.loads(await enable_exploration(_autoskillit_exploration_request_token=token))

    assert result == {"status": "error", "code": "snapshot_truncated"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_exception_type", "expected_reason"),
    (
        (
            OwnerBoundExplorationContextStore.SnapshotStale(
                SnapshotCaptureReason.IDENTITY_DRIFT, "stale"
            ),
            "snapshot_stale",
            "SnapshotStale",
            SnapshotCaptureReason.IDENTITY_DRIFT,
        ),
        (
            OwnerBoundExplorationContextStore.SnapshotTruncated(
                SnapshotCaptureReason.FILE_BYTES_EXCEEDED, "truncated"
            ),
            "snapshot_truncated",
            "SnapshotTruncated",
            SnapshotCaptureReason.FILE_BYTES_EXCEEDED,
        ),
        (
            OwnerBoundExplorationContextStore.SnapshotCaptureFailed(
                SnapshotCaptureReason.GIT_TIMEOUT, "capture failed"
            ),
            "snapshot_capture_failed",
            "SnapshotCaptureFailed",
            SnapshotCaptureReason.GIT_TIMEOUT,
        ),
    ),
    ids=("stale", "truncated", "capture_failed"),
)
async def test_snapshot_terminal_status_failure_logs_structured_fields(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    exploration_snapshot_service: MagicMock,
    exc: BaseException,
    expected_code: str,
    expected_exception_type: str,
    expected_reason: SnapshotCaptureReason,
) -> None:
    """T-C8: the failure detail must reach a sink an operator can actually read.

    A ``code``/``exception_type``/``reason`` field on the log record is only a
    diagnostic improvement if the record is genuinely emitted with those keys —
    this is the reachability half of #4756's fix, complementing the code-value
    assertions in test_snapshot_stale_returns_own_code and its FAILED/TRUNCATED
    siblings above, which check the tool's JSON response but never inspect the
    log record itself.

    Uses structlog.testing.capture_logs(), not caplog: the conftest's autouse
    _structlog_to_null fixture intercepts all structlog output before it would
    reach stdlib logging handlers, so caplog.records would silently stay empty
    regardless of what tools_exploration.py actually logs (see the identical
    caveat documented on
    test_run_skill_logs_warning_when_output_dir_resolved_from_recipe in
    tests/server/test_tools_execution_step_resolution.py).
    """
    import structlog.testing

    with structlog.testing.capture_logs() as cap:
        await _bind_raising(
            monkeypatch,
            tool_ctx,
            exploration_snapshot_service,
            exc=exc,
            expected_code=expected_code,
        )

    (entry,) = (e for e in cap if e.get("event") == "enable_exploration_store_failure")
    assert entry["code"] == expected_code
    assert entry["exception_type"] == expected_exception_type
    assert entry["reason"] == expected_reason
