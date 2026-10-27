"""Identifier checks for extracted documents.

The project may only ever hold test SSNs whose first three digits are 900-999
(the IRS never issues these). Anything else is treated as possibly real: it is
not stored, not logged and not echoed back in an error message.
"""

from __future__ import annotations

import re
from typing import Literal

_SSN_RE = re.compile(r"^\s*(\d{3})-?(\d{2})-?(\d{4})\s*$")
_EIN_RE = re.compile(r"^\s*(\d{2})-?(\d{7})\s*$")

SsnCheck = Literal["ok", "not_test_range", "malformed", "blank"]


def check_ssn(raw: str | None) -> tuple[SsnCheck, str | None]:
    """Return (result, normalized SSN or None). Only test-range SSNs are returned."""
    if raw is None or not raw.strip():
        return "blank", None
    m = _SSN_RE.match(raw)
    if not m:
        return "malformed", None
    area, group, serial = m.groups()
    if not 900 <= int(area) <= 999:
        return "not_test_range", None
    return "ok", f"{area}-{group}-{serial}"


def normalize_ein(raw: str | None) -> str | None:
    if raw is None:
        return None
    m = _EIN_RE.match(raw)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def redact_ssns(text: str) -> str:
    """Mask anything SSN-shaped that is not a test SSN. Use before logging model output."""

    def _mask(m: re.Match) -> str:
        return m.group(0) if 900 <= int(m.group(1)) <= 999 else "***-**-****"

    return re.sub(r"\b(\d{3})-(\d{2})-(\d{4})\b", _mask, text)
