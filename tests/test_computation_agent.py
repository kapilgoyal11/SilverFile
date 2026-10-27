"""Tests for the Computation agent. The clean case is the same hand-worked return
used in test_derived_lines.py: single filer 65+, wages 20,000, interest 1,000,
pension 10,000, Social Security 15,000, 2,500 withheld."""
import json
from copy import deepcopy

from langgraph.graph import END, START, StateGraph

from src.agents.computation import computation_agent
from src.state import SilverFileState

BASE_STATE = {
    "intake": {"filing_status": "single"},
    "eligibility": {"in_scope": True, "boxes_checked": 1, "senior_taxpayer": True},
    "documents": [
        {"doc_id": "w2_1", "doc_type": "W-2",
         "fields": {"box_1_wages": 20000, "box_2_withholding": 2500}},
        {"doc_id": "int_1", "doc_type": "1099-INT",
         "fields": {"box_1_interest": 1000, "box_8_tax_exempt_interest": 0,
                    "box_4_withholding": 0}},
        {"doc_id": "r_1", "doc_type": "1099-R",
         "fields": {"box_1_gross": 10000, "box_2a_taxable": 10000,
                    "box_2b_taxable_not_determined": False, "box_4_withholding": 0,
                    "is_ira": False}},
        {"doc_id": "ssa_1", "doc_type": "SSA-1099",
         "fields": {"box_5_net_benefits": 15000, "box_6_withheld": 0}},
    ],
}


def make_state(intake=None, eligibility=None, extra_docs=None):
    state = deepcopy(BASE_STATE)
    state["intake"].update(intake or {})
    state["eligibility"].update(eligibility or {})
    state["documents"] += deepcopy(extra_docs or [])
    return state


def amounts(result):
    return {line: rec["amount"] for line, rec in result["provenance"].items()}


def blocked_reason(state):
    result = computation_agent(state)
    assert "provenance" not in result
    return result["escalations"][0]["reason"]


def test_clean_single_return_matches_hand_calculation():
    expected = {"1z": 20000, "2a": 0, "2b": 1000, "3a": 0, "3b": 0, "4a": 0, "4b": 0,
                "5a": 10000, "5b": 10000, "6a": 15000, "6b": 8325, "8": 0, "9": 39325,
                "10": 0, "11a": 39325, "12e": 17750, "13b": 6000, "14": 23750,
                "15": 15575, "16": 1631, "17": 0, "21": 0, "23": 0, "24": 1631,
                "25a": 2500, "25b": 0, "25c": 0, "25d": 2500, "26": 0, "32": 0,
                "33": 2500, "34": 869, "37": 0}
    assert amounts(computation_agent(make_state())) == expected


def test_every_record_is_valid_json_with_a_known_kind():
    for line, rec in computation_agent(make_state())["provenance"].items():
        assert rec["line"] == line
        assert rec["kind"] in {"document", "computed", "user_input"}
        assert isinstance(rec["amount"], int)
        json.dumps(rec)


def test_document_lines_name_their_source():
    rec = computation_agent(make_state())["provenance"]["1z"]
    assert (rec["kind"], rec["document"], rec["field"]) == ("document", "w2_1", "box_1_wages")


def test_two_w2s_are_summed_and_formula_matches_inputs():
    second = {"doc_id": "w2_2", "doc_type": "W-2",
              "fields": {"box_1_wages": 5000, "box_2_withholding": 300}}
    rec = computation_agent(make_state(extra_docs=[second]))["provenance"]["1z"]
    assert rec["amount"] == 25000
    assert rec["formula"].split(" + ") == list(rec["inputs"])


def test_ira_distributions_go_to_lines_4_not_5():
    ira = {"doc_id": "r_2", "doc_type": "1099-R",
           "fields": {"box_1_gross": 4000, "box_2a_taxable": 3000,
                      "box_2b_taxable_not_determined": False, "box_4_withholding": 0,
                      "is_ira": True}}
    got = amounts(computation_agent(make_state(extra_docs=[ira])))
    assert (got["4a"], got["4b"], got["5a"], got["5b"]) == (4000, 3000, 10000, 10000)


def test_user_entered_amounts_are_marked_as_user_input():
    result = computation_agent(make_state(intake={"estimated_payments": 400,
                                                  "entered_by": "taxpayer"}))
    rec = result["provenance"]["26"]
    assert (rec["kind"], rec["amount"], rec["entered_by"]) == ("user_input", 400, "taxpayer")
    got = amounts(result)
    assert got["33"] == 2900 and got["34"] == 1269


def test_unreported_schedule_amounts_are_recorded_as_zero_not_invented():
    rec = computation_agent(make_state())["provenance"]["17"]
    assert rec["kind"] == "user_input" and rec["amount"] == 0
    assert rec["entered_by"] == "none reported"


def test_blocked_when_eligibility_has_not_cleared_the_return():
    assert blocked_reason(make_state(eligibility={"in_scope": False})) == "not_cleared_by_eligibility"


def test_blocked_when_filing_status_is_missing():
    state = make_state()
    del state["intake"]["filing_status"]
    assert blocked_reason(state) == "missing_input"


def test_blocked_on_unknown_filing_status():
    assert blocked_reason(make_state(intake={"filing_status": "married"})) == "invalid_input"


def test_blocked_on_qualified_dividends():
    div = {"doc_id": "div_1", "doc_type": "1099-DIV",
           "fields": {"box_1a_ordinary_dividends": 800, "box_1b_qualified_dividends": 500,
                      "box_4_withholding": 0}}
    assert blocked_reason(make_state(extra_docs=[div])) == "needs_decision:qualified_dividends"


def test_blocked_on_1099r_taxable_amount_not_determined():
    bad = {"doc_id": "r_9", "doc_type": "1099-R",
           "fields": {"box_1_gross": 5000, "box_2a_taxable": 0,
                      "box_2b_taxable_not_determined": True, "box_4_withholding": 0,
                      "is_ira": False}}
    assert blocked_reason(make_state(extra_docs=[bad])) == "out_of_scope:1099r_taxable_not_determined"


def test_adjustments_need_the_social_security_breakdown():
    state = make_state(intake={"adjustments_10": 1000})
    assert blocked_reason(state) == "missing_input"
    state["intake"]["adjustments_ss_worksheet"] = 1000
    got = amounts(computation_agent(state))
    assert got["10"] == 1000 and got["11a"] == got["9"] - 1000


def test_mfs_without_lived_apart_answer_is_blocked():
    assert blocked_reason(make_state(intake={"filing_status": "mfs"})) == "invalid_input"


def test_mfs_gets_no_senior_deduction():
    state = make_state(intake={"filing_status": "mfs", "lived_apart_all_year": False})
    assert amounts(computation_agent(state))["13b"] == 0


def test_running_twice_gives_the_same_amounts():
    assert amounts(computation_agent(make_state())) == amounts(computation_agent(make_state()))


def test_works_as_a_langgraph_node_and_merges_into_state():
    builder = StateGraph(SilverFileState)
    builder.add_node("computation_agent", computation_agent)
    builder.add_edge(START, "computation_agent")
    builder.add_edge("computation_agent", END)
    result = builder.compile().invoke(make_state())
    assert result["provenance"]["34"]["amount"] == 869
    assert result["handoff_log"][0]["agent"] == "computation"