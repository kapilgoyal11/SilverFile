# Document Extraction agent

Owner: Rodrigo (Inter-agent communication). Code: `src/agents/extraction.py`, `src/tools/w2_reader.py`, `src/tools/ssn.py`. Tests: `tests/test_extraction.py`, `tests/test_ssn.py`.

Extraction turns the documents a taxpayer supplies into typed records. The W-2 is read from an image by Claude, and the other forms come in as JSON. It copies boxes as printed and never decides what a value means for the return. For example, Computation is the agent that decides W-2 box 1 goes on line 1z.

## Handoff contract

```
run.py / dashboard ──document_files──▶ router ──Send per file──▶ extraction_agent ×N
                                                                    │
                     documents (operator.add) ◀─────────────────────┤──▶ Eligibility, Computation, Review
                     escalations (any problem) ◀────────────────────┘──▶ Orchestrator → human
```

| State key | Who writes it | What Extraction does |
|---|---|---|
| `document_files` | run.py or dashboard | **reads** `[{"doc_id", "doc_type", "path"}]`. This is a new key in `src/state.py` |
| `documents` | Extraction | **appends** one entry per readable file |
| `escalations` | any agent | **appends** one entry per problem, with `"id": "extraction:<doc_id>:<reason>"` |
| `handoff_log` | any agent | **appends** one entry per file |

### A `documents` entry

```json
{"doc_id": "w2_1", "doc_type": "W-2", "source_file": "data/fixtures/w2_1.png",
 "status": "ok",
 "fields": {"box_1_wages": 18450, "box_2_withholding": 1210, "employee_ssn": "900-12-3456", "...": "..."},
 "field_provenance": {"box_1_wages": {"kind": "document", "document": "w2_1", "field": "box_1_wages",
     "raw_text": "18450.00", "confidence": 0.99, "model": "claude-opus-5-5", "source_sha256": "..."}},
 "issues": [], "source_sha256": "...", "model": "claude-opus-5-5", "extracted_by": "extraction"}
```

- `doc_id`, `doc_type`, `fields` and `source_file` are the shape Computation and Eligibility already read. The other keys are extra, and agents that don't need them can ignore them.
- Field names match `src/agents/computation.py`. For the W-2 these are `box_1_wages` and `box_2_withholding`, plus every other box (`box_3_ss_wages` … `box_14_other`, `box_12a_code`/`box_12a_amount`, `box_13_*` flags, `state_1_*`).
- Amounts are whole-dollar ints (the team rule). Cents are rounded half up, and the exact printed value is kept in `field_provenance[...]["rounded_from"]`.
- A blank box is `0`. A box that couldn't be read is **left out**, never guessed. Computation then blocks on the missing field instead of reading it as 0.
- `tests/test_extraction.py::test_computation_accepts_extraction_output` runs Extraction's output through `computation_agent` to prove the handoff works.

### When something goes wrong

| Situation | `documents` | `escalations` reason |
|---|---|---|
| Low-confidence or unreadable box | added, `status: needs_confirmation` | `extraction_needs_confirmation` (detail names the fields) |
| SSN outside 900-999 | **not added**, SSN dropped everywhere | `invalid_ssn` |
| Not a W-2, or JSON of a different form | not added | `wrong_document_type` |
| File missing, unsupported type, model error | not added | `extraction_failed` |

A file that already has a document entry or an extraction escalation is not sent again, so the Orchestrator can route to Extraction repeatedly without duplicates or loops.

## Wiring it into `src/graph.py` (Orchestrator owner)

```python
from src.agents.extraction import NODE, extraction_agent, make_document_router

builder.add_node(NODE, extraction_agent)                    # NODE == "extraction_agent"
builder.add_conditional_edges("orchestrator", make_document_router("orchestrator"))
builder.add_edge(NODE, "orchestrator")
```

`make_document_router(done)` returns one `Send` per file not yet handled, or `done` when there is nothing to extract.

## Running it

```
python -m src.agents.extraction data/fixtures/w2_1.png data/fixtures/int_1.json --offline
python -m src.agents.extraction data/fixtures/w2_1.png      # Claude reads the image; needs ANTHROPIC_API_KEY in .env
```

`python data/generate_fixtures.py` redraws the synthetic W-2s (`w2_1`, `w2_2`, and `w2_bad_ssn` with a deliberately invalid SSN) and their answer keys. Tests use the answer keys, so CI never calls a model.

## Open questions for the team

1. **`document_files`.** Is the new state key OK, and who fills it: `run.py`, the dashboard, or Intake once it knows the files?
2. **Re-extraction.** `documents` uses `operator.add`, so a second extraction of the same file would append a duplicate. If Review needs to send a document back, should `documents` switch to a reducer keyed by `doc_id`?
3. **LLM provider.** The W-2 reader uses Claude (`claude-opus-5-5`, override with `EXTRACTION_MODEL`). Another provider only needs one new reader class in `src/tools/w2_reader.py`.
