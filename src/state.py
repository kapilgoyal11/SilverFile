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


def merge_answers(left: Optional[dict], right: Optional[dict]) -> dict:
    """Reducer for Intake's answer ledger (Karan). Merges slot by slot, and when
    a slot is answered again the earlier version is moved into that answer's
    `history` instead of being overwritten. Nothing the taxpayer said is lost."""
    merged = dict(left or {})
    for slot, new in (right or {}).items():
        old = merged.get(slot)
        if old is None or (old.get("entered_at") == new.get("entered_at")
                           and old.get("value") == new.get("value")):
            merged[slot] = new
            continue
        history = list(new.get("history") or [])
        old_version = {k: old.get(k) for k in
                       ("value", "raw_text", "entered_by", "entered_at", "turn", "status")}
        if old_version not in history:
            for h in old.get("history") or []:
                if h not in history:
                    history.append(h)
            history.append(old_version)
            history.sort(key=lambda v: (v.get("turn", 0), v.get("entered_at", "")))
        merged[slot] = {**new, "history": history}
    return merged


def merge_escalations(left: Optional[list], right: Optional[list]) -> list:
    """Reducer for escalations. Behaves like operator.add (append), with one
    addition: an entry that has an "id" matching an existing entry replaces it.
    That is how a human marks an escalation resolved. Entries without an "id"
    are always appended, so agents that don't use ids are unaffected."""
    out = list(left or [])
    index = {e["id"]: i for i, e in enumerate(out) if isinstance(e, dict) and "id" in e}
    for e in right or []:
        if isinstance(e, dict) and "id" in e and e["id"] in index:
            out[index[e["id"]]] = e
        else:
            if isinstance(e, dict) and "id" in e:
                index[e["id"]] = len(out)
            out.append(e)
    return out


class SilverFileState(TypedDict, total=False):
    # --- session ---
    session_id: str

    # --- Intake (Karan) writes ---
    # Plain answers from the taxpayer, written once intake is complete. Key list
    # and meanings: docs/INTAKE_INTERFACE.md. Amounts are whole-dollar ints;
    # filing_status uses the shared strings (single, mfj, mfs, hoh, qss).
    intake: Annotated[dict, merge_dicts]
    # Answer ledger: one record per question with value, the taxpayer's own
    # (SSN-redacted) words, who entered it, when, and earlier versions.
    answers: Annotated[dict, merge_answers]
    transcript: Annotated[list, operator.add]       # SSN-redacted conversation
    intake_progress: dict                           # which question we're on, etc.
    intake_complete: bool
    volunteer_notes: Annotated[list, operator.add]  # advice questions noted for the human preparer
    token_log: Annotated[list, operator.add]        # input tokens per model call (docs/metrics.md)

    # --- Document Extraction (Rodrigo) writes ---
    # One entry per document, appended from parallel branches:
    # {"doc_id": "w2_1", "doc_type": "W-2", "fields": {"box_1_wages": 50000,
    #   "box_2_withholding": 4000}, "source_file": "w2_image_1.jpg"}
    documents: Annotated[list, operator.add]
    # Input to Extraction: the files the taxpayer supplied, one per document,
    # {"doc_id": "w2_1", "doc_type": "W-2", "path": "demo/w2_image_1.png"}.
    # Written by run.py / the dashboard. See docs/EXTRACTION.md.
    document_files: list

    # --- Eligibility & Rules (Narain) writes ---
    # {"eligible_1040sr": True, "age_box_count": 1, "blind_box_count": 0,
    #  "in_scope": True, "schedules": [...], "citations": [...]}
    eligibility: Annotated[dict, merge_dicts]

    # --- Computation (Kapil) writes ---
    # Line number -> provenance record, e.g. provenance["9"]["amount"].
    # The provenance record is the single source of truth for every amount.
    provenance: Annotated[dict, merge_dicts]

    # --- Review / Audit writes (via src/tools/audit_adapter.run_review_audit) ---
    # LangGraph silently drops any key a node returns that is not declared here,
    # so these must be listed or the audit record never reaches the state.
    # Shape: docs/review_audit_contract.md section 4.
    audit: dict                                      # latest audit record: verdict, findings, send_back_to, ...
    audit_history: Annotated[list, operator.add]     # one entry per review round
    review_round: int                                # rounds run so far (Review bounds this at MAX_REVIEW_ROUNDS)
    needs_human_review: bool
    # Dashboard / human_review appends {finding_key, decision, resolved_by, resolved_at};
    # Review treats a finding with a resolution as resolved_by_human and stops blocking on it.
    hitl_resolutions: Annotated[list, operator.add]
    intake_flags: Annotated[dict, merge_dicts]       # optional flags Review reads (contract section 3)
    # Kept for compatibility with earlier drafts; the audit record above is the source of truth.
    review_findings: list
    review_iterations: int

    # --- Escalation (any agent appends) ---
    # {"raised_by": "eligibility", "reason": "out_of_scope:schedule_c",
    #  "detail": "...", "resolved": False}
    # Optional extra keys: "id" (lets a human resolve it in place), "blocking"
    # (True = stop the conversation now), "slot" (which intake question).
    escalations: Annotated[list, merge_escalations]

    # --- Orchestrator (David) writes --- see src/agents/orchestrator.py
    next_agent: str
    status: str  # in_progress | needs_human | draft_ready | failed
    # Every hop, appended by whoever makes it. The orchestrator's own entries are
    # {"from": "orchestrator", "to": "<node>", "at": "<iso time>"}; the loop guard
    # reads only those. Other agents may append entries of their own shape.
    handoff_log: Annotated[list, operator.add]
    dispatched_round: int        # last review round whose send-back has been dispatched to its owner
    recheck_eligibility: bool    # set by human_review; cleared when eligibility re-runs
    release_gate: dict           # {"passed": bool, "violations": [...]} from the final gate