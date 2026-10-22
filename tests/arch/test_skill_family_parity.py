"""Parametrized parity tests for GitHub API skill families.

Each family defines required API patterns; every member must satisfy them.
"""

from __future__ import annotations

from typing import ClassVar

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


def test_thread_resolvers_require_drift_discipline() -> None:
    family = next(
        family for family in GITHUB_API_SKILL_FAMILIES if family.name == "thread-resolvers"
    )
    assert "drift-discipline" in family.required_patterns


@pytest.mark.parametrize("family_name", ["thread-resolvers", "review-posters"])
def test_review_families_require_response_body_guard(family_name: str) -> None:
    family = next(family for family in GITHUB_API_SKILL_FAMILIES if family.name == family_name)
    assert "response-body-guard" in family.required_patterns


class _PatternGuard:
    PATTERN: ClassVar[str]

    @pytest.fixture(autouse=True)
    def _filter(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        if self.PATTERN not in family_skill[2].required_patterns:
            pytest.skip(f"{self.PATTERN} not required for this family")


class TestGraphqlBatchAliases(_PatternGuard):
    PATTERN = "graphql-batch-aliases"

    def test_uses_batch_aliases(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        text_lower = text.lower()
        assert "alias" in text_lower or "resolve1:" in text or "resolve${" in text, (
            f"{family_skill[1]} must use batched aliased GraphQL mutations"
        )


class TestMutatingCallDelay(_PatternGuard):
    PATTERN = "mutating-call-delay"

    def test_has_sleep_delay(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        assert "sleep 1" in text or "sleep(1)" in text, (
            f"{family_skill[1]} must include sleep 1 between mutating API calls"
        )


class TestUnpostablePrefilter(_PatternGuard):
    PATTERN = "unpostable-prefilter"

    def test_has_unpostable_bucket(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = _read_skill_md(family_skill[1])
        assert "UNPOSTABLE" in text, (
            f"{family_skill[1]} must include UNPOSTABLE_FINDINGS pre-classification"
        )


class TestResponseBodyGuard(_PatternGuard):
    PATTERN = "response-body-guard"

    def test_has_response_body_guard(self, family_skill: tuple[str, str, SkillFamilyDef]) -> None:
        text = " ".join(_read_skill_md(family_skill[1]).split())
        assert (
            "Never infer publication success from the GitHub HTTP response `comments` array "
            "or its length."
        ) in text, f"{family_skill[1]} must prohibit inferring success from response comments"


class TestDriftDiscipline(_PatternGuard):
    PATTERN = "drift-discipline"

    def test_groups_descends_and_rereads_live_source(
        self, family_skill: tuple[str, str, SkillFamilyDef]
    ) -> None:
        text = _read_skill_md(family_skill[1])
        text_lower = text.lower()

        assert "group" in text_lower, f"{family_skill[1]} must group concrete edits by file"
        assert "descending" in text_lower or "-line" in text_lower, (
            f"{family_skill[1]} must edit each file in descending-line order"
        )
        assert "live" in text_lower and ("re-read" in text_lower or "reread" in text_lower), (
            f"{family_skill[1]} must re-read live source before each edit"
        )


def test_pattern_guards_match_required_patterns() -> None:
    """Every local guard covers a pattern required by a skill family."""
    guard_classes = [
        cls for name, cls in globals().items() if name.startswith("Test") and isinstance(cls, type)
    ]
    assert all(issubclass(cls, _PatternGuard) for cls in guard_classes)
    assert {cls.PATTERN for cls in guard_classes} == set().union(
        *(family.required_patterns for family in GITHUB_API_SKILL_FAMILIES)
    )
