"""
A small graph that runs Intake on its own, with save/resume.

This is for building, testing and demoing the Intake + Memory part before the
full graph exists. In the team graph (src/graph.py, owned by David), reuse:

    from src.agents.intake import intake_node, route_after_intake
    builder.add_node("intake", intake_node)
    builder.add_conditional_edges("intake", route_after_intake,
        {"intake": "intake", "human_review": "human_review", "done": "orchestrator"})

`human_review_node` below is a PLACEHOLDER so the escalation path can be
demoed end to end. The real HITL node belongs to Guardrails / Orchestrator.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.agents.intake import intake_node, route_after_intake
from src.memory import get_checkpointer, now_iso
from src.state import SilverFileState


def human_review_node(state: dict) -> dict:
    """Pause for a Kushim volunteer to resolve any open blocking escalation."""
    open_items = [e for e in state.get("escalations") or [] if e.get("blocking") and not e.get("resolved")]
    if not open_items:
        return {}
    decision = interrupt({"type": "human_review", "escalations": open_items,
                          "text": "A volunteer needs to review this session before it continues."})
    note = decision.get("resolution", "") if isinstance(decision, dict) else str(decision)
    return {"escalations": [{**e, "resolved": True, "resolution": f"{note} (resolved {now_iso()})"}
                            for e in open_items if "id" in e]}


def build_intake_graph(checkpointer=None):
    b = StateGraph(SilverFileState)
    b.add_node("intake", intake_node)
    b.add_node("human_review", human_review_node)
    b.add_edge(START, "intake")
    b.add_conditional_edges("intake", route_after_intake,
                            {"intake": "intake", "human_review": "human_review", "done": END})
    b.add_edge("human_review", "intake")
    return b.compile(checkpointer=checkpointer or get_checkpointer(":memory:"))
