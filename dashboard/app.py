"""
SilverFile review console: the Kushim staff dashboard.

    python -m dashboard            # then open http://localhost:8000

It lists every session saved by the team's checkpointer, shows each draft
1040-SR line with its provenance record, and lets a Kushim employee resolve a
human-in-the-loop event (resume the paused graph) from the browser.

The dashboard only *uses* the team's code: build_graph(), get_checkpointer(),
thread_config() and the human_review resume contract in
src/agents/orchestrator.py (docs/ELIGIBILITY_HANDOFF.md section 5). It does not
change any agent, the state schema or the graph.

Environment:
    SILVERFILE_DB          SQLite checkpoint file (same default as run.py)
    SILVERFILE_OFFLINE=1   read W-2 images from fixture answer keys (no API key)
                           (automatic when ANTHROPIC_API_KEY is not set)
    SILVERFILE_DEMO=1      start the two eval cases if the database is empty
    PORT                   default 8000
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # the team's fixtures and extraction use repo-relative paths

try:  # same .env handling as the rest of the project, if python-dotenv is present
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.agents.extraction import NODE as EXTRACTION, make_extraction_agent
from src.graph import build_graph
from src.memory import get_checkpointer, thread_config

STATIC = Path(__file__).parent / "static"
FIXTURE_DIR = ROOT / "data" / "fixtures"
SCENARIO_DIR = FIXTURE_DIR / "eligibility"
TEST_SSN = re.compile(r"^9\d{2}-?\d{2}-?\d{4}$")

LINE_LABELS = {
    "1a": "W-2 box 1 wages", "1z": "Total wages", "2a": "Tax-exempt interest", "2b": "Taxable interest",
    "3a": "Qualified dividends", "3b": "Ordinary dividends", "4a": "IRA distributions",
    "4b": "IRA distributions, taxable", "5a": "Pensions and annuities", "5b": "Pensions, taxable",
    "6a": "Social Security benefits", "6b": "Social Security, taxable", "7a": "Capital gain or loss",
    "8": "Additional income (Schedule 1)", "9": "Total income", "10": "Adjustments to income",
    "11a": "Adjusted gross income", "11b": "Adjusted gross income", "12e": "Standard deduction",
    "13a": "Qualified business income deduction", "13b": "Additional deductions (Schedule 1-A)",
    "14": "Total deductions", "15": "Taxable income", "16": "Tax", "17": "Schedule 2, line 3",
    "18": "Lines 16 + 17", "19": "Child tax credit", "20": "Schedule 3, line 8", "21": "Lines 19 + 20",
    "22": "Line 18 minus line 21", "23": "Other taxes", "24": "Total tax",
    "25a": "Withheld: Forms W-2", "25b": "Withheld: Forms 1099", "25c": "Withheld: other forms",
    "25d": "Total federal tax withheld", "26": "Estimated tax payments", "32": "Total other payments",
    "33": "Total payments", "34": "Overpayment", "35a": "Refunded to you", "37": "Amount you owe",
}

STATUS_LABELS = {
    "needs_review": "Awaiting review", "awaiting_taxpayer": "Awaiting taxpayer",
    "draft_ready": "Draft ready", "failed": "Stopped", "in_progress": "In progress",
}


def _line_key(line: str):
    m = re.match(r"(\d+)([a-z]?)", line)
    return (int(m.group(1)), m.group(2)) if m else (999, line)


def _offline() -> bool:
    flag = os.environ.get("SILVERFILE_OFFLINE")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes")
    return not os.environ.get("ANTHROPIC_API_KEY")


class Console:
    """One graph and one checkpointer for the whole app. LangGraph's SQLite saver
    shares one connection, so every graph call goes through a lock."""

    def __init__(self, db_path: Optional[str] = None):
        agents = {}
        if _offline():
            from src.tools.w2_reader import FixtureReader
            agents[EXTRACTION] = make_extraction_agent(FixtureReader())
        self.checkpointer = get_checkpointer(db_path)
        self.graph = build_graph(self.checkpointer, agents)
        self.lock = threading.Lock()

    # ---------- reading ----------
    def thread_ids(self) -> list[str]:
        try:
            rows = self.checkpointer.conn.execute(
                "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id").fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]

    def snapshot(self, session_id: str):
        snap = self.graph.get_state(thread_config(session_id))
        if not snap or not snap.values:
            return None
        return snap

    @staticmethod
    def waiting_on(snap) -> tuple[str, Optional[dict]]:
        payload = snap.interrupts[0].value if snap.interrupts else None
        values = snap.values
        if payload is not None:
            if isinstance(payload, dict) and payload.get("type") == "intake_question":
                return "awaiting_taxpayer", payload
            return "needs_review", payload
        status = values.get("status") or "in_progress"
        if status == "needs_human":
            status = "needs_review"
        return status, None

    def summary(self, session_id: str) -> Optional[dict]:
        snap = self.snapshot(session_id)
        if snap is None:
            return None
        v = snap.values
        state, payload = self.waiting_on(snap)
        prov = v.get("provenance") or {}
        intake = v.get("intake") or {}
        open_esc = [e for e in v.get("escalations") or [] if isinstance(e, dict) and not e.get("resolved")]
        hops = [e for e in v.get("handoff_log") or [] if isinstance(e, dict) and e.get("from") == "orchestrator"]
        return {
            "id": session_id,
            "taxpayer": intake.get("taxpayer_name") or "Unnamed taxpayer",
            "filing_status": intake.get("filing_status") or (v.get("eligibility") or {}).get("filing_status"),
            "status": state,
            "status_label": STATUS_LABELS.get(state, state.replace("_", " ").title()),
            "refund": (prov.get("34") or {}).get("amount"),
            "owed": (prov.get("37") or {}).get("amount"),
            "agi": (prov.get("11a") or {}).get("amount"),
            "open_escalations": len(payload.get("escalations", [])) if isinstance(payload, dict) and payload.get("escalations") is not None else len(open_esc),
            "documents": len(v.get("documents") or []),
            "hops": len(hops),
            "updated_at": snap.created_at,
            "audit_verdict": (v.get("audit") or {}).get("verdict"),
        }

    def detail(self, session_id: str) -> Optional[dict]:
        snap = self.snapshot(session_id)
        if snap is None:
            return None
        v = snap.values
        state, payload = self.waiting_on(snap)
        prov = v.get("provenance") or {}
        audit = v.get("audit") or {}
        line_status = audit.get("line_status") or {}
        lines = []
        for line in sorted(prov, key=_line_key):
            rec = prov[line] or {}
            lines.append({
                "line": line,
                "label": LINE_LABELS.get(line, f"Line {line}"),
                "amount": rec.get("amount"),
                "kind": rec.get("kind"),
                "formula": rec.get("formula") or rec.get("calculation"),
                "inputs": rec.get("inputs") or {},
                "document": rec.get("document"),
                "field": rec.get("field"),
                "tool": rec.get("tool"),
                "source": rec.get("source"),
                "entered_by": rec.get("entered_by"),
                "created_at": rec.get("created_at"),
                "audit": line_status.get(line),
            })
        timeline = []
        for e in v.get("handoff_log") or []:
            if not isinstance(e, dict):
                continue
            if e.get("from") == "orchestrator":
                timeline.append({"kind": "hop", "to": e.get("to"), "at": e.get("at")})
            elif e.get("from") == "human_review":
                timeline.append({"kind": "human", "action": e.get("action"), "resolved": e.get("resolved"), "at": e.get("at")})
            elif e.get("from") == "release_gate":
                timeline.append({"kind": "gate", "passed": e.get("passed"), "violations": e.get("violations")})
        return {
            **self.summary(session_id),
            "interrupt": payload,
            "intake": v.get("intake") or {},
            "eligibility": v.get("eligibility") or {},
            "lines": lines,
            "documents": v.get("documents") or [],
            "escalations": v.get("escalations") or [],
            "audit": {k: audit.get(k) for k in ("round", "verdict", "findings", "send_back_to", "checks_run", "resolved_by_human")} if audit else None,
            "release_gate": v.get("release_gate"),
            "hitl_resolutions": v.get("hitl_resolutions") or [],
            "volunteer_notes": v.get("volunteer_notes") or [],
            "timeline": timeline,
        }

    # ---------- acting ----------
    def _run(self, payload, session_id: str):
        with self.lock:
            self.graph.invoke(payload, thread_config(session_id))

    def resume(self, session_id: str, decision) -> dict:
        snap = self.snapshot(session_id)
        if snap is None:
            raise KeyError(session_id)
        if not snap.interrupts:
            raise ValueError("This session is not waiting for anyone.")
        self._run(Command(resume=decision), session_id)
        return self.detail(session_id)

    def start(self, session_id: str, scenario: Optional[str], docs: list[str], ssn: Optional[str],
              spouse_ssn: Optional[str]) -> dict:
        if self.snapshot(session_id) is not None:
            raise ValueError(f"A session called '{session_id}' already exists.")
        state: dict[str, Any] = {"session_id": session_id}
        if scenario:
            state.update(json.loads((SCENARIO_DIR / scenario).read_text()))
            state["intake_complete"] = True
            answers = {}
            if ssn:
                answers["taxpayer_ssn"] = {"slot": "taxpayer_ssn", "value": ssn, "source": "direct user input"}
            if spouse_ssn:
                answers["spouse_ssn"] = {"slot": "spouse_ssn", "value": spouse_ssn, "source": "direct user input"}
            if answers:
                state["answers"] = {**(state.get("answers") or {}), **answers}
        if docs:
            state["document_files"] = [_doc_entry(FIXTURE_DIR / d) for d in docs]
        self._run(state, session_id)
        return self.detail(session_id)


def _doc_entry(p: Path) -> dict:
    """Same shape as run.py's doc_entry, so extraction sees what it always sees."""
    rel = str(p.relative_to(ROOT))
    if p.suffix.lower() == ".json":
        doc_type = json.loads(p.read_text()).get("doc_type", "W-2")
        return {"doc_id": p.stem, "doc_type": doc_type, "path": rel, "media_type": "application/json"}
    return {"doc_id": p.stem, "doc_type": "W-2", "path": rel}


# ---------------- HTTP ----------------

class ResolveBody(BaseModel):
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=2000)
    action: str = Field(default="proceed", pattern="^(proceed|stop)$")
    escalation_ids: Optional[list[str]] = None   # None = resolve all pending


class AnswerBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class StartBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    scenario: Optional[str] = None
    documents: list[str] = []
    ssn: Optional[str] = None
    spouse_ssn: Optional[str] = None


DEMO_SESSIONS = [
    # The two evaluation cases from the brief: a clean run, and one that pauses for a human.
    {"session_id": "eval-case-1", "scenario": "clean_single.json",
     "documents": ["w2_1.png", "int_1.json"], "ssn": "900-12-3456", "spouse_ssn": None},
    {"session_id": "eval-case-2", "scenario": "hitl_spouse_died.json",
     "documents": [], "ssn": "900-12-3456", "spouse_ssn": "900-65-4321"},
]


def seed_demo(console: "Console") -> None:
    """With SILVERFILE_DEMO=1, start the two eval cases when the database is empty,
    so the queue opens with one finished draft and one session awaiting review."""
    if os.environ.get("SILVERFILE_DEMO", "").strip().lower() not in ("1", "true", "yes"):
        return
    if console.thread_ids():
        return
    for case in DEMO_SESSIONS:
        console.start(case["session_id"], case["scenario"], case["documents"], case["ssn"], case["spouse_ssn"])


def create_app(db_path: Optional[str] = None) -> FastAPI:
    console = Console(db_path)
    seed_demo(console)
    app = FastAPI(title="SilverFile Review Console", docs_url="/api/docs")
    app.state.console = console

    @app.get("/api/meta")
    def meta():
        return {"offline": _offline(), "db": os.environ.get("SILVERFILE_DB", "silverfile_sessions.sqlite")}

    @app.get("/api/sessions")
    def sessions():
        out = [s for s in (console.summary(t) for t in console.thread_ids()) if s]
        out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return out

    @app.get("/api/sessions/{session_id}")
    def session(session_id: str):
        d = console.detail(session_id)
        if d is None:
            raise HTTPException(404, "No such session")
        return d

    @app.post("/api/sessions/{session_id}/resolve")
    def resolve(session_id: str, body: ResolveBody):
        snap = console.snapshot(session_id)
        if snap is None:
            raise HTTPException(404, "No such session")
        payload = snap.interrupts[0].value if snap.interrupts else None
        if not isinstance(payload, dict) or payload.get("type") != "human_review":
            raise HTTPException(409, "This session is not waiting for a Kushim reviewer.")
        if body.action == "stop":
            decision = {"reviewer": body.reviewer, "action": "stop", "note": body.note}
        elif body.escalation_ids is None:
            decision = {"reviewer": body.reviewer, "resolve_all": True, "note": body.note}
        else:
            chosen = [e for e in payload.get("escalations") or [] if e.get("id", e.get("reason")) in body.escalation_ids]
            if not chosen:
                raise HTTPException(400, "Select at least one escalation to resolve.")
            decision = {"reviewer": body.reviewer,
                        "resolve": [{"reason": e.get("reason"), "source": e.get("source"), "note": body.note} for e in chosen]}
        try:
            return console.resume(session_id, decision)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/sessions/{session_id}/answer")
    def answer(session_id: str, body: AnswerBody):
        snap = console.snapshot(session_id)
        if snap is None:
            raise HTTPException(404, "No such session")
        payload = snap.interrupts[0].value if snap.interrupts else None
        if not isinstance(payload, dict) or payload.get("type") != "intake_question":
            raise HTTPException(409, "This session is not waiting for a taxpayer answer.")
        return console.resume(session_id, body.text)

    @app.get("/api/fixtures")
    def fixtures():
        scenarios = []
        for p in sorted(SCENARIO_DIR.glob("*.json")):
            data = json.loads(p.read_text())
            intake = data.get("intake") or {}
            scenarios.append({"file": p.name, "filing_status": intake.get("filing_status"),
                              "hitl": p.stem.startswith("hitl_"),
                              "title": p.stem.replace("hitl_", "").replace("_", " ").capitalize()})
        docs = [p.name for p in sorted(FIXTURE_DIR.iterdir())
                if p.is_file() and (p.suffix == ".json" and not p.name.endswith(".reading.json") or p.suffix in (".png", ".jpg"))]
        return {"scenarios": scenarios, "documents": docs}

    @app.post("/api/sessions")
    def start(body: StartBody):
        if body.scenario and not (SCENARIO_DIR / body.scenario).is_file():
            raise HTTPException(400, "Unknown scenario.")
        for d in body.documents:
            if "/" in d or not (FIXTURE_DIR / d).is_file():
                raise HTTPException(400, f"Unknown document {d}.")
        for value in (body.ssn, body.spouse_ssn):
            if value and not TEST_SSN.match(value):
                raise HTTPException(400, "Only test SSNs in the 900-999 range are accepted.")
        try:
            return console.start(body.session_id, body.scenario, body.documents, body.ssn, body.spouse_ssn)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app() if os.environ.get("SILVERFILE_DASHBOARD_NO_AUTOAPP") != "1" else None
