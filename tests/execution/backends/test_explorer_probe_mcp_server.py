"""Tests for the deliberately tiny explorer capability probe broker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.client import Client

from tests.execution.backends._explorer_probe_mcp_server import (
    FORBIDDEN_OPERATIONS,
    ForbiddenOperation,
    build_probe_server,
)


def _result_json(result: object) -> dict[str, object]:
    content = getattr(result, "content")
    assert len(content) == 1
    return json.loads(content[0].text)


def _server(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    (repository / "readable.txt").write_text(
        "FIRST_MARKER\nsecond FIRST_MARKER\n", encoding="utf-8"
    )
    (repository / "semantic.py").write_text(
        "def alpha():\n    return 1\n\nasync def beta():\n    return 2\n",
        encoding="utf-8",
    )
    audit = tmp_path / "audit" / "probe.jsonl"
    return repository, audit, build_probe_server(repository, audit)


@pytest.mark.anyio
async def test_only_the_closed_probe_tool_surface_is_exposed(tmp_path: Path) -> None:
    _, _, server = _server(tmp_path)

    async with Client(server) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {
        "deny_operations",
        "parse_python_ast",
        "bounded_literal_search",
        "optional_capability_status",
    }


@pytest.mark.anyio
async def test_fixed_readers_return_bounded_allowed_results_and_audit(tmp_path: Path) -> None:
    _, audit, server = _server(tmp_path)

    async with Client(server) as client:
        search = _result_json(
            await client.call_tool("bounded_literal_search", {"needle": "FIRST_"})
        )
        functions = _result_json(await client.call_tool("parse_python_ast", {}))
        optional = _result_json(await client.call_tool("optional_capability_status", {}))

    assert search == {
        "matches": [
            {"line": 1, "text": "FIRST_MARKER"},
            {"line": 2, "text": "second FIRST_MARKER"},
        ],
        "total_matches": 2,
        "truncated": False,
    }
    assert functions == {"function_names": ["alpha", "beta"], "truncated": False}
    assert optional == {
        "lsp": "LSP_UNSUPPORTED",
        "tree_sitter": "TREE_SITTER_UNSUPPORTED",
    }
    assert [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()] == [
        {"operation": "bounded_literal_search", "status": "allowed"},
        {"operation": "parse_python_ast", "status": "allowed"},
        {"operation": "optional_capability_status", "status": "allowed"},
    ]


@pytest.mark.anyio
async def test_fixed_readers_reject_symlinks_and_oversized_files(tmp_path: Path) -> None:
    repository, audit, server = _server(tmp_path)
    target = repository / "target.txt"
    target.write_text("FIRST_MARKER\n", encoding="utf-8")
    (repository / "readable.txt").unlink()
    (repository / "readable.txt").symlink_to(target)

    async with Client(server) as client:
        result = await client.call_tool(
            "bounded_literal_search", {"needle": "FIRST_"}, raise_on_error=False
        )

    assert result.is_error
    assert "non-symlink regular file" in result.content[0].text
    assert json.loads(audit.read_text(encoding="utf-8")) == {
        "operation": "bounded_literal_search",
        "status": "denied",
    }

    clean_repository, _, oversized_server = _server(tmp_path / "oversized")
    (clean_repository / "semantic.py").write_bytes(b"#" * (64 * 1024 + 1))
    async with Client(oversized_server) as client:
        result = await client.call_tool("parse_python_ast", {}, raise_on_error=False)
    assert result.is_error
    assert "exceeds 65536 bytes" in result.content[0].text


@pytest.mark.anyio
async def test_closed_denial_enum_denies_and_audits_every_requested_operation(
    tmp_path: Path,
) -> None:
    _, audit, server = _server(tmp_path)

    async with Client(server) as client:
        result = _result_json(
            await client.call_tool(
                "deny_operations",
                {
                    "operations": [
                        ForbiddenOperation.SOURCE_OVERWRITE.value,
                        ForbiddenOperation.NETWORK_ACCESS.value,
                    ]
                },
            )
        )
        invalid = await client.call_tool(
            "deny_operations", {"operations": ["made_up_operation"]}, raise_on_error=False
        )

    assert result == {
        "denied": [
            {"operation": "source_overwrite", "status": "denied"},
            {"operation": "network_access", "status": "denied"},
        ],
        "count": 2,
    }
    assert invalid.is_error
    assert "made_up_operation" in invalid.content[0].text
    assert FORBIDDEN_OPERATIONS == (
        "source_overwrite",
        "ordinary_file_create",
        "file_delete",
        "file_rename",
        "chmod",
        "symlink_create",
        "git_add",
        "git_config",
        "git_commit",
        "git_branch",
        "git_remote",
        "target_execution",
        "credential_read",
        "network_access",
        "repository_policy_load",
    )
    assert [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()] == [
        {"operation": "source_overwrite", "status": "denied"},
        {"operation": "network_access", "status": "denied"},
    ]
