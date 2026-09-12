"""Parity tests pinning scripts/check_complexity.py's counter to Ruff's C901 (mccabe).

Ruff is a required development dependency for this file: a missing binary or a failed
version probe fails these tests outright -- it never quietly skips parity coverage.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import production_interpreter_env
from tests.infra.conftest import _CONSTRUCT_CASES, load_check_script

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_complexity.py"
_CHECK_MODULE_NAME = "_autoskillit_check_complexity_ruff_parity"
_RUFF_TIMEOUT_SECONDS = 30

check = load_check_script(_CHECK_MODULE_NAME, _CHECK_SCRIPT)


# --- ruff invocation: a required dependency, never skipped --------------------------------


def _require_ruff() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=_RUFF_TIMEOUT_SECONDS,
            env=production_interpreter_env(),
        )
    except OSError as exc:
        raise AssertionError(f"ruff is not runnable via {sys.executable} -m ruff: {exc}") from exc
    assert result.returncode == 0, f"ruff --version failed:\n{result.stderr}"


@pytest.fixture(autouse=True)
def _ruff_required():
    _require_ruff()


_RUFF_ARGS = [
    "check",
    "--isolated",
    "--no-cache",
    "--select",
    "C901",
    "--config",
    "lint.mccabe.max-complexity=1",
    "--config",
    "target-version='py311'",
    "--output-format",
    "json",
]

_TOO_COMPLEX_RE = re.compile(r"`(.+?)` is too complex \((\d+) > 1\)")


def _run_ruff_json(path: Path) -> list[dict]:
    """Ruff exits 1 when it reports diagnostics -- that is expected success, not a failure.
    Any other exit code, or output that fails to parse as JSON, fails loudly."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", *_RUFF_ARGS, str(path)],
        capture_output=True,
        text=True,
        timeout=_RUFF_TIMEOUT_SECONDS,
        env=production_interpreter_env(),
    )
    assert result.returncode in (0, 1), (
        f"ruff exited {result.returncode}, expected 0 or 1\nstderr:\n{result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"ruff output did not parse as JSON: {exc}\n{result.stdout}") from exc


def _ruff_mapping(path: Path) -> dict[tuple[int, str], int]:
    mapping: dict[tuple[int, str], int] = {}
    for entry in _run_ruff_json(path):
        match = _TOO_COMPLEX_RE.match(entry["message"])
        assert match is not None, f"unexpected ruff message shape: {entry['message']!r}"
        name = match.group(1)
        complexity = int(match.group(2))
        row = entry["location"]["row"]
        mapping[(row, name)] = complexity
    return mapping


def _own_mapping(source: str) -> dict[tuple[int, str], int]:
    tree = ast.parse(source)
    metrics = check.function_metrics(tree)
    return {
        (m.lineno, qualname.rsplit(".", 1)[-1]): m.complexity
        for qualname, m in metrics.items()
        if m.complexity >= 2
    }


# --- construct fixture: every branch type T1 exercises, as uniquely-named top-level defs --


def _build_construct_fixture() -> str:
    parts = []
    for label, source, _expected in _CONSTRUCT_CASES:
        name = f"construct_{label}"
        # T1's snippets are each a lone top-level `def f():` (or `async def f():`); giving
        # each a unique name avoids top-level-name collisions when concatenated into one file.
        renamed = source.replace("def f():", f"def {name}():", 1)
        assert renamed != source, f"{label}: snippet did not contain 'def f():' to rename"
        parts.append(renamed)
    return "\n\n".join(parts)


def test_counter_matches_ruff_on_construct_fixture(tmp_path):
    fixture_source = _build_construct_fixture()
    fixture_path = tmp_path / "constructs.py"
    fixture_path.write_text(fixture_source, encoding="utf-8")

    ruff_mapping = _ruff_mapping(fixture_path)
    own_mapping = _own_mapping(fixture_source)

    assert ruff_mapping == own_mapping
    assert len(own_mapping) >= 20


# --- real repository files -----------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path", ["tests/_test_filter.py", "src/autoskillit/recipe/validator.py"]
)
def test_counter_matches_ruff_on_repository_files(relative_path):
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"{relative_path} not found at {path}"
    source = path.read_text(encoding="utf-8")

    assert _ruff_mapping(path) == _own_mapping(source)
