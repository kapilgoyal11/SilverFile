"""Guardrails & Ethics: SSN checks, redaction, free-text risk detection,
and the release gate that runs before any draft reaches a human.

Design principle: deterministic checks first, LLM second, and when in doubt
escalate. A false alarm costs a volunteer a few minutes; a missed case of elder
financial abuse or an unsourced number on a tax return costs far more.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from pydantic import BaseModel, Field

from .eligibility_policy import assert_cited
from .eligibility_schemas import Escalation, IntakeData, Utterance

# ================================================================== SSNs (R10)
_SSN_TEST_RE = re.compile(r"^9\d{2}-?\d{2}-?\d{4}$")
_SSN_ANY_RE = re.compile(r"(?<!\d)(\d{3})[- ]?(\d{2})[- ]?(\d{4})(?!\d)")


class SSNCheck(BaseModel):
    ok: bool
    normalized: Optional[str] = None
    message: str


def validate_ssn(raw: str | None) -> SSNCheck:
    """R10.1 — accept only 900-999 test SSNs."""
    if raw is None or not raw.strip():
        return SSNCheck(ok=False, message="No number was entered.")
    s = raw.strip()
    if not _SSN_TEST_RE.match(s):
        return SSNCheck(
            ok=False,
            message=("That number can't be used here. This practice system only accepts "
                     "test numbers that start with 9, like 901-23-4567. Please don't enter "
                     "your real Social Security number."))
    digits = re.sub(r"\D", "", s)
    return SSNCheck(ok=True, normalized=f"{digits[:3]}-{digits[3:5]}-{digits[5:]}",
                    message="Thank you.")


def redact_ssn(text: str) -> str:
    """R10.2 — replace any SSN-shaped number with ***-**-1234."""
    return _SSN_ANY_RE.sub(lambda m: f"***-**-{m.group(3)}", text)


def find_ssns(text: str) -> list[str]:
    return ["".join(m.groups()) for m in _SSN_ANY_RE.finditer(text)]


def check_intake_ssns(intake: IntakeData) -> list[Escalation]:
    """Every SSN the user entered must be a test SSN (R10.1)."""
    found: list[tuple[str, Optional[str]]] = [("intake.taxpayer.ssn", intake.taxpayer.ssn)]
    if intake.spouse:
        found.append(("intake.spouse.ssn", intake.spouse.ssn))
    found += [(f"intake.dependents[{i}].ssn", d.ssn) for i, d in enumerate(intake.dependents)]
    out = []
    for src, val in found:
        if val and not validate_ssn(val).ok:
            out.append(Escalation(
                code="INVALID_SSN", rule_id=assert_cited("R10.1"),
                reason="A number that is not a 9XX test SSN was entered. Ask again.",
                source=src, action="reask_user", evidence=redact_ssn(val)))
    return out


# ================================================================== free-text detection
class TextFinding(BaseModel):
    category: str            # "ADVICE_REQUEST" | "EXPLOITATION_RISK" | "OUT_OF_SCOPE"
    rule_id: str
    reason: str
    evidence: str
    detected_by: str = "keyword"
    confidence: float = 1.0


# --- R8: advice. Needs BOTH an advice frame AND a tax topic, to avoid flagging
#     "should I upload my W-2 now?".
_ADVICE_FRAME = re.compile(
    r"\b(should (i|we)|would (i|we)|is it (better|worth|smarter)|can (i|we) deduct|"
    r"how (can|do) (i|we) (pay less|save|reduce|lower|avoid)|do you recommend|"
    r"what would you (do|recommend)|which is better|better off)\b", re.I)
_ADVICE_TOPIC = re.compile(
    r"\b(tax(es)?|deduct\w*|roth|ira|convert\w*|separately|jointly|pay less|owe less|"
    r"lower|save|refund|withholding|estimated payments?|medications?|medical)\b", re.I)

# --- R9.3: exploitation / pressure
_EXPLOIT_PATTERNS = [
    r"\b(my|the) (nephew|niece|grandson|granddaughter|son|daughter|caregiver|neighbou?r|"
    r"friend|boyfriend|girlfriend|new friend) (says?|said|told me|wants?|handles?|takes care of)\b",
    r"\b(put|send|deposit) (the|my) refund (in|into|to) (his|her|their|someone)",
    r"\b(not my|someone else'?s|don'?t know (the|whose|which)) (account|bank)",
    r"\bi (don'?t|do not) know (the|that|this) (account|bank)",
    r"\b(he|she|they) (won'?t let me|makes? me|told me not to tell)\b",
    r"\b(answering|filling this out) (for|on behalf of) (him|her|my (mother|father|mom|dad|aunt|uncle))\b",
    r"\bi'?m (scared|afraid) (of|to)\b",
]
_EXPLOIT_RE = [re.compile(p, re.I) for p in _EXPLOIT_PATTERNS]

# --- R6: out-of-scope items mentioned in conversation
_SCOPE_PATTERNS = [
    ("R6.2", r"\bitemi[sz]\w*|schedule a\b"),
    ("R6.3", r"\bself[- ]employ\w*|1099-?nec|my (own )?business|freelanc\w*|side (job|gig)|consulting income"),
    ("R6.4", r"\brent(al|ed out| out)\b|tenants?|landlord income"),
    ("R6.5", r"\b(sold|sell|selling) (some |my |the )?(stock|shares|house|property|investments?)|capital gains?|1099-?b\b"),
    ("R6.6", r"\bcrypto\w*|bitcoin|ethereum|digital assets?|nft\b|1099-?da"),
    ("R6.7", r"\bforeign (income|account|bank|pension)|overseas (account|income|pension)|fbar"),
    ("R6.8", r"\bamend\w*|last year'?s return|prior[- ]year|state (tax )?return|1040-?x"),
]
_SCOPE_RE = [(rid, re.compile(p, re.I)) for rid, p in _SCOPE_PATTERNS]


def keyword_scan(text: str) -> list[TextFinding]:
    findings: list[TextFinding] = []
    safe = redact_ssn(text)[:200]
    if _ADVICE_FRAME.search(text) and _ADVICE_TOPIC.search(text):
        findings.append(TextFinding(
            category="ADVICE_REQUEST", rule_id="R8.1", evidence=safe,
            reason="The taxpayer asked for tax advice. Noted for the volunteer preparer."))
    if any(r.search(text) for r in _EXPLOIT_RE):
        findings.append(TextFinding(
            category="EXPLOITATION_RISK", rule_id="R9.3", evidence=safe,
            reason="The conversation suggests someone else may be steering the "
                   "taxpayer's money or answers."))
    for rid, rx in _SCOPE_RE:
        if rx.search(text):
            findings.append(TextFinding(
                category="OUT_OF_SCOPE", rule_id=rid, evidence=safe,
                reason="The taxpayer mentioned something SilverFile does not handle."))
    return findings


LLMClassifier = Callable[[str], list[TextFinding]]


def classify_transcript(transcript: Iterable[Utterance],
                        llm: Optional[LLMClassifier] = None) -> list[Escalation]:
    """Scan every user utterance. Keywords always run; the LLM (if given) adds
    findings the keywords miss. If the LLM fails, escalate (fail-safe)."""
    escalations: list[Escalation] = []
    for i, u in enumerate(transcript):
        if u.role != "user":
            continue
        findings = keyword_scan(u.text)
        if llm is not None:
            try:
                findings += llm(u.text)
            except Exception as exc:  # noqa: BLE001 — any failure must be visible
                escalations.append(Escalation(
                    code="CLASSIFIER_UNAVAILABLE", rule_id=assert_cited("R9.3"),
                    reason=f"Risk check could not run ({type(exc).__name__}); "
                           "a person should read this message.",
                    source=f"transcript[{i}]", detected_by="llm",
                    evidence=redact_ssn(u.text)[:200]))
        for f in findings:
            escalations.append(Escalation(
                code=f.category, rule_id=assert_cited(f.rule_id), reason=f.reason,
                source=f"transcript[{i}]", detected_by=f.detected_by,
                evidence=f.evidence))
        # R10.3 — an SSN volunteered in free text
        if find_ssns(u.text):
            escalations.append(Escalation(
                code="INVALID_SSN", rule_id=assert_cited("R10.3"),
                reason="An SSN was typed into the conversation where it was not asked "
                       "for. It has been redacted and not stored.",
                source=f"transcript[{i}]", action="reask_user",
                evidence=redact_ssn(u.text)[:200]))
    return escalations


# ================================================================== release gate (R11)
_ADVICE_OUTPUT_RE = re.compile(
    r"\b(you should (convert|file|deduct|claim|switch)|i (recommend|suggest|advise)|"
    r"you('d| would) (save|pay less)|it would be better (for you )?to|consider (converting|filing))\b",
    re.I)


class GateResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)


def release_gate(draft: dict, escalations: list[Escalation],
                 resolved_keys: set[tuple] | None = None,
                 assistant_messages: Iterable[str] = ()) -> GateResult:
    """Last check before a draft is shown to a human reviewer.

    `draft` format (agree with Computation/Review):
        {"lines": {"1z": {"value": 52000, "provenance": {...}}, ...},
         "ssns": ["901-23-4567", ...]}
    """
    resolved_keys = resolved_keys or set()
    v: list[str] = []

    for line, entry in (draft.get("lines") or {}).items():          # R11.1
        if isinstance(entry, dict) and entry.get("value") is not None and not entry.get("provenance"):
            v.append(f"R11.1: line {line} has a value but no provenance record")

    open_items = [e for e in escalations                              # R11.2
                  if e.action == "human_review" and e.key not in resolved_keys]
    for e in open_items:
        v.append(f"R11.2: unresolved escalation {e.code} ({e.rule_id}) from {e.source}")

    for s in draft.get("ssns") or []:                                 # R11.3
        if not validate_ssn(s).ok:
            v.append(f"R11.3: non-test SSN {redact_ssn(s)} on draft")

    for msg in assistant_messages:                                    # R11.4
        if _ADVICE_OUTPUT_RE.search(msg):
            v.append(f"R11.4: system message contains advice language: '{msg[:80]}'")

    if draft.get("filed") or draft.get("submit_to_irs"):              # R11.5
        v.append("R11.5: draft is marked for filing; SilverFile never files")

    return GateResult(passed=not v, violations=v)


def release_gate_from_state(state: dict) -> GateResult:
    """Release gate reading the team's SilverFileState directly.

    Amounts live in state["provenance"] (line -> provenance record with "amount").
    A record that has an amount but nothing else (no source / calculation) fails R11.1.
    """
    from .eligibility_schemas import open_escalations   # local import avoids a cycle

    v: list[str] = []
    for line, rec in (state.get("provenance") or {}).items():
        if isinstance(rec, dict) and rec.get("amount") is not None \
                and not [k for k, val in rec.items() if k != "amount" and val]:
            v.append(f"R11.1: line {line} has an amount but no source or calculation")

    for e in open_escalations(state.get("escalations") or []):
        v.append(f"R11.2: unresolved escalation from {e.get('raised_by')}: {e.get('reason')}")

    intake = state.get("intake") or {}
    ssns = [intake.get("taxpayer_ssn"), intake.get("spouse_ssn")] + \
           [d.get("ssn") for d in intake.get("dependents") or [] if isinstance(d, dict)]
    for s in filter(None, ssns):
        if not validate_ssn(s).ok:
            v.append(f"R11.3: non-test SSN {redact_ssn(s)} in intake")

    transcript = state.get("transcript") or intake.get("transcript") or []
    for u in transcript:
        if u.get("role") == "assistant" and _ADVICE_OUTPUT_RE.search(u.get("text", "")):
            v.append(f"R11.4: system message contains advice language: '{u['text'][:80]}'")

    if state.get("status") == "filed":
        v.append("R11.5: status is 'filed'; SilverFile never files")
    return GateResult(passed=not v, violations=v)
