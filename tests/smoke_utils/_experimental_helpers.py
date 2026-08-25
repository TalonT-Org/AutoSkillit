"""Shared builders for experimental-review tests."""

_EXPERIMENTAL_BOUNDARIES = (
    "reflection_decorators",
    "dependency_injection",
    "plugin_registry",
    "cli_entrypoint",
    "serialization",
    "generated_code",
    "public_api",
)


def _experimental_candidate(dimension: str, *, file: str = "src/app.py", line: int = 42) -> dict:
    return {
        "file": file,
        "line": line,
        "dimension": dimension,
        "severity": "warning",
        "message": "The abstraction has no reachable consumer",
        "requires_decision": False,
        "evidence": [
            {"path": file, "line": line, "role": "anchor", "claim": "Declaration"},
            {"path": file, "line": line + 1, "role": "consumer", "claim": "Only consumer"},
        ],
        "trace": [{"path": file, "line": line + 1, "relation": "calls"}],
        "boundary_checks": [
            {
                "boundary": boundary,
                "status": "checked_no_reachable_path",
                "claim": f"{boundary} has no reachable path",
            }
            for boundary in _EXPERIMENTAL_BOUNDARIES
        ],
        "confidence": 0.9,
        "simpler_behavior": (
            "Equivalent return values, exceptions, ordering, persistence, "
            "concurrency, and compatibility"
        ),
    }
