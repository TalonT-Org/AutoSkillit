"""Tests for lossless artifact-backed output spilling."""

from __future__ import annotations

import hashlib
import os
import re

import pytest

from autoskillit.core import SpillSpec, spill_output

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_small_output_stays_inline_without_artifact(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    result = spill_output("small", artifact_dir, "stdout", SpillSpec(inline_max_chars=5))

    assert result.spilled is False
    assert result.text == "small"
    assert result.artifact_path is None
    assert not artifact_dir.exists()


def test_large_output_is_published_losslessly_with_metadata(tmp_path):
    text = "αβγ\n" + ("middle" * 100) + "\ntail"
    result = spill_output(
        text,
        tmp_path,
        "command output",
        SpillSpec(inline_max_chars=20, head_chars=8, tail_chars=7),
    )

    assert result.spilled is True
    assert result.artifact_path is not None
    artifact = tmp_path / os.path.basename(result.artifact_path)
    assert artifact.read_text() == text
    assert re.fullmatch(r"command_output_[0-9a-f]{8}\.log", artifact.name)
    assert result.sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert result.total_chars == len(text)
    assert result.total_utf8_bytes == len(text.encode())
    assert result.total_lines == len(text.splitlines())
    assert result.head == text[:8]
    assert result.tail == text[-7:]
    if os.name == "posix":
        assert artifact.stat().st_mode & 0o777 == 0o600


def test_publication_failure_returns_no_path_or_partial_file(tmp_path, monkeypatch):
    from autoskillit.core import io

    def fail_write(*_args, **_kwargs):
        raise OSError("ENOSPC")

    monkeypatch.setattr(io, "atomic_write", fail_write)
    with pytest.raises(OSError, match="ENOSPC"):
        spill_output("x" * 100, tmp_path, "stdout", SpillSpec(inline_max_chars=10))
    assert list(tmp_path.iterdir()) == []
