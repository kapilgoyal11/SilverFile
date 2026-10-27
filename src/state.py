"""Shared state for SilverFile. Every agent reads this and returns only the
fields it changes. Amounts are whole-dollar ints; provenance records come
from src/tools/provenance.py.
"""
import operator
from typing import Annotated, Optional, TypedDict


def merge_dicts(left: Optional[dict], right: Optional[dict]) -> dict:
    """Reducer for dict fields. Keys from `right` overwrite the same keys in
    `left`. Parallel branches should write different keys, so order does not
    matter; a re-run (e.g. after Review sends work back) overwrites old values."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class SilverFileState(TypedDict, total=False):
    # --- session ---
    session_id: str

    # --- Intake (Karan) writes ---
    # Plain answers from the taxpayer, e.g. filing_status, birth_date,
    # is_blind, spouse_*, dependents, estimated_payments, document_inventory.
    intake: Annotated[dict, merge_dicts]

    # --- Document Extraction (Rodrigo) writes ---
    # One entry per document, appended from parallel branches:
    # {"doc_id": "w2_1", "doc_type": "W-2", "fields": {"box_1_wages": 50000,
    #   "box_2_withholding": 4000}, "source_file": "w2_image_1.jpg"}
    documents: Annotated[list, operator.add]

    # --- Eligibility & Rules (Narain) writes ---
    # {"eligible_1040sr": True, "age_box_count": 1, "blind_box_count": 0,
    #  "in_scope": True, "schedules": [...], "citations": [...]}
    eligibility: Annotated[dict, merge_dicts]

    # --- Computation (Kapil) writes ---
    # Line number -> provenance record, e.g. provenance["9"]["amount"].
    # The provenance record is the single source of truth for every amount.
    provenance: Annotated[dict, merge_dicts]

    # --- Review / Audit writes ---
    # Each finding: {"line": "6b", "problem": "...", "send_back_to": "computation"}
    review_findings: list
    review_iterations: int

    # --- Escalation (any agent appends) ---
    # {"raised_by": "eligibility", "reason": "out_of_scope:schedule_c",
    #  "detail": "...", "resolved": False}
    escalations: Annotated[list, operator.add]

    # --- Orchestrator (David) writes ---
    next_agent: str
    status: str  # in_progress | needs_human | draft_ready | failed
    handoff_log: Annotated[list, operator.add]  # for the loop guard