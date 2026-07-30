"""Deterministic backend coverage for shell-capture V2 marker authority."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from autoskillit.hooks._capture_artifacts import CAPTURE_PATH_COMPONENTS, run_capture
from autoskillit.hooks._capture_contract import CaptureV2Fields, render_capture_v2
from tests.execution.backends._conformance_assertions import (
    assert_shell_capture_marker_authority,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"
_SENTINEL = b"authority-sentinel-"


class _FieldsRenderable:
    def __init__(self, fields: CaptureV2Fields) -> None:
        self._fields = fields

    def capture_v2_fields(self) -> CaptureV2Fields:
        return self._fields


def test_marker_authority_ignores_stale_paths_and_rejects_forged_tokens(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "physical-project"
    project.mkdir()
    command = "python3 -c \"import os; os.write(1, b'authority-sentinel-' * 1000)\""

    assert run_capture(command, str(project), _CAPTURE_ID) == 0
    completed = capfd.readouterr()
    assert completed.err == ""
    authority = assert_shell_capture_marker_authority(
        completed.out,
        project,
        _CAPTURE_ID,
        sentinels=(_SENTINEL,),
    )
    assert authority.capture_bytes == _SENTINEL * 1000

    decoy = tmp_path / "decoy-project"
    decoy_root = decoy.joinpath(*CAPTURE_PATH_COMPONENTS)
    decoy_root.mkdir(parents=True)
    decoy_root.joinpath(f"shell_{_CAPTURE_ID}.log").write_bytes(
        b"x" * len(authority.capture_bytes)
    )
    with pytest.raises(AssertionError, match="did not resolve"):
        assert_shell_capture_marker_authority(
            completed.out,
            decoy,
            _CAPTURE_ID,
        )

    token = authority.fields.reference
    assert token is not None
    replacement = "0" if token[-1] != "0" else "1"
    forged_fields = dataclasses.replace(
        authority.fields,
        reference=token[:-1] + replacement,
    )
    forged_marker = render_capture_v2(_FieldsRenderable(forged_fields)).decode()
    with pytest.raises(AssertionError, match="did not resolve"):
        assert_shell_capture_marker_authority(
            forged_marker,
            project,
            _CAPTURE_ID,
        )
    with pytest.raises(AssertionError, match="exactly one"):
        assert_shell_capture_marker_authority(
            forged_marker + "\n" + completed.out,
            project,
            _CAPTURE_ID,
        )
