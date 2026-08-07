"""Small, test-only MCP broker for the explorer capability probe.

The broker deliberately exposes no generic file, process, resource, import, or
network operation.  Its two readers are fixed to named files directly below the
configured repository root, and use descriptor-relative, no-follow opens.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import stat
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

_MAX_AUDIT_BYTES = 64 * 1024
_MAX_FILE_BYTES = 64 * 1024
_MAX_INPUT_BYTES = 256
_MAX_MATCHES = 32
_MAX_OPERATION_COUNT = 16
_MAX_RESULT_LINE_BYTES = 256
_MAX_FUNCTION_NAMES = 64


class ForbiddenOperation(StrEnum):
    """Operations this broker can attest as denied without attempting them."""

    SOURCE_OVERWRITE = "source_overwrite"
    ORDINARY_FILE_CREATE = "ordinary_file_create"
    FILE_DELETE = "file_delete"
    FILE_RENAME = "file_rename"
    CHMOD = "chmod"
    SYMLINK_CREATE = "symlink_create"
    GIT_ADD = "git_add"
    GIT_CONFIG = "git_config"
    GIT_COMMIT = "git_commit"
    GIT_BRANCH = "git_branch"
    GIT_REMOTE = "git_remote"
    TARGET_EXECUTION = "target_execution"
    CREDENTIAL_READ = "credential_read"
    NETWORK_ACCESS = "network_access"
    REPOSITORY_POLICY_LOAD = "repository_policy_load"


FORBIDDEN_OPERATIONS: tuple[str, ...] = tuple(operation.value for operation in ForbiddenOperation)


def _bounded_utf8(value: str, *, limit: int, field: str) -> None:
    if not value:
        raise ValueError(f"{field} must not be empty")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"{field} exceeds {limit} UTF-8 bytes")


def _truncate_utf8(value: str, *, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    return raw[:limit].decode("utf-8", errors="ignore")


def _validated_root(repository_root: Path) -> Path:
    try:
        mode = repository_root.lstat().st_mode
    except FileNotFoundError as exc:
        raise ValueError("repository root does not exist") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ValueError("repository root must be a non-symlink directory")
    return repository_root.resolve(strict=True)


def _read_fixed_regular_file(repository_root: Path, filename: str) -> bytes:
    """Read one fixed direct child through a descriptor that refuses symlinks."""
    root_fd = os.open(repository_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        try:
            child_fd = os.open(filename, flags, dir_fd=root_fd)
        except FileNotFoundError as exc:
            raise ValueError(f"{filename} is not available") from exc
        except OSError as exc:
            raise ValueError(f"{filename} must be a readable non-symlink regular file") from exc
        try:
            metadata = os.fstat(child_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"{filename} must be a regular file")
            if metadata.st_size > _MAX_FILE_BYTES:
                raise ValueError(f"{filename} exceeds {_MAX_FILE_BYTES} bytes")
            content = os.read(child_fd, _MAX_FILE_BYTES + 1)
        finally:
            os.close(child_fd)
    finally:
        os.close(root_fd)
    if len(content) > _MAX_FILE_BYTES:
        raise ValueError(f"{filename} exceeds {_MAX_FILE_BYTES} bytes")
    return content


class _ProbeAudit:
    def __init__(self, audit_path: Path) -> None:
        self._audit_path = audit_path

    def append(self, operation: str, status: str) -> None:
        record = (
            json.dumps(
                {
                    "operation": operation,
                    "status": status,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(record) > 512:
            raise RuntimeError("probe audit record exceeded its fixed bound")
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        if self._audit_path.exists() and self._audit_path.is_symlink():
            raise ValueError("audit path must not be a symlink")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW
        try:
            audit_fd = os.open(self._audit_path, flags, 0o600)
        except OSError as exc:
            raise ValueError("audit path is not writable as a regular file") from exc
        try:
            metadata = os.fstat(audit_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("audit path must be a regular file")
            if metadata.st_size + len(record) > _MAX_AUDIT_BYTES:
                raise ValueError("probe audit exceeds its fixed byte bound")
            os.write(audit_fd, record)
        finally:
            os.close(audit_fd)


def build_probe_server(repository_root: Path, audit_jsonl_path: Path) -> FastMCP:
    """Build the standalone stdio server with its intentionally closed tool surface."""
    root = _validated_root(repository_root)
    audit = _ProbeAudit(audit_jsonl_path)
    server = FastMCP("explorer-probe-broker")

    @server.tool(name="bounded_literal_search")
    def bounded_literal_search(needle: str) -> dict[str, Any]:
        """Find bounded literal occurrences in the fixed ``readable.txt`` probe file."""
        try:
            _bounded_utf8(needle, limit=_MAX_INPUT_BYTES, field="needle")
            text = _read_fixed_regular_file(root, "readable.txt").decode("utf-8")
            matches: list[dict[str, Any]] = []
            total_matches = 0
            for line_number, line in enumerate(text.splitlines(), start=1):
                if needle in line:
                    total_matches += 1
                    if len(matches) < _MAX_MATCHES:
                        matches.append(
                            {
                                "line": line_number,
                                "text": _truncate_utf8(line, limit=_MAX_RESULT_LINE_BYTES),
                            }
                        )
            result = {
                "matches": matches,
                "total_matches": total_matches,
                "truncated": total_matches > len(matches),
            }
        except (UnicodeDecodeError, ValueError):
            audit.append("bounded_literal_search", "denied")
            raise
        audit.append("bounded_literal_search", "allowed")
        return result

    @server.tool(name="parse_python_ast")
    def parse_python_ast() -> dict[str, Any]:
        """Parse fixed ``semantic.py`` source with stdlib AST without importing it."""
        try:
            source = _read_fixed_regular_file(root, "semantic.py").decode("utf-8")
            tree = ast.parse(source, filename="semantic.py", mode="exec")
            names: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if len(node.name.encode("utf-8")) > _MAX_INPUT_BYTES:
                        raise ValueError("function name exceeds the fixed byte bound")
                    names.append(node.name)
                    if len(names) == _MAX_FUNCTION_NAMES:
                        break
            result = {"function_names": names, "truncated": len(names) == _MAX_FUNCTION_NAMES}
        except (SyntaxError, UnicodeDecodeError, ValueError):
            audit.append("parse_python_ast", "denied")
            raise
        audit.append("parse_python_ast", "allowed")
        return result

    @server.tool(name="optional_capability_status")
    def optional_capability_status() -> dict[str, str]:
        """Report intentionally unavailable optional analyzers without probing the host."""
        result = {
            "lsp": "LSP_UNSUPPORTED",
            "tree_sitter": "TREE_SITTER_UNSUPPORTED",
        }
        audit.append("optional_capability_status", "allowed")
        return result

    @server.tool(name="deny_operations")
    def deny_operations(operations: list[ForbiddenOperation]) -> dict[str, Any]:
        """Record closed-set operation requests as denied, without attempting any action."""
        if len(operations) > _MAX_OPERATION_COUNT:
            audit.append("deny_operations", "denied")
            raise ValueError(f"operations exceeds {_MAX_OPERATION_COUNT} entries")
        encoded_size = len(
            json.dumps(
                [operation.value for operation in operations], separators=(",", ":")
            ).encode("utf-8")
        )
        if encoded_size > _MAX_INPUT_BYTES:
            audit.append("deny_operations", "denied")
            raise ValueError(f"operations exceeds {_MAX_INPUT_BYTES} UTF-8 bytes")
        denied = []
        for operation in operations:
            audit.append(operation.value, "denied")
            denied.append({"operation": operation.value, "status": "denied"})
        return {"denied": denied, "count": len(denied)}

    return server


def main(argv: Sequence[str] | None = None) -> None:
    """Run the test-only broker over stdio."""
    parser = argparse.ArgumentParser(description="Run the explorer capability probe broker")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--audit-jsonl-path", type=Path, required=True)
    args = parser.parse_args(argv)
    build_probe_server(args.repository_root, args.audit_jsonl_path).run(transport="stdio")


if __name__ == "__main__":
    main()
