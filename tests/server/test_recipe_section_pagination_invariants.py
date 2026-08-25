"""Recipe-section pagination invariants: sizing, descriptor integrity, manifest, determinism.

End-to-end properties the system holds regardless of which subsection or
strategy is exercised. Sibling of cache.py (admission/eviction) and the
planning/reconstruction modules (error-case and round-trip contracts).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import replace
from typing import Any

import pytest

from autoskillit.core import recipe_section_plan_digest
from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server import _recipe_section_planning as planning
from autoskillit.server._recipe_section_pagination import (
    RecipeSectionPaginationError,
)
from tests.conftest import production_interpreter_env
from tests.server._recipe_section_pagination_test_helpers import (
    _PAGE_TEST_BOUND,
    _build,
    _clear_page_plan_cache,
    _decoded_pages,
    _generation,
    _payload,
    _rendered_pages,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.fixture(autouse=True)
def _fresh_page_plan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", pagination.PagePlanCache())


@pytest.mark.parametrize("strategy", ["raw", "scalar", "array", "fragment"])
def test_candidate_sizing_uses_binary_search_scale_oracle_calls(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    if strategy == "raw":
        payload = _payload(content="r" * 50_000)
        section = "content"
        search_space = 50_000
    elif strategy == "scalar":
        payload = _payload(ingredients_table="s" * 50_000)
        section = "ingredients_table"
        search_space = 50_000
    elif strategy == "array":
        values = [f"value-{index:04d}-" + ("a" * 40) for index in range(2_000)]
        payload = _payload(warnings=values)
        section = "warnings"
        search_space = len(values)
    else:
        fragment = "f" * 50_000
        payload = _payload(warnings=[fragment])
        section = "warnings"
        search_space = len(fragment)

    oracle_calls = 0
    original_fits = planning._fits

    def _counted_fits(**kwargs: Any) -> bool:
        nonlocal oracle_calls
        oracle_calls += 1
        return original_fits(**kwargs)

    monkeypatch.setattr(planning, "_fits", _counted_fits)

    plan = _build(payload, section, bound=_PAGE_TEST_BOUND)

    binary_search_scale = math.ceil(math.log2(search_space + 1))
    assert oracle_calls <= 6 * plan.total_parts * (binary_search_scale + 2)
    if strategy == "fragment":
        assert {json.loads(page)["content_format"] for page in plan.rendered_pages} == {
            "json-element-fragment"
        }


def test_final_digest_injection_revalidates_descriptor_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_render = planning._render_candidate

    def _corrupt_final_boundary(**kwargs: Any) -> str:
        rendered = original_render(**kwargs)
        if kwargs["page_plan_sha256"] != planning._PLAN_DIGEST_PLACEHOLDER and kwargs["part"] == 1:
            response = json.loads(rendered)
            response["byte_start"] += 1
            return json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        return rendered

    monkeypatch.setattr(pagination, "_render_candidate", _corrupt_final_boundary)

    with pytest.raises(
        RecipeSectionPaginationError,
        match="changed plan identity or boundaries",
    ):
        _build(_payload(content="boundary-check-" * 500), "content", bound=_PAGE_TEST_BOUND)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("fragment_count", "fragment identity changed"),
        ("element_digest", "fragment identity changed"),
        ("fragment_range", "fragment range does not match its content"),
        ("reconstruction", "do not reconstruct their element"),
        ("page_content_digest", "page content digest changed"),
    ],
)
def test_final_verifier_rejects_fragment_descriptor_and_content_corruption(
    mutation: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = pagination.verify_finalized_recipe_section_plan

    def _verify_corrupted(**kwargs: Any) -> None:
        pages = list(kwargs["pages"])
        first = pages[0]
        descriptor = first.descriptor
        if mutation == "fragment_count":
            assert descriptor.fragment_count is not None
            descriptor = replace(
                descriptor,
                fragment_count=descriptor.fragment_count + 1,
            )
        elif mutation == "element_digest":
            descriptor = replace(descriptor, element_sha256=f"sha256:{'f' * 64}")
        elif mutation == "fragment_range":
            assert descriptor.fragment_byte_end is not None
            descriptor = replace(
                descriptor,
                fragment_byte_end=descriptor.fragment_byte_end - 1,
            )
        elif mutation == "reconstruction":
            decoded = json.loads(first.content)
            first = replace(
                first,
                content=json.dumps(
                    "x" + decoded[1:],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        else:
            descriptor = replace(descriptor, page_content_sha256=f"sha256:{'f' * 64}")
        pages[0] = replace(first, descriptor=descriptor)
        original_verify(**{**kwargs, "pages": pages})

    monkeypatch.setattr(
        pagination,
        "verify_finalized_recipe_section_plan",
        _verify_corrupted,
    )

    with pytest.raises(RecipeSectionPaginationError, match=message):
        _build(_payload(warnings=["\\" * 5_000]), "warnings", bound=_PAGE_TEST_BOUND)


def test_plan_manifest_is_complete_and_plan_digest_is_non_self_referential() -> None:
    bound = _PAGE_TEST_BOUND
    generation = _generation()
    plan = _build(
        _payload(warnings=["a", "b", "c"] * 20),
        "warnings",
        bound=bound,
        generation=generation,
    )
    pages = _decoded_pages(plan, bound=bound)
    manifest = dataclasses.asdict(plan.manifest)

    assert set(manifest) == {
        "initialization_id",
        "pagination_version",
        "section_registry_sha256",
        "pagination_policy_sha256",
        "generation",
        "section",
        "section_strategy",
        "section_sha256",
        "recipe_section_bound_bytes",
        "pages",
    }
    assert manifest["recipe_section_bound_bytes"] == bound
    assert plan.page_plan_sha256 == recipe_section_plan_digest(plan.manifest)
    assert {page["page_plan_sha256"] for page in pages} == {plan.page_plan_sha256}
    assert len(plan.page_plan_sha256) == len(f"sha256:{'0' * 64}")


def test_repeat_builds_and_fresh_cache_are_deterministic() -> None:
    payload = _payload(warnings=[f"value-{index}" for index in range(50)])
    first = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)
    second = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)

    assert first == second
    assert _rendered_pages(first) == _rendered_pages(second)
    assert first.page_plan_sha256 == second.page_plan_sha256

    _clear_page_plan_cache()
    third = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)
    assert third == first


def test_cross_process_plan_and_rendering_are_deterministic() -> None:
    payload = _payload(warnings=["alpha", "snowman-☃", 'quote-"', "slash-\\"] * 20)
    local = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)
    local_render_sha = hashlib.sha256(
        "\0".join(_rendered_pages(local)).encode("utf-8")
    ).hexdigest()
    script = f"""
import hashlib
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    RecipeArtifactGeneration,
)
from autoskillit.server._recipe_section_pagination import (
    build_recipe_section_page_plan,
    render_recipe_section_page,
    select_recipe_section,
)
payload = {payload!r}
generation = RecipeArtifactGeneration(
    producer_tool="open_kitchen",
    recipe_name="remediation",
    descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
    payload_sha256="sha256:" + "1" * 64,
    artifact_blob_sha256="sha256:" + "2" * 64,
    artifact_blob_size_bytes=4096,
    body_sha256="sha256:" + "3" * 64,
    body_size_bytes=2048,
    flow_schema_version=RECIPE_FLOW_SCHEMA_VERSION,
    flow_sha256="sha256:" + "4" * 64,
    flow_size_bytes=512,
    flow_record_count=2,
)
plan = build_recipe_section_page_plan(
    kitchen_id="kitchen-test",
    generation=generation,
    selected=select_recipe_section(payload, "warnings"),
    recipe_section_bound_bytes=2000,
)
rendered = [render_recipe_section_page(plan, part) for part in range(plan.total_parts)]
print(plan.page_plan_sha256)
print(hashlib.sha256("\\0".join(rendered).encode("utf-8")).hexdigest())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=production_interpreter_env(),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.stdout.splitlines() == [
        local.page_plan_sha256,
        local_render_sha,
    ]
