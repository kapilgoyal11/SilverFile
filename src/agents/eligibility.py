"""The Eligibility & Rules agent as a LangGraph node.

This is the ONLY thing the team graph needs from me:

    from src.agents.eligibility import eligibility_agent
    builder.add_node("eligibility_agent", eligibility_agent)

Node name is "eligibility_agent" (node names must not match state fields).

Reads  (src/state.py SilverFileState): intake, documents, transcript, escalations
Writes:                                eligibility, escalations (only NEW ones)

* state["eligibility"] uses the merge_dicts reducer, so re-runs overwrite.
* state["escalations"] uses operator.add, so I only return escalations that are
  not already in state. Running this node again after Review or a human
  decision never creates duplicates.
* I never call interrupt(). I raise flags; the orchestrator routes to human
  review.
"""
from __future__ import annotations

from typing import Any, Optional

from .eligibility_guardrails import LLMClassifier, check_intake_ssns, classify_transcript
from .eligibility_llm import get_llm_classifier
from .eligibility_rules import run_rules
from .eligibility_schemas import (
    Document,
    EligibilityResult,
    IntakeData,
    Utterance,
    escalation_key,
)


def evaluate(intake: IntakeData, documents: list[Document],
             transcript: list[Utterance],
             llm: Optional[LLMClassifier] = None) -> EligibilityResult:
    """Pure function: everything the agent decides, with no graph involved."""
    result = run_rules(intake, documents)
    extra = check_intake_ssns(intake) + classify_transcript(transcript, llm=llm)

    seen = {e.key for e in result.escalations}
    for e in extra:
        if e.key not in seen:
            result.escalations.append(e)
            seen.add(e.key)
    result.citations = sorted(
        set(result.citations) | {e.rule_id for e in result.escalations},
        key=lambda r: [int(x) for x in r[1:].split(".")])
    if any(e.code == "OUT_OF_SCOPE" for e in result.escalations):
        result.in_scope = False      # e.g. out-of-scope item mentioned in conversation
    return result


def _transcript(state: dict[str, Any]) -> list[dict]:
    """Transcript may live at state["transcript"] or state["intake"]["transcript"]."""
    return state.get("transcript") or (state.get("intake") or {}).get("transcript") or []


def eligibility_agent(state: dict[str, Any]) -> dict[str, Any]:
    intake_raw = {k: v for k, v in (state.get("intake") or {}).items() if k != "transcript"}
    intake = IntakeData.model_validate(intake_raw)
    documents = [Document.model_validate(d) for d in state.get("documents") or []]
    transcript = [Utterance.model_validate(u) for u in _transcript(state)]

    result = evaluate(intake, documents, transcript, llm=get_llm_classifier())

    already = {escalation_key(e) for e in state.get("escalations") or []} - {None}
    current = [e.to_state() for e in result.escalations]
    new = [e for e in current if escalation_key(e) not in already]

    eligibility = result.model_dump(mode="json")
    eligibility["escalations"] = current      # this run's view (used for reask routing)
    return {"eligibility": eligibility, "escalations": new}


# Backwards-compatible name used in earlier versions / README.
eligibility_node = eligibility_agent
