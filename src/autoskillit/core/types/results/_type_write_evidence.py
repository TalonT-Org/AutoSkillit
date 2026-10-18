"""Write evidence and delegated-write target contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

__all__ = [
    "DelegatedOutputScope",
    "DelegatedWriteObservation",
    "DelegatedWriteTarget",
    "WriteEvidence",
]


@dataclass(frozen=True, slots=True)
class DelegatedWriteTarget:
    """A canonical absolute target path and its invocation content baseline."""

    path: str
    baseline_digest: str | None

    @staticmethod
    def _content_digest(path: str) -> str | None:
        """Hash file bytes.

        Metadata changes and identical rewrites do not establish a content change.
        Unexpected I/O errors propagate rather than becoming an absent baseline.
        """
        candidate = Path(path)
        try:
            if not S_ISREG(candidate.stat().st_mode):
                return None
            return hashlib.sha256(candidate.read_bytes()).hexdigest()
        except (FileNotFoundError, NotADirectoryError):
            return None

    @classmethod
    def bind(cls, path: str) -> DelegatedWriteTarget:
        """Capture the invocation baseline.

        Admission also needs a successful owned tool observation; this is not an
        exclusive filesystem lock or proof of the writer's identity.
        """
        return cls(path=path, baseline_digest=cls._content_digest(path))

    def changed(self) -> bool:
        current_digest = self._content_digest(self.path)
        return current_digest is not None and current_digest != self.baseline_digest


@dataclass(frozen=True, slots=True)
class DelegatedOutputScope:
    """Canonical output root and regular files observed during a completed scan.

    The baseline is not an atomic snapshot or an attestation of artifact bytes.
    """

    root: str
    preexisting: frozenset[str]

    def target_for(self, path: str) -> DelegatedWriteTarget | None:
        if path == self.root or not Path(path).is_relative_to(self.root):
            return None
        if path in self.preexisting:
            return None
        return DelegatedWriteTarget(path=path, baseline_digest=None)


@dataclass(frozen=True, slots=True)
class DelegatedWriteObservation:
    """A parsed child write-tool observation."""

    agent_tool_use_id: str
    agent_type: str
    tool_use_id: str
    tool_name: str
    file_path: str
    succeeded: bool


@dataclass(frozen=True, slots=True)
class WriteEvidence:
    """Bundled write evidence signals — either explicitly constructed or absent.

    Owned child paths await declared-output admission and are never evidence alone.
    """

    write_call_count: int
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes_count: int = 0
    delegated_write_count: int = 0
    delegated_write_paths: tuple[str, ...] = ()

    @classmethod
    def none_observed(cls) -> WriteEvidence:
        return cls(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=0,
        )

    @property
    def has_evidence(self) -> bool:
        return (
            self.write_call_count >= 1
            or self.fs_writes_detected
            or self.git_writes_detected
            or self.file_changes_count >= 1
            or self.delegated_write_count >= 1
        )

    @property
    def has_implementation_evidence(self) -> bool:
        return (
            self.write_call_count >= 1
            or self.file_changes_count >= 1
            or self.delegated_write_count >= 1
        )
