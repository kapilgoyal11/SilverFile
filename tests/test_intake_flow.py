"""Intake behaviour tests (accessibility, drift, PII, escalation)."""
import json

from src.memory import active_answers, get_dependents, intake_record
from tests.intake_helpers import CLEAN_RUN, Session


def test_clean_run_completes_with_expected_handoff():
    s = Session().say_all(CLEAN_RUN)
    assert not s.waiting
    st = s.state
    assert st["intake_complete"] is True
    h = st["intake"]                      # what the other agents read
    assert h == intake_record(st)
    assert h["filing_status"] == "mfj"    # team's shared strings
    assert h["birth_date"] == "1950-03-04"
    assert h["spouse_birth_date"] == "1948-06-01"
    assert h["dependents"] == [{"name": "Emma Lee", "relationship": "granddaughter", "ssn": "901-23-4567"}]
    assert h["document_inventory"]["W-2"] == 1
    assert h["estimated_payments"] == 1200          # whole-dollar int
    assert h["adjustments_10"] == 0                 # user said no adjustments
    assert h["schedule_2_line_3"] == 0 and h["credits_21"] == 0
    assert h["entered_by"] == "taxpayer"
    # provenance on every user answer
    for slot, rec in st["answers"].items():
        assert rec["source"] == "direct user input"
        assert rec["entered_by"] == "taxpayer"
        assert rec["entered_at"]


def test_handoff_feeds_computation_agent():
    """Intake's output is accepted by Kapil's Computation agent as-is."""
    from src.agents.computation import computation_agent
    s = Session().say_all(CLEAN_RUN)
    state = {**s.state,
             "eligibility": {"in_scope": True, "boxes_checked": 2, "senior_taxpayer": True, "senior_spouse": True},
             "documents": [{"doc_id": "w2_1", "doc_type": "W-2",
                            "fields": {"box_1_wages": 20000, "box_2_withholding": 1500}}]}
    out = computation_agent(state)
    assert "escalations" not in out, out.get("escalations")
    assert out["provenance"]["26"]["amount"] == 1200
    assert out["provenance"]["26"]["kind"] == "user_input"


def test_unsure_amount_blocks_computation_instead_of_using_zero():
    from src.agents.computation import computation_agent
    s = Session().say_all(CLEAN_RUN[:27] + ["I'm not sure"] + CLEAN_RUN[28:])
    h = s.state["intake"]
    assert h["estimated_payments"] is None and "estimated_payments_total" in h["unsure"]
    state = {**s.state, "eligibility": {"in_scope": True, "boxes_checked": 2, "senior_taxpayer": True},
             "documents": []}
    out = computation_agent(state)
    assert out["escalations"][0]["reason"] == "invalid_input"


def test_mfs_asks_lived_apart_and_cents_are_not_rounded_by_intake():
    s = Session().say_all(CLEAN_RUN[:7] + ["2", "Robert Lee", "no"])
    assert s.last["slot"] == "lived_apart_all_year"
    s.say("yes")
    s.say_all(["no", "no", "no", "no", "no", "no", "no", "no", "yes"])
    assert s.last["slot"] == "estimated_payments_total"
    s.say("1,234.56")
    assert s.last["slot"] == "estimated_payments_total"
    assert "round to the nearest dollar" in s.last["text"]
    s.say("1,235")
    assert s.state["answers"]["estimated_payments_total"]["value"] == 1235


def test_one_question_at_a_time():
    """Every prompt in a full run contains exactly one question mark."""
    s = Session().say_all(CLEAN_RUN)
    for p in s.prompts:
        assert p["text"].count("?") == 1, p["text"]


def test_jargon_explained_only_once():
    s = Session().say_all(CLEAN_RUN)
    gloss = "Legally blind means"
    assert sum(gloss in p["text"] for p in s.prompts) == 1


def test_go_back_keeps_old_answer_in_history():
    s = Session().say_all(["yes", "Margaret Lee", "March 4, 1950", "no"])
    # now on claimable_as_dependent; user corrects their birthday in one sentence
    s.say("Actually, I made a mistake. I was born March 4, 1951")
    dob = s.state["answers"]["taxpayer_dob"]
    assert dob["value"] == "1951-03-04"
    assert dob["history"][0]["value"] == "1950-03-04"
    assert "updated" in s.last["text"]
    assert s.last["slot"] == "claimable_as_dependent"   # carries on where we were


def test_bare_go_back_returns_to_previous_question():
    s = Session().say_all(["yes", "Margaret Lee"])
    assert s.last["slot"] == "taxpayer_dob"
    s.say("go back")
    assert s.last["slot"] == "taxpayer_name"
    s.say("Margaret A. Lee")
    assert s.state["answers"]["taxpayer_name"]["value"] == "Margaret A. Lee"
    assert len(s.state["answers"]["taxpayer_name"]["history"]) == 1
    assert s.last["slot"] == "taxpayer_dob"


def test_changed_branch_hides_but_keeps_spouse_answers():
    s = Session().say_all(CLEAN_RUN[:11])   # through spouse_blind
    s.say("wait, I need to change my married answer")
    assert s.last["slot"] == "married_end_of_year"
    s.say("no")
    st = s.state
    assert "spouse_name" in st["answers"]                 # nothing lost
    assert "spouse_name" not in active_answers(st)        # but no longer used
    assert s.last["slot"] == "spouse_died_recent"
    s.say("no")
    assert s.last["slot"] == "filing_status"              # old "jointly" no longer fits, so it's asked again
    assert "Single" in s.last["text"] and "jointly" not in s.last["text"]


def test_confirmation_no_lets_user_change_then_reconfirms():
    s = Session().say_all(CLEAN_RUN[:-1])
    assert s.last["slot"] == "confirm_all"
    s.say("no")
    assert s.last["slot"] == "change_what"
    s.say("my date of birth")
    assert s.last["slot"] == "taxpayer_dob"
    s.say("March 5, 1950")
    assert s.last["slot"] == "confirm_all"
    assert "March 5, 1950" in s.last["text"]
    s.say("yes")
    assert not s.waiting and s.state["intake_complete"]


def test_unsure_is_flagged_and_moves_on():
    s = Session().say_all(["yes", "Margaret Lee", "March 4, 1950"])
    s.say("I'm not sure, I don't think so but I don't know")
    st = s.state
    assert st["answers"]["taxpayer_blind"]["status"] == "unsure"
    assert any(e["kind"] == "user_unsure" and e["slot"] == "taxpayer_blind" for e in st["escalations"])
    assert s.last["slot"] == "claimable_as_dependent"
    assert "volunteer" in s.last["text"]


def test_tax_advice_is_declined_noted_and_question_repeated():
    s = Session().say_all(["yes", "Margaret Lee", "March 4, 1950", "no", "no", "yes", "yes"])
    assert s.last["slot"] == "filing_status"
    s.say("Would I pay less if we filed separately?")
    st = s.state
    assert s.last["slot"] == "filing_status"
    assert "tax advice" in s.last["text"]
    assert st["volunteer_notes"] == ["Would I pay less if we filed separately?"]
    adv = [e for e in st["escalations"] if e["kind"] == "tax_advice_request"]
    assert adv and adv[0]["blocking"] is False


def test_real_ssn_is_rejected_and_never_stored():
    s = Session().say_all(CLEAN_RUN[:14])
    assert s.last["slot"] == "dependent_1_ssn"
    s.say("123-45-6789")
    st = s.state
    assert "dependent_1_ssn" not in st["answers"]
    assert "only accepts test numbers" in s.last["text"]
    assert "123-45-6789" not in json.dumps(st)
    s.say("901-23-4567")
    assert st is not None and s.state["answers"]["dependent_1_ssn"]["value"] == "901-23-4567"
    assert "901-23-4567" not in json.dumps(s.state["transcript"])


def test_volunteered_ssn_is_not_saved_anywhere():
    s = Session().say_all(["yes"])
    s.say("Margaret Lee, my social is 912-34-5678")
    st = s.state
    assert st["answers"]["taxpayer_name"]["value"] == "Margaret Lee"
    assert "912-34-5678" not in json.dumps(st)
    assert "didn't save it" in s.last["text"]


def test_helper_without_authority_stops_for_human():
    s = Session().say_all(["no, I'm doing this for my mother", "her son", "no"])
    st = s.state
    esc = [e for e in st["escalations"] if e["kind"] == "possible_exploitation"]
    assert esc and esc[0]["blocking"]
    assert s.last["type"] == "human_review"
    # volunteer resolves it; intake continues and records the helper as the source
    s.say({"resolution": "Spoke with taxpayer by phone; she confirmed her son may help."})
    assert s.last["slot"] == "taxpayer_name"
    assert "their full name" in s.last["text"]
    s.say("Margaret Lee")
    assert s.state["answers"]["taxpayer_name"]["entered_by"] == "helper (her son)"


def test_pressure_mid_conversation_stops_for_human():
    s = Session().say_all(["yes", "Margaret Lee", "March 4, 1950", "no", "no", "yes", "yes", "1"])
    s.say("My nephew told me to put the refund into his account, I don't know that bank")
    assert s.last["type"] == "human_review"
    assert any(e["kind"] == "possible_exploitation" and e["blocking"] for e in s.state["escalations"])


def test_drift_tolerance_flags_after_three_misses():
    s = Session().say_all(["yes", "Margaret Lee"])
    for _ in range(2):
        s.say("the weather is nice today")
        assert s.last["slot"] == "taxpayer_dob"
        assert "month, day, and year" in s.last["text"]
    s.say("blue")
    assert s.last["slot"] == "taxpayer_blind"
    assert any(e["kind"] == "repeated_parse_failure" for e in s.state["escalations"])


def test_help_and_repeat_do_not_lose_place():
    s = Session().say_all(["yes", "Margaret Lee", "March 4, 1950"])
    s.say("what does legally blind mean")
    assert s.last["slot"] == "taxpayer_blind"
    s.say("can you repeat that")
    assert s.last["slot"] == "taxpayer_blind"
    s.say("no")
    assert s.last["slot"] == "claimable_as_dependent"


def test_spouse_died_this_year_records_fact_and_offers_all_statuses():
    s = Session().say_all(["yes", "Margaret Lee", "March 4, 1950", "no", "no", "yes", "no", "yes", "2025"])
    assert s.last["slot"] == "filing_status"
    for label in ("jointly", "separately", "Single", "Head of household", "surviving spouse"):
        assert label in s.last["text"]


def test_approximate_amount_is_flagged():
    s = Session().say_all(CLEAN_RUN[:27])
    assert s.last["slot"] == "estimated_payments_total"
    s.say("about 1200 dollars")
    rec = s.state["answers"]["estimated_payments_total"]
    assert rec["value"] == 1200 and rec["status"] == "approximate"


def test_multiple_dependents():
    replies = CLEAN_RUN[:15] + ["yes", "Leo Lee", "grandson", "902-11-2222", "no"] + CLEAN_RUN[16:]
    s = Session().say_all(replies)
    deps = get_dependents(s.state)
    assert [d["name"] for d in deps] == ["Emma Lee", "Leo Lee"]
