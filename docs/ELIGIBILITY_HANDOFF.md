# Handoff: Eligibility & Rules agent → team graph

From: Narain (Eligibility & Rules · Guardrails & Ethics)
To: David (Orchestrator). Sections 4–5 are also for the Dashboard owner.

Working reference: `src/eligibility_standalone_graph.py`, tested in `tests/test_eligibility_hitl.py`.
Copy from it freely.

---

## 1. The node

```python
from src.agents.eligibility import eligibility_agent
builder.add_node("eligibility_agent", eligibility_agent)
```

| | |
|---|---|
| Reads | `intake`, `documents`, `transcript` (or `intake["transcript"]`), `escalations` |
| Writes | `eligibility` (merge_dicts), `escalations` (only new ones; operator.add) |
| Calls `interrupt()`? | **No.** It raises flags; the graph routes. |
| Safe to re-run? | **Yes.** It never re-adds an escalation already in state, resolved or not. |
| Document order | Doesn't matter. |
| API key needed? | No. |

**`state["eligibility"]`** includes everything on the team checklist plus `state.py`:
`in_scope`, `boxes_checked`, `senior_taxpayer`, `senior_spouse`,
`eligible_1040sr`, `age_box_count`, `blind_box_count`, `schedules`, `citations`.
It also includes `std_deduction_lookup`, `schedule_1a_qualifiers`, and
`escalations` (this run's view, used for re-ask routing).

**Escalations I append** (team format, plus extra keys):
```json
{"raised_by": "eligibility", "reason": "undetermined_taxable_1099r:R6.9",
 "detail": "This 1099-R says the taxable amount was not determined (box 2b)...",
 "resolved": false,
 "code": "UNDETERMINED_TAXABLE_1099R", "rule_id": "R6.9", "source": "documents.r_2",
 "action": "human_review", "detected_by": "rule", "evidence": null}
```
`action` is `"human_review"` (a person decides) or `"reask_user"` (Intake asks
again, e.g. missing birth date or a non-test SSN).

## 2. When to run it

- after Extraction's aggregation step (all documents in),
- after every `human_review` (re-check with the human's decision),
- whenever Review sends work back and intake or documents changed.

## 3. Routing after it

```python
from src.agents.eligibility_schemas import open_escalations

MAX_HUMAN_REVIEW_ROUNDS = 3


def human_rounds(state):
    return sum(1 for h in state.get("handoff_log") or [] if h.get("from") == "human_review")


def route_after_eligibility(state):
    if human_rounds(state) >= MAX_HUMAN_REVIEW_ROUNDS:
        return "supervisor"                              # loop guard
    if open_escalations(state.get("escalations") or []):
        return "human_review"
    current = (state.get("eligibility") or {}).get("escalations") or []
    if any(e.get("action") == "reask_user" for e in current):
        return "intake_agent"
    return "computation_agent"


builder.add_conditional_edges("eligibility_agent", route_after_eligibility,
    ["human_review", "intake_agent", "computation_agent", "supervisor"])
```

`open_escalations()` works for **every** agent's escalations, not just mine.
Others count as `human_review`.

## 4. Resolving: append, don't edit

`state["escalations"]` uses `operator.add`, so an entry can't be flipped to
`resolved: True` in place. Instead, **append a resolved copy**:

```python
from src.agents.eligibility_schemas import resolve
resolve(escalation, reviewer="kushim-staff-1", note="...")
# -> {...same fields..., "resolved": True, "resolved_by": "...", "note": "..."}
```

`open_escalations()` treats an escalation as closed once a resolved copy exists.

## 5. Human-review node (owns the interrupt)

```python
from langgraph.types import interrupt
from src.agents.eligibility_schemas import escalation_identity, open_escalations, resolve


def human_review(state):
    pending = open_escalations(state.get("escalations") or [])
    decision = interrupt({"message": "Human review needed.", "escalations": pending})
    reviewer = decision.get("reviewer", "unknown")
    if decision.get("resolve_all"):
        chosen = [(e, decision.get("note", "")) for e in pending]
    else:
        wanted = {(r["reason"], r.get("source")): r.get("note", "") for r in decision.get("resolve", [])}
        chosen = [(e, wanted[(e["reason"], e.get("source"))]) for e in pending
                  if (e["reason"], e.get("source")) in wanted]
    update = {
        "escalations": [resolve(e, reviewer, note) for e, note in chosen],
        "handoff_log": [{"from": "human_review", "to": "eligibility_agent",
                         "resolved": [list(escalation_identity(e)) for e, _ in chosen]}],
    }
    if decision.get("intake_patch"):
        update["intake"] = decision["intake_patch"]      # merge_dicts merges it
    return update


builder.add_node("human_review", human_review)
builder.add_edge("human_review", "eligibility_agent")
```

Compile with a checkpointer and run with a `thread_id`, or `interrupt()` can't pause.

**Dashboard "resolve" button** sends:
```python
graph.invoke(Command(resume={
    "reviewer": "kushim-staff-1",
    "resolve": [{"reason": "filing_status_ambiguous:R2.3",
                 "source": "intake.spouse.date_of_death",
                 "note": "Volunteer confirmed MFJ for year of death."}],
    # or: "resolve_all": True, "note": "..."
    "intake_patch": {}                                    # optional
}), {"configurable": {"thread_id": session_id}})
```
Show employees `detail` and `evidence` (plain English, SSNs already redacted).

## 6. Release gate (just before the draft is marked ready)

```python
from src.agents.eligibility_guardrails import release_gate_from_state


def release_gate(state):
    gate = release_gate_from_state(state)
    return {"status": "draft_ready" if gate.passed else "needs_human",
            "handoff_log": [{"from": "release_gate", "passed": gate.passed,
                             "violations": gate.violations}]}
```

It blocks if:
- **R11.1** a `provenance` record has an amount but no source or calculation,
- **R11.2** any escalation from any agent is unresolved,
- **R11.3** a non-test SSN is in intake,
- **R11.4** an assistant message gave advice,
- **R11.5** status is `filed`.

## 7. Checklist

- [ ] `eligibility_agent` node + conditional routing out of it
- [ ] `human_review` node with `interrupt()`, edge back to `eligibility_agent`
- [ ] Resolution by appending (`resolve()`), not editing
- [ ] Loop guard on `handoff_log`, with a test
- [ ] Checkpointer + `thread_id`
- [ ] Release gate before `draft_ready`
- [ ] Dashboard resume format matches section 5
