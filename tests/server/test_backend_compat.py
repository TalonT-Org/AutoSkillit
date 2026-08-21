from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.parametrize(
    ("malformation", "expected"),
    [
        (
            "undeclared_refusal",
            "backend 'test-backend' reported unsupported semantic operation "
            "'child_spawn' not declared by the semantic plan",
        ),
        (
            "incomplete_supported",
            "semantic adaptation omitted observable instructions",
        ),
    ],
)
def test_semantic_preflight_propagates_malformed_adapter_result(
    malformation: str,
    expected: str,
) -> None:
    from autoskillit.core import (
        GitMetadataWriteSpec,
        SkillContractError,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
        SkillSemanticPlan,
    )
    from autoskillit.server.tools._preflight import check_skill_semantic_feasibility

    plan = SkillSemanticPlan(
        schema_version=1,
        git_metadata_writes=(GitMetadataWriteSpec(purpose="create one commit"),),
    )
    if malformation == "undeclared_refusal":
        result = SkillSemanticAdaptationResult.unsupported(
            backend="test-backend",
            operation=SkillSemanticOperation.CHILD_SPAWN,
        )
    else:
        result = SkillSemanticAdaptationResult()
    backend = SimpleNamespace(
        name="test-backend",
        adapt_skill_semantics=lambda _plan: result,
    )

    with pytest.raises(SkillContractError, match=f"^{expected}$"):
        check_skill_semantic_feasibility((plan,), backend)
