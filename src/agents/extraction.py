"""Document Extraction agent (SilverFile).

Turns supplied tax documents into typed records. The W-2 is read from an image
by a vision model; the other forms come in as JSON. Extraction copies boxes as
printed and never decides what a value means for the return: mapping box 1 to
line 1z is Computation's job.

READS
  state["document_files"]  list of {"doc_id", "doc_type", "path"}, one per file
                           the taxpayer supplied (written by run.py / dashboard)
  state["documents"], state["escalations"]  only to skip files already handled

WRITES (one parallel branch per file, via Send)
  documents    one entry per readable file, in the shape Computation reads:
               {"doc_id", "doc_type", "source_file", "fields": {...},
                "status": "ok" | "needs_confirmation", "field_provenance": {...},
                "issues": [...], "source_sha256", "model", "extracted_by"}
               Amounts are whole-dollar ints. A blank box is 0; a box that
               could not be read is left out, never guessed.
  escalations  one entry per problem: unreadable or low-confidence boxes,
               a non-test SSN, a file that is not what Intake said, or a
               read failure. Rejected or failed files are NOT added to documents.
  handoff_log  one entry per file

W-2 field names match Computation (box_1_wages, box_2_withholding, ...).
Only SSNs in the 900-999 test range are ever stored.

Wiring (Orchestrator owner):
    builder.add_node(NODE, extraction_agent)
    builder.add_conditional_edges("orchestrator", make_document_router("orchestrator"))
    builder.add_edge(NODE, "orchestrator")

Run on its own:  python -m src.agents.extraction data/fixtures/w2_1.png [--offline]
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from langgraph.graph import END
from langgraph.types import Send

from src.tools.ssn import check_ssn, normalize_ein, redact_ssns
from src.tools.w2_reader import BoxReading, VisionReader, W2Reading, default_reader

NODE = "extraction_agent"
AGENT = "extraction"
CONFIRM_THRESHOLD = 0.85
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
_MONEY_RE = re.compile(r"^-?\d+(\.\d{1,2})?$")

log = logging.getLogger("silverfile.extraction")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Builder:
    """Collects the fields of one document, their provenance and any issues."""

    def __init__(self, doc_id: str, source_file: str, sha256: str, method: str, model: str | None):
        self.doc_id, self.source_file, self.sha256 = doc_id, source_file, sha256
        self.method, self.model = method, model
        self.fields: dict = {}
        self.provenance: dict = {}
        self.issues: list[dict] = []

    def issue(self, code: str, field: str | None, detail: str) -> None:
        self.issues.append({"code": code, "field": field, "detail": detail})

    def _put(self, field: str, value, raw_text: str | None, confidence: float, **extra) -> None:
        self.fields[field] = value
        self.provenance[field] = {
            "kind": "document", "document": self.doc_id, "field": field,
            "raw_text": raw_text, "confidence": round(confidence, 2), "method": self.method,
            "model": self.model, "source_file": self.source_file,
            "source_sha256": self.sha256, "extracted_by": AGENT, "created_at": _now(), **extra,
        }

    def _readable(self, field: str, r: BoxReading) -> float | None:
        """Confidence if the box can be used, None if it must be confirmed by a human."""
        conf = min(max(r.confidence, 0.0), 1.0)
        if conf >= CONFIRM_THRESHOLD:
            return conf
        if r.text is None:
            self.issue("unreadable", field, "box could not be read")
            return None
        self.issue("low_confidence", field, f"read with confidence {conf:.2f}")
        return conf

    def money(self, field: str, r: BoxReading) -> None:
        conf = self._readable(field, r)
        if conf is None:
            return
        if r.text is None or not r.text.strip():
            self._put(field, 0, r.text, conf)  # blank box on the form
            return
        cleaned = r.text.replace("$", "").replace(",", "").replace(" ", "")
        try:
            if not _MONEY_RE.match(cleaned):
                raise InvalidOperation
            exact = Decimal(cleaned)
        except InvalidOperation:
            self.issue("unparseable_amount", field, "printed text is not an amount")
            return
        dollars = int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        extra = {"rounded_from": str(exact)} if exact != dollars else {}
        self._put(field, dollars, r.text, conf, **extra)

    def text(self, field: str, r: BoxReading) -> None:
        conf = self._readable(field, r)
        if conf is not None:
            value = r.text.strip() if r.text and r.text.strip() else None
            self._put(field, value, r.text, conf)

    def flag(self, field: str, r: BoxReading) -> None:
        conf = self._readable(field, r)
        if conf is not None:
            self._put(field, bool(r.text and r.text.strip()), r.text, conf)

    def ssn(self, field: str, r: BoxReading) -> None:
        result, normalized = check_ssn(r.text)
        if result == "ok":
            conf = self._readable(field, r)
            if conf is not None:
                self._put(field, normalized, normalized, conf)
        elif result == "not_test_range":
            # Possibly a real SSN: keep nothing, not even in raw_text or the issue detail.
            self.issue("invalid_ssn", field, "SSN is outside the 900-999 test range and was not stored")
        elif result == "malformed":
            self.issue("unreadable", field, "SSN is not in NNN-NN-NNNN form")
        else:
            self.issue("unreadable", field, "SSN box is blank")

    def ein(self, field: str, r: BoxReading) -> None:
        conf = self._readable(field, r)
        if conf is None:
            return
        normalized = normalize_ein(r.text)
        if normalized is None:
            self.issue("unreadable", field, "EIN is not in NN-NNNNNNN form")
        else:
            self._put(field, normalized, r.text, conf)


def _w2_fields(b: _Builder, r: W2Reading) -> None:
    b.text("tax_year", r.tax_year)
    b.ssn("employee_ssn", r.box_a)
    b.ein("employer_ein", r.box_b)
    b.text("employer_name_address", r.box_c)
    b.text("control_number", r.box_d)
    b.text("employee_name", r.box_e)
    b.text("employee_address", r.box_f)
    b.money("box_1_wages", r.box_1)
    b.money("box_2_withholding", r.box_2)
    b.money("box_3_ss_wages", r.box_3)
    b.money("box_4_ss_tax_withheld", r.box_4)
    b.money("box_5_medicare_wages", r.box_5)
    b.money("box_6_medicare_tax_withheld", r.box_6)
    b.money("box_7_ss_tips", r.box_7)
    b.money("box_8_allocated_tips", r.box_8)
    b.money("box_10_dependent_care", r.box_10)
    b.money("box_11_nonqualified_plans", r.box_11)
    for e in r.box_12:
        b.text(f"box_12{e.slot}_code", e.code)
        b.money(f"box_12{e.slot}_amount", e.amount)
    b.flag("box_13_statutory_employee", r.box_13_statutory_employee)
    b.flag("box_13_retirement_plan", r.box_13_retirement_plan)
    b.flag("box_13_third_party_sick_pay", r.box_13_third_party_sick_pay)
    b.text("box_14_other", r.box_14)
    for i, s in enumerate(r.state_lines, start=1):
        b.text(f"state_{i}_state", s.state)
        b.text(f"state_{i}_employer_state_id", s.employer_state_id)
        b.money(f"state_{i}_box_16_wages", s.box_16)
        b.money(f"state_{i}_box_17_income_tax", s.box_17)
        b.money(f"state_{i}_box_18_local_wages", s.box_18)
        b.money(f"state_{i}_box_19_local_income_tax", s.box_19)
        b.text(f"state_{i}_box_20_locality", s.box_20)


def _json_fields(b: _Builder, data: dict) -> None:
    for name, raw in data.get("fields", {}).items():
        reading = BoxReading(text=None if raw is None else str(raw), confidence=1.0)
        if "ssn" in name or "tin" in name:
            b.ssn(name, reading)
        elif isinstance(raw, bool):
            b._put(name, raw, str(raw), 1.0)
        elif isinstance(raw, (int, float)):
            b.money(name, reading)
        else:
            b.text(name, reading)


def _escalation(doc_id: str, reason: str, detail: str) -> dict:
    return {"id": f"extraction:{doc_id}:{reason}", "raised_by": AGENT, "doc_id": doc_id,
            "reason": reason, "detail": detail, "resolved": False}


def extract_document(doc: dict, reader: VisionReader | None = None, attempt: int = 1) -> dict:
    """Extract one file. Returns the state update for it; never raises."""
    doc_id, doc_type, path = doc["doc_id"], doc["doc_type"], str(doc["path"])
    media_type = doc.get("media_type") or mimetypes.guess_type(path)[0] or "application/octet-stream"
    is_json = media_type == "application/json"
    method = "json_fixture" if is_json else "vision_llm"

    def stop(reason: str, detail: str, status: str) -> dict:
        detail = redact_ssns(detail)
        log.warning("extraction %s for %s: %s", status, doc_id, detail)
        return {"escalations": [_escalation(doc_id, reason, detail)],
                "handoff_log": [{"agent": AGENT, "doc_id": doc_id, "result": status}]}

    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return stop("extraction_failed", f"cannot read {path}: {e.strerror}", "failed")
    sha = hashlib.sha256(data).hexdigest()

    try:
        if doc_type == "W-2" and not is_json:
            if media_type not in IMAGE_TYPES:
                return stop("extraction_failed", f"unsupported file type {media_type}", "failed")
            reader = reader or default_reader()
            model = reader.model_id
            reading = reader.read_w2(data, media_type, path)
        elif is_json:
            model = None
            payload = json.loads(data)
            reading = W2Reading.model_validate(payload) if doc_type == "W-2" else payload
        else:
            return stop("extraction_failed", f"{doc_type} is only supported as JSON", "failed")
    except Exception as e:  # noqa: BLE001 - one bad file must not take the graph down
        return stop("extraction_failed", f"{type(e).__name__}: {e}", "failed")

    b = _Builder(doc_id, path, sha, method, model)
    if isinstance(reading, W2Reading):
        if not reading.is_w2:
            return stop("wrong_document_type", f"{Path(path).name} does not look like a Form W-2", "rejected")
        _w2_fields(b, reading)
    else:
        if reading.get("doc_type", doc_type) != doc_type:
            return stop("wrong_document_type",
                        f"{Path(path).name} is a {reading.get('doc_type')}, not a {doc_type}", "rejected")
        _json_fields(b, reading)

    if any(i["code"] == "invalid_ssn" for i in b.issues):
        return stop("invalid_ssn", f"{doc_id}: SSN is outside the 900-999 test range and was not stored",
                    "rejected")

    status = "needs_confirmation" if b.issues else "ok"
    entry = {"doc_id": doc_id, "doc_type": doc_type, "source_file": path, "fields": b.fields,
             "status": status, "attempt": attempt, "field_provenance": b.provenance,
             "issues": b.issues, "source_sha256": sha, "model": model,
             "extracted_by": AGENT, "extracted_at": _now()}
    update = {"documents": [entry],
              "handoff_log": [{"agent": AGENT, "doc_id": doc_id, "result": status}]}
    if b.issues:
        fields = ", ".join(sorted({i["field"] for i in b.issues if i["field"]}))
        update["escalations"] = [_escalation(doc_id, "extraction_needs_confirmation",
                                             f"{doc_id}: please confirm {fields} with the taxpayer")]
    return update


def make_extraction_agent(reader: VisionReader | None = None):
    def extraction_agent(task: dict) -> dict:
        """LangGraph node for one Send branch: task = {"doc": {...}}."""
        return extract_document(task["doc"], reader, task.get("attempt", 1))

    return extraction_agent


extraction_agent = make_extraction_agent()


def handled_doc_ids(state: dict) -> set[str]:
    done = {d.get("doc_id") for d in state.get("documents") or []}
    done |= {e.get("doc_id") for e in state.get("escalations") or []
             if isinstance(e, dict) and e.get("raised_by") == AGENT}
    return done


def make_document_router(done: str = END):
    """Conditional edge: one Send per file not handled yet, else go to `done`."""

    def route_documents(state: dict):
        handled = handled_doc_ids(state)
        sends = [Send(NODE, {"doc": f}) for f in state.get("document_files") or []
                 if f["doc_id"] not in handled]
        return sends or done

    return route_documents


def build_extraction_graph(reader: VisionReader | None = None):
    """Standalone graph (START -> fan-out -> END), used by tests and the CLI."""
    from langgraph.graph import START, StateGraph

    from src.state import SilverFileState

    builder = StateGraph(SilverFileState)
    builder.add_node(NODE, make_extraction_agent(reader))
    builder.add_conditional_edges(START, make_document_router(END), [NODE, END])
    builder.add_edge(NODE, END)
    return builder.compile()


def _file_entry(path: Path) -> dict:
    if path.suffix.lower() == ".json":
        doc_type = json.loads(path.read_text()).get("doc_type", "W-2")
        return {"doc_id": path.stem, "doc_type": doc_type, "path": str(path), "media_type": "application/json"}
    return {"doc_id": path.stem, "doc_type": "W-2", "path": str(path)}


def main() -> None:
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(description="Run the Extraction agent on its own.")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--offline", action="store_true", help="use fixture answer keys instead of Claude")
    args = p.parse_args()
    if args.offline:
        os.environ["EXTRACTION_OFFLINE"] = "1"
    reader = default_reader()
    print(f"reader: {reader.model_id}")
    files = [_file_entry(f) for f in args.files if not f.name.endswith(".reading.json")]
    out = build_extraction_graph(reader).invoke({"session_id": "cli", "document_files": files})
    for d in sorted(out.get("documents", []), key=lambda d: d["doc_id"]):
        print(f"\n{d['doc_id']}  {d['doc_type']}  status={d['status']}")
        for name, value in d["fields"].items():
            if isinstance(value, int) and not isinstance(value, bool) and value:
                print(f"  {name:32} {value:>10}   conf={d['field_provenance'][name]['confidence']:.2f}")
    for e in out.get("escalations", []):
        print(f"\nescalation  {e['doc_id']}  {e['reason']}: {e['detail']}")


if __name__ == "__main__":
    main()
