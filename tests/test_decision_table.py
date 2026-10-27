"""Decision table for the Eligibility & Rules agent (deterministic rules only)."""
from datetime import date

import pytest

from src.agents.eligibility_rules import is_65_or_older, run_rules
from src.agents.eligibility_schemas import Document, IntakeData

OLD, YOUNG = "1955-05-05", "1970-05-05"


def intake(**kw) -> IntakeData:
    base = {"filing_status": "single", "taxpayer": {"dob": OLD}}
    base.update(kw)
    return IntakeData.model_validate(base)


def codes(result):
    return {(e.code, e.rule_id) for e in result.escalations}


# ----------------------------------------------------------------- R1 / R3.2 boundary
@pytest.mark.parametrize("dob, expected", [
    (date(1961, 1, 1), True),     # boundary: born 1 Jan 1961 qualifies
    (date(1961, 1, 2), False),    # born on the cutoff does not
    (date(1960, 12, 31), True),
    (None, False),
])
def test_65_boundary(dob, expected):
    assert is_65_or_older(dob) is expected


@pytest.mark.parametrize("case, eligible_1040sr, escalation", [
    (dict(taxpayer={"dob": OLD}), True, None),
    (dict(taxpayer={"dob": YOUNG}), False, ("NOT_SR_ELIGIBLE", "R1.4")),
    # MFJ: only the spouse is 65+ → still eligible (R1.2)
    (dict(filing_status="mfj", taxpayer={"dob": YOUNG}, spouse={"dob": OLD}), True, None),
    # MFS: spouse's age does NOT carry over
    (dict(filing_status="mfs", taxpayer={"dob": YOUNG}, spouse={"dob": OLD}),
     False, ("NOT_SR_ELIGIBLE", "R1.4")),
])
def test_sr_eligibility(case, eligible_1040sr, escalation):
    r = run_rules(intake(**case), [])
    assert r.eligible_1040sr is eligible_1040sr
    if escalation:
        assert escalation in codes(r)


def test_missing_dob_asks_again_instead_of_guessing():
    r = run_rules(intake(taxpayer={}), [])
    esc = [e for e in r.escalations if e.code == "MISSING_INFO"]
    assert esc and esc[0].action == "reask_user"


# ----------------------------------------------------------------- R3 box count
@pytest.mark.parametrize("case, boxes", [
    (dict(taxpayer={"dob": OLD}), 1),
    (dict(taxpayer={"dob": OLD, "blind": True}), 2),
    (dict(filing_status="mfj", taxpayer={"dob": OLD, "blind": True},
          spouse={"dob": OLD, "blind": True}), 4),
    (dict(filing_status="mfj", taxpayer={"dob": OLD}, spouse={"dob": YOUNG, "blind": True}), 2),
    (dict(filing_status="mfs", taxpayer={"dob": OLD}, spouse={"dob": OLD, "blind": True}), 1),
])
def test_box_count(case, boxes):
    assert run_rules(intake(**case), []).boxes_checked == boxes


# ----------------------------------------------------------------- R2 filing status
@pytest.mark.parametrize("case, rule", [
    (dict(filing_status="mfj", spouse={"dob": OLD, "date_of_death": "2025-06-10"}), "R2.3"),
    (dict(filing_status="qss", spouse={"dob": OLD, "date_of_death": "2025-01-15"}), "R2.3"),
    (dict(filing_status="mfj"), "R2.2"),
    (dict(filing_status="hoh"), "R2.4"),
    (dict(filing_status="hoh", has_qualifying_person=True, spouse={"dob": OLD}), "R2.4"),
    (dict(filing_status="qss", spouse={"dob": OLD, "date_of_death": "2024-03-01"}), "R2.5"),
    (dict(filing_status="single", spouse={"dob": OLD}), "R2.6"),
])
def test_filing_status_escalates(case, rule):
    r = run_rules(intake(**case), [])
    assert ("FILING_STATUS_AMBIGUOUS", rule) in codes(r)
    assert r.filing_status_valid is False


def test_valid_qss():
    r = run_rules(intake(filing_status="qss",
                         spouse={"dob": OLD, "date_of_death": "2024-03-01"},
                         dependents=[{"name": "Sam", "relationship": "son",
                                      "qualifying_child": True}]), [])
    assert r.filing_status_valid and not r.escalations


# ----------------------------------------------------------------- R4 chart
@pytest.mark.parametrize("case, rule", [
    (dict(can_be_claimed_as_dependent=True), "R4.1"),
    (dict(filing_status="mfs", spouse={"dob": OLD, "itemizes": True}), "R4.2"),
    (dict(dual_status_alien=True), "R4.3"),
])
def test_chart_not_applicable(case, rule):
    r = run_rules(intake(**case), [])
    assert ("STD_DEDUCTION_CHART_NA", rule) in codes(r)
    assert r.std_deduction_chart_applies is False
    assert r.std_deduction_lookup is None


@pytest.mark.parametrize("status, rate", [
    ("single", "unmarried"), ("hoh", "unmarried"), ("mfj", "married"),
])
def test_per_box_rate(status, rate):
    extra = {"spouse": {"dob": OLD}} if status == "mfj" else {}
    if status == "hoh":
        extra["has_qualifying_person"] = True
    r = run_rules(intake(filing_status=status, **extra), [])
    assert r.std_deduction_lookup.per_box_rate == rate


# ----------------------------------------------------------------- R5 schedules
def test_schedule_1a_two_qualifiers_on_mfj():
    r = run_rules(intake(filing_status="mfj", spouse={"dob": OLD}), [])
    assert r.schedule_1a_qualifiers == 2 and "1-A" in r.schedules
    assert "R5.2" in r.citations      # senior deduction is separate from chart boxes


def test_schedule_1a_only_spouse_qualifies():
    r = run_rules(intake(filing_status="mfj", taxpayer={"dob": YOUNG},
                         spouse={"dob": OLD}), [])
    assert r.schedule_1a_qualifiers == 1


def test_user_reported_schedules():
    r = run_rules(intake(schedules_reported=["1", "3", "Z"]), [])
    assert r.schedules == ["1", "1-A", "3"]


# ----------------------------------------------------------------- R6 scope
def doc(t, **fields):
    return Document(doc_id=f"{t}-x", doc_type=t, fields=fields)


@pytest.mark.parametrize("d, code, rule", [
    (doc("1099-NEC"), "OUT_OF_SCOPE", "R6.3"),
    (doc("1099-B"), "OUT_OF_SCOPE", "R6.5"),
    (doc("1099-DA"), "OUT_OF_SCOPE", "R6.6"),
    (doc("1098-T"), "OUT_OF_SCOPE", "R6.1"),          # unknown type
    (doc("1099-R", box_2b_taxable_not_determined=True), "UNDETERMINED_TAXABLE_1099R", "R6.9"),
])
def test_scope_escalations(d, code, rule):
    r = run_rules(intake(), [d])
    assert (code, rule) in codes(r)
    assert r.in_scope is False


@pytest.mark.parametrize("t", ["W-2", "SSA-1099", "1099-R", "1099-INT", "1099-DIV", "Form W-2"])
def test_in_scope_documents(t):
    r = run_rules(intake(), [doc(t)])
    assert r.in_scope and not r.escalations


def test_reported_but_not_uploaded_out_of_scope_doc():
    r = run_rules(intake(documents_reported=["1099-NEC"]), [])
    assert ("OUT_OF_SCOPE", "R6.3") in codes(r)


# ----------------------------------------------------------------- R7 / R9 structured
def test_user_document_conflict():
    r = run_rules(intake(income_sources_denied=["wages"]), [doc("W-2")])
    assert ("USER_DOCUMENT_CONFLICT", "R7.1") in codes(r)


def test_refund_to_unknown_account():
    r = run_rules(intake(refund={"wants_direct_deposit": True,
                                 "taxpayer_can_describe_account": False}), [])
    assert ("EXPLOITATION_RISK", "R9.1") in codes(r)


@pytest.mark.parametrize("respondent, flagged", [
    ({"is_taxpayer": False, "relationship": "nephew"}, True),
    ({"is_taxpayer": False, "relationship": "daughter", "stated_authority": "power of attorney"}, False),
    ({"is_taxpayer": True}, False),
])
def test_third_party_answering(respondent, flagged):
    r = run_rules(intake(respondent=respondent), [])
    assert (("EXPLOITATION_RISK", "R9.2") in codes(r)) is flagged


# ----------------------------------------------------------------- clean case
def test_clean_case_has_no_escalations():
    r = run_rules(intake(filing_status="mfj", spouse={"dob": OLD}),
                  [doc("W-2"), doc("SSA-1099"), doc("1099-R")])
    assert r.escalations == [] and r.in_scope and r.eligible_1040sr


# ----------------------------------------------------------------- team checklist fields
@pytest.mark.parametrize("case, senior_tp, senior_sp, age_boxes, blind_boxes", [
    (dict(taxpayer={"dob": OLD}), True, False, 1, 0),
    (dict(filing_status="mfj", taxpayer={"dob": OLD, "blind": True},
          spouse={"dob": OLD}), True, True, 2, 1),
    (dict(filing_status="mfs", taxpayer={"dob": OLD}, spouse={"dob": OLD}), True, False, 1, 0),
    (dict(filing_status="mfj", taxpayer={"dob": YOUNG}, spouse={"dob": OLD, "blind": True}),
     False, True, 1, 1),
])
def test_senior_flags_and_box_counts(case, senior_tp, senior_sp, age_boxes, blind_boxes):
    r = run_rules(intake(**case), [])
    assert r.senior_taxpayer is senior_tp
    assert r.senior_spouse is senior_sp            # only possible for mfj
    assert (r.age_box_count, r.blind_box_count) == (age_boxes, blind_boxes)
    assert r.boxes_checked == age_boxes + blind_boxes


# ----------------------------------------------------------------- R6.10 qualified dividends
@pytest.mark.parametrize("qualified, flagged", [(600, True), (0, False), (None, False), ("??", True)])
def test_qualified_dividends(qualified, flagged):
    d = doc("1099-DIV", box_1a_ordinary_dividends=900, box_1b_qualified_dividends=qualified,
            box_4_withholding=0)
    r = run_rules(intake(), [d])
    assert (("OUT_OF_SCOPE", "R6.10") in codes(r)) is flagged
    assert r.in_scope is (not flagged)


# ----------------------------------------------------------------- team flat intake format
def test_team_flat_intake_matches_nested():
    flat = IntakeData.model_validate({
        "filing_status": "mfj", "birth_date": OLD, "is_blind": True,
        "spouse_birth_date": OLD, "spouse_is_blind": False,
        "document_inventory": ["W-2"], "estimated_payments": 500,       # extra keys ignored
        "refund_direct_deposit": True, "refund_account_known": False,
        "answered_by_taxpayer": False, "answered_by_relationship": "nephew"})
    nested = intake(filing_status="mfj", taxpayer={"dob": OLD, "blind": True},
                    spouse={"dob": OLD}, documents_reported=["W-2"],
                    refund={"wants_direct_deposit": True, "taxpayer_can_describe_account": False},
                    respondent={"is_taxpayer": False, "relationship": "nephew"})
    assert flat == nested


def test_flat_intake_without_spouse_has_no_spouse():
    assert IntakeData.model_validate({"filing_status": "single", "birth_date": OLD}).spouse is None
