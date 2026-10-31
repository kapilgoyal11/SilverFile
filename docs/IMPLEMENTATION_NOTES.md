# Implementation notes

Where each part of SilverFile is documented, and the decisions that shaped the build.

## Design documents

| Part | Code | Notes |
|---|---|---|
| Orchestrator and graph | `src/agents/orchestrator.py`, `src/graph.py`, `run.py` | `docs/ORCHESTRATION.md` (decision table, loop guards, human review) |
| Intake and agent memory | `src/agents/intake.py`, `src/memory.py` | `docs/INTAKE_AND_MEMORY.md`, `docs/INTAKE_INTERFACE.md` |
| Document Extraction | `src/agents/extraction.py`, `src/tools/w2_reader.py` | `docs/EXTRACTION.md` |
| Eligibility & Rules | `src/agents/eligibility*.py`, `data/policy.md` | `docs/ELIGIBILITY_HANDOFF.md` |
| Computation and tax tools | `src/agents/computation.py`, `src/tools/tax_math.py`, `src/tools/tax_constants.py` | docstrings at the top of each file |
| Review & Audit | `src/agents/review_audit/`, `src/tools/audit_adapter.py` | `docs/review_audit_contract.md` |
| Review console and filled 1040-SR PDF | `dashboard/` | `dashboard/README.md` |
| Context-window strategy | `src/memory.py`, `scripts/measure_context.py` | `docs/metrics.md` |
| Running and evaluating | `Dockerfile`, `docker-compose.yml` | `HOW_TO_RUN.md`, `demo/eval_case_1.md`, `demo/eval_case_2.md` |

## Key decisions

- **Hub and spoke.** Every agent returns to the Orchestrator, which picks the next node from a fixed decision table with no model call, so the same state always gives the same route.
- **One shared state with reducers.** Parallel extraction branches append documents, Intake's answer ledger keeps earlier versions of changed answers, and an escalation is resolved by replacing it with a copy that has the same id.
- **One human-in-the-loop point.** Every escalation goes to `human_review`, which pauses with `interrupt()`. A SQLite checkpointer lets a session resume later by its id.
- **Deterministic arithmetic.** Computation only maps document boxes to lines and calls the functions in `src/tools/tax_math.py`. Every amount carries a provenance record (document, computed or user input).
- **Independent audit.** Review & Audit recomputes the key lines with its own code, so it does not repeat Computation's mistakes, and its verdict is deterministic.
- **Offline by default.** Without an Anthropic API key, W-2 images are not read; their saved answer keys in `data/fixtures/` are used instead, so tests and demos never call a model.
- **Test SSNs only.** Only 900 to 999 SSNs are accepted, and SSNs are redacted from transcripts and logs and never sent to a model.

## Known gaps

- Intake does not collect the filers' own SSNs, which Review needs; prepared cases supply them with `--ssn` or through the console.
- Line 16 comes from the IRS Tax Table, while Review checks it against the rate schedule with a $1 tolerance.
- The layout of lines 17 to 23 follows the 2024 form and still needs checking against the 2025 form.
- The filled 1040-SR PDF is drawn in our own layout; it does not fill the official IRS PDF.
