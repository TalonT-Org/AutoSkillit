"""Type-safety: SerializedChars and Utf8ByteLimit are nominally distinct.

Both are ``int`` subclasses — runtime arithmetic works transparently.
Cross-unit misuse is caught by mypy (static type checking). This test
verifies the runtime properties: distinct types, correct `.value`,
and construction-time validation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_serialized_chars_and_utf8_bytes_are_distinct_int_subclasses() -> None:
    """SerializedChars and Utf8ByteLimit are distinct types (same int base)."""
    from autoskillit.core import SerializedChars, Utf8ByteLimit

    chars = SerializedChars(100)
    bytes_ = Utf8ByteLimit(100)

    # Same numeric value, distinct types — both are int subclasses
    assert type(chars) is not type(bytes_)
    assert isinstance(chars, int) and isinstance(bytes_, int)
    assert chars == bytes_  # same numeric value via int comparison
    assert chars.value == bytes_.value == 100


def test_serialized_chars_rejects_negative() -> None:
    from autoskillit.core import SerializedChars

    with pytest.raises(ValueError, match="non-negative"):
        SerializedChars(-1)


def test_serialized_chars_allows_zero() -> None:
    from autoskillit.core import SerializedChars

    assert SerializedChars(0).value == 0


@pytest.mark.medium
def test_mypy_rejects_cross_unit_assignment(tmp_path: Path) -> None:
    """mypy rejects a SerializedChars where Utf8ByteLimit is expected, and vice versa.

    The tests above prove nominal distinctness at runtime; the actual enforcement
    of the boundary these wrapper types exist for — refusing to silently swap a
    byte-domain value for a char-domain one (or vice versa) at a typed call site —
    is a mypy-only guarantee. This drives mypy against a stand-alone snippet (no
    ``from __future__ import annotations``, so mypy evaluates the annotations
    directly rather than as deferred strings) and asserts it rejects both
    directions of the mismatch.
    """
    mypy_path = shutil.which("mypy")
    if mypy_path is None:
        pytest.skip("mypy not on PATH")

    snippet = """
from autoskillit.core.types._type_dimensions import SerializedChars, Utf8ByteLimit


def take_bytes(limit: Utf8ByteLimit) -> None:
    pass


def take_chars(count: SerializedChars) -> None:
    pass


take_bytes(SerializedChars(100))  # should be rejected: SerializedChars is not Utf8ByteLimit
take_chars(Utf8ByteLimit(100))  # should be rejected: Utf8ByteLimit is not SerializedChars
"""
    snippet_path = tmp_path / "cross_unit_snippet.py"
    snippet_path.write_text(snippet)

    # MYPYPATH points at src/ so the snippet resolves against source directly,
    # with no dependency on an editable/wheel install of autoskillit.
    src_dir = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "MYPYPATH": str(src_dir)}
    result = subprocess.run(
        [
            mypy_path,
            "--ignore-missing-imports",
            # The assertions below match plain substrings. Without this, mypy
            # honours FORCE_COLOR/COLORTERM from the inherited environment and
            # interleaves ANSI escapes inside the quoted type names.
            "--no-color-output",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(snippet_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0, (
        f"mypy unexpectedly accepted the cross-unit assignment:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert 'Argument 1 to "take_bytes" has incompatible type "SerializedChars"' in result.stdout, (
        result.stdout
    )
    assert 'Argument 1 to "take_chars" has incompatible type "Utf8ByteLimit"' in result.stdout, (
        result.stdout
    )
