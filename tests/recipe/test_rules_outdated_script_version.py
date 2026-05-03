from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestOutdatedScriptVersionRule:
    @pytest.mark.parametrize(
        "script_ver,installed_ver,expected_count",
        [
            ("0.1.0", "0.2.0", 1),  # MSR1: below installed → fires
            ("0.2.0", "0.2.0", 0),  # MSR2: matches installed → does not fire
            (None, "0.2.0", 1),  # MSR3: None → fires
        ],
    )
    def test_outdated_recipe_version_rule(
        self, monkeypatch: pytest.MonkeyPatch, script_ver, installed_ver, expected_count
    ) -> None:
        import autoskillit.recipe.rules_inputs as _rules_mod

        import autoskillit
        import autoskillit.core.types as _core_types

        monkeypatch.setattr(autoskillit, "__version__", installed_ver)
        monkeypatch.setattr(_core_types, "AUTOSKILLIT_INSTALLED_VERSION", installed_ver)
        monkeypatch.setattr(_rules_mod, "AUTOSKILLIT_INSTALLED_VERSION", installed_ver)
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = script_ver
        findings = [f for f in run_semantic_rules(wf) if f.rule == "outdated-recipe-version"]
        assert len(findings) == expected_count

    def test_outdated_recipe_version_rule_severity_is_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.recipe.rules_inputs as _rules_mod

        import autoskillit
        import autoskillit.core.types as _core_types

        monkeypatch.setattr(autoskillit, "__version__", "0.2.0")
        monkeypatch.setattr(_core_types, "AUTOSKILLIT_INSTALLED_VERSION", "0.2.0")
        monkeypatch.setattr(_rules_mod, "AUTOSKILLIT_INSTALLED_VERSION", "0.2.0")
        wf = _make_workflow(
            {
                "do_thing": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        wf.version = "0.1.0"
        findings = [f for f in run_semantic_rules(wf) if f.rule == "outdated-recipe-version"]
        assert findings[0].severity == Severity.WARNING
