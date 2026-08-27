"""Size-ceiling guard for the session-skill and projected-artifact decomposition.

Every original facade file AND every new source shard named in the
implementation plan must be at most 750 physical lines. The list is
explicit (not a directory glob) so an unbudgeted shard cannot silently land.
"""

from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.small]

_LINE_CEILING = 750


_SIZE_CEILING_FILES: tuple[tuple[str, int], ...] = (
    # Both original facade files (now identity-preserving compatibility surfaces)
    ("workspace/session_skills.py", _LINE_CEILING),
    ("workspace/_projected_artifact/materialization.py", _LINE_CEILING),
    # Five new session-skill shards
    ("workspace/session_skill_catalog.py", _LINE_CEILING),
    ("workspace/session_skill_provider.py", _LINE_CEILING),
    ("workspace/session_skill_lifecycle.py", _LINE_CEILING),
    ("workspace/session_skill_materialization.py", _LINE_CEILING),
    ("workspace/session_skill_manager.py", _LINE_CEILING),
    # Three new projected-artifact shards
    ("workspace/_projected_artifact/_documents.py", _LINE_CEILING),
    ("workspace/_projected_artifact/_publication.py", _LINE_CEILING),
    ("workspace/_projected_artifact/_validation.py", _LINE_CEILING),
)


@pytest.mark.parametrize(
    ("relative_path", "ceiling"),
    _SIZE_CEILING_FILES,
    ids=[p for p, _ in _SIZE_CEILING_FILES],
)
def test_module_under_size_ceiling(relative_path: str, ceiling: int) -> None:
    """Each named module file must exist and be at most 750 lines."""
    abs_path = SRC_ROOT / relative_path
    assert abs_path.is_file(), f"{relative_path} must exist as a regular file after decomposition"
    line_count = len(abs_path.read_text().splitlines())
    assert line_count <= ceiling, (
        f"{relative_path} is {line_count} lines, exceeds the {ceiling}-line ceiling. "
        f"Move a complete responsibility to an already-planned owner rather than "
        f"splitting functions mechanically or adding another exemption."
    )
