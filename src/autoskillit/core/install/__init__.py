"""IL-0 install detection and sync subprocess runner primitives.

Exposes the canonical public surface of `install_detect` and `cmd_runner`
through the `autoskillit.core.install` namespace. Backward-compat shims at
`core/_install_detect.py` and `core/_cmd_runner.py` preserve old import paths.
"""

from __future__ import annotations

from autoskillit.core.install.cmd_runner import (
    CmdRunner,
    default_cmd_runner,
    run_gh,
    run_git,
)
from autoskillit.core.install.install_detect import (
    DirectUrlInfo,
    _is_release_tag,
    _is_stable_track,
    distribution_version_at,
    is_dev_install,
    parse_direct_url,
)

__all__ = [
    "CmdRunner",
    "DirectUrlInfo",
    "_is_release_tag",
    "_is_stable_track",
    "default_cmd_runner",
    "distribution_version_at",
    "is_dev_install",
    "parse_direct_url",
    "run_gh",
    "run_git",
]
