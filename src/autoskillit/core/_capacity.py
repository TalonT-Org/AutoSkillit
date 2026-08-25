"""Filesystem capacity probe seam.

Root-level, stdlib-only IL-0 module -- mirrors ``core/_cmd_runner.py``'s Protocol +
default-free-function + defaulted-parameter template. Lives here, not in
``core/runtime/_reclamation.py``, because ``core.runtime`` already imports from
``core.types`` (``artifact_lease.py``), and ``core.types``'s ``TestRunner`` Protocol needs
``SpaceProbe``/``default_space_probe`` as a real (non-type-checking-only) default value --
importing from ``core.runtime`` there would be a circular import.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "MIN_FREE_BYTES_THRESHOLD",
    "SpaceProbe",
    "default_space_probe",
    "platform_temp_root",
]


@runtime_checkable
class SpaceProbe(Protocol):
    """Callable that reports (total, used, free) bytes for the filesystem holding `path`.

    Injectable so a capacity check can be exercised without a real full filesystem.
    """

    def __call__(self, path: Path) -> tuple[int, int, int]: ...


def default_space_probe(path: Path) -> tuple[int, int, int]:
    """Real `shutil.disk_usage()`-backed SpaceProbe, as a (total, used, free) triple."""
    usage = shutil.disk_usage(path)
    return (usage.total, usage.used, usage.free)


def platform_temp_root() -> Path:
    """The platform temp root a capacity preflight should probe: /dev/shm on Linux, /tmp
    elsewhere -- the same root Taskfile.yml's PYTEST_TMP_ROOT resolves to. Shared so every
    capacity-preflight caller (DefaultTestRunner.check_infrastructure, the doctor check)
    names the identical mount rather than each re-deriving the platform branch.
    """
    return Path("/dev/shm") if sys.platform == "linux" else Path("/tmp")


#: Below this many free bytes on the mount holding a reclaimable store's platform root, a
#: capacity preflight reports exhaustion rather than letting the run proceed to an opaque
#: OSError(ENOSPC) mid-collection. 2 GiB is a conservative floor relative to the 20 GiB
#: /dev/shm allocation measured during this rectify -- comfortably above the bytes a single
#: pytest generation needs to start, comfortably below "still plenty of room".
MIN_FREE_BYTES_THRESHOLD = 2_000_000_000
