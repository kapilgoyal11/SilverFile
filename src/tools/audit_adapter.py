"""Translate the team's shared state into the shape the Review/Audit agent reads.

The Review/Audit contract (docs/review_audit_contract.md) uses its own key names
(return_lines, type, box1, taxpayer, line_12d, ...). The rest of the team uses
state["provenance"], doc_type, box_1_wages, state["intake"] and so on. This file
is the one place that knows both. It never changes the state it is given.

    from src.tools.audit_adapter import run_review_audit
    result = run_review_audit(state)      # {"audit": {...}}
"""
from __future__ import annotations

from typing import Any

# Review/Audit route names -> the node names used in the team graph.
NODE_NAMES = {
    "intake": "intake_agent",
    "document_extraction": "extraction_agent",
    "eligibility_rules": "eligibility_agent",
    "computation": "computation_agent",
    "human": "human_review",
}

# Team field name -> Review/Audit box name, per document type.
BOX_MAP = {
    "W-2": {"box_1_wages": "box1", "box_2_withholding": "box2",
            "box_3_ss_wages": "box3"},
    "SSA-1099": {"box_5_net_benefits": "box5", "box_6_withheld": "box6"},
    "1099-INT": {"box_1_interest": "box1", "box_8_tax_exempt_interest": "box8",
                 "box_4_withholding": "box4"},
    "1099-DIV": {"box_1a_ordinary_dividends": "box1a",
                 "box_1b_qualified_dividends": "box1b",
                 "box_4_withholding": "box4"},
    "1099-R": {"box_1_gross": "box1", "box_2a_taxable": "box2a",
               "box_4_withholding": "box4",
               "box_2b_taxable_not_determined": "box2b_not_determined",
               "is_ira": "ira_sep_simple"},
}


def _adapt_documents(docs: list[dict] | None) -> list[dict]:
    out = []
    for d in docs or []:
        dtype = d.get("doc_type") or d.get("type")
        fields = dict(d.get("fields") or {})
        for ours, theirs in BOX_MAP.get(dtype, {}).items():
            if ours in fields and theirs not in fields:
                fields[theirs] = fields[ours]
        out.append({**d, "type": dtype, "fields": fields})
    return out


def _input_refs(inputs: Any, known_lines: set[str]) -> list[str]:
    """Our 'inputs' is a dict. Review/Audit wants ["line:6a", ...]."""
    refs = []
    for key in (inputs or {}):
        name = str(key).removeprefix("line:").removeprefix("line_")
        if name in known_lines:
            refs.append(f"line:{name}")
    return refs


def _adapt_lines(provenance: dict | None) -> dict[str, dict]:
    provenance = provenance or {}
    known = set(provenance)
    lines: dict[str, dict] = {}
    for line, rec in provenance.items():
        kind = rec.get("kind")
        if kind == "document":
            prov = {"kind": "document", "doc_id": rec.get("document"),
                    "field": rec.get("field")}
        elif kind == "computed":
            prov = {"kind": "computed", "tool": rec.get("tool"),
                    "formula": rec.get("formula", ""),
                    "inputs": _input_refs(rec.get("inputs"), known)}
        elif kind == "user_input":
            prov = {"kind": "user_input", "entered_by": rec.get("entered_by"),
                    "entered_at": rec.get("created_at")}
        else:
            prov = {"kind": kind}
        lines[line] = {"value": rec["amount"], "provenance": prov}
    # Two lines the form defines as copies. SilverFile has no income on 1b-1h,
    # so 1a equals 1z, and 11b is by definition the amount from 11a.
    if "1z" in lines and "1a" not in lines:
        lines["1a"] = dict(lines["1z"])
    if "11a" in lines and "11b" not in lines:
        lines["11b"] = dict(lines["11a"])
    return lines


def _split_name(name: str | None) -> tuple[str, str]:
    first, _, last = (name or "").strip().partition(" ")
    return first, last


def _taxpayer(intake: dict) -> dict:
    first, last = _split_name(intake.get("taxpayer_name"))
    return {
        "first_name": first, "last_name": last, "ssn": None,
        "dob": intake.get("birth_date"),
        "blind": bool(intake.get("is_blind")),
        "can_be_claimed_as_dependent": bool(intake.get("claimable_as_dependent")),
        # Assumption: not a full-year resident = dual-status / nonresident.
        "dual_status_alien": intake.get("full_year_resident") is False,
    }


def _spouse(intake: dict) -> dict | None:
    if (intake.get("filing_status") or "") not in ("mfj", "mfs"):
        return None
    first, last = _split_name(intake.get("spouse_name"))
    return {
        "first_name": first, "last_name": last, "ssn": None,
        "dob": intake.get("spouse_birth_date"),
        "blind": bool(intake.get("spouse_is_blind")),
        "can_be_claimed_as_dependent": False,
        "itemizes_separately": bool(intake.get("spouse_itemizes")),
    }


def to_audit_state(state: dict) -> dict:
    """Return a copy of the team state in the Review/Audit agent's shape."""
    intake = state.get("intake") or {}
    elig = state.get("eligibility") or {}
    out = dict(state)
    out.update({
        "case_id": state.get("session_id") or "unknown",
        "filing_status": intake.get("filing_status"),
        "taxpayer": _taxpayer(intake),
        "spouse": _spouse(intake),
        "dependents": list(intake.get("dependents") or []),
        "line_12d": {
            "taxpayer_65": bool(elig.get("senior_taxpayer")),
            "taxpayer_blind": bool(intake.get("is_blind")),
            "spouse_65": bool(elig.get("senior_spouse")),
            "spouse_blind": bool(intake.get("spouse_is_blind")),
        },
        "documents": _adapt_documents(state.get("documents")),
        "return_lines": _adapt_lines(state.get("provenance")),
        "intake_flags": state.get("intake_flags") or {},
        "mfs_lived_apart_all_year": bool(intake.get("lived_apart_all_year")),
    })
    return out


def _answer_value(state: dict, key: str):
    return ((state.get("answers") or {}).get(key) or {}).get("value")


def with_ssns(audit_state: dict, state: dict) -> dict:
    """Fill the filer SSNs from Intake's answers, if Intake collected them.

    Intake currently asks only for dependents' SSNs (dependent_N_ssn). Once it
    also asks 'taxpayer_ssn' / 'spouse_ssn', they are picked up here.
    """
    out = dict(audit_state)
    tp = dict(out.get("taxpayer") or {})
    tp["ssn"] = _answer_value(state, "taxpayer_ssn")
    out["taxpayer"] = tp
    if out.get("spouse"):
        sp = dict(out["spouse"])
        sp["ssn"] = _answer_value(state, "spouse_ssn")
        out["spouse"] = sp
    return out


def run_review_audit(state: dict) -> dict:
    from src.agents.review_audit import review_audit_node
    return review_audit_node(with_ssns(to_audit_state(state), state))