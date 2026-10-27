# Review / Audit agent — handoff contract

Owner: Security specialization. Code: `src/agents/review_audit/`. Tests: `tests/test_review_audit.py`.

The audit agent adversarially checks the assembled return before any human sees it
(spec §4). It **never edits the return**: it records findings and sends work back to the
agent that has to fix it, or escalates to a human. It is fully deterministic — no LLM
call, no API key — so the same return always gets the same verdict.

## 1. What the audit agent reads from the shared state

Proposed key names. If the team settles on different names, only `checks.py` needs to change.

| Key | Written by | Shape |
|---|---|---|
| `case_id` | Orchestrator | `"eval-1"` |
| `filing_status` | Intake / Eligibility | `"single" \| "mfj" \| "mfs" \| "hoh" \| "qss"` |
| `taxpayer`, `spouse` | Intake | `{first_name, last_name, ssn, dob: "YYYY-MM-DD", blind, can_be_claimed_as_dependent, dual_status_alien, itemizes_separately}` |
| `dependents` | Intake | list of `{first_name, last_name, ssn, relationship}` |
| `line_12d` | Eligibility & Rules | `{taxpayer_65, taxpayer_blind, spouse_65, spouse_blind}` (booleans) |
| `documents` | Document Extraction | list of `{doc_id, type, fields, confidence, extraction_attempts}` |
| `return_lines` | Computation | `{"1a": {"value": 52000, "provenance": {...}}, ...}` |
| `intake_flags` | Intake / Guardrails | see §3 |
| `mfs_lived_apart_all_year` | Intake | boolean |
| `hitl_resolutions` | Dashboard (HITL) | list of `{finding_key, decision, resolved_by, resolved_at}` |
| `review_round`, `audit_history` | **this agent** | see §4 |

### Document field names

| type | fields used |
|---|---|
| `W-2` | `box1` wages, `box2` fed. withheld, `box3`, `box4`, `box5`, `box6`, `employee_ssn`, `employee_name`, `employer_name`, `employer_ein` |
| `1099-INT` | `box1`, `box3` (→ 2b), `box8` (→ 2a), `box4` withheld |
| `1099-DIV` | `box1a` (→ 3b), `box1b` (→ 3a), `box4` withheld |
| `1099-R` | `box1`, `box2a`, `box2b_not_determined` (bool), `box4`, `box7` code, `ira_sep_simple` (bool), `qcd_amount` |
| `SSA-1099` | `box5` net benefits (→ 6a), `box6` voluntary withholding, `recipient_tin` |

### Provenance record (one per monetary line — spec §3.3 / §4.2)

```json
{"kind": "document",   "doc_id": "w2-1", "field": "box1"}
{"kind": "computed",   "tool": "tax_math.add", "inputs": ["line:1a", "doc:w2-1.box1"], "formula": "1a + ..."}
{"kind": "user_input", "entered_by": "taxpayer", "entered_at": "2026-10-20"}
{"kind": "rule",       "source": "Standard Deduction Chart", "citation": "policy.md#standard-deduction"}
```

`computed` **must** name the deterministic MCP tool (Computation may not do arithmetic itself).

## 2. What the audit agent checks

1. **Provenance** — every non-zero line has a record; document refs exist and match the document value; computed lines name a tool; user inputs have who + when.
2. **Recomputation** — 1z, 9, 11a/b, 14, 15, 16 (2025 rate schedule), 18, 21, 22, 24, 25d, 32, 33, 34/37, recomputed independently of the Computation agent's code.
3. **Document totals** — 1a/25a from W-2s, 2a/2b, 3a/3b, 4a/5a, 6a, 25b from the 1099s.
4. **The four rules in spec §3.2** — 1040-SR eligibility, line 12d boxes vs date of birth, standard deduction chart, $6,000 senior deduction with 6% phase-out (and the 12e/13b conflation), taxable Social Security worksheet.
5. **Contradictions** — negative amounts, 3a > 3b, 4b > 4a, 5b > 5a, 6b > 85% of 6a, refund and balance due together.
6. **Scope** — any §3.3 out-of-scope item, 1099-R box 2b, line 7a / 13a, plus HITL conditions 2, 4, 5, 6 from §4.4.
7. **Security** — every SSN must be 900–999; documents must belong to the filers; free text from W-2 images is screened for prompt injection; low-confidence extraction goes back to Extraction.

## 3. `intake_flags` keys

`itemized_deductions`, `self_employment`, `rental_income`, `capital_gains_schedule_d`, `digital_assets`,
`foreign_income_or_accounts`, `amended_or_prior_year`, `state_return`, `user_document_conflicts` (list of strings),
`advice_requested`, `spouse_died_in_tax_year`, `third_party_answering_without_authority`,
`direct_deposit_requested`, `refund_account_described_by_taxpayer`.

## 4. What the audit agent writes

```python
{
  "audit": {
    "case_id": "eval-1", "round": 1,
    "verdict": "approve" | "revise" | "escalate",
    "send_back_to": ["computation"],          # only when verdict == "revise", upstream agent first
    "findings": [{"key": "LINE_MISMATCH:9", "code": "LINE_MISMATCH", "severity": "error",
                  "route_to": "computation", "line": "9", "expected": "72400", "actual": "72500",
                  "message": "Line 9 should be 72400 (...) but the return shows 72500.",
                  "escalation_condition": null}],
    "resolved_by_human": [...],
    "line_status": {"9": "failed", "1a": "ok", ...},   # for the dashboard
    "checks_run": 136, "return_fingerprint": "<sha256>", "loop_guard": null, ...
  },
  "audit_history": [ {...one entry per round...} ],   # reducer: operator.add
  "escalations":   [ {...one per human finding...} ], # reducer: operator.add
  "review_round": 1,
  "needs_human_review": false
}
```

All PII in `audit` is masked (`XXX-XX-4567`, `****6789`).

## 5. Wiring (Orchestrator)

```python
from src.agents.review_audit import review_audit_node, route_after_audit

graph.add_node("review_audit", review_audit_node)
graph.add_conditional_edges("review_audit", route_after_audit, {
    "intake": "intake", "document_extraction": "document_extraction",
    "eligibility_rules": "eligibility_rules", "computation": "computation",
    "human": "human_review",          # calls langgraph interrupt()
    "approve": "generate_draft",
})
```

* **Bounded loop:** `MAX_REVIEW_ROUNDS = 3`. Round 3 with errors still open → escalate (`loop_guard = "MAX_ROUNDS"`).
* **Loop guard:** if a revision returns the exact same errors as the previous round → escalate immediately (`loop_guard = "NO_PROGRESS"`). `detect_handoff_cycle(handoff_log)` is also provided for the Orchestrator to catch A↔B ping-pong elsewhere in the graph. Both are covered by tests.
* **HITL resolution:** the dashboard appends `{finding_key, decision, resolved_by, resolved_at}` to `hitl_resolutions` and routes back to `review_audit`; a resolved finding no longer blocks and is listed under `resolved_by_human`.
* **Tamper check:** before generating the PDF call `approval_still_valid(state)`; it is `False` if anything changed after the audit approved it.

## 6. Points for the team to confirm

1. **Senior deduction and MFS:** the law says a married person must file jointly to take the $6,000 deduction; the spec does not mention it. The audit raises a *warning* routed to Eligibility & Rules rather than an error.
2. **Line 16:** checked against the 2025 *rate schedule* (spec §3.1), with a $1 rounding tolerance. If Computation uses the IRS tax table instead, results can differ by a few dollars and will be flagged.
3. **Field names** in §1 are a proposal; agree on them with Extraction and Computation.

## 7. Commands

```bash
python -m pytest tests/test_review_audit.py -q
python -m src.agents.review_audit check-fixtures data/fixtures   # fails if any non-900-series SSN
python -m src.agents.review_audit audit path/to/state.json        # prints the audit record
```
