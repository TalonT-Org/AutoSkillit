from typing import TypedDict

__all__ = ["FigureSpec", "REQUIRED_CONSUMER_FIELDS", "PRODUCER_SCHEMA_FIELDS"]


class FigureSpec(TypedDict, total=False):
    figure_id: str
    figure_title: str
    spec_version: str
    chart_type: str
    chart_type_fallback: str
    perceptual_justification: str
    data_source: str
    report_section: str
    image_path: str
    priority: str
    placement_tier: str
    format: str
    target_dpi: int
    library: str
    palette: str


REQUIRED_CONSUMER_FIELDS: frozenset[str] = frozenset(
    {
        "report_section",
        "figure_title",
        "figure_id",
        "image_path",
    }
)

PRODUCER_SCHEMA_FIELDS: frozenset[str] = frozenset(FigureSpec.__annotations__.keys())
