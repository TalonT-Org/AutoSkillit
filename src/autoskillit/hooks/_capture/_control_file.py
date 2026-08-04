"""Shared descriptor-relative publication for private capture control files."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def validate_private_file(value: os.stat_result, error: OSError) -> None:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink != 1
    ):
        raise error


def publish_private_file(
    root_fd: int,
    *,
    target_name: str,
    temp_prefix: str,
    payload: bytes,
    validate_file: Callable[[os.stat_result], None],
    write_all: Callable[[int, bytes], None],
) -> None:
    temp_name = f"{temp_prefix}{secrets.token_hex(8)}"
    fd = os.open(temp_name, _WRITE_FLAGS, 0o600, dir_fd=root_fd)
    try:
        validate_file(os.fstat(fd))
        write_all(fd, payload)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=root_fd)
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.replace(temp_name, target_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=root_fd)
        except OSError:
            pass
        raise
    os.fsync(root_fd)
