"""Tests for the typed CLI install boundary contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from autoskillit.cli._install_contract import (
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallProcessStatus,
    InstallRequest,
    InstallResult,
    process_status_for_result,
    result_from_process_status,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _request(
    *,
    mode: InstallMode = InstallMode.DIRECT,
    require_registered_plugin: bool = True,
) -> InstallRequest:
    return InstallRequest(
        scope="user",
        mode=mode,
        require_registered_plugin=require_registered_plugin,
        expected_version="1.2.3",
    )


def test_process_status_values_are_stable_and_shell_safe() -> None:
    assert {status.name: status.value for status in InstallProcessStatus} == {
        "SUCCESS": 0,
        "DECLINED": 10,
        "DEFERRED": 11,
        "FAILED_PREFLIGHT": 20,
        "FAILED_CHILD": 21,
        "FAILED_POSTCONDITION": 22,
        "RECOVERY_REQUIRED": 23,
        "INDETERMINATE": 24,
    }
    assert all(0 <= status <= 127 for status in InstallProcessStatus)


def test_request_and_result_are_immutable_slots() -> None:
    request = _request()
    result = InstallResult(
        outcome=InstallOutcome.COMPLETED,
        verified_identity="sha256:abc",
        findings=("verified current plugin",),
    )

    assert not hasattr(request, "__dict__")
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(request, "scope", "project")
    with pytest.raises(FrozenInstanceError):
        setattr(result, "outcome", InstallOutcome.DECLINED)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scope", 1, "scope must be a string"),
        ("mode", "maintenance-update", "mode must be an InstallMode"),
        (
            "require_registered_plugin",
            1,
            "require_registered_plugin must be a boolean",
        ),
        ("expected_version", 1, "expected_version must be a string or None"),
    ],
)
def test_request_rejects_untyped_boundary_values(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        replace(_request(), **{field: value})


@pytest.mark.parametrize(
    ("install_request", "result", "expected_status"),
    [
        (
            _request(),
            InstallResult(
                InstallOutcome.COMPLETED,
                verified_identity="incarnation-completed",
                findings=("completed diagnostic",),
            ),
            InstallProcessStatus.SUCCESS,
        ),
        (
            _request(
                mode=InstallMode.MAINTENANCE_UPDATE,
                require_registered_plugin=False,
            ),
            InstallResult(
                InstallOutcome.NOT_REQUIRED,
                findings=("not-required diagnostic",),
            ),
            InstallProcessStatus.SUCCESS,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.DECLINED,
                findings=("declined diagnostic",),
            ),
            InstallProcessStatus.DECLINED,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.DEFERRED,
                findings=("deferred diagnostic",),
            ),
            InstallProcessStatus.DEFERRED,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.FAILED,
                InstallFailureKind.PREFLIGHT,
                findings=("preflight diagnostic",),
            ),
            InstallProcessStatus.FAILED_PREFLIGHT,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.FAILED,
                InstallFailureKind.CHILD,
                findings=("child diagnostic",),
            ),
            InstallProcessStatus.FAILED_CHILD,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.FAILED,
                InstallFailureKind.POSTCONDITION,
                findings=("postcondition diagnostic",),
            ),
            InstallProcessStatus.FAILED_POSTCONDITION,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.RECOVERY_REQUIRED,
                InstallFailureKind.ROLLBACK,
                findings=("recovery diagnostic",),
            ),
            InstallProcessStatus.RECOVERY_REQUIRED,
        ),
        (
            _request(),
            InstallResult(
                InstallOutcome.INDETERMINATE,
                findings=("indeterminate diagnostic",),
            ),
            InstallProcessStatus.INDETERMINATE,
        ),
    ],
)
def test_every_outcome_round_trips_with_diagnostic_evidence(
    install_request: InstallRequest,
    result: InstallResult,
    expected_status: InstallProcessStatus,
) -> None:
    status = process_status_for_result(result)
    assert status is expected_status

    reconstructed = result_from_process_status(
        int(status),
        install_request,
        verified_identity=result.verified_identity,
        findings=result.findings,
    )

    assert reconstructed == result
    assert reconstructed.findings


@pytest.mark.parametrize(
    ("status", "outcome", "failure_kind"),
    [
        (10, InstallOutcome.DECLINED, None),
        (11, InstallOutcome.DEFERRED, None),
        (20, InstallOutcome.FAILED, InstallFailureKind.PREFLIGHT),
        (21, InstallOutcome.FAILED, InstallFailureKind.CHILD),
        (22, InstallOutcome.FAILED, InstallFailureKind.POSTCONDITION),
        (23, InstallOutcome.RECOVERY_REQUIRED, InstallFailureKind.ROLLBACK),
        (24, InstallOutcome.INDETERMINATE, None),
    ],
)
def test_result_from_every_nonzero_process_status(
    status: int,
    outcome: InstallOutcome,
    failure_kind: InstallFailureKind | None,
) -> None:
    result = result_from_process_status(status, _request())
    assert result.outcome is outcome
    assert result.failure_kind is failure_kind


@pytest.mark.parametrize(
    ("mode", "require_registered_plugin", "expected"),
    [
        (InstallMode.DIRECT, True, InstallOutcome.COMPLETED),
        (InstallMode.DIRECT, False, InstallOutcome.COMPLETED),
        (InstallMode.MAINTENANCE_UPDATE, True, InstallOutcome.COMPLETED),
        (InstallMode.MAINTENANCE_UPDATE, False, InstallOutcome.NOT_REQUIRED),
    ],
)
def test_zero_status_is_interpreted_against_the_request(
    mode: InstallMode,
    require_registered_plugin: bool,
    expected: InstallOutcome,
) -> None:
    result = result_from_process_status(
        0,
        _request(
            mode=mode,
            require_registered_plugin=require_registered_plugin,
        ),
    )
    assert result.outcome is expected


@pytest.mark.parametrize("status", [-1, 1, 9, 12, 25, 127, 128])
def test_unknown_or_negative_status_is_indeterminate(status: int) -> None:
    result = result_from_process_status(status, _request())
    assert result == InstallResult(outcome=InstallOutcome.INDETERMINATE)


def test_result_reconstruction_preserves_identity_and_findings() -> None:
    result = result_from_process_status(
        InstallProcessStatus.FAILED_CHILD,
        _request(),
        verified_identity="sha256:abc",
        findings=("child exited 7", "rollback completed"),
    )
    assert result.verified_identity == "sha256:abc"
    assert result.findings == ("child exited 7", "rollback completed")


@pytest.mark.parametrize(
    ("outcome", "failure_kind"),
    [
        (InstallOutcome.FAILED, None),
        (InstallOutcome.FAILED, InstallFailureKind.ROLLBACK),
        (InstallOutcome.RECOVERY_REQUIRED, None),
        (InstallOutcome.RECOVERY_REQUIRED, InstallFailureKind.CHILD),
        (InstallOutcome.COMPLETED, InstallFailureKind.PREFLIGHT),
        (InstallOutcome.DECLINED, InstallFailureKind.CHILD),
    ],
)
def test_result_rejects_incoherent_failure_kind(
    outcome: InstallOutcome,
    failure_kind: InstallFailureKind | None,
) -> None:
    with pytest.raises(ValueError):
        InstallResult(outcome=outcome, failure_kind=failure_kind)


def test_result_rejects_mutable_or_non_string_findings() -> None:
    with pytest.raises(TypeError):
        InstallResult(outcome=InstallOutcome.COMPLETED, findings=["mutable"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        InstallResult(outcome=InstallOutcome.COMPLETED, findings=("ok", 1))  # type: ignore[arg-type]
