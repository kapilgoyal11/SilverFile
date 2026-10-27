"""
Orchestrator agent for SilverFile.

Owner: David (Task Division & Management).

The orchestrator is a pure function of state: it reads the shared
SilverFileState, picks the next node, and records the hop. It makes no LLM
call and, per the brief, never extracts, computes, or talks to the taxpayer.

What lives here
---------------
  decide(state)        the decision table (tests/test_orchestrator.py checks every row)
  guard_reason(state)  loop guards: pathological handoff cycle, too many hops, unanswered re-ask
  orchestrator(state)  the hub node
  route(state)         the conditional edge out of the hub (fans out Sends for extraction)
  review_agent(state)  wraps Review/Audit so its escalations use the team's resolvable shape
  human_review(state)  the ONE interrupt() point; resolves escalations the way the dashboard does
  release_gate(state)  Narain's release gate; marks the draft ready or sends it back to a human

Task division
-------------
Work is handed between agents through two shared lists:
  escalations   anything that needs a human (any agent appends; human_review resolves)
  audit         Review's verdict and send_back_to; the orchestrator dispatches the
                send-back to its owner once per review round (dispatched_round)
"""

from __future__ import annotations

from langgraph.graph import END
from langgraph.types import interrupt

from src.agents.eligibility_guardrails import release_gate_from_state
from src.agents.eligibility_schemas import open_escalations, resolve
from src.agents.extraction import handled_doc_ids, make_document_router
from src.memory import now_iso
from src.tools.audit_adapter import run_review_audit

# ------------------------------------------------------------- node names --

HUB = "orchestrator"
INTAKE = "intake_agent"
ELIGIBILITY = "eligibility_agent"
COMPUTATION = "computation_agent"
REVIEW = "review_agent"
HUMAN = "human_review"
GATE = "release_gate"
EXTRACT = "extract"          # pseudo-target: route() turns it into one Send per file

# Review's route_to values (docs/review_audit_contract.md) -> node names.
# Extraction cannot be re-run automatically (handled files are skipped), so a
# request to re-extract goes to a human who can re-upload the document.
SEND_BACK = {
    "intake": INTAKE,
    "eligibility_rules": ELIGIBILITY,
    "computation": COMPUTATION,
    "document_extraction": HUMAN,
    "human": HUMAN,
}

# ----------------------------------------------------------------- limits --

MAX_REVIEW_ROUNDS = 3     # matches Review's own bound; after this a human decides
MAX_HOPS = 60             # hub hops per session before a human is asked
LOOP_WINDOW = 6           # hops the cycle detector inspects

CODE_LOOP_GUARD = "loop_guard"
CODE_MAX_HOPS = "max_hops"
CODE_REEXTRACT = "reextract_requested"


# ---------------------------------------------------------------- helpers --

def hub_hops(state: dict) -> list[str]:
    """The sequence of nodes the orchestrator has routed to, plus "orchestrator"
    entries written by human_review (those break a repeating pattern)."""
    return [e["to"] for e in state.get("handoff_log") or []
            if isinstance(e, dict) and e.get("from") in (HUB, HUMAN) and e.get("to")]


def open_human(state: dict) -> list[dict]:
    return open_escalations(state.get("escalations") or [], "human_review")


def open_reask(state: dict) -> list[dict]:
    """Eligibility's 'ask the taxpayer again' items. Intake has no re-ask path yet,
    so a volunteer answers these through human_review with an intake_patch."""
    return open_escalations(state.get("escalations") or [], "reask_user")


def pending_for_human(state: dict) -> list[dict]:
    return open_human(state) + open_reask(state)


def unhandled_files(state: dict) -> list[dict]:
    handled = handled_doc_ids(state)
    return [f for f in state.get("document_files") or [] if f.get("doc_id") not in handled]


def is_pathological_cycle(hops: list[str]) -> bool:
    """True when the last LOOP_WINDOW hops repeat with period 1, 2 or 3.

    Triggers:      [a,a,a,a,a,a]  [a,b,a,b,a,b]  [a,b,c,a,b,c]
    Does not:      any pattern containing review_agent, because the
                   review -> revise cycle is legitimate and Review bounds it itself.
    """
    recent = hops[-LOOP_WINDOW:]
    if len(recent) < LOOP_WINDOW:
        return False
    for period in (1, 2, 3):
        pattern = recent[:period]
        if REVIEW in pattern:
            continue
        if len(set(pattern)) == period and recent == pattern * (LOOP_WINDOW // period):
            return True
    return False


def _overridden(state: dict, code: str) -> bool:
    """A human already resolved an orchestrator escalation with this code."""
    return any(e.get("raised_by") == HUB and str(e.get("reason", "")).startswith(code) and e.get("resolved")
               for e in state.get("escalations") or [])


def guard_reason(state: dict) -> tuple[str, str]:
    """(code, detail) for an escalation the orchestrator itself raises, or ("", "")."""
    hops = hub_hops(state)

    if is_pathological_cycle(hops) and not _overridden(state, CODE_LOOP_GUARD):
        return CODE_LOOP_GUARD, f"Agents are cycling without progress: {hops[-LOOP_WINDOW:]}"

    if len(hops) >= MAX_HOPS and not _overridden(state, CODE_MAX_HOPS):
        return CODE_MAX_HOPS, f"{len(hops)} handoffs without finishing the return."

    audit = state.get("audit") or {}
    if audit.get("verdict") == "revise" and SEND_BACK.get((audit.get("send_back_to") or [""])[0]) == HUMAN \
            and not _overridden(state, CODE_REEXTRACT):
        return CODE_REEXTRACT, "Review asked for a document to be re-extracted; a person must re-upload it."

    return "", ""


def _escalation(code: str, detail: str, state: dict) -> dict:
    return {
        "id": f"{HUB}:{code}:{len(hub_hops(state))}",
        "raised_by": HUB, "reason": code, "detail": detail,
        "resolved": False, "blocking": True, "raised_at": now_iso(),
    }


# ---------------------------------------------------------- decision table --

def decide(state: dict) -> str:
    """State in, next node out. Rows are checked top to bottom; first match wins."""

    if state.get("status") in ("draft_ready", "failed"):
        return "end"

    if pending_for_human(state) or guard_reason(state)[0]:
        return HUMAN                                      # anything needing a person goes first

    if not state.get("intake_complete"):
        return INTAKE

    if unhandled_files(state):
        return EXTRACT                                    # route() fans this out, one Send per file

    if not state.get("eligibility") or state.get("recheck_eligibility"):
        return ELIGIBILITY

    if not state.get("provenance"):
        return COMPUTATION

    audit = state.get("audit") or {}
    verdict = audit.get("verdict")
    if not audit:
        return REVIEW

    if verdict == "approve":
        return GATE

    if verdict == "revise":
        if state.get("dispatched_round", 0) < state.get("review_round", 0):
            return SEND_BACK[audit["send_back_to"][0]]    # hand the finding to its owner
        return REVIEW                                     # owner has run; audit again

    # verdict == "escalate" and every escalation is resolved (row 2 would have caught open ones)
    if state.get("review_round", 0) >= MAX_REVIEW_ROUNDS:
        return GATE                                       # a human has had the final say; gate decides
    return REVIEW


# ------------------------------------------------------------------ nodes --

def orchestrator(state: dict) -> dict:
    nxt = decide(state)
    update = {"next_agent": nxt, "handoff_log": [{"from": HUB, "to": nxt, "at": now_iso()}]}

    if nxt == HUMAN:
        update["status"] = "needs_human"
        code, detail = guard_reason(state)
        if code:
            update["escalations"] = [_escalation(code, detail, state)]
    elif nxt != "end":
        update["status"] = "in_progress"

    if nxt == ELIGIBILITY:
        update["recheck_eligibility"] = False
    if (state.get("audit") or {}).get("verdict") == "revise" and nxt in SEND_BACK.values():
        update["dispatched_round"] = state.get("review_round", 0)
    return update


def route(state: dict):
    """Conditional edge out of the hub."""
    nxt = state["next_agent"]
    if nxt == "end":
        return END
    if nxt == EXTRACT:
        return make_document_router(HUB)(state)          # list of Send, one per unhandled file
    return nxt


def review_agent(state: dict) -> dict:
    """Review/Audit through the team-state adapter, with its escalations put in
    the shared shape (id, raised_by, reason, detail, resolved) so human_review
    can resolve them in place."""
    out = run_review_audit(state)
    out["escalations"] = [
        {"id": f"review:{e.get('finding_key')}", "raised_by": "review",
         "reason": e.get("finding_key"), "detail": e.get("message"), "resolved": False, **e}
        for e in out.get("escalations") or []
    ]
    out["handoff_log"] = [{"from": REVIEW, "to": HUB, "round": out.get("review_round"),
                           "verdict": (out.get("audit") or {}).get("verdict"), "at": now_iso()}]
    return out


def human_review(state: dict) -> dict:
    """The one human-in-the-loop point. Pauses until a Kushim employee resumes.

    Resume with graph.invoke(Command(resume=decision), config) where decision is
    either a string ("proceed <note>" or "stop <note>") or the dashboard dict from
    docs/ELIGIBILITY_HANDOFF.md section 5:
      {"reviewer": "...", "resolve_all": True, "note": "...", "intake_patch": {...}}
      {"reviewer": "...", "resolve": [{"reason": ..., "source": ..., "note": ...}]}
      {"action": "stop", "note": "..."}
    """
    pending = pending_for_human(state)
    if not pending:
        return {}

    decision = interrupt({
        "type": "human_review",
        "message": "A Kushim employee needs to review this session before it continues.",
        "escalations": pending,
        "how_to_resume": "Resume with 'proceed <note>', 'stop <note>', or the dashboard dict.",
    })

    if isinstance(decision, dict):
        reviewer = decision.get("reviewer", "kushim-staff")
        note = decision.get("note", "")
        stop = decision.get("action") == "stop"
        if decision.get("resolve_all") or stop or not decision.get("resolve"):
            chosen = [(e, note) for e in pending]
        else:
            wanted = {(r.get("reason"), r.get("source")): r.get("note", "") for r in decision["resolve"]}
            chosen = [(e, wanted[(e.get("reason"), e.get("source"))]) for e in pending
                      if (e.get("reason"), e.get("source")) in wanted]
        intake_patch = decision.get("intake_patch") or {}
    else:
        text = str(decision).strip()
        reviewer, note = "kushim-staff", text
        stop = text.lower().startswith("stop")
        chosen = [(e, note) for e in pending]
        intake_patch = {}

    resolved_at = now_iso()
    update = {
        "escalations": [resolve(e, reviewer, n) for e, n in chosen],
        "hitl_resolutions": [{"finding_key": e["finding_key"], "decision": "accept",
                              "resolved_by": reviewer, "resolved_at": resolved_at}
                             for e, _ in chosen if e.get("finding_key")],
        "handoff_log": [{"from": HUMAN, "to": HUB, "resolved": [e.get("id") or e.get("reason") for e, _ in chosen],
                         "action": "stop" if stop else "proceed", "at": resolved_at}],
        "status": "failed" if stop else "in_progress",
    }
    if intake_patch:
        update["intake"] = intake_patch
    if not stop and state.get("eligibility"):
        update["recheck_eligibility"] = True              # Narain: re-check after every human review
    return update


def release_gate(state: dict) -> dict:
    """Last step before the draft is handed to a person (docs/ELIGIBILITY_HANDOFF.md section 6)."""
    gate = release_gate_from_state(state)
    # A human who resolved an earlier gate escalation has accepted the violations.
    accepted = any(e.get("raised_by") == GATE and e.get("resolved") for e in state.get("escalations") or [])
    passed = gate.passed or accepted
    update = {
        "release_gate": {"passed": passed, "violations": list(gate.violations), "accepted_by_human": accepted and not gate.passed},
        "status": "draft_ready" if passed else "needs_human",
        "handoff_log": [{"from": GATE, "to": "end" if passed else HUB, "passed": passed,
                         "violations": list(gate.violations), "at": now_iso()}],
    }
    if not passed:
        update["escalations"] = [{
            "id": f"{GATE}:{len(hub_hops(state))}", "raised_by": GATE, "reason": "release_gate",
            "detail": "; ".join(gate.violations), "resolved": False, "blocking": True, "raised_at": now_iso(),
        }]
    return update
