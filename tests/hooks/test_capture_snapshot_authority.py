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

import autoskillit.hooks._capture._snapshot as capture_snapshot
from autoskillit.hooks._capture._lifecycle_policy import CaptureStatus
from autoskillit.hooks._capture._replay import render_degraded_capture
from autoskillit.hooks._capture._snapshot import (
    CaptureAuthorityError,
    CaptureFinalManifest,
    CaptureMeasurement,
    CommandOutcome,
    VerifiedCaptureSnapshot,
    decode_capture_manifest_wire,
    encode_capture_final_manifest,
    parse_capture_reference,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture._types import (
    CaptureFailureEvidence,
    LegacyCleanupOnly,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"
_INCARNATION = "0123456789abcdef0123456789abcdef"


def test_lifecycle_only_types_have_canonical_owners() -> None:
    assert CaptureStatus.__module__ in {
        "_capture._lifecycle_policy",
        "autoskillit.hooks._capture._lifecycle_policy",
    }
    type_modules = {"_capture._types", "autoskillit.hooks._capture._types"}
    assert CaptureFailureEvidence.__module__ in type_modules
    assert LegacyCleanupOnly.__module__ in type_modules
    assert not hasattr(capture_snapshot, "CaptureStatus")
    assert not hasattr(capture_snapshot, "CaptureFailureEvidence")
    assert not hasattr(capture_snapshot, "LegacyCleanupOnly")


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
        token, reference_hash = capture_snapshot._issue_capture_reference(
            snapshot,
            expiry=1_500.0,
        )
        finalized = capture_snapshot._bind_finalized_snapshot(
            snapshot,
            reference_token=token,
            reference_hash=reference_hash,
            reference_expiry=1_500.0,
        )
        assert finalized.issuance is not None
        published = capture_snapshot._make_published_reference(finalized.issuance)
        unavailable = capture_snapshot._make_unavailable_reference(
            snapshot,
            "TEST_UNAVAILABLE",
        )
        values = (
            snapshot,
            snapshot.manifest,
            finalized,
            finalized.issuance,
            published,
            unavailable,
        )
        for value in values:
            with pytest.raises(CaptureAuthorityError):
                replace(value)
            with pytest.raises(CaptureAuthorityError):
                copy.copy(value)
            with pytest.raises(CaptureAuthorityError):
                copy.deepcopy(value)
            with pytest.raises(CaptureAuthorityError):
                pickle.dumps(value)
            constructor: Any = type(value)
            with pytest.raises(TypeError):
                constructor()
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
        bad_preview = replace(
            measurement,
            tail=b"X" + measurement.tail[1:],
        )
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


def test_descriptor_verification_rejects_nonregular_and_linked_carriers(
    tmp_path: Path,
) -> None:
    fifo = tmp_path / "capture.fifo"
    os.mkfifo(fifo, 0o600)
    fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    linked = tmp_path / "capture"
    linked.write_bytes(b"linked")
    linked.chmod(0o600)
    linked_fd = os.open(linked, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    hardlink = tmp_path / "capture.link"
    os.link(linked, hardlink)
    try:
        fifo_value = os.fstat(fifo_fd)
        with pytest.raises(CaptureAuthorityError, match="metadata changed"):
            verify_capture_snapshot(
                fd=fifo_fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(fifo_value.st_dev, fifo_value.st_ino),
                measurement=_measurement(b""),
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
        linked_value = os.fstat(linked_fd)
        with pytest.raises(CaptureAuthorityError, match="metadata changed"):
            verify_capture_snapshot(
                fd=linked_fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(linked_value.st_dev, linked_value.st_ino),
                measurement=_measurement(b"linked"),
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
    finally:
        os.close(linked_fd)
        os.close(fifo_fd)


def test_descriptor_verification_rejects_replaced_descriptor_and_early_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    original.chmod(0o600)
    replacement.chmod(0o600)
    fd = os.open(original, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    replacement_fd = os.open(
        replacement,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    original_value = os.fstat(fd)
    try:
        os.dup2(replacement_fd, fd)
        with pytest.raises(CaptureAuthorityError, match="identity changed"):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(original_value.st_dev, original_value.st_ino),
                measurement=_measurement(b"original"),
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )

        value = os.fstat(fd)
        monkeypatch.setattr(capture_snapshot.os, "pread", lambda *_args: b"")
        with pytest.raises(CaptureAuthorityError, match="ended early"):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(value.st_dev, value.st_ino),
                measurement=_measurement(b"replacement"),
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
    finally:
        os.close(replacement_fd)
        os.close(fd)


@pytest.mark.parametrize("field_name", ("inline", "head", "tail"))
def test_measurement_rejects_incorrect_preview_lengths(field_name: str) -> None:
    measurement = _measurement(b"abcdefghijklmno", inline_bytes=9)
    value = getattr(measurement, field_name)

    with pytest.raises(CaptureAuthorityError, match=field_name):
        replace(measurement, **{field_name: value[:-1]})


@pytest.mark.parametrize("field_name", ("inline", "head", "tail"))
def test_descriptor_verification_rejects_incorrect_preview_bytes(
    field_name: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "capture"
    data = b"abcdefghijklmno"
    path.write_bytes(data)
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    measurement = _measurement(data, inline_bytes=9)
    preview = getattr(measurement, field_name)
    mutated = replace(
        measurement,
        **{field_name: b"X" + preview[1:]},
    )
    value = os.fstat(fd)
    try:
        with pytest.raises(CaptureAuthorityError, match="preview changed"):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(value.st_dev, value.st_ino),
                measurement=mutated,
                command_outcome=CommandOutcome.exited(0),
                expected_revision=1,
                finalized_at=10.0,
                retention_deadline=20.0,
            )
    finally:
        os.close(fd)


def test_descriptor_verification_rejects_measurement_digest_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capture"
    data = b"digest"
    path.write_bytes(data)
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    value = os.fstat(fd)
    try:
        with pytest.raises(CaptureAuthorityError, match="content changed"):
            verify_capture_snapshot(
                fd=fd,
                capture_id=_CAPTURE_ID,
                incarnation=_INCARNATION,
                project_identity=(1, 2),
                root_identity=(3, 4),
                carrier_name=f"shell_{_CAPTURE_ID}.log",
                carrier_identity=(value.st_dev, value.st_ino),
                measurement=replace(_measurement(data), sha256="0" * 64),
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
        assert decoded.capture_status is CaptureStatus.COMPLETE
        with pytest.raises(CaptureAuthorityError, match="fields"):
            replace(decoded, schema_version=decoded.schema_version + 1)

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
        primitive = json.loads(encoded)
        primitive["capture_status"] = "partial"
        with pytest.raises(CaptureAuthorityError, match="fields"):
            decode_capture_manifest_wire(
                json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()
            )
    finally:
        os.close(fd)


def test_manifest_wire_rejects_full_strict_codec_matrix(tmp_path: Path) -> None:
    fd, snapshot = _verify(tmp_path / "capture", b"manifest")
    encoded = encode_capture_final_manifest(snapshot.manifest)
    primitive = json.loads(encoded)

    def canonical(value: object) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")

    mutations: list[dict[str, object]] = []
    missing = dict(primitive)
    del missing["producer"]
    mutations.append(missing)
    for field_name, value in (
        ("schema_version", 99),
        ("total_bytes", True),
        ("project_identity", [1]),
        ("capture_status", "pending"),
        ("command_outcome_value", True),
    ):
        changed = dict(primitive)
        changed[field_name] = value
        mutations.append(changed)
    bad_reference = dict(primitive)
    bad_reference["reference_hash"] = "A" * 64
    bad_reference["reference_expiry"] = 1_500.0
    mutations.append(bad_reference)

    try:
        for changed in mutations:
            with pytest.raises(CaptureAuthorityError):
                decode_capture_manifest_wire(canonical(changed))
        for invalid in (
            encoded[:-1],
            b"\xff",
            b"x" * (capture_snapshot.MAX_MANIFEST_BYTES + 1),
            encoded.replace(b'"finalized_at":1000.0', b'"finalized_at":NaN'),
            b'{"nested":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
        ):
            with pytest.raises(CaptureAuthorityError):
                decode_capture_manifest_wire(invalid)
    finally:
        os.close(fd)


@pytest.mark.parametrize("isolated_first", (True, False))
def test_package_and_isolated_import_orders_share_authority_modules(
    *,
    isolated_first: bool,
) -> None:
    hooks_dir = Path(__file__).parents[2] / "src" / "autoskillit" / "hooks"
    suffixes = (
        "_authority",
        "_descriptor",
        "_snapshot",
        "_reference",
        "_reader",
        "_ledger",
        "_ledger_view",
        "_lifecycle_policy",
        "_migration",
        "_store_port",
        "_types",
        "_sweep",
        "_sweep_cursor",
        "_delivery",
        "_replay",
        "_reconcile",
        "_resolver",
    )
    first = "_capture" if isolated_first else "autoskillit.hooks._capture"
    second = "autoskillit.hooks._capture" if isolated_first else "_capture"
    checks = "".join(
        f"a=importlib.import_module({first + '.' + suffix!r});"
        f"b=importlib.import_module({second + '.' + suffix!r});"
        "assert a is b;"
        for suffix in suffixes
    )
    code = f"import importlib,sys;sys.path.insert(0,{str(hooks_dir)!r});{checks}"
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env=production_interpreter_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "token",
    (
        "é" * 512,
        f"ascr2:{_CAPTURE_ID}:{_INCARNATION}:{'a' * 63}é",
        "a" * 193,
    ),
)
def test_reference_parser_rejects_non_ascii_and_oversized_input(token: str) -> None:
    with pytest.raises(CaptureAuthorityError, match="invalid capture reference"):
        parse_capture_reference(token)


def test_reference_parser_accepts_well_formed_token() -> None:
    token = f"ascr2:{_CAPTURE_ID}:{_INCARNATION}:{'a' * 64}"
    hint = parse_capture_reference(token)
    assert hint.capture_id == _CAPTURE_ID
    assert hint.incarnation == _INCARNATION
    assert hint.token == token


@pytest.mark.parametrize(
    "token",
    (
        "ascr2:not-hex:0123456789abcdef0123456789abcdef:" + "a" * 64,
        "ascr2:0123456789abcdef:not-hex:" + "a" * 64,
        "ascr2:0123456789abcdef:0123456789abcdef0123456789abcdef:" + "z" * 64,
        "ascr2:0123456789abcdef:0123456789abcdef0123456789abcdef:" + "a" * 63,
        "ascr2:0123456789abcdef:0123456789abcdef0123456789abcdef:" + "a" * 65,
        "wrong:0123456789abcdef:0123456789abcdef0123456789abcdef:" + "a" * 64,
        "ascr2:0123456789abcdef:" + "a" * 64,
    ),
)
def test_reference_parser_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(CaptureAuthorityError, match="invalid capture reference"):
        parse_capture_reference(token)


@pytest.mark.parametrize("bad_input", (None, 123, b"ascr2:00:00:00", [], {"x": 1}))
def test_reference_parser_rejects_non_string(bad_input: object) -> None:
    with pytest.raises(CaptureAuthorityError, match="invalid capture reference"):
        parse_capture_reference(bad_input)  # type: ignore[arg-type]


def test_reference_hash_is_stable_and_expiry_overwrite_changes_digest(
    tmp_path: Path,
) -> None:
    """_reference_hash must produce a 64-char hex digest that depends on reference_expiry."""

    fd, snapshot = _verify(tmp_path / "capture", b"hash stable bytes")
    try:
        token = f"ascr2:{_CAPTURE_ID}:{_INCARNATION}:{'a' * 64}"
        hash_default = capture_snapshot._reference_hash(token, snapshot.manifest)
        hash_with_expiry = capture_snapshot._reference_hash(
            token,
            snapshot.manifest,
            reference_expiry=1_500.0,
        )
        assert isinstance(hash_default, str) and len(hash_default) == 64
        assert isinstance(hash_with_expiry, str) and len(hash_with_expiry) == 64
        assert hash_default != hash_with_expiry
        repeated = capture_snapshot._reference_hash(token, snapshot.manifest)
        assert repeated == hash_default
    finally:
        os.close(fd)


def test_reference_hash_rejects_malformed_token(tmp_path: Path) -> None:
    """_reference_hash must reject tokens that fail parse_capture_reference."""

    fd, snapshot = _verify(tmp_path / "capture", b"hash bad token bytes")
    try:
        with pytest.raises(CaptureAuthorityError, match="invalid capture reference"):
            capture_snapshot._reference_hash("not-a-real-token", snapshot.manifest)
    finally:
        os.close(fd)


def test_bind_finalized_snapshot_rejects_non_verified_snapshot(tmp_path: Path) -> None:
    """_bind_finalized_snapshot must enforce the type guard on its snapshot argument."""

    fd, snapshot = _verify(tmp_path / "capture", b"bind guard bytes")
    try:
        with pytest.raises(CaptureAuthorityError, match="verified snapshot"):
            capture_snapshot._bind_finalized_snapshot(
                object(),  # type: ignore[arg-type]
                reference_token=None,
                reference_hash=None,
                reference_expiry=None,
            )
        with pytest.raises(CaptureAuthorityError, match="verified snapshot"):
            capture_snapshot._bind_finalized_snapshot(
                snapshot.manifest,  # type: ignore[arg-type]
                reference_token=None,
                reference_hash=None,
                reference_expiry=None,
            )
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("token", "digest"),
    (
        (None, "a" * 64),
        ("ascr2:0123456789abcdef:0123456789abcdef0123456789abcdef:" + "a" * 64, None),
    ),
)
def test_bind_finalized_snapshot_rejects_incomplete_reference(
    tmp_path: Path,
    token: str | None,
    digest: str | None,
) -> None:
    """XOR check: exactly one of (reference_token, reference_hash) must be None."""

    fd, snapshot = _verify(tmp_path / "capture", b"bind incomplete bytes")
    try:
        with pytest.raises(CaptureAuthorityError, match="incomplete issued reference"):
            capture_snapshot._bind_finalized_snapshot(
                snapshot,
                reference_token=token,
                reference_hash=digest,
                reference_expiry=1_500.0,
            )
    finally:
        os.close(fd)


def test_bind_finalized_snapshot_rejects_mismatched_hash(tmp_path: Path) -> None:
    """Hash mismatch must raise CaptureAuthorityError via hmac.compare_digest path."""

    fd, snapshot = _verify(tmp_path / "capture", b"bind hash mismatch bytes")
    try:
        token, _ = capture_snapshot._issue_capture_reference(
            snapshot,
            expiry=1_500.0,
        )
        with pytest.raises(CaptureAuthorityError, match="hash does not match"):
            capture_snapshot._bind_finalized_snapshot(
                snapshot,
                reference_token=token,
                reference_hash="0" * 64,
                reference_expiry=1_500.0,
            )
    finally:
        os.close(fd)


def test_bind_finalized_snapshot_binds_without_reference(tmp_path: Path) -> None:
    """Success: issuance=None when reference_token is None."""

    fd, snapshot = _verify(tmp_path / "capture", b"bind unreferenced bytes")
    try:
        finalized = capture_snapshot._bind_finalized_snapshot(
            snapshot,
            reference_token=None,
            reference_hash=None,
            reference_expiry=None,
        )
        assert finalized.issuance is None
        assert finalized.snapshot.manifest.capture_id == snapshot.manifest.capture_id
        assert finalized.snapshot.manifest.reference_hash is None
    finally:
        os.close(fd)


def test_bind_finalized_snapshot_binds_with_reference(tmp_path: Path) -> None:
    """Success: issuance is populated when reference_token + reference_hash are valid."""

    fd, snapshot = _verify(tmp_path / "capture", b"bind referenced bytes")
    try:
        token, reference_hash = capture_snapshot._issue_capture_reference(
            snapshot,
            expiry=1_500.0,
        )
        finalized = capture_snapshot._bind_finalized_snapshot(
            snapshot,
            reference_token=token,
            reference_hash=reference_hash,
            reference_expiry=1_500.0,
        )
        assert finalized.issuance is not None
        assert finalized.issuance.token == token
    finally:
        os.close(fd)


def test_make_published_reference_rejects_non_issued(tmp_path: Path) -> None:
    """_make_published_reference must enforce the type guard on issuance."""

    fd, snapshot = _verify(tmp_path / "capture", b"published guard bytes")
    try:
        with pytest.raises(CaptureAuthorityError, match="issued reference"):
            capture_snapshot._make_published_reference(object())  # type: ignore[arg-type]
        with pytest.raises(CaptureAuthorityError, match="issued reference"):
            capture_snapshot._make_published_reference(snapshot)  # type: ignore[arg-type]
    finally:
        os.close(fd)


def test_make_published_reference_constructs_with_factory_token(tmp_path: Path) -> None:
    """_make_published_reference must propagate the factory token to the published type."""

    fd, snapshot = _verify(tmp_path / "capture", b"published factory bytes")
    try:
        token, reference_hash = capture_snapshot._issue_capture_reference(
            snapshot,
            expiry=1_500.0,
        )
        finalized = capture_snapshot._bind_finalized_snapshot(
            snapshot,
            reference_token=token,
            reference_hash=reference_hash,
            reference_expiry=1_500.0,
        )
        assert finalized.issuance is not None
        published = capture_snapshot._make_published_reference(finalized.issuance)
        assert published.token == token
        assert published.snapshot.manifest.capture_id == snapshot.manifest.capture_id
        assert published.snapshot.manifest.reference_hash == reference_hash
    finally:
        os.close(fd)


def test_make_unavailable_reference_constructs_with_factory_token(tmp_path: Path) -> None:
    """_make_unavailable_reference must propagate the factory token to the unavailable type."""

    fd, snapshot = _verify(tmp_path / "capture", b"unavailable factory bytes")
    try:
        unavailable = capture_snapshot._make_unavailable_reference(
            snapshot,
            "TEST_UNAVAILABLE",
        )
        assert unavailable.reason_code == "TEST_UNAVAILABLE"
        assert unavailable.snapshot is snapshot
    finally:
        os.close(fd)


def test_reference_matches_validates_issued_reference_through_snapshot(tmp_path: Path) -> None:
    """Reader-side _reference_matches must accept issued digests and reject mismatches."""

    fd, snapshot = _verify(tmp_path / "capture", b"reader match bytes")
    try:
        token, reference_hash = capture_snapshot._issue_capture_reference(
            snapshot,
            expiry=1_500.0,
        )
        finalized = capture_snapshot._bind_finalized_snapshot(
            snapshot,
            reference_token=token,
            reference_hash=reference_hash,
            reference_expiry=1_500.0,
        )
        assert capture_snapshot._reference_matches(token, finalized.snapshot.manifest)
        assert not capture_snapshot._reference_matches("bogus-token", finalized.snapshot.manifest)
        assert not capture_snapshot._reference_matches(token, snapshot.manifest)
    finally:
        os.close(fd)


def test_render_degraded_capture_returns_inline_when_within_cap(tmp_path: Path) -> None:
    """render_degraded_capture returns measurement.inline when total_bytes <= inline_bytes."""

    fd, snapshot = _verify(tmp_path / "capture", b"within cap")
    try:
        assert snapshot.measurement.total_bytes <= snapshot.measurement.inline_bytes
        rendered = render_degraded_capture(snapshot, reason_code="TEST_REASON")
        assert rendered == snapshot.measurement.inline
    finally:
        os.close(fd)


def test_render_degraded_capture_renders_oversized_via_reference_factory(
    tmp_path: Path,
) -> None:
    """render_degraded_capture returns head+V2 unavailable marker+tail when total exceeds cap."""

    payload = b"oversized capture payload that exceeds the inline cap"
    inline_bytes = 4
    path = tmp_path / "capture"
    path.write_bytes(payload)
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    value = os.fstat(fd)
    measurement = CaptureMeasurement.from_bytes(payload, inline_bytes=inline_bytes)
    snapshot = verify_capture_snapshot(
        fd=fd,
        capture_id=_CAPTURE_ID,
        incarnation=_INCARNATION,
        project_identity=(101, 202),
        root_identity=(303, 404),
        carrier_name=f"shell_{_CAPTURE_ID}.log",
        carrier_identity=(value.st_dev, value.st_ino),
        measurement=measurement,
        command_outcome=CommandOutcome.exited(0),
        expected_revision=3,
        finalized_at=1_000.0,
        retention_deadline=2_000.0,
    )
    try:
        assert snapshot.measurement.total_bytes > snapshot.measurement.inline_bytes
        rendered = render_degraded_capture(
            snapshot,
            reason_code="TEST_OVERSIZED",
        )
        head = snapshot.measurement.head
        tail = snapshot.measurement.tail
        assert rendered.startswith(head)
        assert rendered.endswith(tail)
        assert b"TEST_OVERSIZED" in rendered
        assert b"unavailable" in rendered
    finally:
        os.close(fd)


def test_render_degraded_capture_rejects_non_verified_snapshot(tmp_path: Path) -> None:
    """render_degraded_capture must reject inputs that are not VerifiedCaptureSnapshot."""

    payload = b"guard the degraded path"
    fd, snapshot = _verify(tmp_path / "capture", payload)
    try:
        with pytest.raises(Exception, match="verified snapshot"):
            render_degraded_capture(snapshot.manifest, reason_code="TEST_GUARD")  # type: ignore[arg-type]
    finally:
        os.close(fd)
