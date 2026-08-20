"""Single-process correctness of the skill capability evidence cache (no concurrency)."""

from __future__ import annotations

from collections import Counter
from dataclasses import FrozenInstanceError

import pytest

import autoskillit.workspace.skill_capabilities as capabilities
from tests.workspace._helpers import _document

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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


def test_entry_count_lru_refresh_and_eviction_are_deterministic(
    make_evidence_cache, scan_calls
) -> None:
    cache = make_evidence_cache(max_entries=2)
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
