"""Tests for the IL-0 kitchen lifecycle and identity types (Step 3.2/3.7 of #4185)."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    CampaignId,
    Closed,
    DispatchId,
    ExecutionLeaseId,
    FreeFormSkillScope,
    KitchenInstanceId,
    LifecycleGeneration,
    OpenEmpty,
    OpenRecipe,
    PipelineScopeId,
    RecipeStepKey,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _kitchen() -> KitchenInstanceId:
    return KitchenInstanceId(value="kitchen-1", process_id=99)


class TestClosed:
    def test_default_construction(self) -> None:
        closed = Closed()
        assert closed.reason == ""

    def test_with_reason(self) -> None:
        closed = Closed(reason="user_close")
        assert closed.reason == "user_close"

    def test_is_hashable(self) -> None:
        a = Closed()
        b = Closed()
        assert hash(a) == hash(b)


class TestOpenEmpty:
    def test_valid_construction(self) -> None:
        opened = OpenEmpty(kitchen_instance_id=_kitchen(), opened_at=12345.0)
        assert opened.kitchen_instance_id.value == "kitchen-1"
        assert opened.opened_at == 12345.0

    def test_zero_opened_at_raises(self) -> None:
        with pytest.raises(ValueError, match="opened_at must be positive"):
            OpenEmpty(kitchen_instance_id=_kitchen(), opened_at=0)

    def test_negative_opened_at_raises(self) -> None:
        with pytest.raises(ValueError, match="opened_at must be positive"):
            OpenEmpty(kitchen_instance_id=_kitchen(), opened_at=-1)


class TestOpenRecipe:
    def _valid(self, **overrides: object) -> OpenRecipe:
        defaults: dict[str, object] = {
            "kitchen_instance_id": _kitchen(),
            "recipe_name": "demo",
            "recipe_kind": "standard",
            "recipe_version": "1.0.0",
            "compilation_key_fingerprint": "fp-abc",
            "opened_at": 12345.0,
        }
        defaults.update(overrides)
        return OpenRecipe(**defaults)  # type: ignore[arg-type]

    def test_valid_construction(self) -> None:
        opened = self._valid()
        assert opened.recipe_name == "demo"
        assert opened.compilation_key_fingerprint == "fp-abc"

    def test_empty_recipe_name_raises(self) -> None:
        with pytest.raises(ValueError, match="recipe_name must be non-empty"):
            self._valid(recipe_name="")

    def test_empty_compilation_fingerprint_raises(self) -> None:
        with pytest.raises(ValueError, match="compilation_key_fingerprint must be non-empty"):
            self._valid(compilation_key_fingerprint="")

    def test_zero_opened_at_raises(self) -> None:
        with pytest.raises(ValueError, match="opened_at must be positive"):
            self._valid(opened_at=0)


class TestLifecycleGeneration:
    def test_initial(self) -> None:
        gen = LifecycleGeneration.initial()
        assert gen.value == 0

    def test_next(self) -> None:
        gen = LifecycleGeneration.initial()
        nxt = gen.next()
        assert nxt.value == 1

    def test_next_of_max_still_works(self) -> None:
        gen = LifecycleGeneration(value=100)
        nxt = gen.next()
        assert nxt.value == 101

    def test_negative_value_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be non-negative"):
            LifecycleGeneration(value=-1)

    def test_is_hashable(self) -> None:
        a = LifecycleGeneration(value=5)
        b = LifecycleGeneration(value=5)
        assert hash(a) == hash(b)


class TestIdentityTypes:
    def test_campaign_id(self) -> None:
        cid = CampaignId(value="campaign-1")
        assert cid.value == "campaign-1"
        assert hash(cid) == hash(CampaignId(value="campaign-1"))

    def test_campaign_id_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be non-empty"):
            CampaignId(value="")

    def test_dispatch_id(self) -> None:
        did = DispatchId(value="dispatch-1")
        assert did.value == "dispatch-1"

    def test_dispatch_id_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be non-empty"):
            DispatchId(value="")

    def test_pipeline_scope_id(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        assert psid.value == "scope-1"

    def test_pipeline_scope_id_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be non-empty"):
            PipelineScopeId(value="")

    def test_execution_lease_id(self) -> None:
        lid = ExecutionLeaseId(value="lease-1", kitchen_instance_id=_kitchen())
        assert lid.value == "lease-1"

    def test_execution_lease_id_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be non-empty"):
            ExecutionLeaseId(value="", kitchen_instance_id=_kitchen())

    def test_campaign_and_dispatch_are_distinct_types(self) -> None:
        cid = CampaignId(value="x")
        did = DispatchId(value="x")
        # Type-level distinction: cid is not a DispatchId.
        assert not isinstance(cid, DispatchId)
        assert not isinstance(did, CampaignId)


class TestStepKeysAndScopes:
    def test_recipe_step_key(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        key = RecipeStepKey(
            recipe_name="demo",
            step_name="build",
            pipeline_scope=psid,
        )
        assert key.recipe_name == "demo"
        assert hash(key) == hash(
            RecipeStepKey(recipe_name="demo", step_name="build", pipeline_scope=psid)
        )

    def test_recipe_step_key_empty_recipe_raises(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        with pytest.raises(ValueError, match="recipe_name must be non-empty"):
            RecipeStepKey(recipe_name="", step_name="build", pipeline_scope=psid)

    def test_recipe_step_key_empty_step_raises(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        with pytest.raises(ValueError, match="step_name must be non-empty"):
            RecipeStepKey(recipe_name="demo", step_name="", pipeline_scope=psid)

    def test_free_form_skill_scope(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        scope = FreeFormSkillScope(skill_command="open-pr", pipeline_scope=psid)
        assert scope.skill_command == "open-pr"
        assert hash(scope) == hash(
            FreeFormSkillScope(skill_command="open-pr", pipeline_scope=psid)
        )

    def test_free_form_skill_scope_empty_command_raises(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        with pytest.raises(ValueError, match="skill_command must be non-empty"):
            FreeFormSkillScope(skill_command="", pipeline_scope=psid)

    def test_recipe_step_key_and_free_form_are_distinct(self) -> None:
        psid = PipelineScopeId(value="scope-1")
        key = RecipeStepKey(recipe_name="x", step_name="y", pipeline_scope=psid)
        scope = FreeFormSkillScope(skill_command="y", pipeline_scope=psid)
        assert not isinstance(key, FreeFormSkillScope)
        assert not isinstance(scope, RecipeStepKey)
