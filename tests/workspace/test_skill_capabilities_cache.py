"""Bounded memoization contracts for semantic skill capability evidence."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Event, Lock
from time import monotonic, sleep

import pytest

import autoskillit.workspace.skill_capabilities as capabilities

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _document(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: Cache fixture.\n---\n{body}\n"


@pytest.fixture
def evidence_cache(monkeypatch):
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=32,
        max_bytes=1024 * 1024,
        max_input_bytes=64 * 1024,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    return cache


@pytest.fixture
def scan_calls(monkeypatch):
    calls: list[tuple[str, str]] = []
    original = capabilities._scan_skill_capability_evidence_uncached

    def recording_scanner(content: str, effective_name: str):
        calls.append((content, effective_name))
        return original(content, effective_name)

    monkeypatch.setattr(
        capabilities,
        "_scan_skill_capability_evidence_uncached",
        recording_scanner,
    )
    return calls


def _wait_for_cache_info(cache, predicate, *, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate(cache.info()):
            return
        sleep(0.001)
    pytest.fail(f"cache state did not converge before timeout: {cache.info()!r}")


@pytest.mark.parametrize("field", ("max_entries", "max_bytes", "max_input_bytes"))
@pytest.mark.parametrize("invalid", (0, -1))
def test_constructor_rejects_nonpositive_limits(field: str, invalid: int) -> None:
    limits = {
        "max_entries": 1,
        "max_bytes": 1,
        "max_input_bytes": 1,
    }
    limits[field] = invalid

    with pytest.raises(ValueError, match=field):
        capabilities._SkillCapabilityEvidenceCache(**limits)


def test_resident_semantic_input_scans_once_and_reuses_tuple(evidence_cache, scan_calls) -> None:
    content = _document("resident", "Call test_check().")

    first = capabilities.classify_skill_capability_evidence(content, "resident")
    second = capabilities.classify_skill_capability_evidence(content, "resident")

    assert first is second
    assert len(scan_calls) == 1
    assert evidence_cache.info().entry_count == 1


def test_cache_info_is_an_immutable_policy_snapshot(evidence_cache) -> None:
    info = evidence_cache.info()

    with pytest.raises(FrozenInstanceError):
        info.entry_count = 0  # type: ignore[misc]


def test_empty_evidence_tuple_is_a_cache_hit(evidence_cache, scan_calls) -> None:
    content = _document("empty", "Pure prose without a capability operation.")

    first = capabilities.classify_skill_capability_evidence(content)
    second = capabilities.classify_skill_capability_evidence(content)

    assert first == ()
    assert first is second
    assert len(scan_calls) == 1
    assert evidence_cache.info().entry_count == 1


def test_content_and_effective_name_are_independent_key_dimensions(
    evidence_cache, scan_calls
) -> None:
    content = _document("alpha", "Use the `/autoskillit:alpha` skill.")

    self_reference = capabilities.classify_skill_capability_evidence(content, "alpha")
    cross_reference = capabilities.classify_skill_capability_evidence(content, "beta")
    edited_content = content.replace("autoskillit:alpha", "autoskillit:gamma")
    edited = capabilities.classify_skill_capability_evidence(edited_content, "beta")

    assert self_reference == ()
    assert cross_reference == ()
    assert edited == ()
    assert len(scan_calls) == 3
    assert evidence_cache.info().entry_count == 3


def test_falsey_and_frontmatter_names_share_one_entry(evidence_cache, scan_calls) -> None:
    content = _document("normalized", "Use the `/autoskillit:other` skill.")

    omitted = capabilities.classify_skill_capability_evidence(content)
    empty = capabilities.classify_skill_capability_evidence(content, "")
    explicit = capabilities.classify_skill_capability_evidence(content, "normalized")

    assert omitted is empty is explicit
    assert len(scan_calls) == 1
    assert evidence_cache.info().entry_count == 1


@pytest.mark.parametrize(
    ("content", "skill_name"),
    (
        (_document("surrogate", "Call test_check(). cold\ud800warm"), "surrogate"),
        (_document("surrogate", "Use the `/autoskillit:other` skill."), "logical\ud800"),
    ),
)
def test_lone_surrogates_are_total_on_cold_and_warm_calls(
    evidence_cache,
    scan_calls,
    content: str,
    skill_name: str,
) -> None:
    first = capabilities.classify_skill_capability_evidence(content, skill_name)
    second = capabilities.classify_skill_capability_evidence(content, skill_name)

    assert first is second
    assert len(scan_calls) == 1
    assert evidence_cache.info().weight_bytes > 0


def test_cold_and_warm_mixed_corpus_preserves_complete_evidence(
    evidence_cache, scan_calls
) -> None:
    content = _document(
        "mixed",
        "\n".join(
            (
                "## Step 1",
                "Call test_check().",
                "## Examples",
                'gh issue edit 42 --body-file "artifact.md"',
            )
        ),
    )

    cold = capabilities.classify_skill_capability_evidence(content, "mixed")
    warm = capabilities.classify_skill_capability_evidence(content, "mixed")

    assert warm is cold
    assert tuple(
        (
            item.capability,
            item.actor,
            item.direction,
            item.classification,
            item.source_span,
            item.source,
        )
        for item in cold
    ) == (
        (
            "test_check",
            "self",
            "outbound",
            "executable",
            (6, 6),
            "Call test_check().",
        ),
        (
            "github_api_write",
            "external",
            "inbound",
            "artifact",
            (8, 8),
            'gh issue edit 42 --body-file "artifact.md"',
        ),
    )
    assert capabilities.detect_skill_capabilities(content, "mixed") == frozenset({"test_check"})
    with pytest.raises(FrozenInstanceError):
        cold[0].source = "mutated"  # type: ignore[misc]
    assert len(scan_calls) == 1


def test_entry_count_lru_refresh_and_eviction_are_deterministic(monkeypatch, scan_calls) -> None:
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=1024 * 1024,
        max_input_bytes=64 * 1024,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    documents = {
        name: _document(name, f"Plain content for {name}.") for name in ("alpha", "beta", "gamma")
    }

    alpha = capabilities.classify_skill_capability_evidence(documents["alpha"])
    capabilities.classify_skill_capability_evidence(documents["beta"])
    assert capabilities.classify_skill_capability_evidence(documents["alpha"]) is alpha
    capabilities.classify_skill_capability_evidence(documents["gamma"])
    capabilities.classify_skill_capability_evidence(documents["beta"])

    counts = Counter(name for _, name in scan_calls)
    assert counts == Counter({"beta": 2, "alpha": 1, "gamma": 1})
    assert cache.info().entry_count == 2
    assert cache.info().weight_bytes <= cache.info().max_bytes


def test_exact_aggregate_byte_boundary_and_one_byte_overflow(monkeypatch, scan_calls) -> None:
    content = _document("boundary", "No recognized capability.")
    key_weight = capabilities._skill_capability_evidence_input_weight_bytes(content, "boundary")
    exact = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=key_weight,
        max_input_bytes=key_weight,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", exact)

    first = capabilities.classify_skill_capability_evidence(content, "boundary")
    assert exact.info().weight_bytes == key_weight
    assert capabilities.classify_skill_capability_evidence(content, "boundary") is first

    overflow = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=key_weight - 1,
        max_input_bytes=key_weight,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", overflow)
    capabilities.classify_skill_capability_evidence(content, "boundary")
    capabilities.classify_skill_capability_evidence(content, "boundary")

    assert overflow.info().entry_count == 0
    assert overflow.info().weight_bytes == 0
    assert len(scan_calls) == 3


def test_non_ascii_input_over_byte_limit_bypasses_cache(monkeypatch, scan_calls) -> None:
    effective_name = "unicode"
    content = _document(
        effective_name,
        "éééééééééééééééé\nCall test_check().",
    )
    character_weight = len(content) + len(effective_name)
    encoded_weight = len(content.encode("utf-8")) + len(effective_name.encode("utf-8"))
    assert encoded_weight > character_weight
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=1024 * 1024,
        max_input_bytes=character_weight,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)

    capabilities.classify_skill_capability_evidence(content, effective_name)
    capabilities.classify_skill_capability_evidence(content, effective_name)

    assert scan_calls == [(content, effective_name), (content, effective_name)]
    assert cache.info().entry_count == 0
    assert cache.info().weight_bytes == 0


def test_record_charge_and_multi_entry_byte_eviction(monkeypatch, scan_calls) -> None:
    charged_content = _document(
        "charged",
        "Call test_check().\ngh issue edit 1 --body-file two.md",
    )
    charged_result = capabilities._scan_skill_capability_evidence_uncached(
        charged_content, "charged"
    )
    charged_key_weight = capabilities._skill_capability_evidence_input_weight_bytes(
        charged_content, "charged"
    )
    charged_weight = capabilities._skill_capability_evidence_entry_weight_bytes(
        charged_key_weight,
        charged_result,
    )
    assert charged_weight == (
        charged_key_weight
        + sum(capabilities._retained_string_weight_bytes(item.source) for item in charged_result)
        + 2 * capabilities._SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES
    )

    small_a = _document("a", "a")
    small_b = _document("b", "b")
    large = _document("large", "x" * 100)
    large_weight = capabilities._skill_capability_evidence_input_weight_bytes(large, "large")
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=8,
        max_bytes=large_weight,
        max_input_bytes=large_weight,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    capabilities.classify_skill_capability_evidence(small_a)
    capabilities.classify_skill_capability_evidence(small_b)
    large_result = capabilities.classify_skill_capability_evidence(large)

    assert cache.info().entry_count == 1
    assert cache.info().weight_bytes == large_weight
    assert capabilities.classify_skill_capability_evidence(large) is large_result
    capabilities.classify_skill_capability_evidence(small_a)
    assert Counter(name for _, name in scan_calls)["a"] == 2
    assert cache.info().entry_count <= cache.info().max_entries
    assert cache.info().weight_bytes <= cache.info().max_bytes


def test_character_preflight_bypasses_exact_accounting_and_retention(
    monkeypatch,
) -> None:
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=1024,
        max_input_bytes=64,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    content = _document("oversized", "Call test_check().\n" + "x" * 80)
    calls = 0
    original_scanner = capabilities._scan_skill_capability_evidence_uncached

    def scanner(body: str, name: str):
        nonlocal calls
        calls += 1
        return original_scanner(body, name)

    monkeypatch.setattr(capabilities, "_scan_skill_capability_evidence_uncached", scanner)
    monkeypatch.setattr(
        capabilities,
        "_skill_capability_evidence_input_weight_bytes",
        lambda *_args: pytest.fail("exact accounting should be bypassed"),
    )

    first = capabilities.classify_skill_capability_evidence(content, "oversized")
    second = capabilities.classify_skill_capability_evidence(content, "oversized")

    assert first == second
    assert first[0].capability == "test_check"
    assert calls == 2
    assert cache.info().entry_count == 0
    assert cache.info().weight_bytes == 0


def test_completed_entry_overflow_scans_correctly_without_retention(
    monkeypatch, scan_calls
) -> None:
    content = _document("completed", "Call test_check().")
    key_weight = capabilities._skill_capability_evidence_input_weight_bytes(content, "completed")
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=key_weight,
        max_input_bytes=key_weight,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)

    first = capabilities.classify_skill_capability_evidence(content, "completed")
    second = capabilities.classify_skill_capability_evidence(content, "completed")

    assert first == second
    assert first[0].capability == "test_check"
    assert len(scan_calls) == 2
    assert cache.info().entry_count == 0
    assert cache.info().weight_bytes == 0


def test_overlapping_cold_callers_share_one_generation(evidence_cache, monkeypatch) -> None:
    content = _document("concurrent", "Call test_check().")
    entered = Event()
    release = Event()
    counter_lock = Lock()
    scans = 0
    original = capabilities._scan_skill_capability_evidence_uncached

    def blocked_scanner(body: str, name: str):
        nonlocal scans
        with counter_lock:
            scans += 1
        entered.set()
        assert release.wait(2)
        return original(body, name)

    monkeypatch.setattr(capabilities, "_scan_skill_capability_evidence_uncached", blocked_scanner)
    futures: list[Future[tuple]] = []
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures.append(
                executor.submit(
                    capabilities.classify_skill_capability_evidence,
                    content,
                    "concurrent",
                )
            )
            assert entered.wait(2)
            futures.extend(
                executor.submit(
                    capabilities.classify_skill_capability_evidence,
                    content,
                    "concurrent",
                )
                for _ in range(3)
            )
            _wait_for_cache_info(
                evidence_cache,
                lambda info: info.inflight_builds == 1 and info.inflight_waiters == 3,
            )
            release.set()
            results = [future.result(timeout=2) for future in futures]
    finally:
        release.set()

    assert scans == 1
    assert all(result is results[0] for result in results)
    assert evidence_cache.info().inflight_builds == 0
    assert evidence_cache.info().inflight_waiters == 0


def test_different_cold_keys_scan_concurrently_outside_global_lock(
    evidence_cache, monkeypatch
) -> None:
    both_entered = Event()
    release = Event()
    counter_lock = Lock()
    active = 0
    max_active = 0
    original = capabilities._scan_skill_capability_evidence_uncached

    def blocked_scanner(body: str, name: str):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_entered.set()
        try:
            assert release.wait(2)
            return original(body, name)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(capabilities, "_scan_skill_capability_evidence_uncached", blocked_scanner)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                capabilities.classify_skill_capability_evidence,
                _document("first", "first"),
            )
            second = executor.submit(
                capabilities.classify_skill_capability_evidence,
                _document("second", "second"),
            )
            assert both_entered.wait(2)
            release.set()
            first.result(timeout=2)
            second.result(timeout=2)
    finally:
        release.set()

    assert max_active == 2
    assert evidence_cache.info().entry_count == 2


def test_synchronized_warm_callers_reuse_resident_identity(evidence_cache, scan_calls) -> None:
    content = _document("warm", "Call test_check().")
    resident = capabilities.classify_skill_capability_evidence(content)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                capabilities.classify_skill_capability_evidence,
                content,
            )
            for _ in range(16)
        ]
        results = [future.result(timeout=2) for future in futures]

    assert all(result is resident for result in results)
    assert len(scan_calls) == 1
    assert evidence_cache.info().inflight_builds == 0


def test_waiter_receives_builder_tuple_after_resident_eviction(
    monkeypatch,
) -> None:
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=1,
        max_bytes=1024 * 1024,
        max_input_bytes=64 * 1024,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    first_content = _document("first", "Call test_check().")
    second_content = _document("second", "Call test_check().")
    scanner_entered = Event()
    scanner_release = Event()
    waiter_holds_result = Event()
    waiter_release = Event()
    original_scanner = capabilities._scan_skill_capability_evidence_uncached
    original_wait = capabilities._SkillCapabilityEvidenceCache._wait_for_build

    def blocked_scanner(body: str, name: str):
        if name == "first":
            scanner_entered.set()
            assert scanner_release.wait(2)
        return original_scanner(body, name)

    def paused_wait(self, key, state):
        result = original_wait(self, key, state)
        waiter_holds_result.set()
        assert waiter_release.wait(2)
        return result

    monkeypatch.setattr(capabilities, "_scan_skill_capability_evidence_uncached", blocked_scanner)
    monkeypatch.setattr(
        capabilities._SkillCapabilityEvidenceCache,
        "_wait_for_build",
        paused_wait,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            builder = executor.submit(
                capabilities.classify_skill_capability_evidence,
                first_content,
            )
            assert scanner_entered.wait(2)
            waiter = executor.submit(
                capabilities.classify_skill_capability_evidence,
                first_content,
            )
            _wait_for_cache_info(cache, lambda info: info.inflight_waiters == 1)
            scanner_release.set()
            builder_result = builder.result(timeout=2)
            assert waiter_holds_result.wait(2)
            capabilities.classify_skill_capability_evidence(second_content)
            waiter_release.set()
            waiter_result = waiter.result(timeout=2)
    finally:
        scanner_release.set()
        waiter_release.set()

    assert waiter_result is builder_result
    assert cache.info().entry_count == 1


def test_resuming_waiter_refreshes_same_resident_generation_to_mru(
    monkeypatch, scan_calls
) -> None:
    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=2,
        max_bytes=1024 * 1024,
        max_input_bytes=64 * 1024,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    first_content = _document("first", "Call test_check().")
    second_content = _document("second", "Call test_check().")
    third_content = _document("third", "Call test_check().")
    scanner_entered = Event()
    scanner_release = Event()
    waiter_awakened = Event()
    waiter_release = Event()
    original_scanner = capabilities._scan_skill_capability_evidence_uncached

    class PausingEvent:
        def __init__(self) -> None:
            self._event = Event()

        def set(self) -> None:
            self._event.set()

        def wait(self, timeout: float | None = None) -> bool:
            assert self._event.wait(timeout)
            waiter_awakened.set()
            assert waiter_release.wait(2)
            return True

    def new_build_state(_self):
        return capabilities._SkillCapabilityEvidenceBuildState(
            event=PausingEvent(),  # type: ignore[arg-type]
        )

    def blocked_scanner(body: str, name: str):
        if name == "first":
            scanner_entered.set()
            assert scanner_release.wait(2)
        return original_scanner(body, name)

    monkeypatch.setattr(
        capabilities._SkillCapabilityEvidenceCache,
        "_new_build_state",
        new_build_state,
    )
    monkeypatch.setattr(
        capabilities,
        "_scan_skill_capability_evidence_uncached",
        blocked_scanner,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            builder = executor.submit(
                capabilities.classify_skill_capability_evidence,
                first_content,
            )
            assert scanner_entered.wait(2)
            waiter = executor.submit(
                capabilities.classify_skill_capability_evidence,
                first_content,
            )
            _wait_for_cache_info(cache, lambda info: info.inflight_waiters == 1)
            scanner_release.set()
            first_result = builder.result(timeout=2)
            assert waiter_awakened.wait(2)
            capabilities.classify_skill_capability_evidence(second_content)
            waiter_release.set()
            assert waiter.result(timeout=2) is first_result
    finally:
        scanner_release.set()
        waiter_release.set()

    capabilities.classify_skill_capability_evidence(third_content)
    assert capabilities.classify_skill_capability_evidence(first_content) is first_result
    capabilities.classify_skill_capability_evidence(second_content)
    assert Counter(name for _, name in scan_calls) == Counter(
        {"second": 2, "first": 1, "third": 1}
    )


def test_scanner_failure_releases_waiters_and_allows_retry(evidence_cache, monkeypatch) -> None:
    content = _document("failure", "Call test_check().")
    entered = Event()
    release = Event()
    failure = RuntimeError("scanner failed")
    original = capabilities._scan_skill_capability_evidence_uncached
    should_fail = True

    def scanner(body: str, name: str):
        if should_fail:
            entered.set()
            assert release.wait(2)
            raise failure
        return original(body, name)

    monkeypatch.setattr(capabilities, "_scan_skill_capability_evidence_uncached", scanner)
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(
                    capabilities.classify_skill_capability_evidence,
                    content,
                )
                for _ in range(3)
            ]
            assert entered.wait(2)
            _wait_for_cache_info(evidence_cache, lambda info: info.inflight_waiters == 2)
            release.set()
            raised_errors: list[RuntimeError] = []
            for future in futures:
                with pytest.raises(RuntimeError) as raised:
                    future.result(timeout=2)
                raised_errors.append(raised.value)
    finally:
        release.set()

    assert sum(error is failure for error in raised_errors) == 1
    waiter_errors = [error for error in raised_errors if error is not failure]
    assert len(waiter_errors) == 2
    assert waiter_errors[0] is not waiter_errors[1]
    assert all(error.__cause__ is failure for error in waiter_errors)
    assert evidence_cache.info().inflight_builds == 0
    assert evidence_cache.info().inflight_waiters == 0
    should_fail = False
    retry = capabilities.classify_skill_capability_evidence(content)
    assert retry[0].capability == "test_check"


def test_partial_bookkeeping_failure_resets_resident_state_and_allows_retry(
    evidence_cache, monkeypatch
) -> None:
    content = _document("bookkeeping", "Call test_check().")
    scanner_entered = Event()
    scanner_release = Event()
    original_eviction = capabilities._SkillCapabilityEvidenceCache._evict_if_needed_locked
    original_scanner = capabilities._scan_skill_capability_evidence_uncached
    failure = RuntimeError("bookkeeping failed")

    def fail_after_insertion(self):
        raise failure

    def blocked_scanner(body: str, name: str):
        scanner_entered.set()
        assert scanner_release.wait(2)
        return original_scanner(body, name)

    monkeypatch.setattr(
        capabilities._SkillCapabilityEvidenceCache,
        "_evict_if_needed_locked",
        fail_after_insertion,
    )
    monkeypatch.setattr(
        capabilities,
        "_scan_skill_capability_evidence_uncached",
        blocked_scanner,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        builder = executor.submit(
            capabilities.classify_skill_capability_evidence,
            content,
        )
        try:
            assert scanner_entered.wait(2)
            waiter = executor.submit(
                capabilities.classify_skill_capability_evidence,
                content,
            )
            _wait_for_cache_info(evidence_cache, lambda info: info.inflight_waiters == 1)
            scanner_release.set()
            with pytest.raises(RuntimeError) as builder_raised:
                builder.result(timeout=2)
            with pytest.raises(RuntimeError) as waiter_raised:
                waiter.result(timeout=2)
        finally:
            scanner_release.set()

    assert builder_raised.value is failure
    assert waiter_raised.value is not failure
    assert waiter_raised.value.__cause__ is failure
    assert evidence_cache.info().entry_count == 0
    assert evidence_cache.info().weight_bytes == 0
    assert evidence_cache.info().inflight_builds == 0
    assert evidence_cache.info().inflight_waiters == 0

    monkeypatch.setattr(
        capabilities._SkillCapabilityEvidenceCache,
        "_evict_if_needed_locked",
        original_eviction,
    )
    monkeypatch.setattr(
        capabilities,
        "_scan_skill_capability_evidence_uncached",
        original_scanner,
    )
    retry = capabilities.classify_skill_capability_evidence(content)
    assert retry[0].capability == "test_check"
