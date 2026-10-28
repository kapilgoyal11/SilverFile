# SilverFile

A LangGraph multi-agent system that helps prepare a **draft** Form 1040-SR (tax year 2025) for seniors, built for Kushim as a proof of concept. It collects information, reads a W-2 image, computes the derived lines, and hands a human a draft return plus an audit record showing where every dollar figure came from.

SilverFile **never files** a return, **never gives tax advice**, and **never states a dollar amount it cannot source**. Anything out of scope, any advice request, and any sign of financial exploitation goes to a human.

## Status

| Part | State |
|---|---|
| Shared state schema (`src/state.py`) | Done (proposal, being reviewed by the team) |
| Provenance records (`src/tools/provenance.py`) | Done |
| Tax calculation tools (`src/tools/tax_math.py`) | Done, tested against IRS tables and worked examples |
| Computation agent (`src/agents/computation.py`) | Done |
| Intake agent and agent memory (`src/agents/intake.py`, `src/memory.py`) | Done, see `docs/INTAKE_INTERFACE.md` |
| Orchestrator and graph (`src/agents/orchestrator.py`, `src/graph.py`, `run.py`) | Done, see `docs/ORCHESTRATION.md` |
| Extraction, Eligibility, Review | Merged; wired into the graph |
| Dashboard, PDF form, demo cases, traces | Not yet merged |

## Setup

Tested on macOS with Python 3.13.7. Python 3.11 or newer should work but has not been tested.

```
git clone https://github.com/kapilgoyal11/SilverFile.git
cd SilverFile
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your own key once the team has chosen an LLM provider. Never commit `.env`.

## Run the tests

```
python -m pytest -q
```

## Try the Intake agent on its own

```
python run_intake.py --session test          # chat in the terminal; type quit to stop
python run_intake.py --session test          # run again later: resumes where you left off
python run_intake.py --session test --show   # print what intake collected
python -m scripts.measure_context --both     # regenerate docs/metrics.md
```

## Repository layout

```
src/state.py          shared state every agent reads and writes
src/graph.py          LangGraph wiring (orchestration)
src/agents/           one file per agent
src/tools/            deterministic tools: tax_math.py, tax_constants.py, provenance.py
data/                 fixtures and reference policy
evals/  tests/        evaluation cases and pytest suites
traces/  docs/  demo/ run traces, notes and metrics, demo material
```

## Team and ownership

| Agent | Specialization | Owner |
|---|---|---|
| Orchestrator | Task division and management | David |
| Intake | Agent memory | Karan |
| Document Extraction | Inter-agent communication | Rodrigo |
| Eligibility & Rules | Guardrails and ethics | Narain |
| Computation | MLOps/DevOps | Kapil |
| Review / Audit | Security | Unassigned |

## Shared contracts

- **Amounts are whole-dollar integers.** Fixture W-2s must use whole dollars.
- **Filing status strings** are exactly `single`, `mfj`, `mfs`, `hoh`, `qss`.
- **Every dollar amount carries a provenance record** with kind `document`, `computed` or `user_input`. Computed records hold a formula and the inputs it used.
- **What Computation reads and writes** is documented at the top of `src/agents/computation.py`. If it cannot source a number, it returns an escalation and no numbers.
- **SSNs**: only fake 900-series numbers may be accepted (not yet implemented).

## Tax sources and known assumptions

All 2025 constants are in `src/tools/tax_constants.py` with their sources: IRS Publication 915 (taxable Social Security), IRS Publication 1040 (Tax Table and Tax Computation Worksheet), and the assignment brief (standard and senior deductions).

Assumptions still to be confirmed with the instructor:

1. Line 16 follows the IRS method: Tax Table under $100,000, Tax Computation Worksheet from $100,000. The brief says "rate schedule".
2. A return with qualified dividends (line 3a above zero) is escalated, because the IRS uses a worksheet we have not built.
3. Rounding: only the final amount of the Social Security worksheet is rounded; the senior deduction rounds line 34 to a whole dollar.
4. The Social Security worksheet needs the Schedule 1 adjustments broken down by line, not just the line 26 total.
5. The structure of Form 1040-SR lines 17 to 23 comes from the 2024 layout and needs checking against the 2025 PDF.

## Orchestration

Hub and spoke. Every agent returns to the orchestrator, which reads the shared state and picks the next hop from a decision table (no LLM call). Extraction fans out as one `Send` per document and the hub is the fan-in. Review's send-backs are dispatched to the agent that owns the finding, once per round, and a human is pulled in through one `interrupt()` node for every escalation. Full design, decision table and loop guards: `docs/ORCHESTRATION.md`.

![Orchestration graph](docs/graph.png)

## Running the full system

```
python run.py --session demo-1 --state data/fixtures/eligibility/clean_single.json --ssn 900-12-3456
python run.py --session demo-2 --state data/fixtures/eligibility/hitl_advice.json --ssn 900-12-3456 --spouse-ssn 900-98-7654
python run.py --session demo-3 --state data/fixtures/eligibility/clean_single.json --ssn 900-12-3456 \
    --doc data/fixtures/w2_1.png --doc data/fixtures/int_1.json --offline
python run.py --session demo-4                       # full intake conversation from scratch
python run.py --session demo-2 --resume              # continue a paused session
```

`--state` seeds a finished intake so the run starts at Eligibility. When the graph pauses it prints why and waits: intake questions take a plain answer, human review takes `proceed <note>` or `stop <note>`. Sessions are checkpointed in SQLite (`silverfile_sessions.sqlite` or `$SILVERFILE_DB`) and resume by `--session` id. The final state is written to `traces/<session>_state.json`.