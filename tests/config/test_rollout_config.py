"""Validation for the optional rollout declaration in configuration."""

from __future__ import annotations

import copy

import pytest

from autoskillit.config import ConfigSchemaError, load_config
from autoskillit.core import dump_yaml_str

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]

_VALID_ACTIVE: dict[str, object] = {
    "change_id": "prompt-cost-study",
    "effective_at": "2026-11-01T18:00:00Z",
    "scope": {
        "skills": ["make-plan"],
        "backends": ["claude-code", "codex"],
        "roles": ["planner"],
    },
    "expected_effect": "Reduce planning input tokens while preserving requirement coverage.",
}


_MISSING = object()


def _write_config(tmp_path, config: dict) -> None:
    config_dir = tmp_path / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(dump_yaml_str(config))


@pytest.mark.parametrize(
    ("effective_at", "expected"),
    [
        ("2099-11-01T18:00:00Z", "2099-11-01T18:00:00+00:00"),
        ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
    ],
    ids=["future-cutover", "past-cutover"],
)
def test_loads_complete_rollout_active_and_normalizes_utc(tmp_path, effective_at, expected):
    active = copy.deepcopy(_VALID_ACTIVE)
    active["effective_at"] = effective_at
    active["expected_effect"] = "  Preserve this prediction text.  "
    _write_config(tmp_path, {"rollout": {"active": active}})

    marker = load_config(tmp_path).rollout.active
    assert marker is not None

    assert marker == {
        **active,
        "effective_at": expected,
    }
    assert marker["expected_effect"] == "  Preserve this prediction text.  "
    assert marker["scope"] == _VALID_ACTIVE["scope"]


def test_rollout_validator_copies_raw_scope_lists() -> None:
    from autoskillit.config._coercion import _build_rollout_active

    raw_active = copy.deepcopy(_VALID_ACTIVE)
    section = {"active": raw_active}

    marker = _build_rollout_active(section, {})
    assert marker is not None
    raw_scope = raw_active["scope"]
    assert isinstance(raw_scope, dict)
    raw_skills = raw_scope["skills"]
    raw_backends = raw_scope["backends"]
    assert isinstance(raw_skills, list)
    assert isinstance(raw_backends, list)
    raw_skills.append("later-skill")
    raw_backends.clear()

    assert marker["scope"] == {
        "skills": ["make-plan"],
        "backends": ["claude-code", "codex"],
        "roles": ["planner"],
    }


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"rollout": {}},
        {"rollout": {"active": None}},
    ],
    ids=["missing-section", "missing-active", "null-active"],
)
def test_missing_or_null_rollout_active_is_inactive(tmp_path, config):
    _write_config(tmp_path, config)

    assert load_config(tmp_path).rollout.active is None


def _invalid_active_cases() -> list[tuple[str, object]]:
    cases: list[tuple[str, object]] = []

    def add(name: str, **changes: object) -> None:
        active: dict[str, object] = copy.deepcopy(_VALID_ACTIVE)
        for key, value in changes.items():
            if value is _MISSING:
                active.pop(key, None)
            else:
                active[key] = value
        cases.append((name, active))

    cases.append(("empty-active", {}))
    cases.append(("partial-active", {"change_id": "only-an-id"}))
    add("missing-change-id", change_id=_MISSING)
    add("blank-change-id", change_id=" \t")
    add("non-string-change-id", change_id=7)
    add("missing-expected-effect", expected_effect=_MISSING)
    add("null-expected-effect", expected_effect=None)
    add("blank-expected-effect", expected_effect=" \t")
    add("non-string-expected-effect", expected_effect=7)
    add("missing-effective-at", effective_at=_MISSING)
    add("malformed-effective-at", effective_at="not-a-date")
    add("naive-effective-at", effective_at="2026-11-01T18:00:00")
    add("non-utc-effective-at", effective_at="2026-11-01T18:00:00-07:00")
    add("non-string-effective-at", effective_at=1700000000)
    add("missing-scope", scope=_MISSING)
    add("null-scope", scope=None)
    add("non-mapping-scope", scope=["make-plan"])
    add("missing-scope-axis", scope={"skills": [], "backends": []})
    add(
        "unknown-scope-axis",
        scope={"skills": [], "backends": [], "roles": [], "repositories": ["repo"]},
    )
    add("non-list-scope-value", scope={"skills": "make-plan", "backends": [], "roles": []})
    add("non-string-scope-item", scope={"skills": [None], "backends": [], "roles": []})
    add("blank-scope-item", scope={"skills": [" "], "backends": [], "roles": []})
    add("unknown-marker-key", cohort="not-supported")
    cases.extend(
        [
            ("non-mapping-active-list", []),
            ("non-mapping-active-string", "enabled"),
        ]
    )
    return cases


_INVALID_ACTIVE_CASES = _invalid_active_cases()


@pytest.mark.parametrize(
    ("case", "active"),
    _INVALID_ACTIVE_CASES,
    ids=[case for case, _active in _INVALID_ACTIVE_CASES],
)
def test_invalid_rollout_active_declarations_raise_schema_error(tmp_path, case, active):
    _write_config(tmp_path, {"rollout": {"active": active}})

    with pytest.raises(ConfigSchemaError):
        load_config(tmp_path)
