"""
Orchestrator tests (David). No API key needed: Intake is bypassed by handing
the graph a completed intake, the W-2 is read from fixture answer keys, and
Eligibility, Computation and Review are deterministic.

Covers brief section 4.1: conditional routing (decision table), the parallel
extraction branch with the hub as fan-in, the bounded review->revise cycle,
the loop guard with a case that triggers it, plus section 4.4 interrupt and
resume, and section 4.3 resumability.
"""

import json
from pathlib import Path

import pytest
from langgraph.types import Command

from src.agents.extraction import NODE as EXTRACTION, make_extraction_agent
from src.agents.orchestrator import (
    COMPUTATION, ELIGIBILITY, GATE, HUMAN, INTAKE, LOOP_WINDOW, MAX_REVIEW_ROUNDS, REVIEW,
    decide, guard_reason, is_pathological_cycle,
)
from src.graph import build_graph
from src.memory import thread_config
from src.tools.w2_reader import FixtureReader

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"
SSN = {"slot": "taxpayer_ssn", "value": "900-12-3456"}
SPOUSE_SSN = {"slot": "spouse_ssn", "value": "900-98-7654"}


def load(name: str, **extra) -> dict:
    """An eligibility fixture as the state a finished Intake would leave behind."""
    state = json.loads((FIXTURES / "eligibility" / name).read_text())
    state.update({"session_id": name, "intake_complete": True,
                  "answers": {"taxpayer_ssn": SSN, "spouse_ssn": SPOUSE_SSN}})
    state.update(extra)
    return state


def hops(result: dict) -> list[str]:
    return [e["to"] for e in result["handoff_log"] if e.get("from") == "orchestrator"]


def run(graph, state, thread):
    return graph.invoke(state, thread_config(thread))


def resume(graph, decision, thread):
    return graph.invoke(Command(resume=decision), thread_config(thread))


# =========================================================== decision table

IN_SCOPE = {"in_scope": True, "eligible_1040sr": True}
PROV = {"1z": {"amount": 1, "source": "W-2"}}
OPEN = [{"id": "x", "raised_by": "eligibility", "reason": "out_of_scope:R6", "detail": "", "resolved": False}]
RESOLVED = [{**OPEN[0], "resolved": True}]
REVISE = {"verdict": "revise", "send_back_to": ["computation"]}

ROWS = [
    ("fresh session goes to intake",
     {}, INTAKE),
    ("open escalation beats everything",
     {"intake_complete": False, "escalations": OPEN}, HUMAN),
    ("resolved escalation does not block",
     {"intake_complete": False, "escalations": RESOLVED}, INTAKE),
    ("eligibility re-ask item goes to a human (intake has no re-ask path yet)",
     {"intake_complete": True, "escalations": [{**OPEN[0], "action": "reask_user"}]}, HUMAN),
    ("unhandled document files fan out to extraction",
     {"intake_complete": True, "document_files": [{"doc_id": "w2_1", "doc_type": "W-2", "path": "x"}]}, "extract"),
    ("handled document files are not extracted again",
     {"intake_complete": True, "document_files": [{"doc_id": "w2_1", "doc_type": "W-2", "path": "x"}],
      "documents": [{"doc_id": "w2_1"}]}, ELIGIBILITY),
    ("no eligibility yet goes to rules",
     {"intake_complete": True}, ELIGIBILITY),
    ("human review asked for an eligibility re-check",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "recheck_eligibility": True}, ELIGIBILITY),
    ("no provenance goes to computation",
     {"intake_complete": True, "eligibility": IN_SCOPE}, COMPUTATION),
    ("provenance but no audit goes to review",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV}, REVIEW),
    ("approve goes to the release gate",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "audit": {"verdict": "approve"}}, GATE),
    ("revise is dispatched to the owner once per round",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "audit": REVISE,
      "review_round": 1, "dispatched_round": 0}, COMPUTATION),
    ("after the owner ran, revise goes back to review",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "audit": REVISE,
      "review_round": 1, "dispatched_round": 1}, REVIEW),
    ("revise routed to intake goes to intake",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV,
      "audit": {"verdict": "revise", "send_back_to": ["intake"]}, "review_round": 1}, INTAKE),
    ("escalate verdict, everything resolved, under the bound: audit again",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "audit": {"verdict": "escalate"},
      "review_round": 1}, REVIEW),
    ("escalate verdict at the bound with a human sign-off goes to the gate",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "audit": {"verdict": "escalate"},
      "review_round": MAX_REVIEW_ROUNDS}, GATE),
    ("draft ready ends",
     {"status": "draft_ready"}, "end"),
    ("human stopped ends",
     {"status": "failed"}, "end"),
    ("pathological cycle escalates",
     {"intake_complete": True, "eligibility": IN_SCOPE,
      "handoff_log": [{"from": "orchestrator", "to": n} for n in [ELIGIBILITY, COMPUTATION] * 3]}, HUMAN),
    ("review cycle is not a pathological cycle",
     {"intake_complete": True, "eligibility": IN_SCOPE, "provenance": PROV, "audit": {"verdict": "approve"},
      "handoff_log": [{"from": "orchestrator", "to": n} for n in [COMPUTATION, REVIEW] * 3]}, GATE),
]


@pytest.mark.parametrize("label,state,expected", ROWS, ids=[r[0] for r in ROWS])
def test_decision_table(label, state, expected):
    assert decide(state) == expected


def test_guard_reasons():
    assert guard_reason({}) == ("", "")
    log = [{"from": "orchestrator", "to": n} for n in [ELIGIBILITY, COMPUTATION] * 3]
    assert guard_reason({"handoff_log": log})[0] == "loop_guard"
    varied = [INTAKE, ELIGIBILITY, COMPUTATION, REVIEW, GATE, HUMAN, INTAKE, COMPUTATION, ELIGIBILITY, REVIEW]
    log = [{"from": "orchestrator", "to": n} for n in varied * 6]          # 60 hops, no short cycle
    assert guard_reason({"handoff_log": log})[0] == "max_hops"
    assert guard_reason({"audit": {"verdict": "revise", "send_back_to": ["document_extraction"]}})[0] == "reextract_requested"


def test_is_pathological_cycle_patterns():
    assert is_pathological_cycle(["a", "b"] * 3)
    assert is_pathological_cycle(["a", "b", "c"] * 2)
    assert is_pathological_cycle(["a"] * LOOP_WINDOW)
    assert not is_pathological_cycle(["a", "b"] * 2)                        # too short
    assert not is_pathological_cycle([COMPUTATION, REVIEW] * 3)              # review cycle has its own bound
    assert not is_pathological_cycle([INTAKE, EXTRACTION, ELIGIBILITY, COMPUTATION, REVIEW, GATE])


# =========================================================== clean runs (eval case 1 shape)

@pytest.mark.parametrize("fixture", ["clean_single.json", "clean_mfj.json"])
def test_clean_run_reaches_draft_ready(fixture):
    result = run(build_graph(), load(fixture), fixture)
    assert result["status"] == "draft_ready"
    assert "__interrupt__" not in result
    assert hops(result) == [ELIGIBILITY, COMPUTATION, REVIEW, GATE]
    assert result["audit"]["verdict"] == "approve"
    assert result["release_gate"]["passed"] is True
    assert result["provenance"]["9"]["amount"] == 18500 if fixture == "clean_single.json" else True
    assert all(r.get("amount") is not None for r in result["provenance"].values())


# =========================================================== parallel extraction

def test_extraction_fans_out_and_the_hub_is_the_fan_in():
    state = load("clean_single.json", documents=[])
    state["intake"]["document_inventory"] = {"W-2": 1, "SSA-1099": 1, "1099-R": 0, "1099-INT": 1, "1099-DIV": 0}
    state["document_files"] = [
        {"doc_id": "w2_1", "doc_type": "W-2", "path": str(FIXTURES / "w2_1.png")},
        {"doc_id": "int_1", "doc_type": "1099-INT", "path": str(FIXTURES / "int_1.json"), "media_type": "application/json"},
        {"doc_id": "ssa_1", "doc_type": "SSA-1099", "path": str(FIXTURES / "ssa_1.json"), "media_type": "application/json"},
    ]
    graph = build_graph(agents={EXTRACTION: make_extraction_agent(FixtureReader())})
    result = run(graph, state, "fanout")

    assert {d["doc_id"] for d in result["documents"]} == {"w2_1", "int_1", "ssa_1"}
    assert hops(result).count("extract") == 1                     # one hub hop, three Send branches
    assert hops(result)[0] == "extract" and hops(result)[1] == ELIGIBILITY   # fan-in, then rules
    assert result["status"] == "draft_ready"
    assert result["provenance"]["2b"]["amount"] == 312             # the 1099-INT reached computation
    assert result["provenance"]["6a"]["amount"] == 21600           # and so did the SSA-1099


# =========================================================== review -> revise cycle

def test_review_revise_cycle_is_dispatched_then_reaudited():
    calls = {"n": 0}
    from src.agents.orchestrator import review_agent

    def flaky_review(state):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"audit": {"verdict": "revise", "send_back_to": ["computation"], "findings": [], "round": 1},
                    "audit_history": [{"round": 1}], "review_round": 1, "escalations": []}
        return review_agent(state)

    result = run(build_graph(agents={REVIEW: flaky_review}), load("clean_single.json"), "cycle")
    assert hops(result) == [ELIGIBILITY, COMPUTATION, REVIEW, COMPUTATION, REVIEW, GATE]
    assert result["status"] == "draft_ready"
    assert result["dispatched_round"] == 1


def test_review_that_never_approves_ends_with_a_human_not_a_spin():
    def always_revise(state):
        n = state.get("review_round", 0) + 1
        return {"audit": {"verdict": "revise", "send_back_to": ["computation"], "findings": [], "round": n},
                "audit_history": [{"round": n}], "review_round": n, "escalations": []}

    graph = build_graph(agents={REVIEW: always_revise})
    result = run(graph, load("clean_single.json"), "spin")
    # Review itself normally stops at MAX_REVIEW_ROUNDS; this stub never does, so the
    # orchestrator's own guards have to. The run must pause for a human, not run forever.
    assert result["status"] == "needs_human"
    reason = result["__interrupt__"][0].value["escalations"][0]["reason"]
    assert reason in ("loop_guard", "max_hops")


# =========================================================== loop guard (the required test case)

def test_loop_guard_breaks_a_pathological_handoff_cycle():
    def computation_that_never_writes(state):
        return {}                                               # misbehaving agent: no provenance, no escalation

    graph = build_graph(agents={COMPUTATION: computation_that_never_writes})
    result = run(graph, load("clean_single.json"), "loop")

    assert result["status"] == "needs_human"
    assert hops(result)[-1] == HUMAN
    assert hops(result)[-LOOP_WINDOW - 1:-1] == [COMPUTATION] * LOOP_WINDOW
    esc = result["__interrupt__"][0].value["escalations"][0]
    assert esc["raised_by"] == "orchestrator" and esc["reason"] == "loop_guard"

    result = resume(graph, "stop: sending this return to a volunteer preparer", "loop")
    assert result["status"] == "failed"
    assert "__interrupt__" not in result
    assert result["escalations"][-1]["resolved"] is True


# =========================================================== HITL (eval case 2 shape)

def test_advice_request_pauses_then_resumes_to_draft():
    graph = build_graph()
    result = run(graph, load("hitl_advice.json"), "advice")
    assert result["status"] == "needs_human"
    pending = result["__interrupt__"][0].value["escalations"]
    assert pending[0]["reason"].startswith("advice_request")

    result = resume(graph, {"reviewer": "kushim-staff-1", "resolve_all": True,
                            "note": "Question noted for the volunteer preparer. Continue."}, "advice")
    assert result["status"] == "draft_ready"
    assert hops(result) == [ELIGIBILITY, HUMAN, ELIGIBILITY, COMPUTATION, REVIEW, GATE]
    assert all(e["resolved"] for e in result["escalations"])
    assert result["escalations"][0]["resolved_by"] == "kushim-staff-1"


def test_spouse_died_resolved_with_an_intake_patch():
    graph = build_graph()
    result = run(graph, load("hitl_spouse_died.json"), "spouse")
    assert result["__interrupt__"][0].value["escalations"][0]["reason"].startswith("filing_status_ambiguous")
    result = resume(graph, {"reviewer": "kushim-staff-1", "resolve_all": True,
                            "note": "Volunteer confirmed MFJ for the year of death.",
                            "intake_patch": {"filing_status": "mfj"}}, "spouse")
    assert result["intake"]["filing_status"] == "mfj"
    assert result["status"] == "draft_ready"


def test_1099r_box2b_escalates_regardless_and_a_human_can_stop():
    graph = build_graph()
    result = run(graph, load("hitl_1099r_box2b.json"), "box2b")
    assert result["__interrupt__"][0].value["escalations"][0]["reason"].startswith("undetermined_taxable_1099r")
    # A human waves it through; computation still refuses (brief 3.3: escalates regardless).
    result = resume(graph, "proceed: checking", "box2b")
    assert result["status"] == "needs_human"
    assert result["__interrupt__"][0].value["escalations"][0]["raised_by"] == "computation"
    result = resume(graph, "stop: volunteer preparer will finish this return", "box2b")
    assert result["status"] == "failed"


def test_release_gate_blocks_then_passes_once_a_human_accepts():
    def bad_computation(state):
        return {"provenance": {"1z": {"amount": 18500}}}        # amount with no source: fails R11.1

    def approve(state):
        return {"audit": {"verdict": "approve", "findings": [], "round": 1}, "review_round": 1, "escalations": []}

    graph = build_graph(agents={COMPUTATION: bad_computation, REVIEW: approve})
    result = run(graph, load("clean_single.json"), "gate")
    assert result["status"] == "needs_human"
    assert result["release_gate"]["passed"] is False
    assert result["__interrupt__"][0].value["escalations"][0]["raised_by"] == GATE

    result = resume(graph, "proceed: volunteer accepts the draft as is", "gate")
    assert result["status"] == "draft_ready"
    assert result["release_gate"]["accepted_by_human"] is True


# =========================================================== resumability (brief 4.3)

def test_session_resumes_from_checkpoint_by_thread_id():
    graph = build_graph()
    run(graph, load("hitl_advice.json"), "friday")
    snapshot = graph.get_state(thread_config("friday"))
    assert snapshot.next == (HUMAN,)
    assert snapshot.values["status"] == "needs_human"
    assert snapshot.interrupts[0].value["type"] == "human_review"
