"""A tiny LangGraph so the Eligibility agent can run end to end BEFORE the team
graph exists. It uses the team's state conventions (src/state.py):

    START → eligibility_agent → (route on state) → human_review ⟲ eligibility_agent
                                      ├→ back_to_intake  (re-ask a question)
                                      ├→ ready           (on to Computation)
                                      └→ supervisor      (loop guard tripped)

* human_review owns the LangGraph interrupt().
* Resolving = sending a copy of the escalation with the same "id" and
  "resolved": True; Karan's merge_escalations reducer replaces it in place.
* Loop guard counts human_review entries in handoff_log.

When combining: keep eligibility_agent; give this routing / human_review
pattern to the orchestrator owner (see docs/ELIGIBILITY_HANDOFF.md).
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .agents.eligibility import eligibility_agent
from .agents.eligibility_schemas import escalation_identity, open_escalations, resolve

MAX_HUMAN_REVIEW_ROUNDS = 3     # loop guard (tested)


def merge_dicts(left: Optional[dict], right: Optional[dict]) -> dict:   # same as team state.py
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def merge_escalations(left: Optional[list], right: Optional[list]) -> list:  # same as team state.py
    """Append, except an entry whose "id" matches an existing one replaces it."""
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


class State(TypedDict, total=False):
    """Subset of the team's SilverFileState (+ transcript)."""
    intake: Annotated[dict, merge_dicts]
    documents: Annotated[list, operator.add]
    transcript: Annotated[list, operator.add]
    eligibility: Annotated[dict, merge_dicts]
    escalations: Annotated[list, merge_escalations]
    handoff_log: Annotated[list, operator.add]
    next_agent: str
    status: str


def human_rounds(state: State) -> int:
    return sum(1 for h in state.get("handoff_log") or [] if h.get("from") == "human_review")


def reask_items(state: State) -> list[dict]:
    """Questions Intake must ask again, from the LATEST eligibility run."""
    current = (state.get("eligibility") or {}).get("escalations") or []
    return [e for e in current if e.get("action") == "reask_user"]


def route_after_eligibility(state: State) -> str:
    if human_rounds(state) >= MAX_HUMAN_REVIEW_ROUNDS:
        return "supervisor"
    if open_escalations(state.get("escalations") or []):
        return "human_review"
    if reask_items(state):
        return "back_to_intake"
    return "ready"


def human_review(state: State) -> dict[str, Any]:
    pending = open_escalations(state.get("escalations") or [])
    decision = interrupt({
        "message": "Human review needed before SilverFile can continue.",
        "escalations": pending,
    })
    # Expected resume value (what the dashboard sends):
    # {"reviewer": "kushim-staff-1",
    #  "resolve": [{"reason": "...", "source": "...", "note": "..."}],   # or "resolve_all": True
    #  "intake_patch": {...}}                                            # optional corrections
    reviewer = decision.get("reviewer", "unknown")
    if decision.get("resolve_all"):
        chosen = [(e, decision.get("note", "")) for e in pending]
    else:
        wanted = {(r.get("reason"), r.get("source")): r.get("note", "")
                  for r in decision.get("resolve", [])}
        chosen = [(e, wanted[(e.get("reason"), e.get("source"))]) for e in pending
                  if (e.get("reason"), e.get("source")) in wanted]
    update: dict[str, Any] = {
        "escalations": [resolve(e, reviewer, note) for e, note in chosen],
        "handoff_log": [{"from": "human_review", "to": "eligibility_agent",
                         "resolved": [list(escalation_identity(e)) for e, _ in chosen]}],
    }
    if decision.get("intake_patch"):
        update["intake"] = decision["intake_patch"]        # merge_dicts reducer merges it
    return update


def back_to_intake(state: State) -> dict[str, Any]:
    return {"status": "in_progress", "next_agent": "intake_agent"}


def ready(state: State) -> dict[str, Any]:
    return {"status": "in_progress", "next_agent": "computation_agent"}


def supervisor(state: State) -> dict[str, Any]:
    return {"status": "needs_human"}


def build_graph(checkpointer: Optional[Any] = None):
    g = StateGraph(State)
    g.add_node("eligibility_agent", eligibility_agent)
    g.add_node("human_review", human_review)
    g.add_node("back_to_intake", back_to_intake)
    g.add_node("ready", ready)
    g.add_node("supervisor", supervisor)
    g.add_edge(START, "eligibility_agent")
    g.add_conditional_edges("eligibility_agent", route_after_eligibility,
                            ["human_review", "back_to_intake", "ready", "supervisor"])
    g.add_edge("human_review", "eligibility_agent")   # re-check after a human decision
    for n in ("back_to_intake", "ready", "supervisor"):
        g.add_edge(n, END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
