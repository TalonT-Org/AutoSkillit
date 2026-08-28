"""Issue #4905: every shard of _api_orchestration is at most 750 lines.

Decomposition acceptance criterion from #4860 / PR #4877 carried forward.
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "recipe"

_TARGETS: tuple[str, ...] = (
    "_api_orchestration.py",
    "_api_orchestration_types.py",
    "_api_orchestration_text.py",
    "_api_orchestration_cache.py",
    "_api_orchestration_match.py",
    "_api_orchestration_parse.py",
    "_api_orchestration_validate.py",
    "_api_orchestration_assemble.py",
)
_LINE_CEILING = 750


@pytest.mark.parametrize("rel_path", _TARGETS)
def test_api_orchestration_shard_under_750_lines(rel_path: str) -> None:
    target = SRC_ROOT / rel_path
    assert target.exists(), f"Missing shard: {rel_path}"
    line_count = len(target.read_text(encoding="utf-8").splitlines())
    assert line_count <= _LINE_CEILING, (
        f"{rel_path} is {line_count} lines, exceeds the {_LINE_CEILING}-line ceiling"
    )
