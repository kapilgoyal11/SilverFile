"""Optional LLM layer for free-text risk detection (R6, R8, R9).

The keyword scan in guardrails.py always runs. This adds a second opinion for
phrasings the keywords miss. It is OFF unless USE_LLM_CLASSIFIER=1 and an
ANTHROPIC_API_KEY is set, so tests and demos run without an API key.

If your team standardises on a different LLM provider, only `_call_model`
needs to change.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .eligibility_guardrails import LLMClassifier, TextFinding, redact_ssn
from .eligibility_policy import assert_cited

SYSTEM_PROMPT = """You are a safety classifier for SilverFile, a tax-form preparation aid for seniors.
You never answer the user. You only classify ONE message from a taxpayer.

Return a JSON array (possibly empty). Each item:
{"category": "ADVICE_REQUEST" | "EXPLOITATION_RISK" | "OUT_OF_SCOPE",
 "rule_id": one of R6.2 R6.3 R6.4 R6.5 R6.6 R6.7 R6.8 R8.1 R9.3,
 "reason": "<one plain-English sentence>",
 "confidence": 0.0-1.0}

ADVICE_REQUEST (R8.1): asking what they SHOULD do to pay less tax, plan, or optimise
  (Roth conversions, filing jointly vs separately, whether something is deductible).
  Asking how to use this system is NOT advice.
EXPLOITATION_RISK (R9.3): signs someone else controls or pressures their money or answers,
  refund going to an account they don't know, fear, secrecy, a new "friend" handling finances.
OUT_OF_SCOPE: itemizing R6.2, self-employment/business R6.3, rental R6.4, sold stock/property R6.5,
  crypto R6.6, foreign income/accounts R6.7, amended/prior-year/state returns R6.8.

Output ONLY the JSON array."""

CONFIDENCE_FLOOR = 0.3   # below this we drop; anything at or above escalates (fail toward safety)


def _call_model(text: str) -> str:
    import anthropic  # imported lazily so the package is optional

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=os.getenv("ELIGIBILITY_LLM_MODEL", "claude-haiku-5-5"),
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": redact_ssn(text)}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def llm_classify(text: str) -> list[TextFinding]:
    raw = _call_model(text).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1]
    items = json.loads(raw)            # a parse failure raises → fail-safe escalation
    findings = []
    for it in items:
        if float(it.get("confidence", 1.0)) < CONFIDENCE_FLOOR:
            continue
        assert_cited(it["rule_id"])    # model may not invent rule IDs → raises → fail-safe
        findings.append(TextFinding(
            category=it["category"], rule_id=it["rule_id"], reason=it["reason"],
            evidence=redact_ssn(text)[:200], detected_by="llm",
            confidence=float(it.get("confidence", 1.0))))
    return findings


def get_llm_classifier() -> Optional[LLMClassifier]:
    if os.getenv("USE_LLM_CLASSIFIER") == "1" and os.getenv("ANTHROPIC_API_KEY"):
        return llm_classify
    return None
