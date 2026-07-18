"""Tests for capture-mode summarize_capture in _process_io."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SpillSpec
from autoskillit.execution.process._process_io import (
    CaptureReadError,
    summarize_capture,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

SPEC = SpillSpec(inline_max_chars=100, head_chars=40, tail_chars=30)


def test_summarize_capture_small_inlines_full_text(tmp_path: Path) -> None:
    f = tmp_path / "small.txt"
    f.write_text("hello world\n")
    result = summarize_capture(f, SPEC)
    assert result.inline_text == "hello world\n"
    assert result.total_bytes == 12
    assert len(result.sha256) == 64
    assert result.complete is True


def test_summarize_capture_large_returns_bounded_slices(tmp_path: Path) -> None:
    content = "A" * 200
    f = tmp_path / "large.txt"
    f.write_text(content)
    result = summarize_capture(f, SPEC)
    assert result.inline_text is None
    assert result.total_bytes == 200
    assert len(result.head) <= 40
    assert len(result.tail) <= 30
    assert result.head == "A" * 40
    assert result.tail == "A" * 30


def test_summarize_capture_multibyte_boundary_replaces(tmp_path: Path) -> None:
    head_part = "X" * 38 + "é"
    tail_part = "é" + "Y" * 28
    content = head_part + "Z" * 100 + tail_part
    f = tmp_path / "multi.txt"
    f.write_bytes(content.encode("utf-8"))
    spec = SpillSpec(inline_max_chars=50, head_chars=40, tail_chars=30)
    result = summarize_capture(f, spec)
    assert result.inline_text is None
    assert isinstance(result.head, str)
    assert isinstance(result.tail, str)


def test_summarize_capture_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CaptureReadError):
        summarize_capture(tmp_path / "missing.txt", SPEC)


def test_summarize_capture_incomplete_flag(tmp_path: Path) -> None:
    f = tmp_path / "partial.txt"
    f.write_text("partial output")
    result = summarize_capture(f, SPEC, complete=False)
    assert result.complete is False
