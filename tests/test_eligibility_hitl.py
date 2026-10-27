"""End-to-end through the mini LangGraph: interrupt, resume, loop guard."""
import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langgraph.types import Command  # noqa: E402

from src.eligibility_standalone_graph import MAX_HUMAN_REVIEW_ROUNDS, build_graph, human_rounds  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "eligibility"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_clean_run_has_no_interrupt():
    g = build_graph()
    out = g.invoke(load("clean_mfj.json"), {"configurable": {"thread_id": "clean"}})
    assert "__interrupt__" not in out
    assert out["next_agent"] == "computation_agent"


def test_interrupt_then_resume_one_item():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "spouse"}}
    out = g.invoke(load("hitl_spouse_died.json"), cfg)
    assert "__interrupt__" in out
    pending = out["__interrupt__"][0].value["escalations"]
    assert pending[0]["reason"] == "filing_status_ambiguous:R2.3"

    out = g.invoke(Command(resume={
        "reviewer": "tester",
        "resolve": [{"reason": pending[0]["reason"], "source": pending[0]["source"],
                     "note": "Volunteer confirmed MFJ for year of death."}]}), cfg)
    assert "__interrupt__" not in out
    assert out["next_agent"] == "computation_agent"
    resolved = [e for e in out["escalations"] if e.get("resolved")]
    assert resolved and resolved[0]["resolved_by"] == "tester"


def test_resolve_all():
    g = build_graph()
    cfg = {"configurable": {"thread_id": "exploit"}}
    out = g.invoke(load("hitl_exploitation.json"), cfg)
    assert len(out["__interrupt__"][0].value["escalations"]) == 1
    out = g.invoke(Command(resume={"reviewer": "t", "resolve_all": True}), cfg)
    assert "__interrupt__" not in out
    # merge_escalations replaced the entry in place (same id), it did not add a copy
    assert len(out["escalations"]) == 1 and out["escalations"][0]["resolved"] is True


def test_loop_guard_breaks_pathological_cycle():
    """A reviewer who never resolves anything must not loop forever."""
    g = build_graph()
    cfg = {"configurable": {"thread_id": "stuck"}}
    out = g.invoke(load("hitl_advice.json"), cfg)
    rounds = 0
    while "__interrupt__" in out:
        rounds += 1
        assert rounds <= MAX_HUMAN_REVIEW_ROUNDS
        out = g.invoke(Command(resume={"reviewer": "x", "resolve": []}), cfg)
    assert out["status"] == "needs_human"
    assert human_rounds(out) == MAX_HUMAN_REVIEW_ROUNDS
