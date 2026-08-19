"""Concurrent and failure-recovery tests for the skill capability evidence cache."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic, sleep

import pytest

import autoskillit.workspace.skill_capabilities as capabilities
from tests.workspace._helpers import _document

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _wait_for_cache_info(cache, predicate, *, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate(cache.info()):
            return
        sleep(0.001)
    pytest.fail(f"cache state did not converge before timeout: {cache.info()!r}")


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
