"""Unit tests for scripts/e2e/e2e_select.py and the catalog schema it reads."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import cast

import pytest

from tests.infra._complexity_helpers import load_check_script

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
_SELECT_SCRIPT = REPO_ROOT / "scripts" / "e2e" / "e2e_select.py"

select = load_check_script("_autoskillit_e2e_select", _SELECT_SCRIPT)
e2e_catalog = select.e2e_catalog

REPOSITORY = "TalonT-Org/AutoSkillit"
SHA_ZERO = "0" * 40
SYNTHETIC_SHAS = [hashlib.sha1(str(index).encode()).hexdigest() for index in range(2000)]


def _facts(
    *,
    number: int = 5230,
    head_sha: str = SHA_ZERO,
    head_repository: str | None = REPOSITORY,
    changed_files: int = 10,
    additions: int = 0,
    deletions: int = 0,
    labels: tuple[str, ...] = (),
):
    return select.PullRequestFacts(
        number=number,
        head_sha=head_sha,
        head_repository=head_repository,
        changed_files=changed_files,
        additions=additions,
        deletions=deletions,
        labels=frozenset(labels),
    )


def _entry(name: str, trigger_paths: tuple[str, ...] = ("src/*",), **overrides) -> dict:
    entry = {
        "name": name,
        "kind": "canary",
        "peak_sessions": 1,
        "timeout_sec": 300,
        "trigger_paths": list(trigger_paths),
    }
    entry.update(overrides)
    return entry


_CLEAN_INSTALL_TRIGGER_PATHS = (
    "src/autoskillit/cli/*",
    "src/autoskillit/workspace/*",
    "scripts/docker/*",
    "scripts/e2e/*",
    ".github/workflows/e2e.yml",
)
_HEADLESS_SMOKE_TRIGGER_PATHS = (
    "src/autoskillit/execution/headless/session.py",
    "src/autoskillit/execution/backends/claude.py",
    "src/autoskillit/cli/fleet/run.py",
    "src/autoskillit/fleet/dispatch/worker.py",
    "src/autoskillit/server/recipe.py",
    "src/autoskillit/hooks/runner.py",
    "scripts/e2e/e2e_harness.py",
    ".github/workflows/e2e.yml",
)


def _clean_install_entry(**overrides) -> dict:
    entry = _entry(
        "clean-install",
        _CLEAN_INSTALL_TRIGGER_PATHS,
        kind="clean-install",
        peak_sessions=0,
        timeout_sec=300,
    )
    entry.update(overrides)
    return entry


_EXPECTED_FAILURE = {
    "severity": "error",
    "check": "install-check",
    "message": "expected install failure",
    "issue": "https://github.com/TalonT-Org/AutoSkillit/issues/123",
}


def _raw_catalog(*entries: dict) -> dict:
    return {"sandbox_repository": "owner/sandbox", "tests": list(entries)}


def _catalog(*entries: dict):
    return e2e_catalog.parse_catalog(_raw_catalog(*entries))


def _sha_with_sample(number: int, *, sampled: bool) -> str:
    for index in itertools.count():
        sha = f"{index:040x}"
        if (select.sample_score(number, sha) < select.SAMPLE_PROBABILITY) == sampled:
            return sha
    raise AssertionError("unreachable")


_FIVE_TESTS = tuple(_entry(f"t{index}", (f"area{index}/*",)) for index in range(5))


class TestEligibility:
    def test_ten_changed_files_is_eligible(self) -> None:
        assert select.is_eligible(_facts(changed_files=10))

    def test_three_hundred_changed_lines_is_eligible(self) -> None:
        assert select.is_eligible(_facts(changed_files=1, additions=200, deletions=100))

    def test_below_both_thresholds_is_not_eligible(self) -> None:
        assert not select.is_eligible(_facts(changed_files=9, additions=200, deletions=99))

    def test_e2e_label_makes_a_one_file_change_eligible(self) -> None:
        assert select.is_eligible(_facts(changed_files=1, labels=("e2e",)))


class TestSampleScore:
    def test_score_is_the_first_eight_digest_bytes_over_two_to_the_sixty_four(self) -> None:
        digest = hashlib.sha256(b"5230:" + b"0" * 40).digest()
        expected = int.from_bytes(digest[:8], "big") / 2**64
        assert select.sample_score(5230, SHA_ZERO) == expected

    def test_score_is_deterministic(self) -> None:
        assert select.sample_score(5230, SHA_ZERO) == select.sample_score(5230, SHA_ZERO)

    def test_half_probability_samples_about_half(self) -> None:
        sampled = sum(select.is_sampled(_facts(head_sha=sha), 0.5) for sha in SYNTHETIC_SHAS)
        assert 0.45 <= sampled / len(SYNTHETIC_SHAS) <= 0.55

    def test_zero_probability_never_samples(self) -> None:
        assert not any(select.is_sampled(_facts(head_sha=sha), 0.0) for sha in SYNTHETIC_SHAS)

    def test_unit_probability_always_samples(self) -> None:
        assert all(select.is_sampled(_facts(head_sha=sha), 1.0) for sha in SYNTHETIC_SHAS)


class TestSelectForPullRequest:
    def test_eligible_and_sampled_is_selected(self) -> None:
        pr = _facts(head_sha=_sha_with_sample(5230, sampled=True))
        selection = select.select_for_pull_request(pr, (), _catalog(_entry("a")), REPOSITORY)
        assert selection.tests == ("a",)
        assert selection.reason == "eligible and sampled"

    def test_eligible_but_sampled_out_is_not_selected(self) -> None:
        pr = _facts(head_sha=_sha_with_sample(5230, sampled=False))
        selection = select.select_for_pull_request(pr, (), _catalog(_entry("a")), REPOSITORY)
        assert not selection.selected
        assert "not sampled" in selection.reason

    def test_e2e_label_bypasses_sampling(self) -> None:
        pr = _facts(head_sha=_sha_with_sample(5230, sampled=False), labels=("e2e",))
        selection = select.select_for_pull_request(pr, (), _catalog(_entry("a")), REPOSITORY)
        assert selection.tests == ("a",)

    def test_fork_head_is_not_selected(self) -> None:
        pr = _facts(head_repository="someone/AutoSkillit", labels=("e2e",))
        selection = select.select_for_pull_request(pr, (), _catalog(_entry("a")), REPOSITORY)
        assert not selection.selected
        assert "someone/AutoSkillit" in selection.reason

    def test_deleted_head_repository_is_not_selected(self) -> None:
        payload = {
            "number": 5230,
            "head": {"sha": SHA_ZERO, "repo": None},
            "changed_files": 50,
            "additions": 1000,
            "deletions": 0,
            "labels": [{"name": "e2e"}],
        }
        pr = select.PullRequestFacts.from_payload(payload)
        selection = select.select_for_pull_request(pr, (), _catalog(_entry("a")), REPOSITORY)
        assert pr.head_repository is None
        assert not selection.selected


class TestPickTests:
    _SEED = f"5230:{SHA_ZERO}"

    def test_all_touched_caps_at_two(self) -> None:
        changed = [f"area{index}/x.py" for index in range(5)]
        assert len(select.pick_tests(_catalog(*_FIVE_TESTS), changed, self._SEED)) == 2

    def test_none_touched_picks_exactly_one(self) -> None:
        assert len(select.pick_tests(_catalog(*_FIVE_TESTS), ["docs/x.md"], self._SEED)) == 1

    @pytest.mark.parametrize("seed_index", range(20))
    def test_one_touched_picks_only_that_test(self, seed_index: int) -> None:
        picked = select.pick_tests(_catalog(*_FIVE_TESTS), ["area3/x.py"], f"{seed_index}:sha")
        assert picked == ("t3",)

    def test_single_test_catalog_picks_it(self) -> None:
        assert select.pick_tests(_catalog(_entry("only")), [], self._SEED) == ("only",)

    @pytest.mark.parametrize(
        "changed",
        [[], ["docs/x.md"], ["area0/x.py"], [f"area{index}/x.py" for index in range(5)]],
    )
    def test_selection_is_never_empty_and_deterministic(self, changed: list[str]) -> None:
        first = select.pick_tests(_catalog(*_FIVE_TESTS), changed, self._SEED)
        assert first
        assert first == select.pick_tests(_catalog(*_FIVE_TESTS), changed, self._SEED)

    def test_rank_is_the_sha256_hex_digest_of_seed_and_name(self) -> None:
        catalog = _catalog(_entry("alpha"), _entry("beta"))
        expected = sorted(
            ["alpha", "beta"],
            key=lambda name: hashlib.sha256(f"{self._SEED}:{name}".encode()).hexdigest(),
        )
        assert select.pick_tests(catalog, ["src/x.py"], self._SEED) == tuple(expected)

    def test_star_matches_across_directories(self) -> None:
        catalog = _catalog(_entry("deep", ("src/*",)), _entry("other", ("docs/*",)))
        assert select.pick_tests(catalog, ["src/a/b/c.py"], self._SEED) == ("deep",)


class TestSelectForEvent:
    def test_merge_group_is_never_selected(self) -> None:
        selection = select.select_for_event(
            "merge_group", {}, (), _catalog(_entry("a")), REPOSITORY
        )
        assert not selection.selected
        assert "merge queue" in selection.reason

    def test_unsupported_event_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported event"):
            select.select_for_event("push", {}, (), _catalog(_entry("a")), REPOSITORY)

    def test_pull_request_without_pull_request_object_raises(self) -> None:
        with pytest.raises(ValueError):
            select.select_for_event("pull_request", {}, (), _catalog(_entry("a")), REPOSITORY)

    def test_dispatch_reads_inputs_tests(self) -> None:
        catalog = e2e_catalog.load_catalog()
        payload = {"inputs": {"tests": "canary"}}
        selection = select.select_for_event("workflow_dispatch", payload, (), catalog, REPOSITORY)
        assert selection.tests == ("canary",)

    def test_dispatch_deduplicates_names(self) -> None:
        catalog = e2e_catalog.load_catalog()
        assert select.select_for_dispatch("canary, canary", catalog).tests == ("canary",)

    @pytest.mark.parametrize("requested", ["t0,t1,t2", "t0,nope", "", " , "])
    def test_dispatch_rejects_invalid_requests(self, requested: str) -> None:
        with pytest.raises(ValueError):
            select.select_for_dispatch(requested, _catalog(*_FIVE_TESTS))

    def test_dispatch_without_inputs_raises(self) -> None:
        with pytest.raises(ValueError, match="inputs.tests"):
            select.select_for_event("workflow_dispatch", {}, (), _catalog(_entry("a")), REPOSITORY)


_PASSING_GATE_ROWS = {("success", "false", "skipped"), ("success", "true", "success")}
_RESULTS = ("success", "failure", "cancelled", "skipped", "")


@pytest.mark.parametrize(
    ("select_result", "selected", "run_result"),
    list(itertools.product(_RESULTS, ("true", "false", ""), _RESULTS)),
)
def test_gate_passes_only_on_the_two_healthy_shapes(
    select_result: str, selected: str, run_result: str
) -> None:
    passed, reason = select.gate_verdict(select_result, selected, run_result)
    assert passed is ((select_result, selected, run_result) in _PASSING_GATE_ROWS)
    assert reason


def test_matrix_for_an_empty_selection_is_parseable() -> None:
    selection = select.Selection((), "nothing selected")
    assert select.matrix_json(selection, _catalog(_entry("a"))) == '{"include":[]}'


def test_matrix_includes_kind_from_each_catalog_entry() -> None:
    catalog = _catalog(_clean_install_entry(), _entry("canary"))
    selection = select.Selection(("clean-install", "canary"), "selected")
    assert select.matrix_json(selection, catalog) == (
        '{"include":[{"test":"clean-install","kind":"clean-install",'
        '"timeout_minutes":37},{"test":"canary","kind":"canary",'
        '"timeout_minutes":37}]}'
    )


_RECIPE_FIELDS = {
    "recipe": "implementation",
    "ingredients": {"task": "Add a greeting"},
    "expected_pull_request_state": "merged",
}
_MAX_TIMEOUT_SEC = (
    e2e_catalog.MAX_JOB_MINUTES - e2e_catalog.JOB_OVERHEAD_MINUTES
) * 60 - e2e_catalog.HARNESS_GRACE_SEC


def _recipe_entry_without(field: str) -> dict:
    fields = {key: value for key, value in _RECIPE_FIELDS.items() if key != field}
    return _entry("impl", kind="recipe", **fields)


_INVALID_CATALOGS = {
    "duplicate-names": _raw_catalog(_entry("a"), _entry("a")),
    "bad-name": _raw_catalog(_entry("Bad_Name")),
    "unknown-kind": _raw_catalog(_entry("a", kind="smoke")),
    "zero-peak-sessions": _raw_catalog(_entry("a", peak_sessions=0)),
    "zero-recipe-peak-sessions": _raw_catalog(
        _entry("impl", kind="recipe", peak_sessions=0, **_RECIPE_FIELDS)
    ),
    "nine-peak-sessions": _raw_catalog(_entry("a", peak_sessions=9)),
    "clean-install-nonzero-peak-sessions": _raw_catalog(_clean_install_entry(peak_sessions=1)),
    "timeout-over-job-limit": _raw_catalog(_entry("a", timeout_sec=_MAX_TIMEOUT_SEC + 1)),
    "recipe-without-recipe": _raw_catalog(_recipe_entry_without("recipe")),
    "recipe-without-ingredients": _raw_catalog(_recipe_entry_without("ingredients")),
    "recipe-without-state": _raw_catalog(_recipe_entry_without("expected_pull_request_state")),
    "recipe-unknown-state": _raw_catalog(
        _entry("a", kind="recipe", **{**_RECIPE_FIELDS, "expected_pull_request_state": "draft"})
    ),
    "recipe-fixture-path": _raw_catalog(
        _entry("a", kind="recipe", recipe_fixture="../sandbox-smoke.yaml", **_RECIPE_FIELDS)
    ),
    "recipe-fixture-extension": _raw_catalog(
        _entry("a", kind="recipe", recipe_fixture="sandbox-smoke.yml", **_RECIPE_FIELDS)
    ),
    "canary-with-recipe-fields": _raw_catalog(_entry("a", **_RECIPE_FIELDS)),
    "canary-with-recipe-fixture": _raw_catalog(_entry("a", recipe_fixture="sandbox-smoke.yaml")),
    "clean-install-with-recipe-fields": _raw_catalog(_clean_install_entry(**_RECIPE_FIELDS)),
    "clean-install-with-recipe-fixture": _raw_catalog(
        _clean_install_entry(recipe_fixture="sandbox-smoke.yaml")
    ),
    "clean-install-unknown-key": _raw_catalog(_clean_install_entry(extra=1)),
    "expected-failures-on-canary": _raw_catalog(
        _entry("a", expected_failures=[_EXPECTED_FAILURE])
    ),
    "expected-failures-warning-on-recipe": _raw_catalog(
        _entry(
            "impl",
            kind="recipe",
            expected_failures=[{**_EXPECTED_FAILURE, "severity": "warning"}],
            **_RECIPE_FIELDS,
        )
    ),
    "expected-failures-missing-field": _raw_catalog(
        _clean_install_entry(
            expected_failures=[
                {key: value for key, value in _EXPECTED_FAILURE.items() if key != "message"}
            ]
        )
    ),
    "expected-failures-extra-field": _raw_catalog(
        _clean_install_entry(expected_failures=[{**_EXPECTED_FAILURE, "extra": "value"}])
    ),
    "expected-failures-empty-message": _raw_catalog(
        _clean_install_entry(expected_failures=[{**_EXPECTED_FAILURE, "message": ""}])
    ),
    "expected-failures-empty-check": _raw_catalog(
        _clean_install_entry(expected_failures=[{**_EXPECTED_FAILURE, "check": ""}])
    ),
    "expected-failures-unknown-severity": _raw_catalog(
        _clean_install_entry(expected_failures=[{**_EXPECTED_FAILURE, "severity": "info"}])
    ),
    "expected-failures-zero-issue": _raw_catalog(
        _clean_install_entry(
            expected_failures=[
                {
                    **_EXPECTED_FAILURE,
                    "issue": "https://github.com/TalonT-Org/AutoSkillit/issues/0",
                }
            ]
        )
    ),
    "expected-failures-bad-issue-url": _raw_catalog(
        _clean_install_entry(
            expected_failures=[{**_EXPECTED_FAILURE, "issue": "https://example.com/issues/123"}]
        )
    ),
    "expected-failures-duplicate-identity": _raw_catalog(
        _clean_install_entry(
            expected_failures=[
                _EXPECTED_FAILURE,
                {
                    **_EXPECTED_FAILURE,
                    "issue": "https://github.com/TalonT-Org/AutoSkillit/issues/124",
                },
            ]
        )
    ),
    "empty-trigger-paths": _raw_catalog(_entry("a", ())),
    "unknown-key": _raw_catalog(_entry("a", extra=1)),
    "unknown-top-level-key": {**_raw_catalog(_entry("a")), "extra": 1},
    "no-tests": _raw_catalog(),
}


class TestCatalog:
    def test_shipped_catalog_holds_the_canary(self) -> None:
        catalog = e2e_catalog.load_catalog()
        canary = catalog.get("canary")
        assert canary.kind == "canary"
        assert canary.recipe is None
        assert e2e_catalog.job_timeout_minutes(canary) == 37

        clean_install = catalog.get("clean-install")
        assert clean_install.kind == "clean-install"
        assert clean_install.peak_sessions == 0
        assert clean_install.timeout_sec == 300
        assert clean_install.trigger_paths == _CLEAN_INSTALL_TRIGGER_PATHS
        assert clean_install.recipe is None

        smoke = catalog.get("headless-smoke")
        assert smoke.kind == "recipe"
        assert smoke.peak_sessions == 2
        assert smoke.timeout_sec == 900
        assert smoke.recipe == "sandbox-smoke"
        assert smoke.recipe_fixture == "sandbox-smoke.yaml"
        assert smoke.ingredients == ()
        assert smoke.expected_pull_request_state == "closed"
        assert smoke.expected_failures == ()
        assert smoke.trigger_paths == (
            "src/autoskillit/execution/headless/*",
            "src/autoskillit/execution/backends/*",
            "src/autoskillit/cli/fleet/*",
            "src/autoskillit/fleet/dispatch/*",
            "src/autoskillit/server/*",
            "src/autoskillit/hooks/*",
            "scripts/e2e/*",
            ".github/workflows/e2e.yml",
        )

    def test_clean_install_expected_failures_are_immutable_mapping_rows(self) -> None:
        rows = [{**_EXPECTED_FAILURE}, {**_EXPECTED_FAILURE, "severity": "warning"}]
        test = _catalog(_clean_install_entry(expected_failures=rows)).get("clean-install")

        assert isinstance(test.expected_failures, tuple)
        assert len(test.expected_failures) == 2
        for row in test.expected_failures:
            assert isinstance(row, Mapping)
            assert not isinstance(row, MutableMapping)
            with pytest.raises(TypeError):
                cast(MutableMapping[str, str], row)["message"] = "changed"

    def test_clean_install_expected_failures_allow_distinct_identities(self) -> None:
        rows = [
            _EXPECTED_FAILURE,
            {**_EXPECTED_FAILURE, "message": "another message"},
            {**_EXPECTED_FAILURE, "severity": "warning"},
        ]
        test = _catalog(_clean_install_entry(expected_failures=rows)).get("clean-install")
        assert len(test.expected_failures) == 3

    def test_recipe_entry_parses(self) -> None:
        catalog = _catalog(
            _entry(
                "impl",
                kind="recipe",
                recipe_fixture="sandbox-smoke.yaml",
                **_RECIPE_FIELDS,
            )
        )
        test = catalog.get("impl")
        assert test.recipe == "implementation"
        assert test.ingredients == (("task", "Add a greeting"),)
        assert test.expected_pull_request_state == "merged"
        assert test.recipe_fixture == "sandbox-smoke.yaml"

    def test_recipe_without_fixture_defaults_to_none(self) -> None:
        test = _catalog(_entry("impl", kind="recipe", **_RECIPE_FIELDS)).get("impl")
        assert test.recipe_fixture is None

    def test_recipe_expected_failure_keeps_exact_error_diagnostic(self) -> None:
        row = {
            **_EXPECTED_FAILURE,
            "check": " exact-check ",
            "message": " exact message ",
        }
        test = _catalog(
            _entry(
                "impl",
                kind="recipe",
                expected_failures=[row],
                **_RECIPE_FIELDS,
            )
        ).get("impl")
        assert dict(test.expected_failures[0]) == row

    @pytest.mark.parametrize("changed_path", _HEADLESS_SMOKE_TRIGGER_PATHS)
    def test_headless_smoke_trigger_paths_match(self, changed_path: str) -> None:
        catalog = e2e_catalog.load_catalog()
        smoke = catalog.get("headless-smoke")
        assert select._touches(smoke, (changed_path,))

    def test_headless_smoke_path_selects_shipped_recipe(self) -> None:
        catalog = e2e_catalog.load_catalog()
        assert select.pick_tests(
            catalog,
            ["src/autoskillit/execution/headless/session.py"],
            f"5230:{SHA_ZERO}",
        ) == ("headless-smoke",)

    def test_dispatch_selects_headless_smoke(self) -> None:
        catalog = e2e_catalog.load_catalog()
        assert select.select_for_dispatch("headless-smoke", catalog).tests == ("headless-smoke",)

    def test_longest_timeout_within_the_job_limit_parses(self) -> None:
        catalog = _catalog(_entry("slow", timeout_sec=_MAX_TIMEOUT_SEC))
        assert e2e_catalog.job_timeout_minutes(catalog.get("slow")) == e2e_catalog.MAX_JOB_MINUTES

    def test_unknown_test_name_raises_catalog_error(self) -> None:
        with pytest.raises(e2e_catalog.CatalogError):
            _catalog(_entry("a")).get("b")

    @pytest.mark.parametrize("raw", _INVALID_CATALOGS.values(), ids=list(_INVALID_CATALOGS))
    def test_invalid_catalog_raises(self, raw: dict) -> None:
        with pytest.raises(e2e_catalog.CatalogError):
            e2e_catalog.parse_catalog(raw)
