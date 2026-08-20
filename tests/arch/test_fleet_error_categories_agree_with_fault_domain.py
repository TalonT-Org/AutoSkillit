"""Cross-layer agreement between ``FaultDomain`` and fleet ``ErrorCodeCategory``.

``autoskillit.core.types.FaultDomain`` (populated at IL-0 exception-classification
sites via ``InfrastructureFaultError``) and ``autoskillit.fleet.state_types.
ErrorCodeCategory`` (populated at the fleet layer via ``_ERROR_CODE_CATEGORIES``)
are two independent taxonomies answering the same infrastructure-vs-logic
question at different layers. Nothing in the type system forces them to stay
in lexical or semantic agreement — this file is that guard.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    FaultDomain,
    FleetErrorCode,
    InfrastructureFaultError,
    ProcessStaleError,
)
from autoskillit.fleet.state_types import ErrorCodeCategory, get_error_category

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_fault_domain_and_error_code_category_share_string_values() -> None:
    """FaultDomain and ErrorCodeCategory must use the identical string vocabulary.

    Both enums independently declare LOGIC/INFRASTRUCTURE members with the
    same string values ("logic" / "infrastructure") rather than one importing
    the other. This test is the lexical tripwire: if either enum's member
    values drift (renamed, respelled, or a new member added to only one
    side), this fails immediately instead of the two taxonomies silently
    diverging into separate vocabularies.
    """
    fault_domain_values = {member.value for member in FaultDomain}
    error_code_category_values = {member.value for member in ErrorCodeCategory}

    assert fault_domain_values == {"logic", "infrastructure"}
    assert error_code_category_values == {"logic", "infrastructure"}
    assert fault_domain_values == error_code_category_values


# Confirmed exception<->FleetErrorCode correspondences. Populated only with
# pairs verified against an actual raise/catch site in src/ — see the
# docstring on the consuming test for how each pair was confirmed and what
# is deliberately left out.
_CONFIRMED_INFRASTRUCTURE_FAULT_PAIRS: list[tuple[type[Exception], FleetErrorCode]] = [
    # fleet/_api.py:499 catches ProcessStaleError and maps it to
    # FleetErrorCode.FLEET_PROCESS_STALE.
    (ProcessStaleError, FleetErrorCode.FLEET_PROCESS_STALE),
]


@pytest.mark.parametrize(
    "exception_type,fleet_error_code",
    _CONFIRMED_INFRASTRUCTURE_FAULT_PAIRS,
    ids=[pair[0].__name__ for pair in _CONFIRMED_INFRASTRUCTURE_FAULT_PAIRS],
)
def test_infrastructure_fault_error_subclasses_agree_with_fleet_categorization(
    exception_type: type[Exception], fleet_error_code: FleetErrorCode
) -> None:
    """IL-0 InfrastructureFaultError classification must agree with fleet ErrorCodeCategory.

    ``tests/fleet/test_error_code_categorization.py::TestAllFleetErrorCodesHaveCategory``
    only asserts every ``FleetErrorCode`` has *some* explicit category — it says
    nothing about whether that category agrees with the IL-0
    ``InfrastructureFaultError`` classification for the exception that produced
    it. This test is the cross-layer complement: for each exception type that
    is both an ``InfrastructureFaultError`` subclass AND has a confirmed
    corresponding ``FleetErrorCode`` (verified against an actual catch site in
    src/, not inferred from naming), both layers must independently classify
    it as INFRASTRUCTURE.

    Known, documented residual gap (not fixed here): several
    ``InfrastructureFaultError`` subclasses — ``StaleGeneratorError``,
    ``PluginArtifactContentionError``, ``PluginArtifactUnavailableError`` —
    have no corresponding named ``FleetErrorCode``. Exceptions with no
    corresponding FleetErrorCode — the two layers can still disagree about
    anything the fleet layer has no code for.
    """
    assert issubclass(exception_type, InfrastructureFaultError)
    assert get_error_category(fleet_error_code) is ErrorCodeCategory.INFRASTRUCTURE
