"""Factory and descriptor authority tests for finalized shell captures."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pickle
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autoskillit.hooks._capture._snapshot import (
    CaptureAuthorityError,
    CaptureFinalManifest,
    CaptureMeasurement,
    CommandOutcome,
    VerifiedCaptureSnapshot,
    decode_capture_manifest_wire,
    encode_capture_final_manifest,
    verify_capture_snapshot,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"
_INCARNATION = "0123456789abcdef0123456789abcdef"


def _measurement(data: bytes, *, inline_bytes: int = 12) -> CaptureMeasurement:
    return CaptureMeasurement.from_bytes(data, inline_bytes=inline_bytes)


def _verify(path: Path, data: bytes) -> tuple[int, VerifiedCaptureSnapshot]:
    path.write_bytes(data)
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    value = os.fstat(fd)
    snapshot = verify_capture_snapshot(
        fd=fd,
        capture_id=_CAPTURE_ID,
        incarnation=_INCARNATION,
        project_identity=(101, 202),
        root_identity=(303, 404),
        carrier_name=f"shell_{_CAPTURE_ID}.log",
        carrier_identity=(value.st_dev, value.st_ino),
        measurement=_measurement(data),
        command_outcome=CommandOutcome.exited(0),
        expected_revision=3,
        finalized_at=1_000.0,
        retention_deadline=2_000.0,
    )
    return fd, snapshot


def test_authority_values_reject_construction_copy_and_pickle(tmp_path: Path) -> None:
    fd, snapshot = _verify(tmp_path / "capture", b"verified bytes")
    try:
        with pytest.raises(CaptureAuthorityError):
            replace(snapshot)
        with pytest.raises(CaptureAuthorityError):
            replace(snapshot.manifest)
        for value in (snapshot, snapshot.manifest):
            with pytest.raises(CaptureAuthorityError):
                copy.copy(value)
            with pytest.raises(CaptureAuthorityError):
                copy.deepcopy(value)
            with pytest.raises(CaptureAuthorityError):
                pickle.dumps(value)
        manifest_constructor: Any = CaptureFinalManifest
        snapshot_constructor: Any = VerifiedCaptureSnapshot
        with pytest.raises(TypeError):
            manifest_constructor()
        with pytest.raises(TypeError):
            snapshot_constructor()
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda fd: os.write(fd, b"!"), "metadata changed"),
        (lambda fd: os.ftruncate(fd, 3), "metadata changed"),
        (
            lambda fd: os.pwrite(fd, b"X", 0),
            "content changed",
        ),
    ),
)
def test_descriptor_verification_rejects_mutation(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    path = tmp_path / "capture"
    data = b"descriptor snapshot"
    path.write_bytes(data)
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        value = os.fstat(fd)
        measurement = _measurement(data)
        os.lseek(fd, 0, os.SEEK_END)
        mutation(fd)
        with pytest.raises(CaptureAuthorityError, match=message):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(value.st_dev, value.st_ino),
                measurement=measurement,
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
    finally:
        os.close(fd)


def test_descriptor_verification_rejects_wrong_identity_and_preview(tmp_path: Path) -> None:
    path = tmp_path / "capture"
    data = b"abcdefghijklmno"
    path.write_bytes(data)
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        value = os.fstat(fd)
        measurement = _measurement(data)
        with pytest.raises(CaptureAuthorityError, match="identity changed"):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(value.st_dev, value.st_ino + 1),
                measurement=measurement,
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
        bad_preview = replace(measurement, tail=b"wrong")
        with pytest.raises(CaptureAuthorityError, match="preview changed"):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(value.st_dev, value.st_ino),
                measurement=bad_preview,
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
    finally:
        os.close(fd)


def test_manifest_wire_decode_is_strict_and_non_authoritative(tmp_path: Path) -> None:
    fd, snapshot = _verify(tmp_path / "capture", b"manifest")
    try:
        encoded = encode_capture_final_manifest(snapshot.manifest)
        decoded = decode_capture_manifest_wire(encoded)
        assert not isinstance(decoded, CaptureFinalManifest)
        assert decoded.sha256 == hashlib.sha256(b"manifest").hexdigest()

        primitive = json.loads(encoded)
        primitive["extra"] = True
        with pytest.raises(CaptureAuthorityError, match="fields"):
            decode_capture_manifest_wire(
                json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()
            )
        with pytest.raises(CaptureAuthorityError, match="canonical"):
            decode_capture_manifest_wire(json.dumps(json.loads(encoded)).encode())
        with pytest.raises(CaptureAuthorityError, match="duplicate"):
            decode_capture_manifest_wire(encoded[:-1] + b',"schema_version":2}')
    finally:
        os.close(fd)


def test_package_and_isolated_import_orders_share_one_snapshot_module() -> None:
    hooks_dir = Path(__file__).parents[2] / "src" / "autoskillit" / "hooks"
    code = (
        "import importlib,sys;"
        f"sys.path.insert(0,{str(hooks_dir)!r});"
        "a=importlib.import_module('_capture._snapshot');"
        "b=importlib.import_module('autoskillit.hooks._capture._snapshot');"
        "assert a is b"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
