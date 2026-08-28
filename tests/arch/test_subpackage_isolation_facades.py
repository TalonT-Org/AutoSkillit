from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_response_budget_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "_response_budget"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_primitives",
        "_projection",
        "_spill",
        "_enforce",
    }


def test_execution_helpers_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "_execution_helpers"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_skill_contract",
        "_dispatch_metadata",
        "_run_cmd_spill",
        "_run_python_coercion",
    }


def test_evidence_reader_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "_evidence_reader"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_authority",
        "_invocation",
        "_reader",
        "_startup",
    }


def test_tools_kitchen_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_kitchen"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_open_kitchen",
        "_open_kitchen_transition",
        "_open_kitchen_errors",
        "_close_kitchen",
        "_lock_ingredients",
        "_reload_session",
        "_disable_quota_guard",
        "_get_recipe",
        "_hook_config",
        "_tracker_authority",
        "_declare_join_batch",
    }


def test_tools_fleet_dispatch_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_provenance",
        "_campaign_state",
        "_handlers",
    }


def test_tools_pipeline_tracker_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_pipeline_tracker"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_authority",
        "_status",
        "_handlers",
    }


def test_tools_execution_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_execution"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_audit_response",
        "_gates",
        "_run_cmd",
        "_run_python",
        "_run_skill_admission",
        "_run_skill_dispatch",
        "_run_skill_finalize",
        "_run_skill_prepare",
        "_run_skill_session",
        "_state",
    }
    assert not (SRC_ROOT / "server" / "tools" / "tools_execution.py").exists()


def test_lifespan_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "_lifespan"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_startup_checks",
        "_session_boots",
        "_lifespan",
    }


def test_prompts_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "prompts"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_prompts",
        "_prompts_campaign",
        "_prompts_kitchen",
        "_prompts_orchestrator",
    }


def test_install_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "install"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_install_contract",
        "_install_info",
        "_installed_plugins",
        "_marketplace",
        "_plugin_artifact",
    }


def test_session_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "session"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_session_backend",
        "_session_constants",
        "_session_cook",
        "_session_launch",
        "_session_onboarding",
        "_session_order",
        "_session_picker",
        "_session_process",
        "_session_reload",
        "_session_startup_trace",
    }


def test_update_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "update"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_obligation_repair",
        "_transaction",
        "_update",
        "_update_checks",
        "_update_checks_fetch",
        "_update_checks_source",
        "_restart",
    }


def test_ops_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "ops"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_capture_store",
        "_codex_attempts",
        "_codex_orphans",
        "_daemon_orphans",
        "_process_orphans",
        "_sessions",
    }


@pytest.mark.parametrize(
    "facade_pkg",
    [
        "autoskillit.cli.prompts",
        "autoskillit.cli.ops",
        "autoskillit.cli.install",
        "autoskillit.smoke_utils.review",
    ],
)
def test_cli_facade_all_resolves(facade_pkg: str) -> None:
    """Guard: facade ``__all__`` entries resolve and match submodule declarations.

    Forward direction (always covered): every name declared in the facade's
    ``__all__`` must resolve via ``hasattr`` — otherwise ``from autoskillit.cli.X
    import <name>`` raises ``ImportError``, the import form used by virtually
    every consumer.

    Reverse direction (covered for submodules that declare ``__all__``): when a
    submodule declares an ``__all__``, every entry must also appear in the
    facade's ``__all__`` and resolve to the same object. This catches drift
    where a builder is added to one layer (e.g. ``_prompts.py``) but not the
    other (e.g. ``prompts/__init__.py``), leaving the two lists silently out of
    sync. Submodules without ``__all__`` are not reverse-checked here — they
    rely on the forward-only ``hasattr`` check and on the existing
    ``TestPromptsReExporter`` guard for the inner-hub case.
    """
    import importlib

    facade = importlib.import_module(facade_pkg)
    declared = set(getattr(facade, "__all__", ()))
    assert declared, f"{facade_pkg}.__all__ is empty or missing"

    # Forward direction: every declared name must resolve.
    missing = sorted(name for name in declared if not hasattr(facade, name))
    assert not missing, f"{facade_pkg} __all__ lists names that do not resolve: {missing}"

    # Reverse direction: where declared, lazy-loaded entries must resolve to
    # the same object as the submodule attribute. ``_*.py`` glob also matches
    # ``__init__.py`` itself — skip the self-comparison.
    pkg_dir = SRC_ROOT / facade_pkg.replace("autoskillit.", "").replace(".", "/")
    for submodule_path in pkg_dir.glob("_*.py"):
        if submodule_path.name == "__init__.py":
            continue
        submodule_name = submodule_path.stem
        submodule = importlib.import_module(f"{facade_pkg}.{submodule_name}")
        for name in getattr(submodule, "__all__", ()):
            if name not in declared:
                assert False, (
                    f"{facade_pkg}.{submodule_name}.{name!r} is in submodule __all__ "
                    f"but missing from facade __all__"
                )
            facade_value = getattr(facade, name)
            submodule_value = getattr(submodule, name)
            assert facade_value is submodule_value, (
                f"{facade_pkg}.{name!r} resolves to a different object than "
                f"{submodule_name}.{name!r}"
            )
