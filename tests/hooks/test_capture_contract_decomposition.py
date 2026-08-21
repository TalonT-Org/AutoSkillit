"""Step-2 facade re-exports + canonical-import preservation for #4726.

Each of these tests fails against the pre-Step-2 codebase because the moved
names do not yet exist at their new module locations.
"""

from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def test_v2_protocol_primitives_live_in_dedicated_module() -> None:
    from autoskillit.hooks._capture._v2_protocol import (
        parse_capture_v2,
        render_capture_v2,
    )

    assert callable(render_capture_v2)
    assert callable(parse_capture_v2)


def test_request_lineage_primitives_live_in_dedicated_module() -> None:
    from autoskillit.hooks._capture._request_lineage import (
        decode_capture_request,
        encode_capture_request,
    )

    assert callable(encode_capture_request)
    assert callable(decode_capture_request)


def test_facade_keeps_v3_envelope_names() -> None:
    from autoskillit.hooks._capture_contract import (
        _MAX_COMMAND_BYTES,
        render_capture_failure_v3,
    )

    assert callable(render_capture_failure_v3)
    assert isinstance(_MAX_COMMAND_BYTES, int)


def test_facade_re_exports_moved_v2_names() -> None:
    from autoskillit.hooks._capture_contract import (
        render_capture_v2,
    )

    assert callable(render_capture_v2)


def test_facade_re_exports_moved_lineage_names() -> None:
    from autoskillit.hooks._capture_contract import (
        encode_capture_request,
    )

    assert callable(encode_capture_request)


def test_v2_protocol_module_is_registered_under_both_spellings() -> None:
    """register_module_aliases(__name__) registers both spellings in sys.modules."""
    mod_dotted = importlib.import_module("autoskillit.hooks._capture._v2_protocol")
    mod_short = importlib.import_module("_capture._v2_protocol")
    assert mod_dotted is mod_short
    assert sys.modules.get("_capture._v2_protocol") is mod_dotted
    assert sys.modules.get("autoskillit.hooks._capture._v2_protocol") is mod_dotted


def test_request_lineage_module_is_registered_under_both_spellings() -> None:
    mod_dotted = importlib.import_module("autoskillit.hooks._capture._request_lineage")
    mod_short = importlib.import_module("_capture._request_lineage")
    assert mod_dotted is mod_short
    assert sys.modules.get("_capture._request_lineage") is mod_dotted
    assert sys.modules.get("autoskillit.hooks._capture._request_lineage") is mod_dotted


def test_facade_max_command_bytes_is_the_original_constant() -> None:
    """shell_capture_hook.py and test_shell_capture_hook.py/_capture_contract.py
    import _MAX_COMMAND_BYTES; this test confirms it remains a public facade
    symbol with the SAME value (64 * 1024) after Step 2."""
    from autoskillit.hooks import _capture_contract

    assert _capture_contract._MAX_COMMAND_BYTES == 64 * 1024
