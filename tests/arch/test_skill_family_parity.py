"""Parametrized parity tests for GitHub API skill families.

Each family defines required API patterns; every member must satisfy them.
"""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.core.types import GITHUB_API_SKILL_FAMILIES, SkillFamilyDef

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _read_skill_md(skill_name: str) -> str:
    path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    if not path.exists():
        path = pkg_root() / "skills" / skill_name / "SKILL.md"
    assert path.exists(), f"SKILL.md not found for {skill_name}"
    return path.read_text()


def _family_members() -> list[tuple[str, str, SkillFamilyDef]]:
    result: list[tuple[str, str, SkillFamilyDef]] = []
    for family in GITHUB_API_SKILL_FAMILIES:
        for member in sorted(family.members):
            result.append((family.name, member, family))
    return result


@pytest.fixture(params=_family_members(), ids=lambda t: f"{t[0]}:{t[1]}")
def family_skill(request: pytest.FixtureRequest) -> tuple[str, str, SkillFamilyDef]:
    return request.param


class TestGraphqlBatchAliases:
    @pytest.fixture(autouse=True)
    def _filter(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        if "graphql-batch-aliases" not in family_skill[2].required_patterns:
            pytest.skip("graphql-batch-aliases not required for this family")

    def test_uses_batch_aliases(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        text_lower = text.lower()
        assert "alias" in text_lower or "resolve1:" in text or "resolve${" in text, (
            f"{family_skill[1]} must use batched aliased GraphQL mutations"
        )


class TestMutatingCallDelay:
    @pytest.fixture(autouse=True)
    def _filter(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        if "mutating-call-delay" not in family_skill[2].required_patterns:
            pytest.skip("mutating-call-delay not required for this family")

    def test_has_sleep_delay(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        assert "sleep 1" in text or "sleep(1)" in text, (
            f"{family_skill[1]} must include sleep 1 between mutating API calls"
        )


class TestUnpostablePrefilter:
    @pytest.fixture(autouse=True)
    def _filter(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        if "unpostable-prefilter" not in family_skill[2].required_patterns:
            pytest.skip("unpostable-prefilter not required for this family")

    def test_has_unpostable_bucket(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        assert "UNPOSTABLE" in text, (
            f"{family_skill[1]} must include UNPOSTABLE_FINDINGS pre-classification"
        )


class TestResponseBodyGuard:
    @pytest.fixture(autouse=True)
    def _filter(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        if "response-body-guard" not in family_skill[2].required_patterns:
            pytest.skip("response-body-guard not required for this family")

    def test_has_response_body_guard(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        text_lower = text.lower()
        assert "http 200" in text_lower, f"{family_skill[1]} must document HTTP 200 success signal"
        idx = text_lower.find("http 200")
        window = text_lower[idx : idx + 800]
        assert (
            "do not inspect" in window
            or "regardless of response body" in window
            or "do not check" in window
        ), f"{family_skill[1]} must prohibit response body inspection near HTTP 200 reference"


class TestOwnPrGuard:
    @pytest.fixture(autouse=True)
    def _filter(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        if "own-pr-guard" not in family_skill[2].required_patterns:
            pytest.skip("own-pr-guard not required for this family")

    def test_has_own_pr_guard(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        text_lower = text.lower()
        has_self_ref = (
            "own pr" in text_lower or "self-review" in text_lower or "authored" in text_lower
        )
        has_comment_fallback = "comment" in text_lower
        assert has_self_ref and has_comment_fallback, (
            f"{family_skill[1]} must include own-PR guard with COMMENT fallback"
        )
