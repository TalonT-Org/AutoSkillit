"""PagePlanCache mechanics and concurrency (single-flight + retirement race).

Split out of the original cache_and_concurrency file so the file name reflects
its actual scope (cache admission/eviction policy + concurrent build races).
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from typing import Any

import pytest

from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server import _recipe_section_planning as planning
from autoskillit.server._recipe_section_pagination import (
    PagePlanCache,
    get_or_build_recipe_section_page_plan,
    select_recipe_section,
)
from tests.server._recipe_section_pagination_test_helpers import (
    _PAGE_TEST_BOUND,
    _build,
    _clear_page_plan_cache,
    _generation,
    _payload,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.fixture(autouse=True)
def _fresh_page_plan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", PagePlanCache())


def test_cached_plans_are_reused_and_cache_clear_forces_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = select_recipe_section(_payload(content="cache me " * 500), "content")
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-test",
        "generation": _generation(),
        "selected": selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    calls = 0
    real_builder = pagination.build_recipe_section_page_plan

    def counted_builder(**builder_kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        return real_builder(**builder_kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pagination, "build_recipe_section_page_plan", counted_builder)

    first = get_or_build_recipe_section_page_plan(**kwargs)
    second = get_or_build_recipe_section_page_plan(**kwargs)
    assert second is first
    assert calls == 1

    _clear_page_plan_cache()
    third = get_or_build_recipe_section_page_plan(**kwargs)
    assert third == first
    assert third is not first
    assert calls == 2


def test_concurrent_same_key_requests_share_one_page_plan_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = select_recipe_section(_payload(content="single flight"), "content")
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-single-flight",
        "generation": _generation(),
        "selected": selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    start = Barrier(3)
    build_started = Event()
    release_build = Event()
    build_calls: list[None] = []
    real_builder = pagination.build_recipe_section_page_plan

    def blocked_builder(**builder_kwargs: object) -> Any:
        build_calls.append(None)
        build_started.set()
        assert release_build.wait(timeout=5)
        return real_builder(**builder_kwargs)  # type: ignore[arg-type]

    def request_plan() -> Any:
        start.wait(timeout=5)
        return get_or_build_recipe_section_page_plan(**kwargs)

    monkeypatch.setattr(pagination, "build_recipe_section_page_plan", blocked_builder)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request_plan) for _ in range(2)]
        start.wait(timeout=5)
        assert build_started.wait(timeout=5)
        release_build.set()
        plans = [future.result(timeout=5) for future in futures]

    assert len(build_calls) == 1
    assert plans[0] is plans[1]


def test_retirement_during_build_prevents_stale_cache_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = select_recipe_section(_payload(content="retirement race"), "content")
    generation = _generation()
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-retirement-race",
        "generation": generation,
        "selected": selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    key = pagination._cache_key(**kwargs)
    cache = pagination._page_plan_cache()
    build_started = Barrier(2)
    release_build = Event()
    retirement_started = Event()
    real_builder = pagination.build_recipe_section_page_plan

    def blocked_builder(**builder_kwargs: object) -> Any:
        build_started.wait(timeout=5)
        assert release_build.wait(timeout=5)
        return real_builder(**builder_kwargs)  # type: ignore[arg-type]

    def retire_kitchen() -> None:
        retirement_started.set()
        cache.evict_kitchen("kitchen-retirement-race")

    monkeypatch.setattr(pagination, "build_recipe_section_page_plan", blocked_builder)
    with ThreadPoolExecutor(max_workers=2) as executor:
        build_future = executor.submit(get_or_build_recipe_section_page_plan, **kwargs)
        build_started.wait(timeout=5)
        retirement_future = executor.submit(retire_kitchen)
        assert retirement_started.wait(timeout=5)
        release_build.set()
        plan = build_future.result(timeout=5)
        retirement_future.result(timeout=5)

    assert cache.get(key) is None
    cache.put(key, plan)
    assert cache.get(key) is None


@pytest.mark.parametrize(
    "dimension",
    [
        "kitchen",
        "generation",
        "section",
        "section_digest",
        "bound",
        "registry_digest",
        "policy_digest",
        "version",
    ],
)
def test_every_cache_key_dimension_prevents_aliasing(
    dimension: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_selected = select_recipe_section(_payload(content="same"), "content")
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-a",
        "generation": _generation(),
        "selected": baseline_selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    baseline = get_or_build_recipe_section_page_plan(**kwargs)

    if dimension == "kitchen":
        kwargs["kitchen_id"] = "kitchen-b"
    elif dimension == "generation":
        kwargs["generation"] = replace(_generation(), body_size_bytes=2049)
    elif dimension == "section":
        kwargs["selected"] = select_recipe_section(
            _payload(orchestration_rules="same"),
            "orchestration_rules",
        )
    elif dimension == "section_digest":
        kwargs["selected"] = select_recipe_section(_payload(content="changed"), "content")
    elif dimension == "bound":
        kwargs["recipe_section_bound_bytes"] = _PAGE_TEST_BOUND + 1
    elif dimension == "registry_digest":
        # Both the manifest builder (pagination) and the page-body renderer
        # (planning) hold independent imported bindings of this constant —
        # both must move together or manifest/render digests diverge.
        monkeypatch.setattr(
            pagination,
            "RECIPE_SECTION_REGISTRY_DIGEST",
            f"sha256:{'a' * 64}",
        )
        monkeypatch.setattr(
            planning,
            "RECIPE_SECTION_REGISTRY_DIGEST",
            f"sha256:{'a' * 64}",
        )
    elif dimension == "policy_digest":
        monkeypatch.setattr(
            pagination,
            "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
            f"sha256:{'b' * 64}",
        )
        monkeypatch.setattr(
            planning,
            "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
            f"sha256:{'b' * 64}",
        )
    else:
        monkeypatch.setattr(
            pagination,
            "RECIPE_SECTION_PAGINATION_VERSION",
            pagination.RECIPE_SECTION_PAGINATION_VERSION + 1,
        )
        monkeypatch.setattr(
            planning,
            "RECIPE_SECTION_PAGINATION_VERSION",
            planning.RECIPE_SECTION_PAGINATION_VERSION + 1,
        )

    changed = get_or_build_recipe_section_page_plan(**kwargs)
    assert changed is not baseline
    assert get_or_build_recipe_section_page_plan(**kwargs) is changed


def test_cache_entry_limit_evicts_oldest_plan() -> None:
    selected = select_recipe_section(_payload(content="entry eviction"), "content")
    generation = _generation()
    first = get_or_build_recipe_section_page_plan(
        kitchen_id="kitchen-0",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    for index in range(1, pagination.PAGE_PLAN_CACHE_MAX_ENTRIES + 1):
        get_or_build_recipe_section_page_plan(
            kitchen_id=f"kitchen-{index}",
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=_PAGE_TEST_BOUND,
        )

    rebuilt = get_or_build_recipe_section_page_plan(
        kitchen_id="kitchen-0",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    assert rebuilt == first
    assert rebuilt is not first


def test_cache_rejects_a_single_plan_over_its_byte_limit() -> None:
    selected = select_recipe_section(_payload(content="overweight"), "content")
    generation = _generation()
    plan = _build(_payload(content="overweight"), "content", bound=_PAGE_TEST_BOUND)
    key = pagination._cache_key(
        kitchen_id="kitchen-overweight",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    cache = PagePlanCache(max_bytes=plan.cache_weight_bytes - 1)

    cache.put(key, plan)

    assert cache.get(key) is None
    assert cache._weight_bytes == 0


def test_cache_byte_limit_evicts_oldest_plan() -> None:
    selected = select_recipe_section(_payload(content="byte eviction"), "content")
    generation = _generation()
    plan = dataclasses.replace(
        _build(_payload(content="byte eviction"), "content", bound=_PAGE_TEST_BOUND),
        cache_weight_bytes=10,
    )
    keys = [
        pagination._cache_key(
            kitchen_id=f"kitchen-{index}",
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=_PAGE_TEST_BOUND,
        )
        for index in range(3)
    ]
    cache = PagePlanCache(max_entries=10, max_bytes=20)

    for key in keys:
        cache.put(key, plan)

    assert cache.get(keys[0]) is None
    assert cache.get(keys[1]) is plan
    assert cache.get(keys[2]) is plan
    assert cache._weight_bytes == 20


def test_cache_replacement_subtracts_the_previous_plan_weight() -> None:
    selected = select_recipe_section(_payload(content="replacement"), "content")
    generation = _generation()
    base_plan = _build(_payload(content="replacement"), "content", bound=_PAGE_TEST_BOUND)
    first = dataclasses.replace(base_plan, cache_weight_bytes=7)
    replacement = dataclasses.replace(base_plan, cache_weight_bytes=3)
    other = dataclasses.replace(base_plan, cache_weight_bytes=7)
    key = pagination._cache_key(
        kitchen_id="kitchen-replacement",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    other_key = pagination._cache_key(
        kitchen_id="kitchen-other",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    cache = PagePlanCache(max_entries=10, max_bytes=10)

    cache.put(key, first)
    cache.put(key, replacement)
    cache.put(other_key, other)

    assert cache.get(key) is replacement
    assert cache.get(other_key) is other
    assert cache._weight_bytes == 10


def test_cache_kitchen_eviction_subtracts_every_matching_plan_weight() -> None:
    selected = select_recipe_section(_payload(content="kitchen eviction"), "content")
    generation = _generation()
    plan = dataclasses.replace(
        _build(_payload(content="kitchen eviction"), "content", bound=_PAGE_TEST_BOUND),
        cache_weight_bytes=5,
    )
    retired_keys = [
        pagination._cache_key(
            kitchen_id="retired-kitchen",
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=bound,
        )
        for bound in (_PAGE_TEST_BOUND, _PAGE_TEST_BOUND + 1)
    ]
    retained_key = pagination._cache_key(
        kitchen_id="retained-kitchen",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    cache = PagePlanCache(max_entries=10, max_bytes=20)
    for key in (*retired_keys, retained_key):
        cache.put(key, plan)

    cache.evict_kitchen("retired-kitchen")

    assert all(cache.get(key) is None for key in retired_keys)
    assert cache.get(retained_key) is plan
    assert cache._weight_bytes == plan.cache_weight_bytes
