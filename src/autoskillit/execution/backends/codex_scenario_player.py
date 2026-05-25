from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["CodexScenarioPlayer", "make_codex_scenario_player"]


@dataclass(frozen=True, slots=True)
class _CodexRunResult:
    stdout: str


@dataclass(frozen=True, slots=True)
class CodexStepRecord:
    step_name: str
    exit_code: int
    duration_ms: int
    model: str
    stdout_path: Path | None
    result_summary: dict[str, Any] | None = None

    @property
    def session_dir(self) -> Path | None:
        if self.stdout_path is None:
            return None
        return self.stdout_path.parent


@dataclass(frozen=True, slots=True)
class CodexScenario:
    step_sequence: tuple[CodexStepRecord, ...]


class _FakeCodexCLI:
    def __init__(self, stdout_path: Path) -> None:
        self._stdout_path = stdout_path

    def run(self) -> _CodexRunResult:
        return _CodexRunResult(stdout=self._stdout_path.read_text(encoding="utf-8"))


_CODEX_SHIM_SCRIPT = """\
import os, sys
sys.stdout.write(os.environ["CODEX_REPLAY_CASSETTE"])
sys.exit(0)
"""


def _write_shim_script(output_dir: Path, binary_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    binary_path.write_text(f"#!/usr/bin/env python3\n{_CODEX_SHIM_SCRIPT}")
    binary_path.chmod(binary_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _load_manifest(scenario_dir: Path) -> CodexScenario:
    raw = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
    steps: list[CodexStepRecord] = []
    for entry in raw:
        stdout_path_raw = entry.get("stdout_path")
        stdout_path = scenario_dir / stdout_path_raw if stdout_path_raw is not None else None
        steps.append(
            CodexStepRecord(
                step_name=entry["step_name"],
                exit_code=entry["exit_code"],
                duration_ms=entry["duration_ms"],
                model=entry["model"],
                stdout_path=stdout_path,
                result_summary=entry.get("result_summary"),
            )
        )
    return CodexScenario(step_sequence=tuple(steps))


class CodexScenarioPlayer:
    def __init__(
        self,
        scenario_dir: str | Path,
        output_dir: str | Path,
        binary_path: str | Path,
    ) -> None:
        self._scenario_dir = Path(scenario_dir)
        self._output_dir = Path(output_dir)
        self._binary_path = Path(binary_path)
        _write_shim_script(self._output_dir, self._binary_path)

    def scenario(self) -> CodexScenario:
        return _load_manifest(self._scenario_dir)

    def build_session_map(self) -> dict[str, list[tuple[_FakeCodexCLI, CodexStepRecord]]]:
        scenario = self.scenario()
        session_map: dict[str, list[tuple[_FakeCodexCLI, CodexStepRecord]]] = {}
        for record in scenario.step_sequence:
            if record.stdout_path is None:
                continue
            cli = _FakeCodexCLI(record.stdout_path)
            session_map.setdefault(record.step_name, []).append((cli, record))
        return session_map


def make_codex_scenario_player(
    scenario_dir: str,
    output_dir: str,
    binary_path: str,
) -> CodexScenarioPlayer:
    return CodexScenarioPlayer(
        scenario_dir=scenario_dir,
        output_dir=output_dir,
        binary_path=binary_path,
    )
