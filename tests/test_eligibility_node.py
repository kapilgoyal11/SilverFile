"""Node-level tests: the contract the team graph relies on (src/state.py)."""
import json
from pathlib import Path

import pytest

from src.agents.eligibility import eligibility_agent
from src.agents.eligibility_policy import load_rules

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "eligibility"
ALL_FIXTURES = sorted(p.name for p in FIXTURES.glob("*.json"))


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_citation_exists_in_policy(name):
    out = eligibility_agent(load(name))
    valid = set(load_rules())
    assert set(out["eligibility"]["citations"]) <= valid
    assert {e["rule_id"] for e in out["escalations"]} <= valid


@pytest.mark.parametrize("name, expected_codes", [
    ("clean_mfj.json", set()),
    ("clean_single.json", set()),
    ("hitl_1099r_box2b.json", {"UNDETERMINED_TAXABLE_1099R"}),
    ("hitl_spouse_died.json", {"FILING_STATUS_AMBIGUOUS"}),
    ("hitl_exploitation.json", {"EXPLOITATION_RISK"}),
    ("hitl_advice.json", {"ADVICE_REQUEST"}),
    ("hitl_qualified_dividends.json", {"OUT_OF_SCOPE"}),
    ("hitl_unsure_birth_date.json", {"MISSING_INFO"}),
])
def test_fixtures(name, expected_codes):
    out = eligibility_agent(load(name))
    assert {e["code"] for e in out["escalations"]} == expected_codes


# ----------------------------------------------------------------- team checklist fields
@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_eligibility_has_team_fields(name):
    elig = eligibility_agent(load(name))["eligibility"]
    assert isinstance(elig["in_scope"], bool)
    assert 0 <= elig["boxes_checked"] <= 4
    assert isinstance(elig["senior_taxpayer"], bool)
    assert isinstance(elig["senior_spouse"], bool)
    for k in ("eligible_1040sr", "age_box_count", "blind_box_count", "schedules", "citations"):
        assert k in elig


def test_clean_mfj_values():
    elig = eligibility_agent(load("clean_mfj.json"))["eligibility"]
    assert elig["in_scope"] and elig["eligible_1040sr"]
    assert (elig["boxes_checked"], elig["age_box_count"], elig["blind_box_count"]) == (3, 2, 1)
    assert elig["senior_taxpayer"] and elig["senior_spouse"]


def test_escalations_use_team_format():
    e = eligibility_agent(load("hitl_1099r_box2b.json"))["escalations"][0]
    assert e["id"] == "eligibility:UNDETERMINED_TAXABLE_1099R:R6.9:documents.r_2"
    assert e["raised_by"] == "eligibility"
    assert e["reason"] == "undetermined_taxable_1099r:R6.9"
    assert e["detail"] and e["resolved"] is False
    assert e["source"] == "documents.r_2"


# ----------------------------------------------------------------- re-runs and other agents
def test_rerun_is_idempotent():
    """Running again after a review loop must not duplicate escalations."""
    state = load("hitl_exploitation.json")
    first = eligibility_agent(state)
    state["escalations"] = first["escalations"]
    second = eligibility_agent(state)
    assert first["escalations"] and second["escalations"] == []


def test_resolved_copies_do_not_cause_duplicates():
    state = load("hitl_advice.json")
    first = eligibility_agent(state)["escalations"]
    state["escalations"] = first + [{**first[0], "resolved": True, "resolved_by": "x"}]
    assert eligibility_agent(state)["escalations"] == []


def test_other_agents_escalations_are_ignored():
    state = load("hitl_advice.json")
    state["escalations"] = [{"raised_by": "computation", "reason": "missing_field",
                             "detail": "box_1_wages missing", "resolved": False}]
    out = eligibility_agent(state)
    assert [e["code"] for e in out["escalations"]] == ["ADVICE_REQUEST"]


def test_transcript_inside_intake_is_read():
    state = load("hitl_advice.json")
    state["intake"]["transcript"] = state.pop("transcript")
    assert any(e["code"] == "ADVICE_REQUEST" for e in eligibility_agent(state)["escalations"])


def test_document_order_does_not_matter():
    state = load("clean_mfj.json")
    a = eligibility_agent(state)
    state["documents"] = list(reversed(state["documents"]))
    b = eligibility_agent(state)
    assert a["eligibility"] == b["eligibility"]


def test_output_is_json_serialisable():
    json.dumps(eligibility_agent(load("clean_mfj.json")))   # checkpointers need plain JSON


def test_empty_state_does_not_crash():
    out = eligibility_agent({})
    assert any(e["code"] == "MISSING_INFO" for e in out["escalations"])


# ----------------------------------------------------------------- working with Intake (Karan)
@pytest.mark.parametrize("fixture, intake_reason", [
    ("hitl_advice.json", "tax_advice_request"),
    ("hitl_exploitation.json", "possible_exploitation"),
])
def test_no_double_flag_when_intake_already_flagged(fixture, intake_reason):
    state = load(fixture)
    state["escalations"] = [{"id": "intake:1", "raised_by": "intake", "reason": intake_reason,
                             "detail": "x", "resolved": False}]
    assert eligibility_agent(state)["escalations"] == []


def test_still_flags_if_intake_missed_it():
    """Backup: if Intake did not flag the advice request, Eligibility still does."""
    out = eligibility_agent(load("hitl_advice.json"))
    assert [e["code"] for e in out["escalations"]] == ["ADVICE_REQUEST"]


def test_unsure_birth_date_goes_to_a_human_not_back_to_intake():
    e = eligibility_agent(load("hitl_unsure_birth_date.json"))["escalations"][0]
    assert e["action"] == "human_review" and e["rule_id"] == "R1.1"


def test_missing_birth_date_is_reasked():
    state = load("hitl_unsure_birth_date.json")
    state["intake"]["unsure"] = []
    e = eligibility_agent(state)["escalations"][0]
    assert e["action"] == "reask_user"


def test_karan_extra_keys_are_ignored():
    state = load("clean_mfj.json")
    state["intake"].update({"adjustments_10": 0, "volunteer_questions": ["Roth?"], "lived_apart_all_year": None})
    assert eligibility_agent(state)["escalations"] == []
