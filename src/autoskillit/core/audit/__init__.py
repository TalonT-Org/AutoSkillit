"""IL-0 audit cycle verification, semantic codec, closure hashing, and closure verification.

Exposes the canonical public surface of ``audit_cycle_verifier``,
``audit_semantic_codec``, ``closure_hashing``, and ``closure_verifier``
through the ``autoskillit.core.audit`` namespace. Backward-compat shims at
``core/audit_cycle_verifier.py``, ``core/audit_semantic_codec.py``,
``core/closure_hashing.py``, and ``core/closure_verifier.py`` preserve old
import paths after the core/audit/ decomposition.

Lazy module loading via PEP 562 ``__getattr__`` is required because
``core.types._type_audit_admission`` imports ``core.audit.closure_hashing``
at module load time. Eagerly importing the cycle submodules here
(``closure_verifier`` -> ``core.io.io`` -> ``core.types._type_results``
-> ``_type_audit_admission``) would leave ``_type_audit_admission`` in a
partial-load state. The lazy facade breaks the cycle by deferring
submodule import until first attribute access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

_LAZY_SUBMODULES = (
    "audit_cycle_verifier",
    "audit_semantic_codec",
    "closure_hashing",
    "closure_verifier",
)

if TYPE_CHECKING:
    from autoskillit.core.audit.audit_cycle_verifier import (
        ArtifactByteReader,
        AuditCycleVerificationError,
        AuditCycleVerifier,
        InventoryAdmissionEvaluator,
        VerifiedAuditCycle,
    )
    from autoskillit.core.audit.audit_semantic_codec import (
        AuditSemanticCodecError,
        canonical_full_reference_records_match,
        load_audit_semantic_result,
        load_standalone_audit_evidence,
    )
    from autoskillit.core.audit.closure_hashing import (
        HASH_RE,
        canonical_json_bytes,
        compute_bytes_hash,
        compute_canonical_hash,
        compute_file_hash,
        compute_report_hash,
        compute_request_hash,
        compute_row_hash,
        parse_canonical_json_bytes,
    )
    from autoskillit.core.audit.closure_verifier import (
        VerificationResult,
        verify_closure_report,
    )


def __getattr__(name: str):
    """Lazy-load audit submodules and re-export their public symbols (PEP 562)."""
    for module_name in _LAZY_SUBMODULES:
        module = globals().get(module_name)
        if module is None:
            import importlib

            full_name = f"{__name__}.{module_name}"
            module = importlib.import_module(full_name)
            globals()[module_name] = module
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ArtifactByteReader",
    "AuditCycleVerificationError",
    "AuditCycleVerifier",
    "AuditSemanticCodecError",
    "HASH_RE",
    "InventoryAdmissionEvaluator",
    "VerificationResult",
    "VerifiedAuditCycle",
    "canonical_full_reference_records_match",
    "canonical_json_bytes",
    "compute_bytes_hash",
    "compute_canonical_hash",
    "compute_file_hash",
    "compute_report_hash",
    "compute_request_hash",
    "compute_row_hash",
    "load_audit_semantic_result",
    "load_standalone_audit_evidence",
    "parse_canonical_json_bytes",
    "verify_closure_report",
]


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_LAZY_SUBMODULES))
