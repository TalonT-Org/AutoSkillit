"""Interprocess serialization and exact-input Codex validation contracts."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import autoskillit.hooks  # noqa: F401 — forces HOOK_REGISTRY population before sync_hooks_to_codex_config validates lifecycle contracts
from autoskillit.core import InteractiveInvocationValidation
from autoskillit.execution.backends import _codex_probes as probes
from autoskillit.execution.backends._codex import interactive_validation
from autoskillit.execution.process._lifecycle import owned_group

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_VALID_CONFIG_BYTES = (
    b'[mcp_servers.autoskillit]\ncommand = "autoskillit"\nargs = []\nenv_vars = []\n'
)
_VALID_INVENTORY_BYTES = json.dumps(
    {
        "servers": [
            {
                "name": "autoskillit",
                "enabled": True,
                "transport": {
                    "type": "stdio",
                    "command": "autoskillit",
                    "args": [],
                    "env_vars": [],
                },
            }
        ]
    }
).encode()


class _InertPluginLease:
    closed = False

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        return ()

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("args", "--flag"),
        ("args", [1]),
        ("env_vars", {"TOKEN": "secret"}),
        ("env_vars", [None]),
    ],
)
def test_mcp_inventory_rejects_non_string_array_fields(
    field: str,
    value: object,
) -> None:
    from autoskillit.execution.backends._codex_probes import _validate_codex_mcp_inventory

    transport: dict[str, object] = {
        "type": "stdio",
        "command": "autoskillit",
        "args": [],
        "env_vars": [],
    }
    transport[field] = value
    stdout = json.dumps({"servers": [{"name": "autoskillit", "transport": transport}]}).encode()

    errors = _validate_codex_mcp_inventory(stdout, _VALID_CONFIG_BYTES)

    assert errors == [f"Codex MCP autoskillit {field} are not an array of strings"]


def test_mcp_inventory_compares_args_in_order_and_env_vars_as_a_set() -> None:
    from autoskillit.execution.backends._codex_probes import _validate_codex_mcp_inventory

    config_bytes = (
        b'[mcp_servers.autoskillit]\ncommand = "autoskillit"\n'
        b'args = ["first", "second"]\nenv_vars = ["TOKEN", "HOME"]\n'
    )
    stdout = json.dumps(
        {
            "servers": [
                {
                    "name": "autoskillit",
                    "transport": {
                        "type": "stdio",
                        "command": "autoskillit",
                        "args": ["second", "first"],
                        "env_vars": ["HOME", "TOKEN"],
                    },
                }
            ]
        }
    ).encode()

    errors = _validate_codex_mcp_inventory(stdout, config_bytes)

    assert errors == ["Codex MCP autoskillit args do not match final config"]


def test_bounded_codex_probe_captures_success(tmp_path: Path) -> None:
    from autoskillit.execution.backends import _codex_probes as probes

    result = probes._run_bounded_codex_probe(
        (sys.executable, "-c", "import os; os.write(1, b'probe-ok')"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.returncode == 0
    assert result.stdout == b"probe-ok"
    assert result.stderr == b""
    assert result.failure is None


def test_bounded_codex_probe_times_out_and_reaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(probes, "_CODEX_PROBE_TIMEOUT_SECONDS", 0.05)

    result = probes._run_bounded_codex_probe(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.returncode is None
    assert result.failure == "timed out"


def test_bounded_codex_probe_owns_and_kills_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_popen = owned_group.subprocess.Popen
    original_killpg = owned_group.os.killpg
    processes: list[probes.subprocess.Popen[bytes]] = []
    group_signals: list[tuple[int, signal.Signals]] = []

    def recording_popen(*args: object, **kwargs: Any) -> probes.subprocess.Popen[bytes]:
        assert kwargs["start_new_session"] is True
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def recording_killpg(pgid: int, sig: signal.Signals) -> None:
        group_signals.append((pgid, sig))
        original_killpg(pgid, sig)

    monkeypatch.setattr(probes, "_CODEX_PROBE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(owned_group.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(owned_group.os, "killpg", recording_killpg)

    result = probes._run_bounded_codex_probe(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.failure == "timed out"
    assert group_signals
    assert {pgid for pgid, _sig in group_signals} == {processes[0].pid}
    assert group_signals[0][1] is signal.SIGTERM
    assert processes[0].poll() is not None


def test_bounded_codex_probe_enforces_stream_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(probes, "_CODEX_PROBE_STREAM_LIMIT", 128)

    result = probes._run_bounded_codex_probe(
        (sys.executable, "-c", "import os; os.write(1, b'x' * 4096)"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.returncode is None
    assert result.failure == "stdout exceeded 128 bytes"
    assert len(result.stdout) == 128


def test_bounded_codex_probe_accepts_explicit_larger_stream_limit(tmp_path: Path) -> None:
    payload_size = 96 * 1024

    result = probes._run_bounded_codex_probe(
        (sys.executable, "-c", f"import os; os.write(1, b'x' * {payload_size})"),
        env=os.environ,
        cwd=str(tmp_path),
        stream_limit_bytes=128 * 1024,
    )

    assert result.returncode == 0
    assert result.failure is None
    assert len(result.stdout) == payload_size


def test_run_bounded_codex_probe_returns_success_with_diagnostic_on_incomplete_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import ProcessCleanupResult
    from autoskillit.execution.process._lifecycle.owned_group import OwnedProcessGroup

    original_cleanup = OwnedProcessGroup.cleanup

    def incomplete_cleanup(
        self: OwnedProcessGroup, timeout: float = 2.0
    ) -> tuple[int | None, ProcessCleanupResult]:
        returncode, result = original_cleanup(self, timeout)
        return returncode, ProcessCleanupResult(
            root_pid=result.root_pid,
            process_identities=result.process_identities,
            terminated_pids=result.terminated_pids,
            survivor_pids=result.survivor_pids,
            access_denied_pids=(999,),
            observation_complete=result.observation_complete,
        )

    monkeypatch.setattr(OwnedProcessGroup, "cleanup", incomplete_cleanup)

    result = probes._run_bounded_codex_probe(
        (sys.executable, "-c", "import os; os.write(1, b'probe-ok')"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.failure is None
    assert result.cleanup_incomplete is True
    assert result.returncode == 0
    assert result.stdout == b"probe-ok"
    assert result.stderr == b""


@pytest.mark.parametrize(
    ("program", "expected_error"),
    [
        ("raise SystemExit(7)", "exited with status 7"),
        ("import os; os.write(1, b'not-json')", "returned malformed JSON"),
    ],
)
def test_mcp_probe_normalizes_process_and_output_failures(
    program: str,
    expected_error: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(probes, "_CODEX_VALIDATION_CACHE", {})

    errors = probes._validate_mcp_probe(
        (sys.executable, "-c", program),
        env=os.environ,
        cwd=str(tmp_path),
        config_bytes=_VALID_CONFIG_BYTES,
    )

    assert len(errors) == 1
    assert expected_error in errors[0]


def test_mcp_probe_caches_successful_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    calls = 0

    def run_probe(*_args: object, **_kwargs: object) -> probes._BoundedProbeResult:
        nonlocal calls
        calls += 1
        return probes._BoundedProbeResult(
            returncode=0,
            stdout=_VALID_INVENTORY_BYTES,
            stderr=b"",
        )

    monkeypatch.setattr(probes, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(probes, "_run_bounded_codex_probe", run_probe)
    command = ("codex", "mcp", "list", "--json")

    assert (
        probes._validate_mcp_probe(
            command,
            env=os.environ,
            cwd=str(tmp_path),
            config_bytes=_VALID_CONFIG_BYTES,
        )
        == []
    )
    assert (
        probes._validate_mcp_probe(
            command,
            env=os.environ,
            cwd=str(tmp_path),
            config_bytes=_VALID_CONFIG_BYTES,
        )
        == []
    )
    assert calls == 1


def test_validate_mcp_probe_returns_clean_result_when_cleanup_incomplete_but_probe_succeeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_probe(*_args: object, **_kwargs: object) -> probes._BoundedProbeResult:
        return probes._BoundedProbeResult(
            returncode=0,
            stdout=_VALID_INVENTORY_BYTES,
            stderr=b"",
            cleanup_incomplete=True,
        )

    monkeypatch.setattr(probes, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(probes, "_run_bounded_codex_probe", run_probe)

    errors = probes._validate_mcp_probe(
        ("codex", "mcp", "list", "--json"),
        env=os.environ,
        cwd=str(tmp_path),
        config_bytes=_VALID_CONFIG_BYTES,
    )

    assert errors == []


def _interactive_discovery_spec(
    tmp_path: Path,
) -> tuple[Any, Any, Path, Path]:
    from autoskillit.core import (
        PROVIDER_PROFILE_ENV_VAR,
        ValidatedAddDir,
        resolve_executable_launch_binding,
    )
    from autoskillit.execution.backends.codex import CodexBackend

    generated_home = tmp_path / "generated-home"
    catalog_dir = generated_home / "add-dir" / "skills"
    managed_skill = catalog_dir / "managed-skill" / "SKILL.md"
    managed_skill.parent.mkdir(parents=True)
    managed_skill.write_text("managed skill", encoding="utf-8")
    (generated_home / "config.toml").write_bytes(_VALID_CONFIG_BYTES)
    (generated_home / "skills").symlink_to("add-dir/skills")
    for name in ("sessions", "archived_sessions"):
        target = generated_home / f".inert-{name}"
        target.mkdir()
        (generated_home / name).symlink_to(target)

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "config.toml").write_bytes(_VALID_CONFIG_BYTES)
    executable = tmp_path / "codex-bound"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    catalog = ValidatedAddDir(
        path=str(generated_home / "add-dir"),
        session_home=str(generated_home),
        skill_entries=(("managed-skill", "managed-skill/SKILL.md"),),
    )
    env_extras = {
        "PATH": str(tmp_path),
        PROVIDER_PROFILE_ENV_VAR: "test-profile",
    }
    backend = CodexBackend(source_codex_home=source_home)
    candidate = backend.build_interactive_cmd(
        add_dirs=(catalog,),
        generated_home=generated_home,
        env_extras=env_extras,
    )
    binding = resolve_executable_launch_binding(
        binary_name=executable.name,
        environment=candidate.env,
        cwd=tmp_path,
    )
    spec = replace(
        backend.build_interactive_cmd(
            add_dirs=(catalog,),
            executable=binding,
            generated_home=generated_home,
            env_extras=env_extras,
        ),
        cwd=str(tmp_path),
    )
    return backend, spec, generated_home, executable


def _projected_plugin_binding(
    tmp_path: Path,
    *,
    skill_entries: tuple[tuple[str, str], ...] = (
        ("projected-skill", "projected-skill/SKILL.md"),
    ),
) -> tuple[Any, Path]:
    from autoskillit.core import PluginArtifactIdentity, PluginLaunchBinding, PluginLoadMode
    from autoskillit.execution.backends._codex_discovery import CODEX_PROJECTED_HOME_ROUTE

    projected_home = tmp_path / "projected-home"
    projected_home.mkdir()
    for name, relative_path in skill_entries:
        skill_path = CODEX_PROJECTED_HOME_ROUTE.catalog_dir(projected_home) / relative_path
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(f"projected skill {name}", encoding="utf-8")
    binding = PluginLaunchBinding(
        load_mode=PluginLoadMode.PROJECTED_HOME,
        plugin_dir=projected_home,
        identity=PluginArtifactIdentity(
            semantic_key="projected-test-plugin",
            incarnation_id="00000000000040008000000000000001",
            manifest_schema_version=1,
            artifact_digest="a" * 64,
            managed_path=projected_home,
            manifest_path=tmp_path / "projected-test-plugin.manifest.json",
        ),
        inherited_fds=(),
        _lease=_InertPluginLease(),
        skill_entries=skill_entries,
    )
    return binding, projected_home


def _projected_interactive_spec(
    tmp_path: Path,
) -> tuple[Any, Any, Path, Path]:
    from autoskillit.core import PROVIDER_PROFILE_ENV_VAR, resolve_executable_launch_binding
    from autoskillit.execution.backends.codex import CodexBackend

    plugin_binding, projected_home = _projected_plugin_binding(tmp_path)
    source_home = tmp_path / "projected-source-home"
    source_home.mkdir()
    executable = tmp_path / "codex-bound"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    env_extras = {
        "PATH": str(tmp_path),
        PROVIDER_PROFILE_ENV_VAR: "test-profile",
    }
    backend = CodexBackend(source_codex_home=source_home)
    candidate = backend.build_interactive_cmd(
        plugin_binding=plugin_binding,
        env_extras=env_extras,
    )
    binding = resolve_executable_launch_binding(
        binary_name=executable.name,
        environment=candidate.env,
        cwd=tmp_path,
    )
    spec = replace(
        backend.build_interactive_cmd(
            executable=binding,
            plugin_binding=plugin_binding,
            env_extras=env_extras,
        ),
        cwd=str(tmp_path),
    )
    return backend, spec, projected_home, executable


def _prompt_input_for_catalog(catalog_dir: Path) -> bytes:
    skills_block = "\n".join(
        (
            "<skills_instructions>",
            "### Skill roots",
            f"- `r0` = `{catalog_dir}`",
            "### Available skills",
            "- managed-skill: managed skill (file: `r0/managed-skill/SKILL.md`)",
            "</skills_instructions>",
        )
    )
    return json.dumps(
        [
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": skills_block}],
            }
        ]
    ).encode()


def test_real_interactive_validator_reaches_successful_native_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import _codex_discovery as discovery
    from autoskillit.execution.backends import codex

    backend, spec, generated_home, executable = _interactive_discovery_spec(tmp_path)
    assert spec.origin is not None
    calls: list[dict[str, object]] = []

    def run_probe(
        command: tuple[str, ...],
        *,
        env: object,
        cwd: object,
        timeout_seconds: float = 15,
        stream_limit_bytes: int | None = None,
    ) -> probes._BoundedProbeResult:
        calls.append(
            {
                "command": command,
                "env": env,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "stream_limit_bytes": stream_limit_bytes,
            }
        )
        if command[-3:] == ("mcp", "list", codex.CodexFlags.JSON):
            return probes._BoundedProbeResult(0, _VALID_INVENTORY_BYTES, b"")
        if command == (str(executable), "--version"):
            return probes._BoundedProbeResult(0, b"codex-cli 0.153.4\n", b"")
        if command[-2:] == discovery.CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe:
            return probes._BoundedProbeResult(
                0,
                _prompt_input_for_catalog(generated_home / "skills"),
                b"",
            )
        pytest.fail(f"unexpected Codex probe command: {command}")

    monkeypatch.setattr(probes, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(probes, "_run_bounded_codex_probe", run_probe)
    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", run_probe)

    assert backend.validate_interactive_invocation(spec).errors == ()

    probe_prefix = interactive_validation._interactive_probe_prefix(spec.origin)
    assert calls == [
        {
            "command": (*probe_prefix, "mcp", "list", codex.CodexFlags.JSON),
            "env": spec.env,
            "cwd": spec.cwd,
            "timeout_seconds": 15,
            "stream_limit_bytes": None,
        },
        {
            "command": (str(executable), "--version"),
            "env": spec.env,
            "cwd": spec.cwd,
            "timeout_seconds": 30,
            "stream_limit_bytes": None,
        },
        {
            "command": (*probe_prefix, *discovery.CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe),
            "env": spec.env,
            "cwd": spec.cwd,
            "timeout_seconds": 30,
            "stream_limit_bytes": discovery._CODEX_DISCOVERY_STREAM_LIMIT,
        },
    ]
    assert probe_prefix[0] == str(executable)
    assert probe_prefix[1:3] == ("--profile", "test-profile")
    assert probe_prefix[-2:] == (
        codex.CodexFlags.CONFIG_OVERRIDE,
        f'sqlite_home="{generated_home}"',
    )
    assert probe_prefix.count(codex.CodexFlags.CONFIG_OVERRIDE) >= 1


def test_interactive_validator_returns_discovery_diagnostics_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    backend, spec, generated_home, executable = _interactive_discovery_spec(tmp_path)
    assert spec.origin is not None
    discovery_errors = ["exact discovery diagnostic"]
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        interactive_validation,
        "_validate_mcp_probe",
        lambda _command, **_kwargs: [],
    )
    monkeypatch.setattr(
        interactive_validation,
        "probe_codex_version",
        lambda **_kwargs: ("codex-cli 0.153.4", "0.153.4", []),
    )

    def attest(**kwargs: object) -> InteractiveInvocationValidation:
        captured.update(kwargs)
        return InteractiveInvocationValidation(errors=tuple(discovery_errors))

    monkeypatch.setattr(interactive_validation, "attest_catalog_discovery", attest)

    assert backend.validate_interactive_invocation(spec).errors == tuple(discovery_errors)
    managed_route = interactive_validation.CODEX_MANAGED_HOME_ROUTE
    assert captured["probe_command"] == (
        *interactive_validation._interactive_probe_prefix(spec.origin),
        *codex.CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe,
    )
    assert captured["env"] == spec.env
    assert captured["cwd"] == spec.cwd
    assert captured["route"] is managed_route
    assert captured["catalog_dir"] == managed_route.catalog_dir(generated_home)
    assert captured["expected_discovery_root"] == managed_route.discovery_root(generated_home)
    assert captured["managed_root_scope"] == generated_home.parent
    assert captured["expected_entries"] == spec.managed_skill_catalog.skill_entries
    assert captured["timeout_seconds"] == 30
    assert str(executable) == spec.origin.binary


def test_projected_interactive_validator_accepts_canonical_home_without_managed_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    monkeypatch.setenv("CODEX_SQLITE_HOME", str(tmp_path / "ambient-sqlite-home"))
    backend, spec, projected_home, executable = _projected_interactive_spec(tmp_path)
    assert spec.origin is not None
    assert spec.managed_skill_catalog is None
    assert spec.env["CODEX_HOME"] == str(projected_home)
    assert "CODEX_SQLITE_HOME" not in spec.env
    assert not (projected_home / "config.toml").exists()
    events: list[str] = []
    version_call: dict[str, object] = {}
    discovery_call: dict[str, object] = {}

    def probe_version(**kwargs: object) -> tuple[str, str, list[str]]:
        events.append("version")
        version_call.update(kwargs)
        return "codex-cli 0.153.4", "0.153.4", []

    def attest(**kwargs: object) -> InteractiveInvocationValidation:
        events.append("prompt-input")
        discovery_call.update(kwargs)
        return InteractiveInvocationValidation(errors=())

    monkeypatch.setattr(interactive_validation, "probe_codex_version", probe_version)
    monkeypatch.setattr(interactive_validation, "attest_catalog_discovery", attest)

    assert backend.validate_interactive_invocation(spec).errors == ()
    assert events == ["version", "prompt-input"]
    assert version_call == {
        "executable": str(executable),
        "env": spec.env,
        "cwd": spec.cwd,
        "timeout_seconds": 30.0,
    }
    assert discovery_call["probe_command"] == (
        *interactive_validation._interactive_probe_prefix(spec.origin),
        *codex.CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe,
    )
    projected_route = interactive_validation.CODEX_PROJECTED_HOME_ROUTE
    assert discovery_call["route"] is projected_route
    assert discovery_call["catalog_dir"] == projected_route.catalog_dir(projected_home)
    assert discovery_call["expected_discovery_root"] == projected_route.discovery_root(
        projected_home
    )
    assert discovery_call["managed_root_scope"] == projected_home.parent
    assert discovery_call["expected_entries"] == spec.projected_skill_entries
    assert discovery_call["version"] == "codex-cli 0.153.4"
    assert discovery_call["timeout_seconds"] == 30.0


def test_interactive_validator_rejects_spec_without_declared_route(tmp_path: Path) -> None:
    backend, spec, _generated_home, _executable = _interactive_discovery_spec(tmp_path)

    assert backend.validate_interactive_invocation(
        replace(spec, skill_discovery_route=None)
    ).errors == ("Codex interactive validation requires a declared skill discovery route",)


def test_interactive_validator_rejects_missing_managed_catalog_evidence(tmp_path: Path) -> None:
    backend, spec, _generated_home, _executable = _interactive_discovery_spec(tmp_path)

    assert backend.validate_interactive_invocation(
        replace(spec, managed_skill_catalog=None)
    ).errors == ("Codex managed discovery route requires managed catalog evidence",)


@pytest.mark.parametrize(
    ("environment_update", "expected_error"),
    [
        ({"CODEX_HOME": ""}, "Codex projected interactive validation requires CODEX_HOME"),
        (
            {"CODEX_HOME": "relative-home"},
            "Codex projected interactive CODEX_HOME must be absolute",
        ),
        (
            {"CODEX_SQLITE_HOME": "/unexpected-sqlite-home"},
            "Codex projected interactive environment must not contain CODEX_SQLITE_HOME",
        ),
    ],
)
def test_projected_interactive_validator_rejects_invalid_home_environment(
    tmp_path: Path,
    environment_update: dict[str, str],
    expected_error: str,
) -> None:
    backend, spec, _projected_home, _executable = _projected_interactive_spec(tmp_path)
    environment = dict(spec.env)
    environment.update(environment_update)

    assert backend.validate_interactive_invocation(replace(spec, env=environment)).errors == (
        expected_error,
    )


def test_projected_interactive_validator_rejects_noncanonical_home(tmp_path: Path) -> None:
    backend, spec, projected_home, _executable = _projected_interactive_spec(tmp_path)
    noncanonical_home = projected_home.parent / "projected-home" / ".." / "projected-home"
    environment = dict(spec.env)
    environment["CODEX_HOME"] = str(noncanonical_home)

    assert backend.validate_interactive_invocation(replace(spec, env=environment)).errors == (
        "Codex projected interactive CODEX_HOME must be a canonical real directory",
    )


def test_projected_interactive_validator_rejects_unreadable_home(tmp_path: Path) -> None:
    backend, spec, _projected_home, _executable = _projected_interactive_spec(tmp_path)
    environment = dict(spec.env)
    environment["CODEX_HOME"] = str(tmp_path / "missing-home")

    errors = backend.validate_interactive_invocation(replace(spec, env=environment)).errors

    assert len(errors) == 1
    assert errors[0].startswith("Codex projected interactive CODEX_HOME is unreadable:")


def test_projected_interactive_validator_rejects_home_file(tmp_path: Path) -> None:
    backend, spec, _projected_home, _executable = _projected_interactive_spec(tmp_path)
    home_file = tmp_path / "home-file"
    home_file.write_text("not a directory", encoding="utf-8")
    environment = dict(spec.env)
    environment["CODEX_HOME"] = str(home_file)

    assert backend.validate_interactive_invocation(replace(spec, env=environment)).errors == (
        "Codex projected interactive CODEX_HOME must be a canonical real directory",
    )


def test_managed_interactive_validator_rejects_mismatched_reserved_homes(tmp_path: Path) -> None:
    backend, spec, _generated_home, _executable = _interactive_discovery_spec(tmp_path)
    environment = dict(spec.env)
    environment["CODEX_SQLITE_HOME"] = str(tmp_path / "different-home")

    assert backend.validate_interactive_invocation(replace(spec, env=environment)).errors == (
        "Codex interactive reserved home and SQLite environment must name the same generated home",
    )


def test_projected_interactive_validator_rejects_missing_catalog_before_prompt_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends._codex_discovery import CODEX_PROJECTED_HOME_ROUTE

    backend, spec, projected_home, _executable = _projected_interactive_spec(tmp_path)
    (
        CODEX_PROJECTED_HOME_ROUTE.catalog_dir(projected_home) / "projected-skill" / "SKILL.md"
    ).unlink()
    monkeypatch.setattr(
        interactive_validation,
        "probe_codex_version",
        lambda **_kwargs: ("codex-cli 0.153.4", "0.153.4", []),
    )

    errors = backend.validate_interactive_invocation(spec).errors

    assert any("catalog validation failed" in error for error in errors)
    assert any("projected-skill/SKILL.md" in error for error in errors)


@pytest.mark.parametrize(
    ("catalog_state", "expected_fragment"),
    [
        ("misplaced", "misplaced expected paths"),
        ("changed", "mutated the managed catalog"),
    ],
)
def test_projected_interactive_validator_rejects_attestation_catalog_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_state: str,
    expected_fragment: str,
) -> None:
    from autoskillit.execution.backends import _codex_discovery as discovery

    backend, spec, projected_home, _executable = _projected_interactive_spec(tmp_path)
    catalog_dir = discovery.CODEX_PROJECTED_HOME_ROUTE.catalog_dir(projected_home)
    monkeypatch.setattr(
        interactive_validation,
        "probe_codex_version",
        lambda **_kwargs: ("codex-cli 0.153.4", "0.153.4", []),
    )
    prompt_root = catalog_dir
    if catalog_state == "misplaced":
        prompt_root = tmp_path / "other-home" / "skills"
        misplaced_skill = prompt_root / "projected-skill" / "SKILL.md"
        misplaced_skill.parent.mkdir(parents=True)
        misplaced_skill.write_text("misplaced", encoding="utf-8")

    def run_probe(*_args: object, **_kwargs: object) -> probes._BoundedProbeResult:
        if catalog_state == "changed":
            (catalog_dir / "projected-skill" / "SKILL.md").write_text("changed", encoding="utf-8")
        return probes._BoundedProbeResult(
            0,
            _prompt_input_for_catalog(prompt_root).replace(b"managed-skill", b"projected-skill"),
            b"",
        )

    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", run_probe)

    errors = backend.validate_interactive_invocation(spec).errors

    assert any(expected_fragment in error for error in errors)


def test_projected_interactive_validator_rejects_empty_or_mixed_catalog_evidence(
    tmp_path: Path,
) -> None:
    backend, spec, _projected_home, _executable = _projected_interactive_spec(tmp_path)
    _, managed_spec, _generated_home, _managed_executable = _interactive_discovery_spec(tmp_path)

    assert backend.validate_interactive_invocation(
        replace(spec, projected_skill_entries=())
    ).errors == ("Codex projected discovery route requires projected catalog evidence",)
    assert backend.validate_interactive_invocation(
        replace(spec, managed_skill_catalog=managed_spec.managed_skill_catalog)
    ).errors == ("Codex interactive validation received mixed managed and projected catalogs",)


def test_projected_interactive_cmd_rejects_empty_frozen_catalog(tmp_path: Path) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    binding, _projected_home = _projected_plugin_binding(tmp_path, skill_entries=())

    with pytest.raises(ValueError, match="requires nonempty skill entries"):
        CodexBackend().build_interactive_cmd(plugin_binding=binding)


def test_cmd_spec_deep_freezes_projected_skill_entries() -> None:
    from autoskillit.core import CmdSpec

    entries: Any = [["projected-skill", "projected-skill/SKILL.md"]]
    spec = CmdSpec(cmd=("codex",), env={}, projected_skill_entries=entries)
    entries[0][0] = "changed-skill"
    entries.append(["second-skill", "second-skill/SKILL.md"])

    assert spec.projected_skill_entries == (("projected-skill", "projected-skill/SKILL.md"),)
    assert isinstance(spec.projected_skill_entries[0], tuple)


def test_interactive_validator_skips_version_and_discovery_when_mcp_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import _codex_discovery as discovery
    from autoskillit.execution.backends import codex

    backend, spec, _generated_home, _executable = _interactive_discovery_spec(tmp_path)
    commands: list[tuple[str, ...]] = []

    def run_probe(command: tuple[str, ...], **_kwargs: object) -> probes._BoundedProbeResult:
        commands.append(command)
        return probes._BoundedProbeResult(7, b"", b"mcp failed")

    monkeypatch.setattr(probes, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(probes, "_run_bounded_codex_probe", run_probe)
    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", run_probe)

    errors = backend.validate_interactive_invocation(spec).errors

    assert len(errors) == 1
    assert "Codex MCP validation exited with status 7" in errors[0]
    assert len(commands) == 1
    assert commands[0][-3:] == ("mcp", "list", codex.CodexFlags.JSON)


@pytest.mark.parametrize(
    ("version_result", "expected_diagnostic"),
    [
        pytest.param(
            probes._BoundedProbeResult(8, b"", b"version failed"),
            "Codex version probe exited with status 8",
            id="nonzero",
        ),
        pytest.param(
            probes._BoundedProbeResult(None, b"", b"", failure="timed out"),
            "Codex version probe timed out",
            id="timeout",
        ),
        pytest.param(
            probes._BoundedProbeResult(0, b"not-a-version", b""),
            "Codex version probe returned malformed output",
            id="malformed",
        ),
    ],
)
def test_interactive_validator_stops_before_discovery_when_exact_version_probe_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version_result: probes._BoundedProbeResult,
    expected_diagnostic: str,
) -> None:
    from autoskillit.execution.backends import _codex_discovery as discovery
    from autoskillit.execution.backends import codex

    backend, spec, _generated_home, executable = _interactive_discovery_spec(tmp_path)
    commands: list[tuple[str, ...]] = []

    def run_probe(command: tuple[str, ...], **_kwargs: object) -> probes._BoundedProbeResult:
        commands.append(command)
        if command[-3:] == ("mcp", "list", codex.CodexFlags.JSON):
            return probes._BoundedProbeResult(0, _VALID_INVENTORY_BYTES, b"")
        assert command == (str(executable), "--version")
        return version_result

    monkeypatch.setattr(probes, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(probes, "_run_bounded_codex_probe", run_probe)
    monkeypatch.setattr(discovery, "_run_bounded_codex_probe", run_probe)

    errors = backend.validate_interactive_invocation(spec).errors

    assert len(errors) == 1
    assert expected_diagnostic in errors[0]
    assert commands[-1] == (str(executable), "--version")
    assert len(commands) == 2


def test_generated_codex_home_validation_uses_the_bound_executable_environment_and_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_RESERVED_HOME_ENV_VARS, resolve_executable_launch_binding
    from autoskillit.execution.backends import codex

    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()
    config_path = generated_home / "config.toml"
    config_path.write_bytes(_VALID_CONFIG_BYTES)
    executable = tmp_path / "codex-bound"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    launch_environment = {"PATH": str(tmp_path), "BOUND_ENV": "present"}
    binding = resolve_executable_launch_binding(
        binary_name=executable.name,
        environment=launch_environment,
        cwd=tmp_path,
    )
    captured: dict[str, object] = {}

    def validate_mcp(
        command: tuple[str, ...],
        *,
        env: object,
        cwd: object,
        config_bytes: bytes,
    ) -> list[str]:
        captured.update(
            command=command,
            env=env,
            cwd=cwd,
            config_bytes=config_bytes,
        )
        return []

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ambient-home"))
    monkeypatch.setattr(probes, "_validate_mcp_probe", validate_mcp)

    assert (
        probes._validate_generated_codex_home(
            generated_home,
            config_path=config_path,
            executable=binding,
        )
        == []
    )
    expected_env = dict(binding.launch_environment)
    for key in CODEX_RESERVED_HOME_ENV_VARS:
        expected_env[key] = str(generated_home)
    assert captured == {
        "command": (
            str(executable),
            codex.CodexFlags.CONFIG_OVERRIDE,
            f'sqlite_home="{generated_home}"',
            "mcp",
            "list",
            codex.CodexFlags.JSON,
        ),
        "env": expected_env,
        "cwd": str(binding.cwd),
        "config_bytes": _VALID_CONFIG_BYTES,
    }


def test_ensure_pre_launch_forwards_the_bound_executable_to_generated_home_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import resolve_executable_launch_binding
    from autoskillit.execution.backends import codex

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "config.toml").write_bytes(_VALID_CONFIG_BYTES)
    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()
    executable = tmp_path / "codex-bound"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    binding = resolve_executable_launch_binding(
        binary_name=executable.name,
        environment={"PATH": str(tmp_path), "BOUND_ENV": "present"},
        cwd=tmp_path,
    )
    captured: dict[str, object] = {}

    def validate_generated(
        received_home: Path,
        *,
        config_path: Path,
        executable: object,
    ) -> list[str]:
        captured.update(
            generated_home=received_home,
            config_path=config_path,
            executable=executable,
        )
        return []

    monkeypatch.setattr(codex, "_validate_generated_codex_home", validate_generated)
    backend = codex.CodexBackend(source_codex_home=source_home)

    readiness = backend.ensure_pre_launch(session_dir=generated_home, executable=binding)

    assert readiness.errors == ()
    assert readiness.attested_env == {
        "CODEX_HOME": str(generated_home),
        "CODEX_SQLITE_HOME": str(generated_home),
    }
    assert captured["generated_home"] == generated_home
    assert captured["config_path"] == generated_home / "config.toml"
    assert captured["executable"] is binding


def _config_writer(
    operation: str,
    config_path: str,
    isolated_home: str,
    ready: Any,
    start: Any,
    result: Any,
) -> None:
    os.environ["HOME"] = isolated_home
    os.environ["XDG_DATA_HOME"] = str(Path(isolated_home) / "xdg")
    ready.put(operation)
    if not start.wait(timeout=10):
        result.put((operation, "start timeout"))
        return
    try:
        if operation == "mcp":
            from autoskillit.execution.backends import ensure_codex_mcp_registered

            ensure_codex_mcp_registered(config_path=Path(config_path))
        else:
            from autoskillit.execution import sync_hooks_to_codex_config

            sync_hooks_to_codex_config(
                config_path=Path(config_path),
                hook_config_format="toml_nested",
            )
    except BaseException as exc:
        result.put((operation, f"{type(exc).__name__}: {exc}"))
    else:
        result.put((operation, "ok"))


def _stop_and_reap(processes: list[multiprocessing.Process]) -> None:
    for process in processes:
        process.join(timeout=10)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def test_cook_and_init_config_writers_preserve_the_union_under_one_canonical_lock(
    tmp_path: Path,
) -> None:
    race_root = tmp_path / "config-race"
    config_path = race_root / "codex-home" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('[foreign]\nowner = "user"\n', encoding="utf-8")
    noncanonical_path = config_path.parent / ".." / "codex-home" / "config.toml"
    child_home = race_root / "child-home"
    child_home.mkdir()
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    result = ctx.Queue()
    start = ctx.Event()
    processes = [
        ctx.Process(
            target=_config_writer,
            args=("mcp", str(config_path), str(child_home), ready, start, result),
        ),
        ctx.Process(
            target=_config_writer,
            args=("hooks", str(noncanonical_path), str(child_home), ready, start, result),
        ),
    ]

    try:
        for process in processes:
            process.start()
        # Spawn startup can exceed shorter bounds on loaded xdist workers.
        assert {ready.get(timeout=30), ready.get(timeout=30)} == {"mcp", "hooks"}
        start.set()
        outcomes = {result.get(timeout=30), result.get(timeout=30)}
        assert outcomes == {("mcp", "ok"), ("hooks", "ok")}
    finally:
        start.set()
        _stop_and_reap(processes)
        ready.close()
        result.close()
        ready.join_thread()
        result.join_thread()

    assert all(not process.is_alive() for process in processes)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["foreign"] == {"owner": "user"}
    assert "autoskillit" in data["mcp_servers"]
    assert data["hooks"]
    assert not (child_home / ".codex" / "config.toml").exists()


def test_generated_home_provisions_runtime_without_mutating_source_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    source_config = source_home / "config.toml"
    source_config.write_text('[foreign]\nowner = "user"\n', encoding="utf-8")
    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()
    ambient_home = tmp_path / "ambient-home"
    ambient_home.mkdir()
    backend = CodexBackend(source_codex_home=source_home)

    monkeypatch.setenv("CODEX_HOME", str(ambient_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: ambient_home))

    readiness = backend.ensure_pre_launch(session_dir=generated_home)

    assert readiness.errors == ()
    assert source_config.read_bytes() == b'[foreign]\nowner = "user"\n'
    data = tomllib.loads((generated_home / "config.toml").read_text(encoding="utf-8"))
    assert data["foreign"] == {"owner": "user"}
    assert "autoskillit" in data["mcp_servers"]
    assert data["hooks"]
    assert data["tool_output_token_limit"] > 0
    assert not (ambient_home / "config.toml").exists()


def test_generated_home_replaces_inherited_runtime_tuning_with_resolved_spec(
    tmp_path: Path,
) -> None:
    from autoskillit.core import CodexRuntimeSpec
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    source_config = source_home / "config.toml"
    source_bytes = (
        b"model_context_window = 90000\n"
        b"model_auto_compact_token_limit = 80000\n"
        b"[profiles.selected]\n"
        b"model_context_window = 70000\n"
        b"model_auto_compact_token_limit = 60000\n"
    )
    source_config.write_bytes(source_bytes)
    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()

    readiness = CodexBackend(
        source_codex_home=source_home,
        runtime_spec=CodexRuntimeSpec(
            context_window_tokens=200_000,
            auto_compact_threshold_tokens=180_000,
        ),
    ).ensure_pre_launch(session_dir=generated_home)

    assert readiness.errors == ()
    assert source_config.read_bytes() == source_bytes
    config = tomllib.loads((generated_home / "config.toml").read_text(encoding="utf-8"))
    assert config["model_context_window"] == 200_000
    assert config["model_auto_compact_token_limit"] == 180_000
    assert "model_context_window" not in config["profiles"]["selected"]
    assert "model_auto_compact_token_limit" not in config["profiles"]["selected"]
    assert config["hooks"]["PreCompact"][0]["matcher"] == "auto"


def test_interactive_cmd_rejects_environment_changed_after_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core import resolve_executable_launch_binding
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    backend = CodexBackend(source_codex_home=source_home)
    extras = {"PATH": str(tmp_path)}
    generated_home = tmp_path / "generated-home"
    candidate = backend.build_interactive_cmd(
        env_extras=extras,
        generated_home=generated_home,
    )
    binding = resolve_executable_launch_binding(
        binary_name="codex",
        environment=candidate.env,
        cwd=tmp_path,
    )

    monkeypatch.setenv("AUTOSKILLIT_CODEX_GUARD_MUTATION", "changed")

    with pytest.raises(
        ValueError,
        match="interactive environment changed after executable binding",
    ):
        backend.build_interactive_cmd(
            executable=binding,
            env_extras=extras,
            generated_home=generated_home,
        )


def test_config_lock_is_non_reentrant_for_the_same_canonical_path(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_config_lock import CodexConfigLock

    config_path = tmp_path / "codex-home" / "config.toml"
    alias = config_path.parent / ".." / "codex-home" / "config.toml"

    with CodexConfigLock(config_path):
        with pytest.raises(RuntimeError, match="non-reentrant|already owns"):
            with CodexConfigLock(alias):
                pytest.fail("same-process nested acquisition must fail before entry")


def test_config_lock_rejects_symlink_sidecar_without_truncating_target(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends._codex_config_lock import CodexConfigLock

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config_path = codex_home / "config.toml"
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    lock_path = codex_home / ".config.toml.autoskillit.lock"
    lock_path.symlink_to(victim)

    with pytest.raises(OSError):
        CodexConfigLock(config_path).acquire()

    assert victim.read_text(encoding="utf-8") == "preserve me"
