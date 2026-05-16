"""Shared test helper utilities."""

from __future__ import annotations

import sys


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
    """Build CoreRunConfig for tests without direct config imports."""
    from autoskillit.config.settings import CoreRunConfig

    return CoreRunConfig(**overrides)


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
    import re

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
