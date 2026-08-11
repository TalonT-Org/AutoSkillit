"""Type-safety: passing a byte value where SerializedChars is expected must fail mypy."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_mypy_rejects_byte_value_as_serialized_chars(tmp_path: Path) -> None:
    snippet = textwrap.dedent("""
        from autoskillit.core.types._type_dimensions import SerializedChars, Utf8ByteLimit

        def accept_chars(c: SerializedChars) -> int:
            return c.value

        byte_val = Utf8ByteLimit(100)
        accept_chars(byte_val)  # type: ignore[arg-type] should catch this
    """)
    test_file = tmp_path / "test_mixing.py"
    test_file.write_text(snippet)
    result = subprocess.run(
        ["mypy", "--strict", str(test_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "arg-type" in result.stdout or result.returncode != 0, (
        f"mypy did not reject Utf8ByteLimit passed as SerializedChars:\n{result.stdout}"
    )
