"""Plugin artifact lifetime guidance must match the launch contract."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.small]

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"


def test_inline_projection_troubleshooting_is_lifetime_accurate() -> None:
    visibility = (DOCS_ROOT / "skills" / "visibility.md").read_text(encoding="utf-8")

    required = (
        "issue #4382",
        "`autoskillit@inline` is a session-only projection",
        "`/plugin` reinstall is inapplicable",
        "Reprojection or `/reload-plugins` can provide temporary recovery",
        "retirement, and reclamation while any reader lease",
    )
    for phrase in required:
        assert phrase in visibility
    assert "session-bound ephemeral skill tree" not in visibility


def test_backend_contract_documents_binding_load_modes_and_fd_ownership() -> None:
    contract = (DOCS_ROOT / "design" / "acp-session-contract.md").read_text(encoding="utf-8")

    for phrase in (
        "`EXPLICIT_PLUGIN_DIR`",
        "`PROJECTED_HOME`",
        "`IMPLICIT_INSTALLED`",
        "`GENERATED_HOME`",
        "`CmdSpec.inherited_fds`",
        "owning binding closes only after final child reap",
        "Plugin source and lifetime are no longer discard sites",
    ):
        assert phrase in contract

    obsolete = (
        "**discards `plugin_source`, `output_format`, `exit_after_stop_delay_ms`**",
        "| `plugin_source` | F841 |",
        "callers can pass\n`plugin_source`",
    )
    for phrase in obsolete:
        assert phrase not in contract
