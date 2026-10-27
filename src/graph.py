"""
The SilverFile graph. Owner: David (Orchestrator).

Topology: hub and spoke. Every agent returns to the orchestrator, which reads
the shared state and picks the next hop (src/agents/orchestrator.py). Two
exceptions: Intake loops on itself one question at a time until it is done,
and Extraction fans out as one Send per document; the hub is the fan-in.

    python -m src.graph            # prints the Mermaid diagram (docs/graph.mmd)

Build with build_graph(checkpointer=...). Every real session needs a
checkpointer and a thread_id so interrupt() can pause and resume:

    from src.graph import build_graph
    from src.memory import get_checkpointer, thread_config
    graph = build_graph(get_checkpointer())              # SQLite, resumable later
    graph.invoke(initial_state, thread_config(session_id))
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agents.computation import computation_agent
from src.agents.eligibility import eligibility_agent
from src.agents.extraction import NODE as EXTRACTION, extraction_agent
from src.agents.intake import intake_node, route_after_intake
from src.agents.orchestrator import (
    COMPUTATION, ELIGIBILITY, GATE, HUB, HUMAN, INTAKE, REVIEW,
    human_review, orchestrator, release_gate, review_agent, route,
)
from src.state import SilverFileState

# Each teammate's node. Tests pass overrides to swap one in or out.
AGENTS = {
    INTAKE: intake_node,            # Karan      (Agent memory)
    EXTRACTION: extraction_agent,   # Rodrigo    (Inter-agent communication)
    ELIGIBILITY: eligibility_agent, # Narain     (Guardrails and ethics)
    COMPUTATION: computation_agent, # Kapil      (Computation)
    REVIEW: review_agent,           # Review/Audit (Security), via the team-state adapter
}

ROUTES = [INTAKE, EXTRACTION, ELIGIBILITY, COMPUTATION, REVIEW, HUMAN, GATE, END]


def build_graph(checkpointer=None, agents: dict | None = None):
    agents = {**AGENTS, **(agents or {})}

    g = StateGraph(SilverFileState)
    g.add_node(HUB, orchestrator)
    g.add_node(HUMAN, human_review)
    g.add_node(GATE, release_gate)
    for name, fn in agents.items():
        g.add_node(name, fn)

    g.add_edge(START, HUB)
    g.add_conditional_edges(HUB, route, ROUTES)

    # Intake asks one question per pass and loops on itself; anything else goes to the hub.
    g.add_conditional_edges(INTAKE, route_after_intake, {"intake": INTAKE, "human_review": HUB, "done": HUB})

    for name in (EXTRACTION, ELIGIBILITY, COMPUTATION, REVIEW, HUMAN):
        g.add_edge(name, HUB)

    # The gate either ends the run with a draft or sends it back for a human.
    g.add_conditional_edges(GATE, lambda s: END if s.get("status") == "draft_ready" else HUB, [END, HUB])

    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver
        checkpointer = InMemorySaver()
    return g.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    print(build_graph().get_graph().draw_mermaid())
