"""Tests for the report deck's view and facet registry."""

import pytest

from autoskillit.core import SessionType, TokenMeasureState
from autoskillit.execution._report_index_rows import ReportSessionRow
from autoskillit.report.deck._registry import (
    AVAILABILITY_VOCABULARY,
    DECK_VIEWS,
    FACETS,
    LANDING_VIEW,
    SESSION_COLUMNS,
    SESSION_TABLE,
    WINDOWS,
    DeckViewDef,
    FacetDef,
    ReasonDef,
    StrikeDef,
    _validate_deck_views,
)

pytestmark = [pytest.mark.small]


def _built_view(
    *,
    view_id: str = "cohort",
    table: str | None = SESSION_TABLE,
    script: str | None = "views/cohort.js",
    strikes: tuple[StrikeDef, ...] = (),
) -> DeckViewDef:
    return DeckViewDef(
        view_id,
        "question",
        "decision",
        "group",
        table,
        script,
        None,
        strikes,
    )


def _planned_view(
    *,
    view_id: str = "future",
    table: str | None = None,
    script: str | None = None,
) -> DeckViewDef:
    return DeckViewDef(
        view_id,
        "question",
        "decision",
        "group",
        table,
        script,
        9999,
    )


def _invalid_registry_cases() -> list[
    tuple[str, tuple[DeckViewDef, ...], tuple[FacetDef, ...], str]
]:
    built = _built_view()
    return [
        ("duplicate view id", (built, built), FACETS, "cohort"),
        ("built view missing script", (_built_view(script=None),), FACETS, "cohort"),
        ("built view missing table", (_built_view(table=None),), FACETS, "cohort"),
        ("planned view has script", (_planned_view(script="views/future.js"),), FACETS, "cohort"),
        ("planned view has table", (_planned_view(table=SESSION_TABLE),), FACETS, "cohort"),
        (
            "strike names unknown facet",
            (_built_view(strikes=(StrikeDef("missing", "x", ReasonDef("reason")),)),),
            FACETS,
            "cohort",
        ),
        (
            "all history cannot be struck",
            (_built_view(strikes=(StrikeDef("window", "all", ReasonDef("reason")),)),),
            FACETS,
            "cohort",
        ),
        ("landing is planned", (built, _planned_view(view_id="future")), FACETS, "future"),
    ]


@pytest.mark.parametrize(
    ("views", "facets", "landing"),
    [(views, facets, landing) for _, views, facets, landing in _invalid_registry_cases()],
    ids=[name for name, *_ in _invalid_registry_cases()],
)
def test_validate_deck_views_rejects_invalid_registries(
    views: tuple[DeckViewDef, ...], facets: tuple[FacetDef, ...], landing: str
) -> None:
    with pytest.raises(ValueError):
        _validate_deck_views(views, facets, landing)


def test_shipped_deck_view_registry_is_valid() -> None:
    _validate_deck_views(DECK_VIEWS, FACETS, LANDING_VIEW)


def test_level_facet_tracks_session_type_values_and_l0_gap() -> None:
    level = next(facet for facet in FACETS if facet.facet_id == "level")
    values = {value.key: value for value in level.declared}

    assert values["L1"].match == SessionType.SKILL.value
    assert values["L2"].match == SessionType.ORCHESTRATOR.value
    assert values["L3"].match == SessionType.FLEET.value
    assert values["L0"].match is None
    assert SESSION_TABLE in dict(values["L0"].unresolvable_in)


def test_session_columns_follow_report_session_row_schema() -> None:
    row_keys = ReportSessionRow.__required_keys__ | ReportSessionRow.__optional_keys__

    assert set(SESSION_COLUMNS) == row_keys - {"schema_version", "kind"}
    assert len(SESSION_COLUMNS) == len(set(SESSION_COLUMNS))


def test_availability_vocabulary_covers_every_token_measure_state() -> None:
    assert set(AVAILABILITY_VOCABULARY) == set(TokenMeasureState)


def test_window_presets_match_the_prototype() -> None:
    assert [window.key for window in WINDOWS] == ["7d", "28d", "all"]
    assert [window.key for window in WINDOWS if window.days is None] == ["all"]
