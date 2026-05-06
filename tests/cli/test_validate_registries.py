"""Tests for the validate registries CLI subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._validate import validate_registries

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _write_yaml(tmp_path: Path, registry_type: str, filename: str, content: str) -> Path:
    """Write a YAML file to the appropriate registry directory."""
    registry_dir = tmp_path / ".autoskillit" / registry_type
    registry_dir.mkdir(parents=True, exist_ok=True)
    file_path = registry_dir / filename
    file_path.write_text(content)
    return file_path


VALID_EXPERIMENT_TYPE = """\
name: my-test-type
schema_version: "1.0"
priority: 1
classification_triggers:
  - trigger_alpha
  - trigger_beta
applicable_lenses:
  primary: exp-lens-estimand-clarity
  secondary: null
dimension_weights:
  clarity: high
  methodological_rigor: medium
  external_validity: low
  practical_significance: medium
  inferential_validity: high
  ethical_compliance: medium
  reporting_completeness: high
  transparency: high
red_team_focus:
  priority_area: test_area
l1_severity:
  severity_rating: medium
"""

VALID_METHODOLOGY_TRADITION = """\
name: my-test-tradition
display_name: My Test Tradition
schema_version: "1.0"
priority: 1
canonical_guideline:
  title: Test Guideline
  url: https://example.com
fields_spanned:
  - field1
detection_keywords:
  - keyword1
  - keyword2
mandatory_figures:
  - figure_type: table
strongly_expected_figures:
  - figure_type: chart
anti_patterns:
  - pattern_name: bad_pattern
"""


class TestValidateRegistries:
    """Tests for validate_registries command."""

    @pytest.fixture(autouse=True)
    def _mock_lens_slugs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch lens slug resolution to return a known set."""
        monkeypatch.setattr(
            "autoskillit.cli._validate._get_valid_lens_slugs",
            lambda: {
                "exp-lens-estimand-clarity",
                "exp-lens-error-budget",
                "exp-lens-validity-threats",
            },
        )

    @pytest.fixture(autouse=True)
    def _mock_bundled_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch bundled types loading to return empty dict."""
        monkeypatch.setattr(
            "autoskillit.cli._validate.load_types_from_dir",
            lambda _: {},
        )

    # T_VAL_1: Valid experiment-type YAML produces ✓ output and exit 0
    def test_valid_experiment_type_valid(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Valid experiment-type YAML produces ✓ output and exits 0."""
        monkeypatch.chdir(tmp_path)
        _write_yaml(tmp_path, "experiment-types", "my_type.yaml", VALID_EXPERIMENT_TYPE)

        validate_registries()
        out = capsys.readouterr().out
        assert "✓" in out
        assert "my_type.yaml" in out
        assert not (tmp_path / ".autoskillit" / "validation-errors").exists()

    # T_VAL_2: Schema-version mismatch produces ⚠ warning and exit 0
    def test_schema_version_mismatch_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Schema-version mismatch produces ⚠ warning and exits 0."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace(
            'schema_version: "1.0"', 'schema_version: "0.9"'
        )
        _write_yaml(tmp_path, "experiment-types", "warn_type.yaml", yaml_content)

        validate_registries()
        out = capsys.readouterr().out
        assert "⚠" in out
        assert "schema_version" in out
        assert "expected '1.0'" in out

    # T_VAL_3: Missing 'name' field produces ✗ error and exit 1
    def test_missing_name_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Missing 'name' field produces ✗ error and exit 1."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace("name: my-test-type\n", "")
        _write_yaml(tmp_path, "experiment-types", "no_name.yaml", yaml_content)

        with pytest.raises(SystemExit) as exc_info:
            validate_registries()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗" in out
        assert "missing" in out
        assert (tmp_path / ".autoskillit" / "validation-errors" / "no_name.yaml.error.md").exists()

    # T_VAL_4: Invalid lens reference produces ✗ error and exit 1
    def test_invalid_lens_reference_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Invalid lens reference produces ✗ error and exit 1."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace(
            "exp-lens-estimand-clarity", "exp-lens-nonexistent"
        )
        _write_yaml(tmp_path, "experiment-types", "bad_lens.yaml", yaml_content)

        with pytest.raises(SystemExit) as exc_info:
            validate_registries()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗" in out
        assert "lens" in out

    # T_VAL_5: Empty classification_triggers produces ✗ error and exit 1
    def test_empty_classification_triggers_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Empty classification_triggers produces ✗ error and exit 1."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace("  - trigger_alpha\n  - trigger_beta", "")
        _write_yaml(tmp_path, "experiment-types", "empty_triggers.yaml", yaml_content)

        with pytest.raises(SystemExit) as exc_info:
            validate_registries()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗" in out
        assert "classification_triggers" in out

    # T_VAL_6: Non-positive priority produces ✗ error and exit 1
    def test_non_positive_priority_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Non-positive priority produces ✗ error and exit 1."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace("priority: 1", "priority: 0")
        _write_yaml(tmp_path, "experiment-types", "bad_priority.yaml", yaml_content)

        with pytest.raises(SystemExit) as exc_info:
            validate_registries()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗" in out
        assert "priority" in out

    # T_VAL_7: Multiple is_fallback entries produces ✗ error and exit 1
    def test_multiple_fallback_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Multiple is_fallback entries produces ✗ error and exit 1."""
        monkeypatch.chdir(tmp_path)
        yaml1 = VALID_EXPERIMENT_TYPE + "\nis_fallback: true\n"
        yaml2 = (
            VALID_EXPERIMENT_TYPE.replace("name: my-test-type", "name: my-other-type")
            + "\nis_fallback: true\n"
        )
        _write_yaml(tmp_path, "experiment-types", "fallback1.yaml", yaml1)
        _write_yaml(tmp_path, "experiment-types", "fallback2.yaml", yaml2)

        with pytest.raises(SystemExit) as exc_info:
            validate_registries()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗" in out
        assert "is_fallback" in out

    # T_VAL_8: No user directories — prints info message and exit 0
    def test_no_user_directories(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """No user directories prints info message and exits 0."""
        monkeypatch.chdir(tmp_path)

        validate_registries()
        out = capsys.readouterr().out
        assert "No user registry directories found" in out

    # T_VAL_9: Valid methodology-tradition YAML produces ✓ output and exit 0
    def test_valid_methodology_tradition_valid(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Valid methodology-tradition YAML produces ✓ output and exits 0."""
        monkeypatch.chdir(tmp_path)
        _write_yaml(
            tmp_path, "methodology-traditions", "my_tradition.yaml", VALID_METHODOLOGY_TRADITION
        )

        validate_registries()
        out = capsys.readouterr().out
        assert "✓" in out
        assert "my_tradition.yaml" in out

    # T_VAL_10: Empty detection_keywords in methodology tradition produces ✗ error
    def test_empty_detection_keywords_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Empty detection_keywords produces ✗ error and exit 1."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_METHODOLOGY_TRADITION.replace("  - keyword1\n  - keyword2", "")
        _write_yaml(tmp_path, "methodology-traditions", "empty_keywords.yaml", yaml_content)

        with pytest.raises(SystemExit) as exc_info:
            validate_registries()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗" in out
        assert "detection_keywords" in out

    # T_VAL_11: Error report markdown file has expected structure
    def test_error_report_structure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Error report markdown file has expected structure."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace("name: my-test-type\n", "")
        _write_yaml(tmp_path, "experiment-types", "parse_error.yaml", yaml_content)

        with pytest.raises(SystemExit):
            validate_registries()

        error_file = tmp_path / ".autoskillit" / "validation-errors" / "parse_error.yaml.error.md"
        assert error_file.exists()
        content = error_file.read_text()
        assert "# Validation error:" in content
        assert "**File:**" in content
        assert "**Validated at:**" in content
        assert "## Error" in content
        assert "## Expected schema" in content
        assert "## Your file" in content
        assert "## How to fix" in content

    # T_VAL_12: Exit code 0 when only warnings (no errors)
    def test_exit_0_with_only_warnings(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Exit code 0 when only warnings (no errors)."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE.replace(
            'schema_version: "1.0"', 'schema_version: "0.9"'
        )
        _write_yaml(tmp_path, "experiment-types", "warn_only.yaml", yaml_content)

        validate_registries()
        out = capsys.readouterr().out
        assert "⚠" in out
        assert "0 error" in out

    # T_VAL_13: Summary line includes correct counts
    def test_summary_counts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Summary line includes correct counts."""
        monkeypatch.chdir(tmp_path)
        _write_yaml(tmp_path, "experiment-types", "valid_type.yaml", VALID_EXPERIMENT_TYPE)
        yaml_warn = VALID_EXPERIMENT_TYPE.replace('schema_version: "1.0"', 'schema_version: "0.9"')
        _write_yaml(tmp_path, "experiment-types", "warn_type.yaml", yaml_warn)
        yaml_err = VALID_EXPERIMENT_TYPE.replace("name: my-test-type\n", "")
        _write_yaml(tmp_path, "experiment-types", "error_type.yaml", yaml_err)

        with pytest.raises(SystemExit):
            validate_registries()
        out = capsys.readouterr().out
        assert "1 valid" in out
        assert "1 warning" in out
        assert "1 error" in out

    # T_VAL_14: is_fallback check allows single fallback entry
    def test_single_fallback_allowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Single fallback entry is allowed."""
        monkeypatch.chdir(tmp_path)
        yaml_content = VALID_EXPERIMENT_TYPE + "\nis_fallback: true\n"
        _write_yaml(tmp_path, "experiment-types", "single_fallback.yaml", yaml_content)

        validate_registries()
        out = capsys.readouterr().out
        assert "✓" in out
        assert "✗" not in out
