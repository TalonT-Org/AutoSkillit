"""Capture artifact construction, policy resolution, and publication binding.

The setup failure boundary for a shell capture: everything that must succeed
before a command body may run — descriptor-anchored artifact creation, the
on-disk capture policy, and the re-verification that an issued reference still
binds to the artifact it was minted for. Kept apart from `_runner.py` so a
setup failure is attributable to this module alone; `_runner.py` owns the
command body and its settlement.

stdlib-only; supports the three shell-capture import spellings.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import InitVar, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _authority, _reader, _snapshot, _types  # noqa: I001
    from autoskillit.hooks._capture._authority import CAPTURE_PATH_COMPONENTS
    from autoskillit.hooks import _capture_contract, _capture_lifecycle
    from autoskillit.hooks._runtime import _hook_settings
    from autoskillit.hooks._capture._module_identity import register_module_aliases
elif __package__ == "_capture":
    from _capture import _authority, _reader, _snapshot, _types  # noqa: I001
    from _capture._authority import CAPTURE_PATH_COMPONENTS
    import _capture_contract
    import _capture_lifecycle
    from _runtime import _hook_settings  # noqa: I001 — bare-name post-move
    from _capture._module_identity import register_module_aliases
else:
    from . import _authority, _reader, _snapshot, _types  # noqa: I001
    from ._authority import CAPTURE_PATH_COMPONENTS
    from .. import _capture_contract, _capture_lifecycle
    from .._runtime import _hook_settings
    from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_DEFAULT_INLINE_BYTES = 12_000
_MAX_INLINE_BYTES = 1_000_000
_MAX_POLICY_FILE_BYTES = 64 * 1024
_CAPTURE_RUNTIME_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    subprocess.SubprocessError,
    RuntimeError,
    TypeError,
    UnicodeError,
    ValueError,
)
_ARTIFACT_FACTORY_TOKEN = object()

_DIRECTORY_FLAGS = _authority._DIRECTORY_FLAGS
_READ_FLAGS = _authority._READ_FLAGS
CaptureRoot = _authority.CaptureRoot
CaptureSetupError = _authority.CaptureSetupError
FileIdentity = _authority.FileIdentity
ProjectAnchor = _authority.ProjectAnchor
_open_directory_component = _authority._open_directory_component
_same_identity = _authority._same_identity
VerifiedCaptureReader = _reader.VerifiedCaptureReader
CaptureWriteAuthority = _snapshot.CaptureWriteAuthority
FinalizedCapture = _snapshot.FinalizedCapture
IssuedCaptureReference = _snapshot.IssuedCaptureReference
CaptureCapacitySpec = _types.CaptureCapacitySpec
CaptureLifecycleStore = _capture_lifecycle.CaptureLifecycleStore
CaptureCapacityError = _capture_lifecycle.CaptureCapacityError
CaptureLifecycleError = _capture_lifecycle.CaptureLifecycleError
CaptureTransitionCommittedError = _capture_lifecycle.CaptureTransitionCommittedError
_CAPTURE_ID_RE = _capture_contract._CAPTURE_ID_RE
CaptureFailureReason = _capture_contract.CaptureFailureReason
HOOK_CONFIG_FILENAME = _hook_settings.HOOK_CONFIG_FILENAME
HOOK_CONFIG_OVERLAY_FILENAME = _hook_settings.HOOK_CONFIG_OVERLAY_FILENAME
merge_hook_configs = _hook_settings.merge_hook_configs


@dataclass(frozen=True, slots=True)
class CaptureArtifact:
    fd: int
    name: str
    identity: FileIdentity
    lease_fd: int
    authority: CaptureWriteAuthority
    drain_writer_fd: int = -1
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _ARTIFACT_FACTORY_TOKEN:
            raise CaptureSetupError.authority(
                "CaptureArtifact must be created by create_capture_artifact"
            )

    def close_artifact_fd(self) -> None:
        if self.fd >= 0:
            descriptor = self.fd
            object.__setattr__(self, "fd", -1)
            os.close(descriptor)

    def release_lease(self) -> None:
        if self.lease_fd >= 0:
            descriptor = self.lease_fd
            object.__setattr__(self, "lease_fd", -1)
            os.close(descriptor)

    def close_drain_writer(self) -> None:
        if self.drain_writer_fd >= 0:
            descriptor = self.drain_writer_fd
            object.__setattr__(self, "drain_writer_fd", -1)
            os.close(descriptor)

    def transfer_to_reader(
        self,
        lifecycle: CaptureLifecycleStore,
        finalized: FinalizedCapture,
    ) -> VerifiedCaptureReader:
        if self.drain_writer_fd >= 0:
            raise CaptureSetupError.authority("capture drain writer is still open")
        if self.fd < 0 or self.lease_fd < 0:
            raise CaptureSetupError.authority("capture artifact ownership is unavailable")
        carrier_fd = self.fd
        lease_fd = self.lease_fd
        object.__setattr__(self, "fd", -1)
        object.__setattr__(self, "lease_fd", -1)
        try:
            os.close(lease_fd)
        except OSError as exc:
            try:
                os.close(carrier_fd)
            except OSError:
                pass
            raise CaptureSetupError.from_os_error(
                exc, "cannot transfer capture carrier lease"
            ) from exc
        return lifecycle._adopt_verified_capture(finalized, carrier_fd)


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    disabled: bool = False
    inline_bytes: int = _DEFAULT_INLINE_BYTES
    capacity: CaptureCapacitySpec | None = None


def create_capture_artifact(
    root: CaptureRoot,
    capture_id: str,
    lifecycle: CaptureLifecycleStore,
) -> CaptureArtifact:
    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        raise CaptureSetupError.unknown("invalid capture id")
    try:
        fd, lease_fd, public_name, raw_identity, authority = lifecycle.create_artifact(capture_id)
        identity = FileIdentity(device=raw_identity[0], inode=raw_identity[1])
        return CaptureArtifact(
            fd=fd,
            name=public_name,
            identity=identity,
            lease_fd=lease_fd,
            authority=authority,
            _factory_token=_ARTIFACT_FACTORY_TOKEN,
        )
    except CaptureCapacityError as exc:
        raise CaptureSetupError(
            exc.failure_reason,
            "capture capacity is exhausted",
        ) from exc
    except (CaptureLifecycleError, CaptureTransitionCommittedError) as exc:
        raise CaptureSetupError(
            CaptureFailureReason.LEDGER_INTEGRITY, "cannot create managed capture artifact"
        ) from exc
    except OSError as exc:
        raise CaptureSetupError.from_os_error(
            exc, "cannot create managed capture artifact"
        ) from exc


def _duplicate_artifact_writer(artifact: CaptureArtifact) -> int:
    writer_fd = -1
    try:
        if artifact.fd < 0 or artifact.drain_writer_fd >= 0:
            raise CaptureSetupError.authority("capture drain writer ownership is unavailable")
        writer_fd = os.dup(artifact.fd)
        if not _same_identity(writer_fd, artifact.identity):
            raise CaptureSetupError.authority("duplicated capture artifact identity changed")
        object.__setattr__(artifact, "drain_writer_fd", writer_fd)
        return writer_fd
    except (CaptureSetupError, OSError) as exc:
        if writer_fd >= 0:
            try:
                os.close(writer_fd)
            except OSError:
                pass
        if isinstance(exc, CaptureSetupError):
            raise
        raise CaptureSetupError.from_os_error(exc, "cannot duplicate capture artifact fd") from exc


def _read_bounded_file_at(directory_fd: int, name: str) -> dict[str, object]:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=directory_fd)
    except OSError:
        return {}
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_mode & stat.S_IWOTH:
            return {}
        data = bytearray()
        while len(data) <= _MAX_POLICY_FILE_BYTES:
            chunk = os.read(fd, min(8192, _MAX_POLICY_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_POLICY_FILE_BYTES:
            return {}
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {}
    finally:
        os.close(fd)


def _policy_inline_bytes(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return _DEFAULT_INLINE_BYTES
    return min(value, _MAX_INLINE_BYTES)


def _policy_capacity(value: object) -> CaptureCapacitySpec | None:
    """Parse an optional ``capture_capacity`` dict from hook config.

    Accepts any nonempty subset of ``CaptureCapacitySpec`` field names
    (partial overrides are the normal case).  Invalid values, unknown
    keys, or non-dict input → ``None`` (fail-safe to defaults).
    """
    if not isinstance(value, dict) or not value:
        return None
    valid_fields = set(CaptureCapacitySpec.__dataclass_fields__)
    validated: dict[str, int] = {}
    for key, val in value.items():
        if key not in valid_fields:
            return None
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            return None
        validated[key] = val
    if not validated:
        return None
    try:
        candidate = replace(CaptureCapacitySpec(), **validated)
    except (TypeError, ValueError):
        return None
    if (
        candidate.compaction_low_bytes < _types.REQUIRED_RETENTION_BYTES
        or candidate.hard_ledger_bytes > _capture_lifecycle.MAX_LEDGER_BYTES
    ):
        return None
    return candidate


def read_capture_policy(anchor: ProjectAnchor) -> CapturePolicy:
    autoskillit_fd = -1
    temp_fd = -1
    try:
        try:
            autoskillit_fd = _open_directory_component(
                anchor.fd, CAPTURE_PATH_COMPONENTS[0], create=False
            )
            temp_fd = _open_directory_component(
                autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], create=False
            )
        except CaptureSetupError:
            return CapturePolicy()
        base = _read_bounded_file_at(temp_fd, HOOK_CONFIG_FILENAME)
        overlay = _read_bounded_file_at(temp_fd, HOOK_CONFIG_OVERLAY_FILENAME)
        merged = merge_hook_configs(base, overlay)
        section = merged.get("output_budget_policy", {})
        if not isinstance(section, dict):
            section = {}
        return CapturePolicy(
            disabled=section.get("disabled") is True,
            inline_bytes=_policy_inline_bytes(section.get("shell_max_inline_bytes")),
            capacity=_policy_capacity(section.get("capture_capacity")),
        )
    finally:
        try:
            if temp_fd >= 0:
                os.close(temp_fd)
        finally:
            if autoskillit_fd >= 0:
                os.close(autoskillit_fd)


def _open_and_match_directory(parent_fd: int, name: str, expected: FileIdentity) -> int:
    try:
        fd = _open_directory_component(parent_fd, name, create=False)
    except CaptureSetupError:
        return -1
    try:
        matches = _same_identity(fd, expected)
    except BaseException:
        os.close(fd)
        raise
    if not matches:
        os.close(fd)
        return -1
    return fd


def verify_reference_publication_binding(
    anchor: ProjectAnchor,
    root: CaptureRoot,
    artifact: CaptureArtifact,
    issuance: IssuedCaptureReference,
) -> bool:
    """Verify that an issued tuple still resolves through the retained authorities."""

    if type(issuance) is not IssuedCaptureReference:
        raise CaptureSetupError.unknown("publication binding requires an issued reference")
    manifest = issuance.snapshot.manifest
    if (
        manifest.project_identity != (anchor.identity.device, anchor.identity.inode)
        or manifest.root_identity != (root.identity.device, root.identity.inode)
        or manifest.carrier_name != artifact.name
        or manifest.carrier_identity != (artifact.identity.device, artifact.identity.inode)
    ):
        return False
    opened: list[int] = []
    try:
        try:
            project_path = Path(os.path.realpath(anchor.supplied_path))
            project_fd = os.open(
                project_path,
                _DIRECTORY_FLAGS,
            )
        except OSError:
            return False
        opened.append(project_fd)
        if not _same_identity(project_fd, anchor.identity):
            return False

        autoskillit_fd = _open_and_match_directory(
            project_fd, CAPTURE_PATH_COMPONENTS[0], root.autoskillit_identity
        )
        if autoskillit_fd < 0:
            return False
        opened.append(autoskillit_fd)

        temp_fd = _open_and_match_directory(
            autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], root.temp_identity
        )
        if temp_fd < 0:
            return False
        opened.append(temp_fd)

        capture_fd = _open_and_match_directory(temp_fd, CAPTURE_PATH_COMPONENTS[2], root.identity)
        if capture_fd < 0:
            return False
        opened.append(capture_fd)

        try:
            current_artifact_fd = os.open(artifact.name, _READ_FLAGS, dir_fd=capture_fd)
        except OSError:
            return False
        opened.append(current_artifact_fd)
        current_value = os.fstat(current_artifact_fd)
        if (
            FileIdentity.from_stat(current_value) != artifact.identity
            or not stat.S_ISREG(current_value.st_mode)
            or current_value.st_nlink != 1
            or current_value.st_mode & stat.S_IWOTH
        ):
            return False
        return True
    finally:
        for fd in reversed(opened):
            os.close(fd)
