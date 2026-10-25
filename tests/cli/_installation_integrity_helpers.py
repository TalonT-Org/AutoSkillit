"""Shared RECORD fixtures for installation-integrity tests."""

from __future__ import annotations

import base64
import hashlib

from autoskillit.cli.doctor._doctor_types import DoctorResult
from autoskillit.core import Severity

CLEAN = DoctorResult(
    Severity.OK,
    "installation_integrity",
    "All discoverable AutoSkillit installations match their RECORD files",
)


def record_hash(content: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
    return f"sha256={encoded.decode()}"
