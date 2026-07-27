"""Tests for tests/conftest.py feature-scope summary (issue #4385).

The shadow-conftest pattern mirrors tests/test_test_filter_plugin.py: a hardcoded
source string is written via pytester.makeconftest(...) so the feature-gate
summary can be exercised in isolation without importing the real conftest.py
(which depends on autoskillit.core and autoskillit.config.settings that do not
resolve inside pytester's throwaway tmp_path).
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

pytestmark = [pytest.mark.medium]


# ---------------------------------------------------------------------------
# Shadow conftest source — must stay in sync with tests/conftest.py:
#   - _feature_scope_key StashKey
#   - _worker_feature_scope module-level accumulator
#   - pytest_collection_modifyitems: feature-scope stash write
#   - pytest_sessionfinish: workeroutput["feature_scope"] BEFORE the early return
#   - pytest_testnodedown: controller-side aggregation
#   - pytest_terminal_summary: stash-then-accumulator fallback, terminal write
# ---------------------------------------------------------------------------

_CONFTEST_FEATURE_SCOPE_SOURCE = """
import os

import pytest

# Match real conftest.py StashKey naming.
_feature_scope_key = pytest.StashKey[dict | None]()

# Module-level accumulator for xdist worker-to-controller IPC.
_worker_feature_scope: dict[str, bool] = {}


def pytest_configure(config):
    _worker_feature_scope.clear()
    config.stash[_feature_scope_key] = None


def pytest_collection_modifyitems(items, config):
    import os

    # Feature gate pass — emulate the real conftest: stash the full feature
    # scope dict for all registered features (not just those encountered on items).
    scope = {"fleet": False, "planner": False, "providers": False}
    config.stash[_feature_scope_key] = scope
    test_features_env = os.environ.get("AUTOSKILLIT_TEST_FEATURES")
    for item in items:
        marker = item.get_closest_marker("feature")
        if marker and marker.args:
            feature_name = marker.args[0]
            if not isinstance(feature_name, str):
                continue
            if feature_name in scope and not scope[feature_name]:
                # Step 2E: split skip reason by mechanism — whitelist mode
                # (AUTOSKILLIT_TEST_FEATURES set) keeps the env-var reference;
                # config-resolution mode uses the dedicated message.
                if test_features_env is not None:
                    env_display = test_features_env or ""
                    reason = (
                        f"feature '{feature_name}' disabled"
                        f" (AUTOSKILLIT_TEST_FEATURES='{env_display}'"
                        f" does not include '{feature_name}')"
                    )
                else:
                    reason = f"feature '{feature_name}' disabled via config resolution"
                item.add_marker(pytest.mark.skip(reason=reason))


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        # CRITICAL: stash write MUST happen before the early return, or the
        # controller never sees it. The early return below is the dead-code
        # failure mode the structural sync guard catches.
        session.config.workeroutput["feature_scope"] = session.config.stash.get(
            _feature_scope_key, None
        )
        return


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    if _worker_feature_scope:
        return
    wo = getattr(node, "workeroutput", {})
    scope = wo.get("feature_scope")
    if scope and not _worker_feature_scope:
        _worker_feature_scope.update(scope)


def pytest_terminal_summary(terminalreporter, config):
    # Fallback order: stash first (in-process run), then accumulator (xdist).
    scope = config.stash.get(_feature_scope_key, None)
    if scope is None:
        scope = _worker_feature_scope or None
    if scope is None:
        return
    parts = " ".join(f"{name}=enabled" if enabled else f"{name}=disabled"
                     for name, enabled in sorted(scope.items()))
    terminalreporter.write_line(f"Feature scope: {parts}")
"""


# ---------------------------------------------------------------------------
# Structural sync guard
# ---------------------------------------------------------------------------


def test_shadow_conftest_has_feature_scope_summary() -> None:
    """Shadow conftest must define pytest_terminal_summary and workeroutput["feature_scope"].

    Mirrors test_shadow_conftest_has_workeroutput_propagation in
    tests/test_test_filter_plugin.py:391-417. Without this guard the shadow
    could silently lose the IPC pathway, causing pytester-based xdist tests
    to pass against stale shadow code that does not match production behavior.
    """
    import ast

    tree = ast.parse(_CONFTEST_FEATURE_SCOPE_SOURCE)
    func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "pytest_terminal_summary" in func_names, (
        "Shadow conftest is missing pytest_terminal_summary hook — "
        "feature-scope summary will not be emitted"
    )
    assert "pytest_testnodedown" in func_names, (
        "Shadow conftest is missing pytest_testnodedown hook — "
        "xdist worker-to-controller aggregation not present"
    )
    # Verify pytest_sessionfinish writes workeroutput["feature_scope"] BEFORE the early return.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_sessionfinish":
            func_src = ast.unparse(node)
            assert "feature_scope" in func_src, (
                "pytest_sessionfinish in shadow conftest must write "
                "workeroutput['feature_scope'] — xdist IPC channel missing"
            )
            # workeroutput assignment must precede the return statement
            assert func_src.index("workeroutput") < func_src.rindex("return"), (
                "workeroutput assignment in pytest_sessionfinish must be "
                "BEFORE the early return (otherwise it is dead code on workers)"
            )
            break
    else:
        raise AssertionError("pytest_sessionfinish not found in shadow conftest")


# ---------------------------------------------------------------------------
# Pytester integration tests — feature-scope summary appears in terminal output
# ---------------------------------------------------------------------------


class TestConftestFeatureScopeSummary:
    """Verify Feature scope: line is emitted via pytest_terminal_summary.

    The summary must bypass --disable-warnings (it goes through
    terminalreporter.write_line, not warnings.warn).
    """

    def test_feature_scope_summary_appears_without_xdist(self, pytester: pytest.Pytester) -> None:
        """In-process run: stash path. Feature scope line appears in stdout."""
        pytester.makeconftest(_CONFTEST_FEATURE_SCOPE_SOURCE)
        pytester.makepyfile(
            test_a="def test_one(): pass",
            test_b="def test_two(): pass",
        )
        result = pytester.runpytest("-q", "--disable-warnings")
        result.stdout.fnmatch_lines(["*Feature scope:*=enabled*"])

    def test_feature_scope_summary_appears_with_xdist(self, pytester: pytest.Pytester) -> None:
        """xdist run (-n 2): worker-to-controller IPC path via workeroutput.

        This is the critical variant: under -n 2 the controller never runs
        pytest_collection_modifyitems directly, so the stash path is dead and
        the only way the summary can appear is through pytest_testnodedown →
        _worker_feature_scope → pytest_terminal_summary.
        """
        pytester.makeconftest(_CONFTEST_FEATURE_SCOPE_SOURCE)
        pytester.makepyfile(
            test_a="def test_one(): pass",
            test_b="def test_two(): pass",
        )
        result = pytester.runpytest("-q", "--disable-warnings", "-n", "2")
        result.stdout.fnmatch_lines(["*Feature scope:*=enabled*"])


# ---------------------------------------------------------------------------
# Skip reason distinction — config-resolution vs whitelist mode
# ---------------------------------------------------------------------------


class TestConftestFeatureScopeSkipReason:
    """Verify the skip reason names the actual gating mechanism (issue #4385).

    Previously both modes produced the same ``AUTOSKILLIT_TEST_FEATURES=...``
    message, which mis-attributed config-resolution skips to the env-var path.
    After the fix, the two mechanisms produce distinct messages.
    """

    def test_skip_reason_uses_config_resolution_message(
        self,
        pytester: pytest.Pytester,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When AUTOSKILLIT_TEST_FEATURES is unset, skip says 'disabled via config resolution'."""
        monkeypatch.delenv("AUTOSKILLIT_TEST_FEATURES", raising=False)
        pytester.makeconftest(_CONFTEST_FEATURE_SCOPE_SOURCE)
        pytester.makepyfile(
            test_a=("import pytest\n\n@pytest.mark.feature('fleet')\ndef test_one(): pass\n"),
        )
        result = pytester.runpytest("-rs")
        result.stdout.fnmatch_lines(["*SKIPPED*disabled via config resolution*"])

    def test_skip_reason_references_env_var_in_whitelist_mode(
        self,
        pytester: pytest.Pytester,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When AUTOSKILLIT_TEST_FEATURES is set, skip references the env var name."""
        monkeypatch.setenv("AUTOSKILLIT_TEST_FEATURES", "")
        pytester.makeconftest(_CONFTEST_FEATURE_SCOPE_SOURCE)
        pytester.makepyfile(
            test_a=("import pytest\n\n@pytest.mark.feature('fleet')\ndef test_one(): pass\n"),
        )
        result = pytester.runpytest("-rs")
        result.stdout.fnmatch_lines(["*SKIPPED*AUTOSKILLIT_TEST_FEATURES*"])
