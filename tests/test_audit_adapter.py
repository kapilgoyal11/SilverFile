"""Real pipeline output -> Review/Audit agent, through the adapter."""
import json
from pathlib import Path

import pytest

from src.agents.computation import computation_agent
from src.agents.eligibility import eligibility_agent
from src.tools.audit_adapter import NODE_NAMES, run_review_audit, to_audit_state

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "eligibility"
CLEAN = ["clean_single.json", "clean_mfj.json"]


def real_state(name):
    state = json.loads((FIXTURES / name).read_text())
    up = eligibility_agent(state)
    state["eligibility"] = {**(state.get("eligibility") or {}), **up["eligibility"]}
    state["escalations"] = list(state.get("escalations") or []) + list(up["escalations"])
    state["provenance"] = computation_agent(state)["provenance"]
    return state


@pytest.mark.parametrize("name", CLEAN)
def test_every_tax_line_passes_the_independent_audit(name):
    audit = run_review_audit(real_state(name))["audit"]
    # The only open finding is the missing filer SSN: Intake does not ask for it yet.
    assert {f["code"] for f in audit["findings"]} <= {"INVALID_SSN"}, audit["findings"]


@pytest.mark.parametrize("name", CLEAN)
def test_approved_once_the_filer_ssns_are_present(name):
    state = real_state(name)
    state["answers"] = {"taxpayer_ssn": {"value": "900-11-1111"},
                        "spouse_ssn": {"value": "900-22-2222"}}
    audit = run_review_audit(state)["audit"]
    assert audit["findings"] == [], audit["findings"]
    assert audit["verdict"] in ("approve", "approved")


def test_adapter_does_not_change_the_original_state():
    state = real_state("clean_single.json")
    before = json.dumps(state, sort_keys=True, default=str)
    to_audit_state(state)
    assert json.dumps(state, sort_keys=True, default=str) == before


def test_route_names_match_the_team_graph():
    assert set(NODE_NAMES.values()) >= {"intake_agent", "extraction_agent",
                                        "eligibility_agent", "computation_agent"}