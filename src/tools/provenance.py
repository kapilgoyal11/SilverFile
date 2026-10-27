"""Provenance records: every dollar amount in the return carries one.

Amounts are whole-dollar integers. A record is a plain dict so it can be
saved as JSON (dashboard, LangGraph state, audit report).
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_amount(amount) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError(f"amount must be a whole-dollar int, got {amount!r}")


def from_document(line: str, amount: int, document: str, field: str) -> dict:
    """A number read directly off a tax document, e.g. W-2 box 1."""
    _check_amount(amount)
    return {
        "line": line,
        "amount": amount,
        "kind": "document",
        "document": document,
        "field": field,
        "created_at": _now(),
    }


def computed(line: str, amount: int, formula: str, inputs: dict, tool: str) -> dict:
    """A number produced by a deterministic calculation tool."""
    _check_amount(amount)
    return {
        "line": line,
        "amount": amount,
        "kind": "computed",
        "formula": formula,
        "inputs": inputs,
        "tool": tool,
        "created_at": _now(),
    }


def user_input(line: str, amount: int, entered_by: str, note: str = "") -> dict:
    """A number the user typed in directly (last-resort provenance)."""
    _check_amount(amount)
    return {
        "line": line,
        "amount": amount,
        "kind": "user_input",
        "entered_by": entered_by,
        "note": note,
        "created_at": _now(),
    }