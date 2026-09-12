"""Backward-compat shim for _install_detect — see core.install.install_detect."""

from autoskillit.core.install.install_detect import (
    DirectUrlInfo,
    _is_release_tag,
    _is_stable_track,
    distribution_version_at,
    is_dev_install,
    parse_direct_url,
)

__all__ = [
    "DirectUrlInfo",
    "_is_release_tag",
    "_is_stable_track",
    "distribution_version_at",
    "is_dev_install",
    "parse_direct_url",
]
