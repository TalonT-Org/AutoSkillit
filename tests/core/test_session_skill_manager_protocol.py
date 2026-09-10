"""Retained skill documents are restored through the session home owner."""

from pathlib import Path
from typing import get_type_hints

import pytest

from autoskillit.core import (
    SessionSkillManager,
    SkillProjectionContextAuthority,
    ValidatedAddDir,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_snapshot_restore_preserves_explicit_home_ownership_contract() -> None:
    assert get_type_hints(SessionSkillManager.restore_snapshot_session) == {
        "session_id": str,
        "snapshot_dir": Path,
        "projection_context": SkillProjectionContextAuthority,
        "return": ValidatedAddDir,
    }
