"""Guardrails & Ethics tests: SSNs, free-text risk detection, release gate."""
import pytest

from src.agents.eligibility_guardrails import (
    TextFinding,
    classify_transcript,
    keyword_scan,
    redact_ssn,
    release_gate,
    validate_ssn,
)
from src.agents.eligibility_schemas import Escalation, Utterance


# ----------------------------------------------------------------- R10 SSNs
@pytest.mark.parametrize("ssn, ok", [
    ("901-23-4567", True), ("999999999", True), ("900-00-0001", True),
    ("123-45-6789", False), ("899-12-3456", False), ("9012345678", False),
    ("90A-12-3456", False), ("", False), (None, False),
])
def test_only_test_ssns_accepted(ssn, ok):
    assert validate_ssn(ssn).ok is ok


def test_rejection_message_is_plain_and_kind():
    msg = validate_ssn("123-45-6789").message
    assert "start with 9" in msg and "real Social Security" in msg


def test_redaction_keeps_last_four_only():
    assert redact_ssn("my number is 123-45-6789 ok") == "my number is ***-**-6789 ok"
    assert "123" not in redact_ssn("123 45 6789")


# ----------------------------------------------------------------- R8 advice
@pytest.mark.parametrize("text", [
    "Should I convert my IRA to a Roth?",
    "Would we pay less if we filed separately?",
    "Can I deduct my medications?",
    "How can I lower my taxes next year?",
])
def test_advice_detected(text):
    assert any(f.category == "ADVICE_REQUEST" for f in keyword_scan(text))


@pytest.mark.parametrize("text", [
    "Should I upload my W-2 now?",
    "What does line 12 mean?",
    "Can you repeat that, please?",
])
def test_not_advice(text):
    assert not any(f.category == "ADVICE_REQUEST" for f in keyword_scan(text))


# ----------------------------------------------------------------- R9 exploitation
@pytest.mark.parametrize("text", [
    "My nephew said to put the refund into his account.",
    "I don't know the bank, my caregiver handles that.",
    "She told me not to tell anyone about the money.",
    "I'm answering for my mother, she's resting.",
])
def test_exploitation_detected(text):
    assert any(f.category == "EXPLOITATION_RISK" for f in keyword_scan(text))


def test_ordinary_family_mention_not_flagged():
    assert not keyword_scan("My daughter visited last week and we had lunch.")


# ----------------------------------------------------------------- R6 scope in chat
@pytest.mark.parametrize("text, rule", [
    ("I sold some stock in March", "R6.5"),
    ("I also have a little rental property", "R6.4"),
    ("I bought bitcoin last year", "R6.6"),
    ("I do some freelance bookkeeping", "R6.3"),
    ("Can you also do my state return?", "R6.8"),
])
def test_out_of_scope_mentions(text, rule):
    assert any(f.rule_id == rule for f in keyword_scan(text))


# ----------------------------------------------------------------- transcript handling
def test_only_user_messages_scanned():
    t = [Utterance(role="assistant", text="Should I convert your IRA to a Roth?")]
    assert classify_transcript(t) == []


def test_volunteered_ssn_is_redacted_and_flagged():
    t = [Utterance(role="user", text="here is my social 123-45-6789")]
    esc = classify_transcript(t)
    assert esc[0].code == "INVALID_SSN" and esc[0].rule_id == "R10.3"
    assert "123-45" not in esc[0].evidence


def test_llm_failure_fails_safe():
    def broken(_):
        raise TimeoutError("api down")
    esc = classify_transcript([Utterance(role="user", text="hello")], llm=broken)
    assert [e.code for e in esc] == ["CLASSIFIER_UNAVAILABLE"]
    assert esc[0].action == "human_review"


def test_llm_findings_added():
    def fake(_):
        return [TextFinding(category="EXPLOITATION_RISK", rule_id="R9.3",
                            reason="pressure", evidence="x", detected_by="llm")]
    esc = classify_transcript([Utterance(role="user", text="he gets upset if I ask")], llm=fake)
    assert any(e.detected_by == "llm" for e in esc)


# ----------------------------------------------------------------- R11 release gate
def esc(code="OUT_OF_SCOPE", rule="R6.3", src="documents.x"):
    return Escalation(code=code, rule_id=rule, reason="r", source=src)


def test_gate_passes_clean_draft():
    draft = {"lines": {"1z": {"value": 100, "provenance": {"source": "w2-1 box 1"}}},
             "ssns": ["901-23-4567"]}
    assert release_gate(draft, []).passed


@pytest.mark.parametrize("draft, escs, msgs, rule", [
    ({"lines": {"1z": {"value": 100}}}, [], [], "R11.1"),
    ({"lines": {}}, [esc()], [], "R11.2"),
    ({"lines": {}, "ssns": ["123-45-6789"]}, [], [], "R11.3"),
    ({"lines": {}}, [], ["I recommend you convert to a Roth."], "R11.4"),
    ({"lines": {}, "filed": True}, [], [], "R11.5"),
])
def test_gate_blocks(draft, escs, msgs, rule):
    g = release_gate(draft, escs, assistant_messages=msgs)
    assert not g.passed and any(v.startswith(rule) for v in g.violations)


def test_gate_passes_once_escalation_resolved():
    e = esc()
    assert release_gate({"lines": {}}, [e], resolved_keys={e.key}).passed


# ----------------------------------------------------------------- release gate on team state
from src.agents.eligibility_guardrails import release_gate_from_state  # noqa: E402


def team_state(**kw):
    s = {"provenance": {"1z": {"amount": 18500, "source": "w2_1 box_1_wages"}},
         "escalations": [], "intake": {"taxpayer_ssn": "901-23-4567"}, "transcript": []}
    s.update(kw)
    return s


def test_state_gate_passes_clean():
    assert release_gate_from_state(team_state()).passed


@pytest.mark.parametrize("override, rule", [
    ({"provenance": {"1z": {"amount": 18500}}}, "R11.1"),
    ({"escalations": [{"raised_by": "computation", "reason": "missing_field",
                       "detail": "x", "resolved": False}]}, "R11.2"),
    ({"intake": {"taxpayer_ssn": "123-45-6789"}}, "R11.3"),
    ({"transcript": [{"role": "assistant", "text": "I recommend you file separately."}]}, "R11.4"),
    ({"status": "filed"}, "R11.5"),
])
def test_state_gate_blocks(override, rule):
    g = release_gate_from_state(team_state(**override))
    assert not g.passed and any(v.startswith(rule) for v in g.violations)


def test_state_gate_respects_appended_resolution():
    e = {"raised_by": "eligibility", "code": "ADVICE_REQUEST", "rule_id": "R8.1",
         "source": "transcript[0]", "reason": "advice_request:R8.1", "detail": "x",
         "resolved": False, "action": "human_review"}
    s = team_state(escalations=[e, {**e, "resolved": True, "resolved_by": "staff"}])
    assert release_gate_from_state(s).passed
