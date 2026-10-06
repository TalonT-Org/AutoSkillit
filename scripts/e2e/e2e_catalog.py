"""Load and validate the E2E test catalog shared by the selector and the harness."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

CATALOG_PATH = Path(__file__).with_name("catalog.json")
# One step fans out to at most six subagents beside its orchestrator and step session, and one
# run at a time keeps CI at or below eight of the roughly twelve concurrent MiniMax sessions.
MAX_PEAK_SESSIONS = 8
# Setup, assertions and cleanup around the test's own budget, so every harness timeout fires
# before the job ceiling.
HARNESS_GRACE_SEC = 120
# Image build and load, redaction and artifact upload.
JOB_OVERHEAD_MINUTES = 30
MAX_JOB_MINUTES = 360
KINDS = ("canary", "recipe", "clean-install")
PULL_REQUEST_STATES = ("open", "merged", "closed")

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RECIPE_FIXTURE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*\.yaml$")
_CATALOG_KEYS = frozenset({"sandbox_repository", "tests"})
_COMMON_KEYS = frozenset({"name", "kind", "peak_sessions", "timeout_sec", "trigger_paths"})
_RECIPE_KEYS = frozenset({"recipe", "ingredients", "expected_pull_request_state"})
_EXPECTED_FAILURE_KEYS = frozenset({"severity", "check", "message", "issue"})
_BUG_URL_PATTERN = re.compile(r"https://github\.com/TalonT-Org/AutoSkillit/issues/[1-9][0-9]*")


class CatalogError(ValueError):
    """The catalog violates its schema."""


@dataclass(frozen=True, slots=True)
class CatalogTest:
    """One E2E test and the resources it is allowed to use."""

    name: str
    kind: str
    peak_sessions: int
    timeout_sec: int
    trigger_paths: tuple[str, ...]
    recipe: str | None
    ingredients: tuple[tuple[str, str], ...]
    expected_pull_request_state: str | None
    expected_failures: tuple[Mapping[str, str], ...] = ()
    recipe_fixture: str | None = None


@dataclass(frozen=True, slots=True)
class Catalog:
    """Every registered E2E test and the sandbox repository recipe tests run against."""

    sandbox_repository: str
    tests: tuple[CatalogTest, ...]

    def get(self, name: str) -> CatalogTest:
        for test in self.tests:
            if test.name == name:
                return test
        raise CatalogError(f"unknown E2E test: {name!r}")


def job_timeout_minutes(test: CatalogTest) -> int:
    return math.ceil((test.timeout_sec + HARNESS_GRACE_SEC) / 60) + JOB_OVERHEAD_MINUTES


def _require_keys(raw: Mapping[str, object], expected: frozenset[str], where: str) -> None:
    missing = sorted(expected - raw.keys())
    unknown = sorted(raw.keys() - expected)
    if missing or unknown:
        raise CatalogError(f"{where}: missing keys {missing}, unknown keys {unknown}")


def _require_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{where} must be a non-empty string")
    return value


def _require_int(value: object, where: str, *, low: int, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogError(f"{where} must be an integer")
    if value < low or (high is not None and value > high):
        bound = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise CatalogError(f"{where} must be {bound}")
    return value


def _parse_trigger_paths(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CatalogError(f"{where} must be a non-empty list")
    return tuple(_require_str(item, f"{where}[]") for item in value)


def _parse_ingredients(value: object, where: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise CatalogError(f"{where} must be an object")
    pairs: list[tuple[str, str]] = []
    for key, item in value.items():
        if not isinstance(item, str):
            raise CatalogError(f"{where}.{key} must be a string")
        pairs.append((_require_str(key, f"{where} key"), item))
    return tuple(pairs)


def _parse_recipe_fields(
    raw: Mapping[str, object], where: str
) -> tuple[str, tuple[tuple[str, str], ...], str]:
    recipe = _require_str(raw["recipe"], f"{where}.recipe")
    ingredients = _parse_ingredients(raw["ingredients"], f"{where}.ingredients")
    state = _require_str(
        raw["expected_pull_request_state"], f"{where}.expected_pull_request_state"
    )
    if state not in PULL_REQUEST_STATES:
        raise CatalogError(
            f"{where}.expected_pull_request_state must be one of {PULL_REQUEST_STATES}"
        )
    return recipe, ingredients, state


def _parse_expected_failures(value: object, where: str) -> tuple[Mapping[str, str], ...]:
    where = f"{where}.expected_failures"
    if not isinstance(value, list):
        raise CatalogError(f"{where} must be a list")
    rows: list[Mapping[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(value):
        location = f"{where}[{index}]"
        if not isinstance(raw, dict):
            raise CatalogError(f"{location} must be an object")
        _require_keys(raw, _EXPECTED_FAILURE_KEYS, location)
        row = {key: _require_str(raw[key], f"{location}.{key}") for key in _EXPECTED_FAILURE_KEYS}
        if any(not item.strip() for item in row.values()):
            raise CatalogError(f"{location} fields must be non-empty")
        if row["severity"] not in ("error", "warning"):
            raise CatalogError(f"{location}.severity must be error or warning")
        if not _BUG_URL_PATTERN.fullmatch(row["issue"]):
            raise CatalogError(f"{location}.issue must link to an AutoSkillit bug issue")
        identity = (row["severity"], row["check"], row["message"])
        if identity in seen:
            raise CatalogError(f"{location}: duplicate diagnostic {identity}")
        seen.add(identity)
        rows.append(MappingProxyType(row))
    return tuple(rows)


def _parse_test(raw: object, index: int) -> CatalogTest:
    where = f"tests[{index}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where} must be an object")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise CatalogError(f"{where}.kind must be one of {KINDS}")
    keys = _COMMON_KEYS | _RECIPE_KEYS if kind == "recipe" else _COMMON_KEYS
    if kind == "recipe" and "recipe_fixture" in raw:
        keys |= {"recipe_fixture"}
    if kind in ("recipe", "clean-install") and "expected_failures" in raw:
        keys |= {"expected_failures"}
    _require_keys(raw, keys, where)
    name = _require_str(raw["name"], f"{where}.name")
    if not _NAME_PATTERN.fullmatch(name):
        raise CatalogError(f"{where}.name must match {_NAME_PATTERN.pattern}")
    recipe, ingredients, state = (
        _parse_recipe_fields(raw, where) if kind == "recipe" else (None, (), None)
    )
    recipe_fixture = None
    if kind == "recipe" and "recipe_fixture" in raw:
        recipe_fixture = _require_str(raw["recipe_fixture"], f"{where}.recipe_fixture")
        if not _RECIPE_FIXTURE_PATTERN.fullmatch(recipe_fixture):
            raise CatalogError(f"{where}.recipe_fixture must be a .yaml basename")
    expected_failures = _parse_expected_failures(raw.get("expected_failures", []), where)
    if kind == "recipe" and any(row["severity"] != "error" for row in expected_failures):
        raise CatalogError(f"{where}.expected_failures severity must be error for recipe tests")
    test = CatalogTest(
        name=name,
        kind=kind,
        peak_sessions=_require_int(
            raw["peak_sessions"],
            f"{where}.peak_sessions",
            low=0 if kind == "clean-install" else 1,
            high=0 if kind == "clean-install" else MAX_PEAK_SESSIONS,
        ),
        timeout_sec=_require_int(raw["timeout_sec"], f"{where}.timeout_sec", low=1),
        trigger_paths=_parse_trigger_paths(raw["trigger_paths"], f"{where}.trigger_paths"),
        recipe=recipe,
        ingredients=ingredients,
        expected_pull_request_state=state,
        expected_failures=expected_failures,
        recipe_fixture=recipe_fixture,
    )
    if job_timeout_minutes(test) > MAX_JOB_MINUTES:
        raise CatalogError(f"{where}.timeout_sec exceeds the {MAX_JOB_MINUTES}-minute job limit")
    return test


def parse_catalog(raw: object) -> Catalog:
    if not isinstance(raw, dict):
        raise CatalogError("catalog must be an object")
    _require_keys(raw, _CATALOG_KEYS, "catalog")
    repository = _require_str(raw["sandbox_repository"], "sandbox_repository")
    if repository.count("/") != 1:
        raise CatalogError("sandbox_repository must be OWNER/REPO")
    entries = raw["tests"]
    if not isinstance(entries, list) or not entries:
        raise CatalogError("tests must be a non-empty list")
    tests = tuple(_parse_test(entry, index) for index, entry in enumerate(entries))
    names = [test.name for test in tests]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise CatalogError(f"duplicate test names: {duplicates}")
    return Catalog(sandbox_repository=repository, tests=tests)


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{path}: {exc}") from exc
    return parse_catalog(raw)
