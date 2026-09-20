"""Side-effect routing coverage for fresh and resumed ``order`` launches."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import autoskillit.cli._preview as _preview
import autoskillit.cli.prompts as _prompts
import autoskillit.cli.session._session_backend as _session_backend
import autoskillit.cli.session._session_launch_intent as _launch_intent
import autoskillit.cli.session._session_order as _order
import autoskillit.cli.ui._timed_input as _timed_input
import autoskillit.config as _config
import autoskillit.recipe as _recipe
from autoskillit import cli
from autoskillit.core import FreshLaunch, RestoreSession, SessionType

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
    "AUTOSKILLIT_SESSION_TYPE": SessionType.ORCHESTRATOR.value,
}
_FEATURE_ENV = {
    "AUTOSKILLIT_SUBSETS__DISABLED": "@json []",
    "AUTOSKILLIT_PACKS__ENABLED": '@json ["kitchen-core", "research"]',
}


class _RoutingBackend:
    def __init__(self, name: str, events: Counter[str]) -> None:
        self.name = name
        self.capabilities = SimpleNamespace(
            has_unguarded_filesystem_access=True,
            managed_fixed_batch_route_capable=False,
        )
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
    picker: int
    expected_session_id: str | None


_INVOCATIONS = (
    _Invocation("recipe-fresh", "implementation", None, False, 1, 1, 0, 0, None),
    _Invocation("recipe-explicit", "implementation", _SESSION_ID, False, 1, 0, 1, 0, _SESSION_ID),
    _Invocation("recipe-bare-resume", "implementation", None, True, 1, 0, 1, 1, _PICKED_ID),
    _Invocation("uuid-resume", _SESSION_ID, None, True, 0, 0, 1, 0, _SESSION_ID),
    _Invocation("bare-resume", None, None, True, 0, 0, 1, 1, _PICKED_ID),
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
    monkeypatch.setattr(_prompts, "_build_open_kitchen_prompt", lambda *a, **kw: "prompt")
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
        if prompt.lstrip().startswith("Launch session?"):
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
    monkeypatch.setattr(_order, "claim_launch_for_session", lambda *a, **kw: "launch-id")
    monkeypatch.setattr(_order, "release_session_claim", lambda *a, **kw: None)
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
    assert picker.call_count == invocation.picker
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
