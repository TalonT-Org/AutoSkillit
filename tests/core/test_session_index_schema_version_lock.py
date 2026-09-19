"""Lock the session-index field set to its explicit schema version."""

from __future__ import annotations

import hashlib
import json

import pytest

from autoskillit.core import SESSION_INDEX_SCHEMA_VERSION
from autoskillit.core.types._type_results import SessionIndexEntry

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_EXPECTED = (14, "58bf96b3a215d9d2b62500e03c1c805135f197086f4a9afdad0d9723d7f56cb3")


def test_session_index_schema_version_matches_field_digest() -> None:
    digest = hashlib.sha256(
        json.dumps(sorted(SessionIndexEntry.__annotations__)).encode()
    ).hexdigest()
    actual = (SESSION_INDEX_SCHEMA_VERSION, digest)
    assert actual == _EXPECTED, (
        "SessionIndexEntry changed without a coordinated schema-version bump: "
        f"expected {_EXPECTED}, got {actual}"
    )
