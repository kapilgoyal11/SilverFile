"""Tests for audit_return(): a clean return passes, and each kind of tampering is caught."""
import json
from copy import deepcopy

from src.agents.computation import computation_agent
from src.tools.audit_checks import audit_return
from tests.test_computation_agent import make_state


def clean():
    return computation_agent(make_state())["provenance"]


def lines_flagged(provenance):
    return {f["line"] for f in audit_return(provenance)}


def test_clean_return_has_no_findings():
    assert audit_return(clean()) == []


def test_changed_total_is_caught():
    p = clean()
    p["9"]["amount"] += 100
    assert "9" in lines_flagged(p)


def test_taxable_social_security_above_benefits_is_caught():
    p = clean()
    p["6b"]["amount"] = 16000
    assert "6b" in lines_flagged(p)


def test_wrong_tax_is_caught_by_recomputing():
    p = clean()
    p["16"]["amount"] = 1000
    assert "16" in lines_flagged(p)


def test_stale_input_is_caught():
    p = clean()
    p["1z"]["amount"] = 21000   # line 9 still holds the old 20,000
    assert "9" in lines_flagged(p)


def test_missing_line_is_caught():
    p = clean()
    del p["37"]
    assert "37" in lines_flagged(p)


def test_negative_amount_is_caught():
    p = clean()
    p["25a"]["amount"] = -5
    assert "25a" in lines_flagged(p)


def test_non_integer_amount_is_caught():
    p = clean()
    p["1z"]["amount"] = 20000.5
    assert "1z" in lines_flagged(p)


def test_unknown_kind_is_caught():
    p = clean()
    p["1z"]["kind"] = "guess"
    assert "1z" in lines_flagged(p)


def test_qualified_dividends_are_sent_to_a_human():
    p = clean()
    p["3a"]["amount"] = 500
    findings = audit_return(p)
    assert any(f["line"] == "3a" and f["send_back_to"] == "human" for f in findings)


def test_refund_and_amount_owed_together_is_a_contradiction():
    p = clean()
    p["34"]["amount"] = 10
    p["37"]["amount"] = 10
    assert any("both a refund" in f["problem"] for f in audit_return(p))


def test_disagreeing_filing_status_is_caught():
    p = clean()
    p["16"]["inputs"]["filing_status"] = "mfj"
    assert "all" in lines_flagged(p)


def test_audit_never_changes_the_records():
    p = clean()
    p["9"]["amount"] += 100
    before = deepcopy(p)
    audit_return(p)
    assert p == before


def test_missing_provenance_is_reported():
    assert audit_return(None)[0]["line"] == "all"


def test_findings_are_json_ready():
    p = clean()
    p["9"]["amount"] += 100
    json.dumps(audit_return(p))