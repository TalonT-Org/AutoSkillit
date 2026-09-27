"""Structural contract: every local lint gate selects the population its tool owns.

A pre-commit hook or edit hook that re-declares its file selection by hand reports
``Passed``/``Skipped`` on files it never inspected once that declaration drifts. These
tests compute each local pre-commit hook's selection over the tracked files exactly as
pre-commit does and compare it with the population the wrapped tool itself reports.
"""

from __future__ import annotations

import functools
import importlib.util
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest

from autoskillit.core.io import load_yaml
from tests._git_inventory import git_ls_files
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_PRECOMMIT = REPO_ROOT / ".pre-commit-config.yaml"
_TOOL_ANNOTATIONS_SCRIPT = REPO_ROOT / "scripts" / "check_tool_annotations.py"
_UNEVALUABLE_SELECTOR_KEYS = ("types", "types_or", "exclude_types")
_RUFF_HOOK_IDS = ("ruff", "ruff-format")
# ruff's default include lists the root pyproject.toml for its RUF200 metadata rule; it is
# TOML, not Python source, and `ruff format` never formats it.
_RUFF_METADATA_INPUT: Final = "pyproject.toml"


def _config() -> dict[str, Any]:
    return load_yaml(_PRECOMMIT)


def _local_hooks() -> dict[str, dict[str, Any]]:
    return {
        hook["id"]: hook
        for repo in _config()["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    }


@functools.cache
def _tracked_regular_files() -> tuple[str, ...]:
    return tuple(
        path
        for path in git_ls_files(REPO_ROOT)
        if (REPO_ROOT / path).is_file() and not (REPO_ROOT / path).is_symlink()
    )


def _selected(hook_id: str) -> frozenset[str]:
    hook = _local_hooks()[hook_id]
    for key in _UNEVALUABLE_SELECTOR_KEYS:
        if key in hook:
            pytest.fail(
                f"local pre-commit hook {hook_id!r} declares {key!r}: declare local hook "
                "selection with `files:`/`exclude:` path regexes; identify tag selectors are "
                "not evaluable here and caused #5182 (`.pyi` is tagged `pyi`, not `python`)"
            )
    config = _config()
    levels = (
        (re.compile(config.get("files", "")), re.compile(config.get("exclude", "^$"))),
        (re.compile(hook.get("files", "")), re.compile(hook.get("exclude", "^$"))),
    )
    return frozenset(
        path
        for path in _tracked_regular_files()
        if all(files.search(path) and not exclude.search(path) for files, exclude in levels)
    )


@functools.cache
def _ruff_population() -> frozenset[str]:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--show-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=production_interpreter_env(),
    )
    listed: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rel = Path(stripped).relative_to(REPO_ROOT).as_posix()
        except ValueError:
            raise AssertionError(
                f"`ruff check --show-files` emitted a path outside REPO_ROOT: {stripped!r}"
            ) from None
        listed.add(rel)
    population = frozenset(listed.intersection(_tracked_regular_files()))
    assert population, f"`ruff check --show-files` listed no tracked file:\n{result.stdout}"
    return population


def _ruff_source_population() -> frozenset[str]:
    return _ruff_population() - {_RUFF_METADATA_INPUT}


def _load_tool_annotations_script() -> Any:
    spec = importlib.util.spec_from_file_location(
        _TOOL_ANNOTATIONS_SCRIPT.stem, _TOOL_ANNOTATIONS_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_hooks_select_by_path_regex_only() -> None:
    offenders = {
        hook_id: [key for key in _UNEVALUABLE_SELECTOR_KEYS if key in hook]
        for hook_id, hook in _local_hooks().items()
    }
    offenders = {hook_id: keys for hook_id, keys in offenders.items() if keys}
    assert not offenders, (
        f"local pre-commit hooks select by identify tags: {offenders} — replace them with "
        "`files:`/`exclude:` path regexes so the selection is evaluable against the tool's "
        "own population (`.pyi` is tagged `pyi`, not `python`, which hid a stub from ruff)"
    )


def test_ruff_hooks_select_every_file_ruff_lints() -> None:
    population = _ruff_source_population()
    missing = {hook_id: sorted(population - _selected(hook_id)) for hook_id in _RUFF_HOOK_IDS}
    missing = {hook_id: paths for hook_id, paths in missing.items() if paths}
    assert not missing, "\n".join(
        f"pre-commit hook {hook_id!r} skips {len(paths)} file(s) ruff lints: {paths[:20]}"
        for hook_id, paths in missing.items()
    ) + (
        "\nwiden the hook's `files:` regex so it selects every file "
        "`ruff check --show-files` lists"
    )


def test_ruff_metadata_input_is_the_root_pyproject() -> None:
    assert _RUFF_METADATA_INPUT in _ruff_population(), (
        f"{_RUFF_METADATA_INPUT!r} is no longer in ruff's population; the exclusion is stale"
    )


def test_local_hook_selectors_are_non_vacuous() -> None:
    vacuous = [
        f"{hook_id}: files={hook.get('files', '')!r} exclude={hook.get('exclude', '^$')!r}"
        for hook_id, hook in _local_hooks().items()
        if hook.get("language") != "fail"
        and hook.get("always_run") is not True
        and not _selected(hook_id)
    ]
    assert not vacuous, (
        "local pre-commit hooks select no tracked file and therefore never run:\n"
        + "\n".join(vacuous)
        + "\nupdate each selector to match the layout the hook's tool actually checks"
    )


def test_fail_language_hooks_select_no_tracked_file() -> None:
    matched = {
        hook_id: sorted(_selected(hook_id))[:20]
        for hook_id, hook in _local_hooks().items()
        if hook.get("language") == "fail"
    }
    matched = {hook_id: paths for hook_id, paths in matched.items() if paths}
    assert not matched, (
        f"deny-list hooks match tracked files, so every commit touching them fails: {matched}"
    )


def test_check_tool_annotations_selector_covers_script_population() -> None:
    module = _load_tool_annotations_script()
    population = {path.relative_to(REPO_ROOT).as_posix() for path in module._collect_tool_paths()}
    assert population, "check_tool_annotations.py collected no tool modules"
    missing = sorted(population - _selected("check-tool-annotations"))
    assert not missing, (
        f"check-tool-annotations does not select {len(missing)} module(s) its script checks: "
        f"{missing[:20]} — widen the hook's `files:` regex to the script's tool layout"
    )


def test_tracked_python_shebang_files_are_in_ruff_population() -> None:
    population = _ruff_population()
    outside: list[str] = []
    for path in _tracked_regular_files():
        # Short-circuit on the first byte: only files whose first byte is `#`
        # can possibly start with a `#!/...` shebang. Skip the rest to avoid an
        # open/readline per non-shebang file (~4k tracked files in this repo).
        try:
            with (REPO_ROOT / path).open("rb") as fh:
                first_byte = fh.read(1)
        except OSError:
            continue
        if first_byte != b"#":
            continue
        with (REPO_ROOT / path).open("rb") as fh:
            line = fh.readline()
        if line.startswith(b"#!") and b"python" in line and path not in population:
            outside.append(path)
    assert not outside, (
        f"tracked Python scripts outside ruff's population: {outside[:20]} — ruff does not "
        "auto-discover extensionless scripts; add the path to `[tool.ruff] extend-include` "
        "so every ruff gate owns it (the selector contract then forces the pre-commit hooks "
        "to select it)"
    )


def test_lint_after_edit_hook_accepts_every_ruff_source_suffix() -> None:
    from autoskillit.hooks.lint_after_edit_hook import RUFF_SOURCE_SUFFIXES

    suffixes = {PurePosixPath(path).suffix for path in _ruff_source_population()}
    missing = sorted(suffixes - set(RUFF_SOURCE_SUFFIXES))
    assert not missing, (
        f"lint_after_edit_hook skips suffixes ruff lints: {missing} — add them to "
        "RUFF_SOURCE_SUFFIXES"
    )
