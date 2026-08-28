"""REQ-ARCH-002 registration façade for subpackage-isolation guards."""

from __future__ import annotations

import pytest

from tests.arch._rules import RuleDescriptor
from tests.arch._subpackage_isolation_line_limits import (
    _LINE_LIMIT_EXEMPTIONS as _LINE_LIMIT_EXEMPTIONS,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

ISOLATION_RULES: dict[str, RuleDescriptor] = {
    "REQ-ARCH-002": RuleDescriptor(
        rule_id="REQ-ARCH-002",
        name="tool-context-service-fields-use-protocol-types",
        lens="module-dependency",
        description=(
            "Every non-exempt ToolContext service field must be annotated with a Protocol "
            "type from autoskillit.core.types, not a concrete implementation class."
        ),
        rationale=(
            "Protocol-typed fields enable dependency injection and make the context "
            "independently testable without importing concrete server or execution classes."
        ),
        exemptions=frozenset({"plugin_dir", "config"}),  # non-service structural fields
        severity="high",
        defense_standard="DS-008",
    ),
}
