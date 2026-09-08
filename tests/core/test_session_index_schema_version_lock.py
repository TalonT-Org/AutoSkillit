"""Lock the session-index field set to its explicit schema version."""

from __future__ import annotations

import hashlib
import json

import pytest

from autoskillit.core import SESSION_INDEX_SCHEMA_VERSION
from autoskillit.core.types._type_results import SessionIndexEntry

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_EXPECTED = (10, "83c36e84773d76f2fea2d9f1d064d3cd49ea7afd07b793abc37c1bfb4d0b23ac")


def test_session_index_schema_version_matches_field_digest() -> None:
    digest = hashlib.sha256(
        json.dumps(sorted(SessionIndexEntry.__annotations__)).encode()
    ).hexdigest()
    actual = (SESSION_INDEX_SCHEMA_VERSION, digest)
    assert actual == _EXPECTED, (
        "SessionIndexEntry changed without a coordinated schema-version bump: "
        f"expected {_EXPECTED}, got {actual}"
    )
