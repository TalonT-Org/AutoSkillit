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
    from autoskillit.hooks._capture import _v2_protocol as canonical
    from autoskillit.hooks._capture_contract import (
        parse_capture_v2,
        render_capture_v2,
    )

    assert callable(render_capture_v2)
    assert callable(parse_capture_v2)
    assert render_capture_v2 is canonical.render_capture_v2
    assert parse_capture_v2 is canonical.parse_capture_v2


def test_request_lineage_primitives_live_in_dedicated_module() -> None:
    from autoskillit.hooks._capture import _request_lineage as canonical
    from autoskillit.hooks._capture_contract import (
        decode_capture_request,
        encode_capture_request,
    )

    assert callable(encode_capture_request)
    assert callable(decode_capture_request)
    assert encode_capture_request is canonical.encode_capture_request
    assert decode_capture_request is canonical.decode_capture_request


def test_facade_keeps_v3_envelope_names() -> None:
    from autoskillit.hooks._capture_contract import (
        _MAX_COMMAND_BYTES,
        render_capture_failure_v3,
    )

    assert callable(render_capture_failure_v3)
    assert isinstance(_MAX_COMMAND_BYTES, int)
    assert _MAX_COMMAND_BYTES == 64 * 1024


def test_facade_re_exports_moved_v2_names() -> None:
    from autoskillit.hooks._capture import _v2_protocol as canonical
    from autoskillit.hooks._capture_contract import (
        CaptureFailureV2,
        CaptureV2Fields,
        capture_v2_worst_case_bytes,
        parse_capture_failure_v2,
        parse_capture_v2,
        render_capture_failure_v2,
        render_capture_v2,
    )

    assert render_capture_v2 is canonical.render_capture_v2
    assert parse_capture_v2 is canonical.parse_capture_v2
    assert render_capture_failure_v2 is canonical.render_capture_failure_v2
    assert parse_capture_failure_v2 is canonical.parse_capture_failure_v2
    assert capture_v2_worst_case_bytes is canonical.capture_v2_worst_case_bytes
    assert CaptureV2Fields is canonical.CaptureV2Fields
    assert CaptureFailureV2 is canonical.CaptureFailureV2


def test_facade_re_exports_moved_lineage_names() -> None:
    from autoskillit.hooks._capture import _request_lineage as canonical
    from autoskillit.hooks._capture_contract import (
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        canonical_json_bytes,
        decode_capture_request,
        decode_lineage_ref_json,
        encode_capture_request,
    )

    assert encode_capture_request is canonical.encode_capture_request
    assert decode_capture_request is canonical.decode_capture_request
    assert decode_lineage_ref_json is canonical.decode_lineage_ref_json
    assert canonical_json_bytes is canonical.canonical_json_bytes
    assert CaptureLineageRef is canonical.CaptureLineageRef
    assert CaptureRequest is canonical.CaptureRequest
    assert CaptureProtocolError is canonical.CaptureProtocolError


def test_facade_v2_round_trip_through_re_exported_codecs() -> None:
    """End-to-end encode/decode round-trip preserves a published capture."""
    from autoskillit.hooks._capture_contract import (
        CaptureV2Fields,
        parse_capture_v2,
        render_capture_v2,
    )

    fields = CaptureV2Fields(
        capture_id="0123456789abcdef",
        finalized_at_revision=4,
        total_bytes=12001,
        sha256="b" * 64,
        command_outcome_kind="exited",
        command_outcome_value=0,
        shell_returncode=0,
        reference_status="unavailable",
        reference=None,
        unavailable_reason="PUBLICATION_BINDING_UNAVAILABLE",
    )

    class _Renderable:
        def __init__(self, value: CaptureV2Fields) -> None:
            self._value = value

        def capture_v2_fields(self) -> CaptureV2Fields:
            return self._value

    encoded = render_capture_v2(_Renderable(fields))
    assert parse_capture_v2(encoded) == fields


def test_facade_request_round_trip_through_re_exported_codecs() -> None:
    """End-to-end request encode/decode round-trip preserves a run request."""
    from autoskillit.hooks._capture_contract import (
        CaptureRequest,
        decode_capture_request,
        encode_capture_request,
    )

    request = CaptureRequest(
        protocol_version=1,
        action="run",
        mode="capture",
        attempt_id=None,
        lineage_ref=None,
        cwd="/command/cwd",
        capture_id="0123456789abcdef",
        command="printf hello",
    )

    assert decode_capture_request(encode_capture_request(request)) == request


def test_v2_protocol_module_is_registered_under_both_spellings() -> None:
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
    from autoskillit.hooks import _capture_contract

    assert _capture_contract._MAX_COMMAND_BYTES == 64 * 1024
