# Intake + Memory: what other agents can rely on

Owner: Karan. Ask before renaming any of these.

## 1. Plugging Intake into the graph (David)

```python
from src.agents.intake import intake_node, route_after_intake
builder.add_node("intake", intake_node)
builder.add_conditional_edges("intake", route_after_intake,
    {"intake": "intake", "human_review": "human_review", "done": "orchestrator"})
```

- Intake asks **one question each time the node runs**, using `interrupt()`.
  - The payload is `{"type": "intake_question", "slot": ..., "text": ..., "session_id": ...}`.
  - The UI or dashboard shows `text` and continues with `graph.invoke(Command(resume="<reply>"), config)`.
- When it's done, Intake sets `intake_complete = True` and `next_agent = "orchestrator"`, and writes the plain answers to `state["intake"]` (section 2).
- If an escalation with `blocking: True` is open, Intake asks nothing and routes to `human_review`.
  - `src/intake_graph.py` has a placeholder `human_review_node` so this can be demoed now. Replace it with the real one.
  - To resolve, write the same escalation back with `resolved: True` and `resolution: "<note>"`, keeping the same `id`.
- Save/resume uses `src.memory.get_checkpointer(db_path)`, with **one `thread_id` per taxpayer session**. Use it when compiling the main graph.

## 2. `state["intake"]` (written when intake completes)

| Key | Type | Notes |
|---|---|---|
| `filing_status` | `single` / `mfj` / `mfs` / `hoh` / `qss`, or `None` | `None` = the user wasn't sure (also listed in `unsure`) |
| `entered_by` | str | `"taxpayer"` or `"helper (<relationship>)"` |
| `taxpayer_name`, `birth_date`, `is_blind` | str, `"YYYY-MM-DD"`, bool | |
| `claimable_as_dependent`, `full_year_resident` | bool | Raw facts for **Rules** (standard-deduction chart exceptions) |
| `married_end_of_year`, `spouse_died_recent`, `spouse_death_year` | bool, bool, int | `spouse_death_year == 2025` is the filing-status ambiguity case for **Rules** |
| `spouse_name` | str | `mfj` / `mfs` only, else `None` |
| `spouse_birth_date`, `spouse_is_blind` | str, bool | `mfj` only |
| `spouse_itemizes`, `lived_apart_all_year` | bool | `mfs` only |
| `dependents` | list of `{name, relationship, ssn}` | SSNs are always 900–999 test numbers |
| `document_inventory` | `{"W-2": n, "SSA-1099": n, "1099-R": n, "1099-INT": n, "1099-DIV": n}` | How many documents **Extraction** should expect |
| `box2b_checked`, `other_income`, `other_income_detail`, `wants_itemize` | bool / str | Raw facts for **Rules** (out-of-scope checks) |
| `estimated_payments` | int | Line 26 |
| `adjustments_10` | int | Line 10 (Schedule 1, line 26) |
| `adjustments_ss_worksheet` | int | Schedule 1 lines 11–20, 23, 25. Asked only when there are adjustments **and** an SSA-1099 |
| `schedule_2_line_3`, `other_taxes_23`, `credits_21`, `other_payments_32` | int | From Schedule 2 line 3, Schedule 2 line 21, Schedule 3 line 8, and Schedule 3 line 15. **Kapil, please check this mapping.** |
| `unsure` | list of question names | The user said "not sure" to these. Each has a `user_unsure` escalation. |
| `volunteer_questions` | list of str | Advice questions noted for the human preparer |

**Dollar keys follow one rule:**

- If the user gave an amount, the key holds that amount.
- If they said they have none, the key is `0`.
- If they **weren't sure**, the key is `None`. Computation then blocks with `invalid_input` instead of silently using 0. There's a test for this.

**Where the detail lives.** For the full record of each answer, read `state["answers"][<question>]`. It holds:

- the value
- the taxpayer's own words (SSN removed)
- `entered_by`
- `entered_at`
- `status` (`answered`, `unsure` or `approximate`)
- `history` of earlier versions

Review can use `history` to see that a value changed during the conversation.

## 3. Escalations Intake raises

Intake uses the shared format (`raised_by`, `reason`, `detail`, `resolved`), plus `id`, `blocking`, `slot` and `raised_at`.

| `reason` | Blocking | When |
|---|---|---|
| `possible_exploitation` | **yes** | A helper says they have no legal authority, or the user mentions pressure or an account they don't recognise |
| `tax_advice_request` | no | "Should I…", "Would I pay less…", "Can I deduct…". The question is also copied to `volunteer_notes` |
| `user_unsure` | no | "I'm not sure", an approximate amount, or unsure at the confirmation step |
| `repeated_parse_failure` | no | Three replies in a row Intake couldn't understand |

Non-blocking ones still need a human before the draft is final. That's the Orchestrator's call.

## 4. Small changes I made to shared files

- `src/state.py`
  - Added Intake's fields: `answers`, `transcript`, `intake_progress`, `intake_complete`, `volunteer_notes`, `token_log`.
  - Added the `merge_answers` and `merge_escalations` reducers.
  - `escalations` now uses `merge_escalations`. It still appends exactly like `operator.add`. The only new behaviour is that an entry with a matching `id` replaces the old one, which is how resolving works. All existing tests pass unchanged.
- `requirements.txt`: added `langgraph-checkpoint-sqlite` (save/resume) and `tiktoken` (token counts).
- `.gitignore`: added `*.sqlite`, the session database.

## 5. Context strategy for your own agents (§4.5)

```python
from src.memory import apply_context_strategy
msgs = apply_context_strategy(msgs, summary="<short summary of decisions so far>")
```

This respects the `--no-context-strategy` flag, which is passed through as the env var `SILVERFILE_CONTEXT_STRATEGY=off`. `run.py` should set it. Results for Intake are in `docs/metrics.md`.

## 6. Security hooks (Abdullah)

`src/security_hooks.py` is a placeholder for the SSN policy. If you replace it, keep these four functions: `find_ssns`, `redact_ssns`, `validate_test_ssn`, `mask_ssn`.
