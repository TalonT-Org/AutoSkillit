"""Side-effect routing coverage for fresh and resumed ``order`` launches."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import autoskillit.cli._preview as _preview
import autoskillit.cli.session._session_backend as _session_backend
import autoskillit.cli.session._session_launch_intent as _launch_intent
import autoskillit.cli.session._session_order as _order
import autoskillit.cli.ui._timed_input as _timed_input
import autoskillit.config as _config
import autoskillit.recipe as _recipe
from autoskillit import cli
from autoskillit.core import FreshLaunch, RestoreSession

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.usefixtures("_stub_interactive_prelaunch"),
    pytest.mark.usefixtures("_stub_owner_binding"),
]

_SESSION_ID = "fa910a41-d1ca-4cae-b878-01028a0c7c1c"
_PICKED_ID = "4b581974-1f19-4aec-8405-78c5ede5e233"
_ENTRY_ENV = {
    "AUTOSKILLIT_LAUNCH_ID": "launch-id",
    "AUTOSKILLIT_SESSION_TYPE": "order",
}
_FEATURE_ENV = {
    "AUTOSKILLIT_SUBSETS__DISABLED": "@json []",
    "AUTOSKILLIT_PACKS__ENABLED": '@json ["kitchen-core", "research"]',
}


class _RoutingBackend:
    def __init__(self, name: str, events: Counter[str]) -> None:
        self.name = name
        self.capabilities = SimpleNamespace(has_unguarded_filesystem_access=True)
        self._events = events

    def recover_cook_history(self) -> None:
        self._events["recover"] += 1

    def session_locator(self) -> object:
        return SimpleNamespace()


@dataclass(frozen=True)
class _Invocation:
    name: str
    recipe: str | None
    session_id: str | None
    resume: bool
    recipe_effects: int
    ceremony: int
    housekeeping: int
    expected_session_id: str | None


_INVOCATIONS = (
    _Invocation("recipe-fresh", "implementation", None, False, 1, 1, 0, None),
    _Invocation("recipe-explicit", "implementation", _SESSION_ID, False, 1, 0, 1, _SESSION_ID),
    _Invocation("recipe-bare-resume", "implementation", None, True, 1, 0, 1, _PICKED_ID),
    _Invocation("uuid-resume", _SESSION_ID, None, True, 0, 0, 1, _SESSION_ID),
    _Invocation("bare-resume", None, None, True, 0, 0, 1, _PICKED_ID),
)


def _install_order_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    backend_name: str,
    is_tty: bool = True,
    picked_session: str | None = _PICKED_ID,
    prompt_answers: list[str] | None = None,
) -> tuple[Counter[str], list[dict[str, object]], MagicMock, _RoutingBackend]:
    events: Counter[str] = Counter()
    launches: list[dict[str, object]] = []
    backend = _RoutingBackend(backend_name, events)
    real_config = _config.load_config(tmp_path)
    config = MagicMock(wraps=real_config)
    config.subsets = SimpleNamespace(disabled=["github"])
    config.packs = SimpleNamespace(enabled=["kitchen-core"])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: is_tty)
    monkeypatch.setattr(_session_backend, "resolve_global_backend", lambda *a, **kw: backend)
    monkeypatch.setattr(_config, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(_order, "detect_autoskillit_mcp_prefix", lambda _caps: "autoskillit")
    monkeypatch.setattr(_order, "validate_skill_tier_roles", lambda *a, **kw: None)
    monkeypatch.setattr(
        _order,
        "DefaultSkillResolver",
        lambda: SimpleNamespace(list_effective=lambda *a, **kw: SimpleNamespace(exclusions=())),
    )
    monkeypatch.setattr(_order, "compile_session_skill_catalog", lambda *a, **kw: object())
    monkeypatch.setattr(_order, "render_skill_catalog_exclusions", lambda _items: None)
    monkeypatch.setattr(_order, "_get_ingredients_table", lambda *a, **kw: "ingredients")
    monkeypatch.setattr(_order, "_build_orchestrator_prompt", lambda *a, **kw: "prompt")
    monkeypatch.setattr(_order, "_get_subsets_needed", lambda *a, **kw: frozenset({"github"}))
    monkeypatch.setattr(_order, "_get_packs_needed", lambda *a, **kw: frozenset({"research"}))

    recipe_info = SimpleNamespace(path=tmp_path / "implementation.yaml", name="implementation")
    parsed = SimpleNamespace(requires_packs=["research"])
    monkeypatch.setattr(
        _recipe,
        "find_recipe_by_name",
        lambda *a, **kw: events.update(["recipe_lookup"]) or recipe_info,
    )
    monkeypatch.setattr(
        _recipe,
        "load_recipe",
        lambda *a, **kw: events.update(["recipe_load"]) or parsed,
    )
    monkeypatch.setattr(
        _recipe,
        "validate_recipe_structure",
        lambda *a, **kw: events.update(["recipe_validation"]) or [],
    )
    monkeypatch.setattr(
        _preview,
        "show_cook_preview",
        lambda *a, **kw: events.update(["preview"]),
    )

    answers = iter(prompt_answers or ["1", "1", ""])

    def timed_prompt(prompt: str, **_kwargs: object) -> str:
        if prompt.startswith("Launch session?"):
            events["confirm"] += 1
        else:
            events["feature_prompt"] += 1
        return next(answers)

    monkeypatch.setattr(_timed_input, "timed_prompt", timed_prompt)
    picker = MagicMock(return_value=picked_session)
    monkeypatch.setattr(_launch_intent, "pick_session", picker)
    sweep = MagicMock(side_effect=lambda *_a, **_kw: events.update(["sweep"]))
    monkeypatch.setattr(_launch_intent, "sweep_orphaned_tethers", sweep)
    monkeypatch.setattr(_launch_intent, "default_tether_dir", lambda: tmp_path / "tethers")
    monkeypatch.setattr(_order, "_enable_subsets_permanently", MagicMock())
    monkeypatch.setattr(_order, "_enable_packs_permanently", MagicMock())
    monkeypatch.setattr(_order, "_write_order_entry", lambda *a, **kw: ("launch-id", _ENTRY_ENV))
    monkeypatch.setattr(
        _order,
        "_launch_cook_session",
        lambda **kwargs: launches.append(kwargs),
    )
    return events, launches, picker, backend


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
@pytest.mark.parametrize("invocation", _INVOCATIONS, ids=lambda case: case.name)
def test_order_side_effect_matrix(
    backend_name: str,
    invocation: _Invocation,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events, launches, picker, _backend = _install_order_harness(
        monkeypatch,
        tmp_path,
        backend_name=backend_name,
    )

    cli.order(invocation.recipe, invocation.session_id, resume=invocation.resume)

    assert events["recipe_lookup"] == invocation.recipe_effects
    assert events["recipe_load"] == invocation.recipe_effects
    assert events["recipe_validation"] == invocation.recipe_effects
    assert events["preview"] == invocation.ceremony
    assert events["confirm"] == invocation.ceremony
    assert events["feature_prompt"] == 2 * invocation.ceremony
    assert events["sweep"] == invocation.housekeeping
    assert events["recover"] == invocation.housekeeping
    assert picker.call_count == int(invocation.resume and invocation.session_id is None)
    assert len(launches) == 1

    launch = launches[0]["launch"]
    if invocation.expected_session_id is None:
        assert isinstance(launch, FreshLaunch)
    else:
        assert launch == RestoreSession(invocation.expected_session_id)
    expected_env = dict(_ENTRY_ENV)
    if invocation.recipe_effects:
        expected_env.update(_FEATURE_ENV)
    assert launches[0]["extra_env"] == expected_env


@pytest.mark.parametrize(
    ("recipe", "session_id", "resume", "is_tty"),
    [
        ("implementation", _SESSION_ID, False, True),
        ("implementation", None, False, False),
    ],
    ids=["explicit-resume", "fresh-non-tty"],
)
def test_order_automatic_feature_enablement_never_prompts_or_writes_config(
    recipe: str,
    session_id: str | None,
    resume: bool,
    is_tty: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events, launches, _picker, _backend = _install_order_harness(
        monkeypatch,
        tmp_path,
        backend_name="claude-code",
        is_tty=is_tty,
    )
    prompt = MagicMock(side_effect=AssertionError("automatic launch must not prompt"))
    subset_write = MagicMock()
    pack_write = MagicMock()
    monkeypatch.setattr(_timed_input, "timed_prompt", prompt)
    monkeypatch.setattr(_order, "_enable_subsets_permanently", subset_write)
    monkeypatch.setattr(_order, "_enable_packs_permanently", pack_write)

    cli.order(recipe, session_id, resume=resume)

    prompt.assert_not_called()
    subset_write.assert_not_called()
    pack_write.assert_not_called()
    assert events["preview"] == int(not resume and session_id is None)
    assert launches[0]["extra_env"] == {**_ENTRY_ENV, **_FEATURE_ENV}


def test_order_bare_resume_without_selection_becomes_fresh_ceremony(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events, launches, picker, _backend = _install_order_harness(
        monkeypatch,
        tmp_path,
        backend_name="claude-code",
        picked_session=None,
        prompt_answers=[""],
    )

    cli.order(resume=True)

    picker.assert_called_once()
    assert events["sweep"] == 1
    assert events["recover"] == 1
    assert events["confirm"] == 0
    assert isinstance(launches[0]["launch"], FreshLaunch)


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for imported in ast.walk(tree):
        if isinstance(imported, ast.ImportFrom) and imported.module is not None:
            for name in imported.names:
                aliases[name.asname or name.name] = f"{imported.module}.{name.name}"
        elif isinstance(imported, ast.Import):
            for name in imported.names:
                aliases[name.asname or name.name] = name.name
    return aliases


def _qualified_name(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_qualified_name(node.value, aliases)}.{node.attr}"
    return ast.dump(node, include_attributes=False)


def _qualified_calls(path: Path, function_names: set[str]) -> Counter[str]:
    """Return a bounded, source-backed call inventory for named functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _import_aliases(tree)

    inventory: Counter[str] = Counter()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in function_names:
            continue
        for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
            inventory[f"{node.name}:{_qualified_name(call.func, aliases)}"] += 1
    return inventory


_ORDER_FUNCTIONS = {
    "order",
    "_resolve_order_recipe",
    "_derive_order_feature_env",
    "_run_fresh_order_ceremony",
}
_EXPECTED_CALLS = Counter(
    {
        "order:autoskillit.cli.session._session_backend.resolve_global_backend": 1,
        "order:autoskillit.cli.session._session_launch_intent.resolve_interactive_launch": 1,
        "order:autoskillit.cli.session._session_launch._launch_cook_session": 1,
        "order:autoskillit.cli.session._session_launch._write_order_entry": 2,
        "order:autoskillit.recipe.find_recipe_by_name": 1,
        "order:autoskillit.recipe.load_recipe": 1,
        "order:autoskillit.recipe.validate_recipe_structure": 1,
        "order:autoskillit.cli._preview.show_cook_preview": 1,
        "order:autoskillit.cli.ui._timed_input.timed_prompt": 3,
        "order:autoskillit.execution.sweep_orphaned_tethers": 1,
        "order:backend.recover_cook_history": 1,
        "resolve_interactive_launch:pick_session": 1,
        "resolve_interactive_launch:backend.session_locator": 1,
        "resolve_interactive_launch:autoskillit.core.FreshLaunch": 2,
        "resolve_interactive_launch:autoskillit.core.RestoreSession": 2,
        "resolve_interactive_launch:typing.assert_never": 1,
    }
)
_EXPECTED_CALLS.update(
    {
        (
            "order:Call(func=Attribute(value=Name(id='random', ctx=Load()), "
            "attr='choice', ctx=Load()), args=[Name(id='_COOK_GREETINGS', "
            "ctx=Load())]).format"
        ): 1,
        "order:Constant(value=', ').join": 2,
        "order:SystemExit": 1,
        "order:TypeError": 1,
        "order:_UUID_RE.match": 1,
        "order:_enable_packs_permanently": 1,
        "order:_enable_subsets_permanently": 1,
        "order:_get_packs_needed": 1,
        "order:_get_subsets_needed": 1,
        "order:_recipes_dir_for": 1,
        "order:autoskillit.cli.prompts._build_open_kitchen_prompt": 1,
        "order:autoskillit.cli.prompts._build_orchestrator_prompt": 1,
        "order:autoskillit.cli.prompts._get_ingredients_table": 1,
        "order:autoskillit.cli.session._session_launch.render_skill_catalog_exclusions": 1,
        (
            "order:autoskillit.cli.session._session_launch."
            "render_skill_contract_composition_failure"
        ): 1,
        "order:autoskillit.cli.ui._ansi.permissions_warning": 1,
        "order:autoskillit.cli.ui._menu.run_selection_menu": 1,
        "order:autoskillit.config.load_config": 2,
        "order:autoskillit.core.PACK_REGISTRY.items": 1,
        "order:autoskillit.core.detect_autoskillit_mcp_prefix": 1,
        "order:autoskillit.core.resume_spec_from_cli": 2,
        "order:autoskillit.execution.default_tether_dir": 1,
        "order:autoskillit.recipe.list_recipes": 2,
        "order:autoskillit.workspace.DefaultSkillResolver": 1,
        "order:autoskillit.workspace.compile_session_skill_catalog": 1,
        "order:autoskillit.workspace.validate_skill_tier_roles": 1,
        "order:config.codex_runtime.resolve": 1,
        "order:config.skill_visibility_spec": 1,
        "order:confirm.lower": 1,
        "order:dataclasses.replace": 2,
        "order:frozenset": 3,
        "order:isinstance": 4,
        "order:json.dumps": 1,
        "order:logger.warning": 1,
        "order:os.environ.get": 1,
        "order:pathlib.Path.cwd": 9,
        "order:print": 21,
        "order:random.choice": 2,
        "order:skill_resolver.list_effective": 1,
        "order:sorted": 3,
        "order:sys.exit": 7,
    }
)
_EFFECT_COLUMNS = {
    "order:autoskillit.recipe.validate_recipe_structure": "recipe_validation",
    "order:autoskillit.cli._preview.show_cook_preview": "preview",
    "order:autoskillit.cli.ui._timed_input.timed_prompt": "feature_or_confirm_prompt",
    "order:autoskillit.execution.sweep_orphaned_tethers": "tether_sweep",
    "order:backend.recover_cook_history": "recover_cook_history",
    "resolve_interactive_launch:pick_session": "bare_resume_picker",
}


def test_order_matrix_inventory_matches_production_calls() -> None:
    """Any new routing call must be counted and assigned to the matrix."""
    root = Path(__file__).parents[2]
    order_path = root / "src/autoskillit/cli/session/_session_order.py"
    intent_path = root / "src/autoskillit/cli/session/_session_launch_intent.py"
    order_tree = ast.parse(order_path.read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in order_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert _ORDER_FUNCTIONS <= defined

    actual = _qualified_calls(order_path, _ORDER_FUNCTIONS)
    actual.update(_qualified_calls(intent_path, {"resolve_interactive_launch"}))
    reviewed = set(_EXPECTED_CALLS) - set(_EFFECT_COLUMNS)
    assert set(_EXPECTED_CALLS) == reviewed | set(_EFFECT_COLUMNS)
    assert actual == _EXPECTED_CALLS
