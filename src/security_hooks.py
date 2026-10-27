"""
SSN helpers used by Intake.

PLACEHOLDER: Security (Abdullah) owns the real SSN policy. Intake only calls
these three functions, so his module can replace this file as long as the
signatures stay the same.

Rule from the brief: only test SSNs in the 900-999 range are accepted, so a
person can never accidentally enter their real number.
"""
from __future__ import annotations

import re

# 9 digits, optionally separated as 3-2-4 by dashes or spaces
_SSN_RE = re.compile(r"(?<!\d)(\d{3})[- ]?(\d{2})[- ]?(\d{4})(?!\d)")
_SSN_DASHED_RE = re.compile(r"(?<!\d)(\d{3})[- ](\d{2})[- ](\d{4})(?!\d)")


def find_ssns(text: str, dashed_only: bool = False) -> list[str]:
    """Return every SSN-shaped number in the text, normalised to 123-45-6789."""
    rx = _SSN_DASHED_RE if dashed_only else _SSN_RE
    return [f"{a}-{b}-{c}" for a, b, c in rx.findall(text or "")]


def redact_ssns(text: str, dashed_only: bool = False) -> str:
    """Replace any SSN-shaped number with a marker. Used before anything is
    written to the transcript, logs, or sent to an LLM."""
    rx = _SSN_DASHED_RE if dashed_only else _SSN_RE
    return rx.sub("[SSN removed]", text or "")


def validate_test_ssn(ssn: str) -> tuple[bool, str]:
    """Accept only 900-999 test numbers. Returns (ok, reason)."""
    m = _SSN_RE.fullmatch((ssn or "").strip())
    if not m:
        return False, "format"
    area = int(m.group(1))
    if 900 <= area <= 999:
        return True, "ok"
    return False, "not_test_range"


def mask_ssn(ssn: str) -> str:
    """***-**-1234 for display in summaries."""
    digits = re.sub(r"\D", "", ssn or "")
    return f"***-**-{digits[-4:]}" if len(digits) == 9 else "***"
