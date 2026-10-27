"""Security controls owned by the Security specialization.

* SSN rule from spec 3.2: only test SSNs in the 900-999 series are allowed, so a
  real SSN can never enter the system by accident.
* PII masking for audit records, traces and logs.
* Prompt-injection screening of free text that came from documents (the W-2 is
  read by an LLM from an image, so its text is untrusted input).
* A fake-data validator for the demo fixtures.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SSN_FORMAT = re.compile(r"^\d{3}-\d{2}-\d{4}$")
# Any 9-digit SSN-shaped token inside free text (with or without dashes).
SSN_IN_TEXT = re.compile(r"(?<!\d)(\d{3})-?(\d{2})-?(\d{4})(?!\d)")
EIN_FORMAT = re.compile(r"^\d{2}-\d{7}$")

# Dictionary keys whose values are always treated as sensitive.
SENSITIVE_KEYS = {
    "ssn", "employee_ssn", "spouse_ssn", "recipient_tin", "tin",
    "routing_number", "account_number", "ip_pin", "identity_protection_pin",
}


def ssn_is_valid_test_ssn(ssn: str | None) -> tuple[bool, str]:
    """Return (ok, reason). Only 9XX-XX-XXXX test numbers are accepted."""
    if not ssn:
        return False, "SSN is missing"
    if not SSN_FORMAT.match(ssn):
        return False, "SSN is not in the format XXX-XX-XXXX"
    area = int(ssn[:3])
    if not 900 <= area <= 999:
        return False, "SSN is outside the 900-999 test series and could be a real number"
    return True, "ok"


def mask_ssn(ssn: str) -> str:
    digits = re.sub(r"\D", "", ssn)
    return f"XXX-XX-{digits[-4:]}" if len(digits) >= 4 else "XXX-XX-XXXX"


def mask_number(value: str) -> str:
    digits = re.sub(r"\D", "", str(value))
    return f"****{digits[-4:]}" if len(digits) > 4 else "****"


def redact_text(text: str) -> str:
    """Mask every SSN-shaped number that appears inside free text."""
    return SSN_IN_TEXT.sub(lambda m: f"XXX-XX-{m.group(3)}", text)


def mask_pii(obj: Any) -> Any:
    """Deep copy of obj with sensitive values masked. Safe for logs and traces."""
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS and value not in (None, ""):
                out[key] = mask_ssn(str(value)) if "ssn" in key.lower() or key.lower() == "tin" else mask_number(value)
            else:
                out[key] = mask_pii(value)
        return out
    if isinstance(obj, list):
        return [mask_pii(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(mask_pii(v) for v in obj)
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


class PIIRedactingFilter(logging.Filter):
    """Attach to any logger/handler so SSNs never reach log files or traces.

    >>> logging.getLogger("silverfile").addFilter(PIIRedactingFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            record.args = tuple(mask_pii(a) for a in (record.args if isinstance(record.args, tuple) else (record.args,)))
        return True


# --- Prompt-injection screening -------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"disregard (the |all )?(rules|instructions|system prompt)",
    r"you are now",
    r"system prompt",
    r"act as (an? )?(admin|administrator|developer)",
    r"(approve|pass) (this|the) (return|audit)",
    r"do not (escalate|flag|report)",
    r"skip (the )?(review|audit|human)",
    r"send (the )?refund to",
    r"</?(system|assistant|user)>",
]
INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def find_injection(text: str) -> str | None:
    """Return the suspicious phrase if text looks like an instruction to the model."""
    match = INJECTION_RE.search(text or "")
    return match.group(0) if match else None


def iter_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield (path, string) for every string inside a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


# --- Fixture validation (Security supports Guardrails & Ethics here) -----------

def validate_fixture_data(data: Any, source: str = "<data>") -> list[str]:
    """Return a list of problems. Empty list = fixture only contains fake SSNs."""
    problems: list[str] = []
    for path, text in iter_strings(data):
        for m in SSN_IN_TEXT.finditer(text):
            if not 900 <= int(m.group(1)) <= 999:
                problems.append(
                    f"{source}:{path}: SSN-shaped value {mask_ssn(m.group(0))} is not in the 900-999 test series"
                )
    return problems


def validate_fixture_dir(directory: str | Path) -> list[str]:
    """Scan every .json file under a fixtures directory."""
    problems: list[str] = []
    for file in sorted(Path(directory).rglob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{file}: not valid JSON ({exc})")
            continue
        problems.extend(validate_fixture_data(data, str(file)))
    return problems
