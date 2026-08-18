"""Contract: each registered policy function has exactly one call site (#4684 Fix D).

Wraps scripts/check_single_enforcement_point.py — a genuinely bespoke AST
call-site equivalence-class resolver with no external prior art in this
codebase (scripts/check_tool_annotations.py's AST scan is a flat decorator
match, not an import-alias/wrapper-equivalence-class resolver). Per the
rectify plan, this mechanism gets thorough test coverage against synthetic
fixtures — import aliases, wrapper-function equivalence classes, and the
backend-specific exemption list — before being trusted as a merge gate,
in addition to the integration check against the real codebase.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_single_enforcement_point.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_single_enforcement_point", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_checker = _load_module()


def _write(root: Path, relpath: str, source: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


# Every "terminal call site" fixture below deliberately uses a two-statement
# body (call, then a conditional), never a bare `return <call>(...)`
# one-liner. A one-liner is indistinguishable by shape from a pure-return
# wrapper (see test_wrapper_function_equivalence_class) and would make the
# fixture itself join the equivalence class instead of being counted — which
# is also why every real production gate (assert_interactive_ordering,
# validate_interactive_invocation) is a multi-statement function, never a
# one-line pass-through.
def _direct_caller_source(func_name: str, *, target: str = "_policy") -> str:
    import_line = (
        "from policy_mod import _policy\n\n\n"
        if target == "_policy"
        else f"from wrapper_mod import {target}\n\n\n"
    )
    return import_line + (
        f"def {func_name}(spec):\n"
        f"    errors = {target}(spec)\n"
        "    if errors:\n"
        "        raise ValueError('policy violated')\n"
    )


def test_single_call_site_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_checker, "SRC_ROOT", tmp_path)
    _write(tmp_path, "policy_mod.py", "def _policy(spec):\n    return []\n")
    _write(tmp_path, "caller_mod.py", _direct_caller_source("gate"))
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert sites == ["caller_mod.py:gate"]


def test_two_direct_call_sites_is_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_checker, "SRC_ROOT", tmp_path)
    _write(tmp_path, "policy_mod.py", "def _policy(spec):\n    return []\n")
    _write(tmp_path, "gate_a.py", _direct_caller_source("check_a"))
    _write(tmp_path, "gate_b.py", _direct_caller_source("check_b"))
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert sites == ["gate_a.py:check_a", "gate_b.py:check_b"]


def test_import_alias_forms_all_resolve_to_the_same_call_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """from X import Y as Z, import X (X.Y(...)), and a bare `from X import Y` all resolve."""
    monkeypatch.setattr(_checker, "SRC_ROOT", tmp_path)
    _write(tmp_path, "policy_mod.py", "def _policy(spec):\n    return []\n")
    _write(
        tmp_path,
        "aliased_from_import.py",
        "from policy_mod import _policy as _renamed\n\n\n"
        "def gate(spec):\n"
        "    errors = _renamed(spec)\n"
        "    if errors:\n"
        "        raise ValueError('policy violated')\n",
    )
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert sites == ["aliased_from_import.py:gate"]

    _write(tmp_path, "aliased_from_import.py", "")  # remove the previous fixture
    _write(
        tmp_path,
        "module_import.py",
        "import policy_mod\n\n\n"
        "def gate(spec):\n"
        "    errors = policy_mod._policy(spec)\n"
        "    if errors:\n"
        "        raise ValueError('policy violated')\n",
    )
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert sites == ["module_import.py:gate"]


def test_wrapper_function_equivalence_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pure `return _policy(...)` wrapper joins the equivalence class.

    Two callers of the SAME wrapper are one call site (the wrapper's);
    but a caller of the wrapper AND a direct caller of the policy are two
    distinct call sites — both are still "the policy," just reached two
    different ways, which is exactly the double-gate shape this contract
    exists to catch. _policy_wrapper itself IS the bare one-line
    pass-through under test here; gate_a/gate_b are deliberately not (see
    _direct_caller_source), so they're counted rather than also absorbed.
    """
    monkeypatch.setattr(_checker, "SRC_ROOT", tmp_path)
    _write(tmp_path, "policy_mod.py", "def _policy(spec):\n    return []\n")
    _write(
        tmp_path,
        "wrapper_mod.py",
        "from policy_mod import _policy\n\n\n"
        "def _policy_wrapper(spec):\n    return _policy(spec)\n",
    )
    _write(tmp_path, "caller_a.py", _direct_caller_source("gate_a", target="_policy_wrapper"))
    _write(tmp_path, "caller_b.py", _direct_caller_source("gate_b", target="_policy_wrapper"))
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert sites == ["caller_a.py:gate_a", "caller_b.py:gate_b"], (
        "both wrapper callers must be attributed to the underlying policy"
    )


def test_backend_specific_exemption_permits_codex_no_op_second_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_checker, "SRC_ROOT", tmp_path)
    monkeypatch.setattr(_checker, "BACKEND_SPECIFIC_EXEMPT_FUNCTIONS", frozenset({"_policy"}))
    monkeypatch.setattr(_checker, "_CODEX_MODULE_RELPATH", "execution/backends/codex.py")
    _write(tmp_path, "policy_mod.py", "def _policy(spec):\n    return []\n")
    _write(tmp_path, "gate_a.py", _direct_caller_source("check_a"))
    codex_dir = tmp_path / "execution" / "backends"
    codex_dir.mkdir(parents=True)
    (codex_dir / "codex.py").write_text(
        "from policy_mod import _policy\n\n\n"
        "def check_codex(spec):  # no-op: Codex has no team concept\n"
        "    errors = _policy(spec)\n"
        "    if errors:\n"
        "        raise ValueError('unreachable — no-op backend')\n"
    )
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert len(sites) == 2
    assert any(_checker._codex_site_is_exempt(s) for s in sites)


def test_backend_exemption_rejects_a_non_codex_or_non_marked_second_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_checker, "SRC_ROOT", tmp_path)
    monkeypatch.setattr(_checker, "BACKEND_SPECIFIC_EXEMPT_FUNCTIONS", frozenset({"_policy"}))
    monkeypatch.setattr(_checker, "_CODEX_MODULE_RELPATH", "execution/backends/codex.py")
    _write(tmp_path, "policy_mod.py", "def _policy(spec):\n    return []\n")
    _write(tmp_path, "gate_a.py", _direct_caller_source("check_a"))
    _write(tmp_path, "gate_b.py", _direct_caller_source("check_b"))
    sites = _checker.find_call_sites("_policy", "policy_mod.py")
    assert len(sites) == 2
    assert not any(_checker._codex_site_is_exempt(s) for s in sites), (
        "a second site outside codex.py with no no-op marker must not be exempt"
    )


def test_real_codebase_has_single_enforcement_point() -> None:
    """Integration check: the actual production policy functions pass today."""
    violations = _checker.check()
    assert not violations, violations
