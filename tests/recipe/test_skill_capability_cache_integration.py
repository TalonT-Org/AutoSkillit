"""Recipe-validation integration contracts for capability evidence caching."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

import pytest

import autoskillit.workspace.skill_capabilities as skill_capabilities
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_bundled_recipe_validation_reuses_capability_evidence_cache(
    monkeypatch,
    record_property,
) -> None:
    cache = skill_capabilities._SkillCapabilityEvidenceCache(
        max_entries=skill_capabilities._SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES,
        max_bytes=skill_capabilities._SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES,
        max_input_bytes=skill_capabilities._SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES,
    )
    monkeypatch.setattr(
        skill_capabilities,
        "_SKILL_CAPABILITY_EVIDENCE_CACHE",
        cache,
    )
    public_keys: list[tuple[str, str]] = []
    scan_keys: list[tuple[str, str]] = []
    results_by_key: dict[tuple[str, str], tuple] = {}
    normalized_findings: list[dict[str, object]] = []
    original_classifier = skill_capabilities.classify_skill_capability_evidence
    original_scanner = skill_capabilities._scan_skill_capability_evidence_uncached

    def recording_classifier(
        content: str,
        skill_name: str | None = None,
    ):
        effective_name = skill_capabilities._normalize_skill_capability_name(
            content,
            skill_name,
        )
        key = (content, effective_name)
        public_keys.append(key)
        result = original_classifier(content, effective_name)
        results_by_key[key] = result
        return result

    def recording_scanner(content: str, effective_name: str):
        scan_keys.append((content, effective_name))
        return original_scanner(content, effective_name)

    monkeypatch.setattr(
        skill_capabilities,
        "classify_skill_capability_evidence",
        recording_classifier,
    )
    monkeypatch.setattr(
        skill_capabilities,
        "_scan_skill_capability_evidence_uncached",
        recording_scanner,
    )

    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        findings = run_semantic_rules(load_recipe(yaml_path))
        normalized_findings.append(
            {
                "recipe": yaml_path.name,
                "findings": [finding.to_dict() for finding in findings],
            }
        )

    canonical_findings = json.dumps(
        normalized_findings,
        sort_keys=True,
        separators=(",", ":"),
    )
    unique_public_keys = set(public_keys)
    scan_counts = Counter(scan_keys)
    info = cache.info()
    assert len(public_keys) > len(scan_keys)
    assert set(scan_keys) == unique_public_keys
    assert scan_counts == Counter({key: 1 for key in unique_public_keys})
    for content, effective_name in unique_public_keys:
        input_weight = skill_capabilities._skill_capability_evidence_input_weight_bytes(
            content,
            effective_name,
        )
        completed_weight = skill_capabilities._skill_capability_evidence_entry_weight_bytes(
            input_weight,
            results_by_key[(content, effective_name)],
        )
        assert input_weight <= info.max_input_bytes
        assert completed_weight <= info.max_bytes
    assert info.entry_count == len(unique_public_keys)
    assert info.inflight_builds == 0
    assert info.inflight_waiters == 0
    record_property("classifier_public_calls", len(public_keys))
    record_property("classifier_unique_semantic_inputs", len(unique_public_keys))
    record_property("classifier_underlying_scans", len(scan_keys))
    record_property("semantic_findings_json", canonical_findings)
    record_property(
        "semantic_findings_fingerprint",
        hashlib.sha256(canonical_findings.encode()).hexdigest(),
    )
