from typing import Any

from .types._type_protocols_backend import CodingAgentBackend

def collect_version_snapshot(
    backend: CodingAgentBackend | None = None,
) -> dict[str, Any]: ...
