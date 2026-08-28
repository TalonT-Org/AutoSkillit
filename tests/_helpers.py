"""Shared test helper utilities."""

from __future__ import annotations

import ast
import importlib
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from autoskillit.core import (
    InstructionExtractionMode,
    OrchestratorSurfaceDef,
    ToolParamRole,
    get_tool_def,
)
from autoskillit.core import (
    strip_markdown_code_regions as strip_markdown_code_regions,
)

_RUN_SKILL_WINDOW = 400
_PROSE_TRIGGER_WINDOW = 60
_PROSE_TRIGGER_WORDS = ("parameter", "pass", "forward")


class _EnvVarReadCollector(ast.NodeVisitor):
    """Collect literal env-var names read by executable env-lookup expressions."""

    def __init__(self) -> None:
        self.reads: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_os_environ_get_call(node):
            name = self._first_string_arg(node)
            if name is not None:
                self.reads.add(name)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_os_environ_subscript(node):
            name = self._subscript_string_value(node)
            if name is not None:
                self.reads.add(name)
        self.generic_visit(node)

    @staticmethod
    def _is_os_environ_get_call(node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}:
            value = func.value
            if isinstance(value, ast.Attribute) and value.attr == "environ":
                if isinstance(value.value, ast.Name) and value.value.id == "os":
                    return True
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        return False

    @staticmethod
    def _is_os_environ_subscript(node: ast.Subscript) -> bool:
        value = node.value
        return (
            isinstance(value, ast.Attribute)
            and value.attr == "environ"
            and isinstance(value.value, ast.Name)
            and value.value.id == "os"
        )

    @staticmethod
    def _first_string_arg(node: ast.Call) -> str | None:
        if not node.args:
            return None
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None

    @staticmethod
    def _subscript_string_value(node: ast.Subscript) -> str | None:
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
        return None


def inject_vanishing_subtree_on_descent(
    monkeypatch: pytest.MonkeyPatch,
    subtree: Path,
) -> None:
    """Delete *subtree* when ``strict_walk`` opens it for descent."""
    import autoskillit.core.io as io_module

    original_open = os.open

    def vanish_before_descent(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == subtree.name and flags & os.O_DIRECTORY:
            shutil.rmtree(subtree)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(io_module.os, "open", vanish_before_descent)


def execution_tuning_param_names() -> tuple[str, ...]:
    """Return run_skill parameters whose role is execution tuning."""
    tool_def = get_tool_def("run_skill")
    assert tool_def is not None, "run_skill must be a registered ToolDef"
    return tuple(
        sorted(
            param.name for param in tool_def.params if param.role is ToolParamRole.EXECUTION_TUNING
        )
    )


def _literal_kwarg_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"(?:" + "|".join(re.escape(name) for name in names) + r")=")


def find_execution_tuning_forwarding_violations(text: str, names: tuple[str, ...]) -> list[str]:
    """Find prose that tells callers to forward server-resolved tuning values."""
    violations: list[str] = []

    for match in _literal_kwarg_pattern(names).finditer(text):
        window_start = max(0, match.start() - _RUN_SKILL_WINDOW)
        window_end = min(len(text), match.end() + _RUN_SKILL_WINDOW)
        if "run_skill" not in text[window_start:window_end]:
            continue
        violations.append(f"literal kwarg form {match.group(0)!r}")

    for name in names:
        for match in re.finditer(re.escape(name), text):
            local_start = max(0, match.start() - _PROSE_TRIGGER_WINDOW)
            local_end = min(len(text), match.end() + _PROSE_TRIGGER_WINDOW)
            local_window = text[local_start:local_end]
            if not any(word in local_window.lower() for word in _PROSE_TRIGGER_WORDS):
                continue
            wide_start = max(0, match.start() - _RUN_SKILL_WINDOW)
            wide_end = min(len(text), match.end() + _RUN_SKILL_WINDOW)
            if "run_skill" not in text[wide_start:wide_end]:
                continue
            violations.append(f"prose form near {name!r}: {local_window!r}")

    return violations


def seed_registry_owner(project_dir: Path, launch_id: str) -> None:
    """Seed stable owner identity fields into a test session registry row."""
    import json

    from autoskillit.core import read_registry, registry_path

    registry = read_registry(project_dir)
    registry[launch_id].update(
        owner_pid=321,
        owner_boot_id="12345678-1234-1234-1234-123456789abc",
        owner_starttime_ticks=654,
    )
    registry_path(project_dir).write_text(json.dumps(registry), encoding="utf-8")


def _collect_structlog_proxies() -> list[object]:
    """Return all BoundLoggerLazyProxy instances from autoskillit modules."""
    import structlog._config as _sc

    proxies: list[object] = []
    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for val in vars(mod).values():
            if isinstance(val, _sc.BoundLoggerLazyProxy):
                proxies.append(val)
    return proxies


def _flush_structlog_proxy_caches() -> None:
    """Reconnect autoskillit module-level loggers to the current structlog config.

    Scans ALL module attributes (not just 'logger'/'_logger') so that loggers
    stored under any name (e.g. '_log' in execution.quota) are repaired.
    """
    import structlog
    import structlog._config as _sc

    current_procs = structlog.get_config()["processors"]
    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for lg in vars(mod).values():
            if isinstance(lg, _sc.BoundLoggerLazyProxy):
                lg.__dict__.pop("_bound_logger", None)
                lg.__dict__.pop("bind", None)
            elif hasattr(lg, "_processors"):
                lg._processors = current_procs


def make_test_config(**overrides):
    """Build AutomationConfig for tests without direct config imports."""
    from autoskillit.config import AutomationConfig

    return AutomationConfig(**overrides)


def make_quota_guard_config(**overrides):
    """Build QuotaGuardConfig for tests without direct config imports."""
    from autoskillit.config.settings import QuotaGuardConfig

    return QuotaGuardConfig(**overrides)


def make_model_config(**overrides):
    """Build CoreRunConfig for tests; uses direct assignment to allow empty/None fields."""
    from autoskillit.config.settings import CoreRunConfig

    cfg = CoreRunConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_tracing_config(**overrides):
    """Build LinuxTracingConfig for tests without direct config imports."""
    from autoskillit.config.settings import LinuxTracingConfig

    return LinuxTracingConfig(**overrides)


def make_run_skill_config(**overrides):
    """Build RunSkillConfig for tests without direct config imports."""
    from autoskillit.config.settings import RunSkillConfig

    return RunSkillConfig(**overrides)


def make_subsetsconfig(**overrides):
    """Build SubsetsConfig for tests without direct config imports."""
    from autoskillit.config.settings import SubsetsConfig

    return SubsetsConfig(**overrides)


def make_skills_config(**overrides):
    """Build SkillsConfig for tests without direct config imports."""
    from autoskillit.config.settings import SkillsConfig

    return SkillsConfig(**overrides)


def make_test_check_config(**overrides):
    """Build TestCheckConfig for tests without direct config imports."""
    from autoskillit.config.settings import TestCheckConfig

    return TestCheckConfig(**overrides)


def make_dynaconf_and_automation_config():
    """Return (_make_dynaconf, AutomationConfig) for integration tests."""
    from autoskillit.config import AutomationConfig
    from autoskillit.config._config_loader import _make_dynaconf

    return _make_dynaconf, AutomationConfig


def extract_never_block(skill_text: str) -> str:
    """Extract the NEVER block from a SKILL.md text.

    Finds the line starting with **NEVER:** or **NEVER** and collects all
    subsequent ``- `` list items until the next ``**`` header or end of text.
    Returns the raw text of the NEVER block (may be empty string if not found).
    """

    never_match = re.search(r"(?m)^\*\*NEVER(?::\*\*|\*\*)\s*$", skill_text)
    if not never_match:
        return ""
    start = never_match.end()
    next_header = re.search(r"(?m)^\*\*[A-Z][^\n]*\*\*", skill_text[start:])
    if next_header:
        end = start + next_header.start()
    else:
        end = len(skill_text)
    block = skill_text[start:end]
    lines = [line for line in block.splitlines() if line.strip().startswith("- ")]
    return "\n".join(lines)


def _extract_python_docstrings(path: Path) -> str:
    """AST-parse a module and concatenate its module/function/class docstrings only.

    Whole-file reading over-triggers on legitimate code (e.g. keyword-argument
    ``model=`` calls); docstring-only extraction is both narrower (no false
    positives) and semantically right — only docstrings ship to the
    orchestrator as MCP tool descriptions.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docs: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        docs.append(module_doc)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node_doc = ast.get_docstring(node)
            if node_doc:
                docs.append(node_doc)
    return "\n\n".join(docs)


def resolve_orchestrator_surface_paths(
    surface: OrchestratorSurfaceDef, src_root: Path
) -> tuple[Path, ...]:
    """Resolve a MARKDOWN_FULL/PYTHON_DOCSTRINGS surface's glob to matched file paths.

    Raises for GENERATED_OUTPUT surfaces, which have no filesystem glob to resolve.
    """
    if surface.extraction_mode is InstructionExtractionMode.GENERATED_OUTPUT:
        raise ValueError(f"{surface.name}: GENERATED_OUTPUT has no path_glob to resolve")
    assert surface.path_glob is not None
    return tuple(sorted(src_root.glob(surface.path_glob)))


def extract_orchestrator_surface_texts(
    surface: OrchestratorSurfaceDef, src_root: Path
) -> dict[str, str]:
    """Extract text per constituent source for one registered surface, honoring its mode.

    Returns ``{identifier: text}`` — one entry per glob-matched file for
    MARKDOWN_FULL/PYTHON_DOCSTRINGS (identifier = path relative to ``src_root``),
    or a single entry for GENERATED_OUTPUT (identifier = ``"module:symbol"``).
    Per-file granularity is preserved (rather than one concatenated blob) so a
    sweep over a glob-matched surface can still name the specific offending file.
    """
    if surface.extraction_mode is InstructionExtractionMode.GENERATED_OUTPUT:
        assert surface.producer_module is not None
        assert surface.producer_symbol is not None
        module = importlib.import_module(surface.producer_module)
        producer = getattr(module, surface.producer_symbol)
        identifier = f"{surface.producer_module}:{surface.producer_symbol}"
        return {identifier: producer()}

    paths = resolve_orchestrator_surface_paths(surface, src_root)
    if surface.extraction_mode is InstructionExtractionMode.MARKDOWN_FULL:
        return {str(p.relative_to(src_root)): p.read_text(encoding="utf-8") for p in paths}
    # PYTHON_DOCSTRINGS
    return {str(p.relative_to(src_root)): _extract_python_docstrings(p) for p in paths}


def extract_always_block(skill_text: str) -> str:
    """Extract the ALWAYS block from a SKILL.md text.

    Finds the line starting with **ALWAYS:** or **ALWAYS** and collects all
    subsequent ``- `` list items until the next ``**`` header or end of text.
    Returns the raw text of the ALWAYS block (may be empty string if not found).
    """

    always_match = re.search(r"(?m)^\*\*ALWAYS(?::\*\*|\*\*)\s*$", skill_text)
    if not always_match:
        return ""
    start = always_match.end()
    next_header = re.search(r"(?m)^\*\*[A-Z][^\n]*\*\*", skill_text[start:])
    if next_header:
        end = start + next_header.start()
    else:
        end = len(skill_text)
    block = skill_text[start:end]
    lines = [line for line in block.splitlines() if line.strip().startswith("- ")]
    return "\n".join(lines)


# Implemented phoropter lens families — designed-only families (e.g.
# refactor-lens) are excluded because they have zero lens directories under
# skills_extended/. Shared between tests/assets/test_phoropter_registry.py
# and tests/skills/test_phoropter_structural.py to keep a single source of
# truth.
IMPLEMENTED_FAMILIES: frozenset[str] = frozenset({"arch-lens", "exp-lens", "vis-lens"})
