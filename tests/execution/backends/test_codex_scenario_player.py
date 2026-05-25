from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from autoskillit.execution.backends.codex_scenario_player import (
    CodexScenario,
    CodexScenarioPlayer,
    CodexStepRecord,
    _CodexRunResult,
    _FakeCodexCLI,
    _load_manifest,
    _write_shim_script,
    make_codex_scenario_player,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _write_scenario(tmp_path: Path, steps: list[dict[str, Any]]) -> Path:
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(steps))
    return scenario_file


def _make_step_dict(
    step_name: str,
    exit_code: int = 0,
    duration_ms: int = 100,
    model: str = "o3",
    stdout_path: str | None = None,
    result_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "step_name": step_name,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "model": model,
        "stdout_path": stdout_path,
    }
    if result_summary is not None:
        d["result_summary"] = result_summary
    return d


class TestCodexStepRecordFields:
    def test_is_frozen_dataclass(self) -> None:
        record = CodexStepRecord(
            step_name="s1", exit_code=0, duration_ms=100, model="o3", stdout_path=None
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.step_name = "other"  # type: ignore[misc]

    def test_required_fields(self) -> None:
        record = CodexStepRecord(
            step_name="s1", exit_code=1, duration_ms=200, model="gpt-4", stdout_path=Path("/a")
        )
        assert record.step_name == "s1"
        assert record.exit_code == 1
        assert record.duration_ms == 200
        assert record.model == "gpt-4"
        assert record.stdout_path == Path("/a")

    def test_result_summary_default_none(self) -> None:
        record = CodexStepRecord(
            step_name="s1", exit_code=0, duration_ms=100, model="o3", stdout_path=None
        )
        assert record.result_summary is None

    def test_result_summary_explicit(self) -> None:
        summary = {"exit_code": 0, "stdout_head": "ok"}
        record = CodexStepRecord(
            step_name="s1",
            exit_code=0,
            duration_ms=100,
            model="o3",
            stdout_path=None,
            result_summary=summary,
        )
        assert record.result_summary == summary


class TestSessionDirProperty:
    def test_none_for_non_session(self) -> None:
        record = CodexStepRecord(
            step_name="s1", exit_code=0, duration_ms=100, model="o3", stdout_path=None
        )
        assert record.session_dir is None

    def test_parent_for_session(self) -> None:
        record = CodexStepRecord(
            step_name="s1",
            exit_code=0,
            duration_ms=100,
            model="o3",
            stdout_path=Path("/a/b/c.ndjson"),
        )
        assert record.session_dir == Path("/a/b")


class TestCodexScenarioFields:
    def test_is_frozen_dataclass(self) -> None:
        scenario = CodexScenario(step_sequence=())
        with pytest.raises(dataclasses.FrozenInstanceError):
            scenario.step_sequence = ()  # type: ignore[misc]

    def test_iterable_step_sequence(self) -> None:
        r1 = CodexStepRecord(
            step_name="s1", exit_code=0, duration_ms=100, model="o3", stdout_path=None
        )
        scenario = CodexScenario(step_sequence=(r1,))
        items = list(scenario.step_sequence)
        assert len(items) == 1
        assert isinstance(items[0], CodexStepRecord)


class TestLoadManifest:
    def test_two_steps_mixed(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session_a"
        session_dir.mkdir()
        stdout_file = session_dir / "codex_stdout.ndjson"
        stdout_file.write_text('{"type":"test"}')

        _write_scenario(
            tmp_path,
            [
                _make_step_dict("step_a", stdout_path="session_a/codex_stdout.ndjson"),
                _make_step_dict(
                    "step_b",
                    stdout_path=None,
                    result_summary={"exit_code": 0, "stdout_head": "ok"},
                ),
            ],
        )

        scenario = _load_manifest(tmp_path)
        assert isinstance(scenario, CodexScenario)
        assert len(scenario.step_sequence) == 2

        s1 = scenario.step_sequence[0]
        assert s1.step_name == "step_a"
        assert s1.stdout_path == tmp_path / "session_a" / "codex_stdout.ndjson"
        assert s1.result_summary is None

        s2 = scenario.step_sequence[1]
        assert s2.step_name == "step_b"
        assert s2.stdout_path is None
        assert s2.result_summary == {"exit_code": 0, "stdout_head": "ok"}


class TestScenarioMethod:
    def test_returns_scenario(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        _write_scenario(
            tmp_path,
            [_make_step_dict("s1", stdout_path=None)],
        )
        player = CodexScenarioPlayer(
            scenario_dir=tmp_path, output_dir=out, binary_path=out / "codex"
        )
        scenario = player.scenario()
        assert isinstance(scenario, CodexScenario)
        assert len(scenario.step_sequence) == 1
        assert scenario.step_sequence[0].step_name == "s1"


class TestBuildSessionMap:
    def test_dict_structure(self, tmp_path: Path) -> None:
        session_a = tmp_path / "session_a"
        session_a.mkdir()
        (session_a / "stdout.ndjson").write_text("data")

        session_b = tmp_path / "session_b"
        session_b.mkdir()
        (session_b / "stdout.ndjson").write_text("data2")

        _write_scenario(
            tmp_path,
            [
                _make_step_dict("step_a", stdout_path="session_a/stdout.ndjson"),
                _make_step_dict("step_b", stdout_path="session_b/stdout.ndjson"),
                _make_step_dict("step_c", stdout_path=None),
            ],
        )

        out = tmp_path / "out"
        out.mkdir()
        player = CodexScenarioPlayer(
            scenario_dir=tmp_path, output_dir=out, binary_path=out / "codex"
        )
        smap = player.build_session_map()

        assert isinstance(smap, dict)
        assert "step_a" in smap
        assert "step_b" in smap
        assert "step_c" not in smap

        for key, entries in smap.items():
            assert isinstance(entries, list)
            for cli, meta in entries:
                assert isinstance(cli, _FakeCodexCLI)
                assert isinstance(meta, CodexStepRecord)


class TestFakeCodexCLIRun:
    def test_reads_cassette(self, tmp_path: Path) -> None:
        cassette = tmp_path / "cassette.ndjson"
        cassette.write_text("hello world\n")
        cli = _FakeCodexCLI(stdout_path=cassette)
        result = cli.run()
        assert isinstance(result, _CodexRunResult)
        assert result.stdout == "hello world\n"


class TestWriteShimScript:
    def test_creates_executable(self, tmp_path: Path) -> None:
        binary = tmp_path / "codex"
        _write_shim_script(output_dir=tmp_path, binary_path=binary)
        assert binary.exists()
        assert binary.is_file()
        assert not binary.is_symlink()
        assert os.access(binary, os.X_OK)
        assert binary.read_text().startswith("#!/")


class TestShimScriptExecution:
    def test_cassette_echo(self, tmp_path: Path) -> None:
        binary = tmp_path / "codex"
        _write_shim_script(output_dir=tmp_path, binary_path=binary)
        env = {**os.environ, "CODEX_REPLAY_CASSETTE": "replay_data_here"}
        result = subprocess.run(
            [sys.executable, str(binary)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout == "replay_data_here"


class TestMakeFactory:
    def test_returns_player(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        _write_scenario(tmp_path, [_make_step_dict("s1", stdout_path=None)])
        player = make_codex_scenario_player(
            scenario_dir=str(tmp_path),
            output_dir=str(out),
            binary_path=str(out / "codex"),
        )
        assert isinstance(player, CodexScenarioPlayer)


class TestModuleExports:
    def test_all_exports(self) -> None:
        import autoskillit.execution.backends.codex_scenario_player as mod

        assert sorted(mod.__all__) == ["CodexScenarioPlayer", "make_codex_scenario_player"]
