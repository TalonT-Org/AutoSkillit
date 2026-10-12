"""Pin the standalone collector's identities and tools to product definitions."""

from pathlib import Path

import pytest

from autoskillit.core import DIRECT_PREFIX, PLUGIN_PREFIX
from autoskillit.core.types import GATED_TOOLS, UNGATED_TOOLS
from tests.infra._complexity_helpers import load_check_script

pytestmark = pytest.mark.medium

sessions = load_check_script(
    "_autoskillit_e2e_sessions_contract",
    Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "e2e_sessions.py",
)


def test_server_identities_match_canonical_prefixes() -> None:
    assert sessions._AUTOSKILLIT_SERVERS == frozenset(
        tuple(prefix.split("__")[:-1]) for prefix in (DIRECT_PREFIX, PLUGIN_PREFIX)
    )


def test_tracked_tools_are_registered_product_tools() -> None:
    assert sessions._TRACKED_TOOLS
    assert sessions._TRACKED_TOOLS <= GATED_TOOLS | UNGATED_TOOLS
