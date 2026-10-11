"""CLI contracts for frozen run-analysis preparation and publication."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import ArtifactLeaseContention
from tests.cli._sessions_helpers import _configure_log_root, _seed_session

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _candidate(status: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "snapshot_digest": status["snapshot_digest"],
        "interpreter": {"role": "test", "model": None, "prompt": None},
        "findings": findings,
    }


def _write_candidate(status: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    candidate_path = Path(status["candidate_path"])
    candidate_path.write_text(json.dumps(_candidate(status, findings)), encoding="utf-8")


def _status_from_stdout(stdout: str) -> dict[str, Any]:
    lines = stdout.splitlines()
    assert lines, "expected final JSON status line"
    return json.loads(lines[-1])


def test_analysis_commands_finish_from_frozen_packet_after_source_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_analysis_finish, sessions_analysis_prepare

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)
    output_dir = tmp_path / "analyses" / "2026-10-11_162824_a"

    sessions_analysis_prepare(str(output_dir))
    prepare_output = capsys.readouterr()
    status = _status_from_stdout(prepare_output.out)
    assert prepare_output.err == ""
    assert set(status) == {
        "output_dir",
        "input_fingerprint",
        "snapshot_digest",
        "interpretation_required",
        "candidate_path",
    }
    assert status["output_dir"] == str(output_dir)
    assert status["interpretation_required"] is True
    _write_candidate(
        status,
        [
            {
                "view_id": "cohort",
                "text": "The frozen corpus contains one retained session.",
                "evidence_ids": ["cohort/table/0"],
            }
        ],
    )

    shutil.rmtree(log_root)

    def unexpected_live_read(*_args: object, **_kwargs: object) -> Any:
        pytest.fail("analysis-finish must use only the frozen run packet")

    monkeypatch.setattr("autoskillit.config.load_config", unexpected_live_read)
    monkeypatch.setattr("autoskillit.execution.update_report_index", unexpected_live_read)
    monkeypatch.setattr("autoskillit.execution.read_report_index", unexpected_live_read)
    monkeypatch.setattr("autoskillit.report.load_render_bundle", unexpected_live_read)

    sessions_analysis_finish(str(output_dir))
    finish_output = capsys.readouterr()
    finished = json.loads(finish_output.out)

    assert finish_output.err == ""
    assert finished["completed"] is True
    assert Path(finished["report_path"]).is_file()


def test_analysis_prepare_reuses_completed_interpretation_after_transient_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import autoskillit.cli.ops._sessions as sessions_module
    from autoskillit.cli.ops._sessions import sessions_analysis_finish, sessions_analysis_prepare

    log_root = tmp_path / "logs"
    _seed_session(log_root)
    _configure_log_root(monkeypatch, log_root)
    first_dir = tmp_path / "analyses" / "2026-10-11_162824_a"
    second_dir = tmp_path / "analyses" / "2026-10-11_162827_a"
    original_refresh = sessions_module._refresh_report_index

    def refresh_with_transient_gap(root: Path, index_dir: Path, *, rebuild: bool) -> None:
        original_refresh(root, index_dir, rebuild=rebuild)
        print("report index: transient source-gap warning")

    monkeypatch.setattr(sessions_module, "_refresh_report_index", refresh_with_transient_gap)
    sessions_analysis_prepare(str(first_dir))
    first_output = capsys.readouterr()
    first_status = _status_from_stdout(first_output.out)
    assert "transient source-gap warning" in first_output.out
    first_findings = [
        {
            "view_id": "cohort",
            "text": "The frozen corpus contains one retained session.",
            "evidence_ids": ["cohort/table/0"],
        }
    ]
    _write_candidate(first_status, first_findings)
    sessions_analysis_finish(str(first_dir))
    capsys.readouterr()

    monkeypatch.setattr(sessions_module, "_refresh_report_index", original_refresh)
    sessions_analysis_prepare(str(second_dir))
    second_output = capsys.readouterr()
    second_status = _status_from_stdout(second_output.out)

    assert "transient source-gap warning" not in second_output.out
    assert first_status["input_fingerprint"] == second_status["input_fingerprint"]
    assert second_status["interpretation_required"] is False
    reused_candidate = json.loads(
        Path(second_status["candidate_path"]).read_text(encoding="utf-8")
    )
    assert reused_candidate == _candidate(first_status, first_findings)


def test_analysis_empty_corpus_uses_explicit_no_observations_finding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_analysis_finish, sessions_analysis_prepare

    log_root = tmp_path / "logs"
    _configure_log_root(monkeypatch, log_root)
    output_dir = tmp_path / "analyses" / "2026-10-11_162825_a"

    sessions_analysis_prepare(str(output_dir))
    status = _status_from_stdout(capsys.readouterr().out)
    _write_candidate(
        status,
        [
            {
                "view_id": "cohort",
                "text": "No observations in the frozen corpus.",
                "evidence_ids": [],
            }
        ],
    )

    sessions_analysis_finish(str(output_dir))
    finished = json.loads(capsys.readouterr().out)

    assert finished["completed"] is True
    assert "No observations in the frozen corpus." in Path(finished["report_path"]).read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid snapshot"),
        OSError("index unavailable"),
        ArtifactLeaseContention(Path("report-index/index.lock")),
    ],
    ids=["invalid-snapshot", "io-error", "lease-contention"],
)
def test_analysis_prepare_reports_domain_errors(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_analysis_prepare

    log_root = tmp_path / "logs"
    _configure_log_root(monkeypatch, log_root)

    def fail_update(*_args: object, **_kwargs: object) -> Any:
        raise error

    monkeypatch.setattr("autoskillit.cli.ops._sessions._refresh_report_index", fail_update)

    with pytest.raises(SystemExit) as raised:
        sessions_analysis_prepare(str(tmp_path / "analyses" / "2026-10-11_162826_a"))

    output = capsys.readouterr()
    assert raised.value.code == 1
    assert output.out == ""
    assert str(error) in output.err


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid frozen packet"),
        ArtifactLeaseContention(Path("analyses/.publication.lock")),
    ],
    ids=["domain-error", "lease-contention"],
)
def test_analysis_finish_reports_domain_errors(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.ops._sessions import sessions_analysis_finish

    def fail_finish(*_args: object, **_kwargs: object) -> Any:
        raise error

    monkeypatch.setattr("autoskillit.report.finish_run_analysis", fail_finish)

    with pytest.raises(SystemExit) as raised:
        sessions_analysis_finish(str(tmp_path / "analyses" / "2026-10-11_162828_a"))

    output = capsys.readouterr()
    assert raised.value.code == 1
    assert output.out == ""
    assert str(error) in output.err
