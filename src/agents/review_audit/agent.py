"""Review / Audit agent: the LangGraph node and its routing helpers.

Usage in the team's graph.py (owned by the Orchestrator):

    from src.agents.review_audit import review_audit_node, route_after_audit

    graph.add_node("review_audit", review_audit_node)
    graph.add_conditional_edges("review_audit", route_after_audit, {
        "computation": "computation",
        "document_extraction": "document_extraction",
        "eligibility_rules": "eligibility_rules",
        "intake": "intake",
        "human": "human_review",      # node that calls langgraph interrupt()
        "approve": "generate_draft",
    })

The node is deterministic (no LLM call, no API key) so the same return always
gets the same verdict, which is what you want from an auditor.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checks import ReturnAuditor
from .schema import AuditRecord, Finding, Route, Severity, Verdict
from .security import PIIRedactingFilter, mask_pii

AUDITOR_VERSION = "review-audit/1.0 (TY2025)"
MAX_REVIEW_ROUNDS = 3  # explicit, tested bound on the review -> revise cycle

# Order in which send-backs are handled when several agents must fix something:
# upstream first, so a fix there is not overwritten by a later recomputation.
ROUTE_PRIORITY = [Route.INTAKE, Route.EXTRACTION, Route.ELIGIBILITY, Route.COMPUTATION]

log = logging.getLogger("silverfile.review_audit")
log.addFilter(PIIRedactingFilter())


def return_fingerprint(state: dict[str, Any]) -> str:
    """SHA-256 over everything that determines the return.

    Stored with the approval so the draft generator can refuse to print a return
    that changed after it was audited (see approval_still_valid).
    """
    payload = {k: state.get(k) for k in (
        "filing_status", "taxpayer", "spouse", "dependents", "line_12d",
        "documents", "return_lines", "intake_flags",
    )}
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def approval_still_valid(state: dict[str, Any]) -> bool:
    audit = state.get("audit") or {}
    return audit.get("verdict") == Verdict.APPROVE and audit.get("return_fingerprint") == return_fingerprint(state)


def _errors_fingerprint(findings: list[Finding]) -> str:
    keys = sorted(f"{f.key}|{f.expected}|{f.actual}" for f in findings if f.severity is Severity.ERROR)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def _resolved_keys(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["finding_key"]: r for r in state.get("hitl_resolutions") or [] if r.get("finding_key")}


def audit_return(state: dict[str, Any], *, max_rounds: int = MAX_REVIEW_ROUNDS) -> AuditRecord:
    """Run all checks and decide the verdict. Pure function of `state`."""
    previous = state.get("audit_history") or []
    round_no = int(state.get("review_round") or 0) + 1

    auditor = ReturnAuditor(state)
    findings = auditor.run()

    # Findings a human already decided on (via the dashboard) no longer block.
    resolutions = _resolved_keys(state)
    resolved = [f for f in findings if f.key in resolutions]
    open_findings = [f for f in findings if f.key not in resolutions]
    errors = [f for f in open_findings if f.severity is Severity.ERROR]

    notes: list[str] = []
    loop_guard: str | None = None
    if any(f.route_to is Route.HUMAN for f in errors):
        verdict = Verdict.ESCALATE
    elif errors:
        verdict = Verdict.REVISE
        fp = _errors_fingerprint(errors)
        if previous and previous[-1].get("errors_fingerprint") == fp:
            # Same errors came back unchanged: the agents are cycling without progress.
            loop_guard = "NO_PROGRESS"
            notes.append("The same errors came back after a revision, so the loop was stopped and sent to a human.")
        elif round_no >= max_rounds:
            loop_guard = "MAX_ROUNDS"
            notes.append(f"Review limit of {max_rounds} rounds reached with errors still open; sent to a human.")
        if loop_guard:
            verdict = Verdict.ESCALATE
            errors.append(Finding("REVIEW_LOOP_STOPPED", Severity.ERROR, Route.HUMAN,
                                  notes[-1], subject=loop_guard, escalation_condition="loop_guard"))
            open_findings.append(errors[-1])
    else:
        verdict = Verdict.APPROVE

    send_back = [] if verdict is not Verdict.REVISE else [
        str(r) for r in ROUTE_PRIORITY if any(f.route_to is r for f in errors)
    ]
    return AuditRecord(
        case_id=str(state.get("case_id", "unknown")),
        round=round_no,
        verdict=verdict,
        send_back_to=send_back,
        findings=[mask_pii(f.to_dict()) for f in open_findings],
        resolved_by_human=[
            mask_pii({**f.to_dict(), "resolution": resolutions[f.key]}) for f in resolved
        ],
        line_status=auditor.line_status,
        checks_run=auditor.checks_run,
        return_fingerprint=return_fingerprint(state),
        audited_at=datetime.now(UTC).isoformat(timespec="seconds"),
        auditor_version=AUDITOR_VERSION,
        loop_guard=loop_guard,
        notes=notes,
    )


def review_audit_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node. Reads the assembled return, writes only audit keys."""
    record = audit_return(state)
    errors = [f for f in record.findings if f["severity"] == "error"]
    log.info("case %s round %s verdict=%s errors=%s warnings=%s",
             record.case_id, record.round, record.verdict, len(errors),
             len(record.findings) - len(errors))
    escalations = [
        {
            "source": "review_audit",
            "finding_key": f["key"],
            "condition": f.get("escalation_condition"),
            "message": f["message"],
            "raised_at": record.audited_at,
        }
        for f in errors if f["route_to"] == Route.HUMAN
    ]
    return {
        "audit": record.to_dict(),
        "audit_history": [{
            "round": record.round,
            "verdict": str(record.verdict),
            "send_back_to": record.send_back_to,
            "errors_fingerprint": _errors_fingerprint([
                Finding(f["code"], Severity(f["severity"]), Route(f["route_to"]), f["message"],
                        f.get("line"), f.get("expected"), f.get("actual"), subject=f.get("subject"))
                for f in errors if f["code"] != "REVIEW_LOOP_STOPPED"
            ]),
            "error_count": len(errors),
            "audited_at": record.audited_at,
        }],
        "escalations": escalations,
        "review_round": record.round,
        "needs_human_review": record.verdict is Verdict.ESCALATE,
    }


def route_after_audit(state: dict[str, Any]) -> str:
    """Suggested conditional edge for the Orchestrator.

    Returns 'approve', 'human', or the first agent that must revise its work.
    """
    audit = state.get("audit") or {}
    verdict = audit.get("verdict")
    if verdict == Verdict.APPROVE:
        return "approve"
    if verdict == Verdict.REVISE and audit.get("send_back_to"):
        return audit["send_back_to"][0]
    return "human"


def detect_handoff_cycle(handoff_log: list[str], *, window: int = 6) -> bool:
    """Loop-guard helper for the Orchestrator: True if the last `window` handoffs
    just bounce between the same two agents (A, B, A, B, ...) with nothing else."""
    tail = handoff_log[-window:]
    if len(tail) < window:
        return False
    return len(set(tail)) == 2 and all(tail[i] != tail[i + 1] for i in range(len(tail) - 1))


def write_audit_record(record: dict[str, Any], traces_dir: str | Path = "traces") -> Path:
    """Save the (already masked) audit record as JSON for the trace folder / dashboard."""
    out = Path(traces_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"audit_{record['case_id']}_round{record['round']}.json"
    path.write_text(json.dumps(mask_pii(record), indent=2, default=str), encoding="utf-8")
    return path
