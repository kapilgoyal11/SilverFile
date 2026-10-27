# Orchestrator and task management

Owner: David (dlr266). Specialization: Task Division & Management. Agent: Orchestrator.

Code: `src/agents/orchestrator.py`, `src/graph.py`, `run.py`, orchestrator fields in `src/state.py`.
Tests: `tests/test_orchestrator.py` (33 tests, no API key needed). Diagram: `docs/graph.png` (`docs/graph.mmd`).

## Topology and why

Hub and spoke. Every agent node returns to the orchestrator, which reads the shared
`SilverFileState` and picks the next hop. Two exceptions: Intake loops on itself one
question at a time until it is done, and Extraction fans out as one `Send` per document
with the hub as the fan-in.

Why this and not a fixed pipeline or peer-to-peer handoffs: the brief says the orchestrator
"decides which agent acts next; owns termination" and "may not itself extract, compute, or
converse with the user." A hub makes those constraints structural: agents never route
themselves, and the orchestrator does no agent work (it is a pure function of state, with
no LLM call). It also gives one place for the loop guards and one place for the dashboard
to read status from.

## Decision table (`decide` in orchestrator.py)

Rows are checked top to bottom; the first match wins. `tests/test_orchestrator.py` has one
test per row.

| # | Condition | Next node |
|---|---|---|
| 1 | `status` is `draft_ready` or `failed` | end |
| 2 | any open escalation (any agent, including eligibility re-ask items), or a guard fires | `human_review` |
| 3 | `intake_complete` is false | `intake_agent` |
| 4 | `document_files` has files not yet extracted | `extraction_agent`, one `Send` per file |
| 5 | no `eligibility`, or `recheck_eligibility` after a human review | `eligibility_agent` |
| 6 | no `provenance` | `computation_agent` |
| 7 | no `audit` | `review_agent` |
| 8 | audit verdict `approve` | `release_gate` |
| 9 | verdict `revise` and this round's send-back not yet dispatched | the owner in `send_back_to` |
| 10 | verdict `revise` and the owner has run | `review_agent` (audit again) |
| 11 | verdict `escalate`, everything resolved, under `MAX_REVIEW_ROUNDS` | `review_agent` |
| 12 | verdict `escalate`, everything resolved, at the bound | `release_gate` (human had the final say) |

## Task division

Work moves between agents through two shared structures:

- `escalations`: anything that needs a person. Any agent appends; `human_review` resolves
  in place by returning a copy with the same `id` and `resolved: True` (Karan's
  `merge_escalations` reducer replaces it). Review's escalations are given ids by the
  `review_agent` wrapper so they can be resolved the same way.
- `audit.send_back_to`: Review names the agent that must fix a finding. The orchestrator
  dispatches it once per review round (`dispatched_round`), then sends the return back to
  Review. Review never fixes anything itself.

Review's `route_to` values map to nodes as: `intake` → `intake_agent`, `eligibility_rules` →
`eligibility_agent`, `computation` → `computation_agent`, `document_extraction` →
`human_review` (a handled file is never re-extracted automatically, so a person re-uploads it).

## Guards (`guard_reason` in orchestrator.py)

| Code | Trigger | Bound |
|---|---|---|
| `loop_guard` | the last 6 hub hops repeat with period 1, 2 or 3 (a,a,a… / a,b,a,b… / a,b,c,a,b,c) | 6 hops |
| `max_hops` | total hub hops in one session | 60 |
| `reextract_requested` | Review asked for a document to be re-extracted | n/a |
| Review's own `MAX_ROUNDS` and `NO_PROGRESS` | review → revise cycle | 3 rounds |

Patterns containing `review_agent` are excluded from `loop_guard` because the review → revise
cycle is legitimate and Review bounds it itself. If a review stub never stops,
`test_review_that_never_approves_ends_with_a_human_not_a_spin` shows the orchestrator's
guards still catch it.

How a pathological cycle is detected and broken: the orchestrator appends
`{"from": "orchestrator", "to": <node>}` to `handoff_log` on every hop and inspects the
last six. When they repeat, it appends an escalation with `raised_by: "orchestrator"` and
routes to `human_review`, which pauses on `interrupt()`. On resume, `human_review` appends a
`{"from": "human_review", "to": "orchestrator"}` entry, which breaks the repeating pattern,
and the resolved escalation stops the same guard firing again. The required test case is
`test_loop_guard_breaks_a_pathological_handoff_cycle`: a Computation stub that writes
nothing produces six `computation_agent` hops, the guard fires, and "stop" ends the run.

## Human in the loop (brief 4.4)

`human_review` is the only `interrupt()` point. Every condition reaches it through
`escalations`: 1 out-of-scope and 3 box 2b (Eligibility), 2 document conflicts and
Review's findings (Review), 4 advice and 6 exploitation (Intake, and Eligibility from the
transcript), 5 filing-status ambiguity (Eligibility), plus Computation when it cannot
source a number, and the orchestrator's own guards.

Resume with either a string or the dashboard dict from `docs/ELIGIBILITY_HANDOFF.md` §5:

```python
graph.invoke(Command(resume="proceed: volunteer confirmed"), config)
graph.invoke(Command(resume="stop: send to a volunteer preparer"), config)   # status = failed
graph.invoke(Command(resume={"reviewer": "kushim-staff-1", "resolve_all": True,
                             "note": "...", "intake_patch": {"filing_status": "mfj"}}), config)
```

After a resolution the orchestrator re-runs Eligibility (`recheck_eligibility`), per Narain's
handoff note. Review findings resolved by a human are also written to `hitl_resolutions`, which
Review reads so it stops blocking on them.

## Release gate

`release_gate` runs Narain's `release_gate_from_state` just before `draft_ready`. If it
blocks, the violations go to a human as an escalation; once a human resolves that, the gate
passes with `accepted_by_human: True` recorded in `state["release_gate"]`.

## Memory and resumability (brief 4.3)

The graph compiles with Karan's checkpointer (`src/memory.get_checkpointer`, SQLite by
default). One `thread_id` per session (`thread_config(session_id)`); a session paused on
Tuesday resumes on Friday by invoking the same id. `graph.get_state(config)` returns where
it is waiting and the full state, which is what the dashboard lists.

## Changes to shared files

- `src/state.py`: added the keys Review writes (`audit`, `audit_history`, `review_round`,
  `needs_human_review`, `hitl_resolutions`, `intake_flags`). LangGraph silently drops any key a
  node returns that the schema does not declare, so without these the audit record never
  reached the state. Also added the orchestrator's `dispatched_round`, `recheck_eligibility`,
  `release_gate`.
- `run.py`: console runner; drives interrupts, seeds a session from a fixture, writes the final
  state to `traces/`.

## Known gaps for the team

1. Intake does not collect the taxpayer or spouse SSN yet, and Review requires it, so a live
   session goes Review → Intake → questions. `run.py --ssn` seeds it for fixture runs.
2. Intake has no path for Eligibility's `reask_user` items, so they go to a human with an
   `intake_patch` instead of back to the taxpayer.
3. The PDF form fill and the dashboard are not in the graph yet. The dashboard needs
   `graph.get_state(config)` for the list and `Command(resume=...)` for the resolve button.
