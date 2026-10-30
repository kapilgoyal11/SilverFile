# SilverFile review console (dashboard)

Owner: Nishitha Bodipati (nb726). Bonus item: HITL links in dashboard.

A web console for Kushim employees. It lists every SilverFile session, shows the
draft 1040-SR with the provenance of every amount, and lets a reviewer resolve a
human-in-the-loop event from the browser.

## Run it

```bash
python -m pip install -r requirements.txt -r dashboard/requirements.txt
python -m dashboard                 # open http://localhost:8000
```

It reads the same SQLite file as `run.py` (`silverfile_sessions.sqlite`, or
`$SILVERFILE_DB`), so sessions started from the console and from `run.py` show
up in the same queue, and either can resume the other's paused sessions.

| Variable | Default | Meaning |
|---|---|---|
| `SILVERFILE_DB` | `silverfile_sessions.sqlite` | checkpoint file |
| `SILVERFILE_OFFLINE` | on unless `ANTHROPIC_API_KEY` is a real `sk-ant-` key | use the saved answer key next to each W-2 image instead of reading the image |
| `SILVERFILE_DEMO` | off (on in Docker) | start the two eval cases when the database is empty |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | use `HOST=0.0.0.0` inside Docker |

## What a reviewer can do

- **Review queue**: every session with its status (awaiting review, awaiting
  taxpayer, draft ready, stopped), filing status, refund or amount owed and open
  items. Filter by status, search by name or session. Refreshes every 8 seconds.
- **Draft return**: the 1040-SR laid out by section. Select a line to trace it:
  formula, inputs, the document field or taxpayer answer it came from, and the
  audit result. Inputs that are other lines are clickable, so a reviewer can walk
  line 34 back through 33 and 25d to the W-2.
- **Review (HITL)**: each open escalation with who raised it, the rule and the
  taxpayer's own words. Resolve selected items, resolve all, or stop the session,
  with a reviewer name and a note for the record. If Intake is waiting on the
  taxpayer, the question is shown and staff can answer on their behalf.
- **Audit, Documents, Activity**: the Review & Audit verdict and findings, the
  extracted documents (SSNs masked), and every hop the orchestrator made.
- **New session**: run any prepared case in `data/fixtures/eligibility/` with
  any fixture documents. Only 900-series test SSNs are accepted.

## How it plugs in (nothing in the team's code changed)

The console only calls the team's public pieces:

- `src.graph.build_graph()` and `src.memory.get_checkpointer()` / `thread_config()`
- `graph.get_state()` to read a session, and
  `graph.invoke(Command(resume=...))` to resume it with the dashboard dict that
  `human_review()` in `src/agents/orchestrator.py` already accepts
  (`docs/ELIGIBILITY_HANDOFF.md` section 5):
  - `{"reviewer", "resolve_all": True, "note"}`
  - `{"reviewer", "resolve": [{"reason", "source", "note"}]}`
  - `{"reviewer", "action": "stop", "note"}`

Backend: FastAPI (`dashboard/app.py`). Frontend: hand-written HTML, CSS and
JavaScript in `dashboard/static/` with no build step. Fonts (Public Sans and
Libre Caslon Text, SIL Open Font License) are bundled, so the console needs no
internet access.

API: `GET /api/sessions`, `GET /api/sessions/{id}`,
`POST /api/sessions/{id}/resolve`, `POST /api/sessions/{id}/answer`,
`POST /api/sessions`, `GET /api/fixtures`. Interactive docs at `/api/docs`.

## Tests

`tests/test_dashboard.py` (7 tests, no API key) covers a clean run, resolving a
HITL event end to end, resolving selected items, stopping a session, the test-SSN
rule and the error cases. They are skipped when the dashboard extras are not
installed, so the core suite is unaffected.
