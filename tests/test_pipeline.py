"""Pipeline check as automatic tests: Eligibility -> Computation -> Audit.
If a teammate renames a field these go red."""
import copy
import json
from pathlib import Path

import pytest

from src.agents.computation import computation_agent
from src.agents.eligibility import eligibility_agent
from src.agents.eligibility_schemas import open_escalations
from src.tools.audit_checks import audit_return

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "eligibility"
CLEAN = ["clean_single.json", "clean_mfj.json"]
HITL = sorted(p.name for p in FIXTURES.glob("hitl_*.json"))


def run_eligibility(name):
    state = json.loads((FIXTURES / name).read_text())
    update = eligibility_agent(state)
    state = dict(state)
    state["eligibility"] = {**(state.get("eligibility") or {}), **update["eligibility"]}
    state["escalations"] = list(state.get("escalations") or []) + list(update["escalations"])
    return state


@pytest.mark.parametrize("name", CLEAN)
def test_clean_returns_flow_through_and_audit_passes(name):
    state = run_eligibility(name)
    assert not open_escalations(state["escalations"])
    result = computation_agent(state)
    assert "escalations" not in result, result.get("escalations")
    assert audit_return(result["provenance"]) == []


@pytest.mark.parametrize("name", HITL)
def test_flagged_returns_stop_before_computation(name):
    state = run_eligibility(name)
    assert open_escalations(state["escalations"])


def test_duplicate_w2_is_not_counted_twice():
    state = run_eligibility("clean_single.json")
    base = computation_agent(state)["provenance"]["1z"]["amount"]
    w2 = next(d for d in state["documents"] if d["doc_type"] == "W-2")
    state["documents"] = list(state["documents"]) + [copy.deepcopy(w2)]
    assert computation_agent(state)["provenance"]["1z"]["amount"] == base