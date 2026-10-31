"""
Draft Form 1040-SR as a PDF, built from a session's provenance records.

    python -m dashboard.form_1040sr <session_id> [out.pdf]     # needs SILVERFILE_DB if not default
    GET /api/sessions/<session_id>/form.pdf                    # the dashboard's Download button

The IRS PDF is not bundled, so this draws a form laid out like the 2025
Form 1040-SR: filer details, the line 12d boxes, every line by section, and a
second page listing where each amount came from. It is marked as a draft and
never claims to be a filed return. Amounts are only ever copied from
state["provenance"]; nothing is computed here.
"""
from __future__ import annotations

import io
import re
from typing import Any

from reportlab.lib.colors import Color, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

INK = Color(0.08, 0.14, 0.23)
RULE = Color(0.55, 0.58, 0.6)
SHADE = Color(0.93, 0.94, 0.92)
FAINT = Color(0.45, 0.48, 0.5)
STAMP = Color(0.55, 0.18, 0.18)

FILING = {"single": "Single", "mfj": "Married filing jointly", "mfs": "Married filing separately",
          "hoh": "Head of household", "qss": "Qualifying surviving spouse"}

SECTIONS = [
    ("Income", ["1a", "1z", "2a", "2b", "3a", "3b", "4a", "4b", "5a", "5b", "6a", "6b", "7a", "8", "9"]),
    ("Adjusted gross income", ["10", "11a", "11b"]),
    ("Deductions and taxable income", ["12e", "13a", "13b", "14", "15"]),
    ("Tax and credits", ["16", "17", "18", "19", "20", "21", "22", "23", "24"]),
    ("Payments", ["25a", "25b", "25c", "25d", "26", "32", "33"]),
    ("Refund or amount you owe", ["34", "35a", "37"]),
]
TOTALS = {"9", "11a", "15", "24", "33", "34", "37"}


def _key(line: str):
    m = re.match(r"(\d+)([a-z]?)", line)
    return (int(m.group(1)), m.group(2)) if m else (999, line)


def _money(v) -> str:
    return "" if v is None else f"{int(v):,}"


def _mask(ssn: str | None) -> str:
    digits = re.sub(r"\D", "", ssn or "")
    return f"XXX-XX-{digits[-4:]}" if len(digits) == 9 else "Not collected"


def _source(rec: dict) -> str:
    kind = rec.get("kind")
    if kind == "document":
        return f"Document {rec.get('document', '?')}, {str(rec.get('field', '')).replace('_', ' ')}"
    if kind == "user_input":
        who = rec.get("entered_by") or "taxpayer"
        if who == "none reported":
            return "Not reported by the taxpayer, so recorded as 0"
        note = rec.get("note")
        return f"Taxpayer answer at intake ({who})" + (f": {note}" if note else "")
    formula = rec.get("formula") or rec.get("calculation") or ""
    tool = rec.get("tool")
    return f"{formula}" + (f"  [{tool}]" if tool else "")


def build_pdf(state: dict[str, Any], labels: dict[str, str]) -> bytes:
    prov = state.get("provenance") or {}
    if not prov:
        raise ValueError("This session has no computed lines yet, so there is no draft form to print.")
    intake = state.get("intake") or {}
    elig = state.get("eligibility") or {}
    answers = state.get("answers") or {}
    boxes = elig.get("boxes_12d") or {}
    status = state.get("status") or ""

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"Draft Form 1040-SR, {intake.get('taxpayer_name') or state.get('session_id')}")
    c.setAuthor("SilverFile (Team 8)")
    W, H = letter
    L, R = 40, W - 40

    def header(page_title: str):
        c.setFillColor(INK)
        c.rect(L, H - 92, 92, 52, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica", 8); c.drawString(L + 8, H - 54, "Form")
        c.setFont("Helvetica-Bold", 19); c.drawString(L + 8, H - 78, "1040-SR")
        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 12.5); c.drawString(L + 104, H - 58, "U.S. Income Tax Return for Seniors")
        c.setFont("Helvetica", 9); c.setFillColor(FAINT)
        c.drawString(L + 104, H - 73, page_title)
        c.drawString(L + 104, H - 86, f"Session {state.get('session_id', '')}   Status: {status.replace('_', ' ')}")
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 24); c.drawRightString(R, H - 76, "2025")
        c.setStrokeColor(INK); c.setLineWidth(1.6); c.line(L, H - 100, R, H - 100)
        c.setFillColor(STAMP); c.setFont("Helvetica-Bold", 8)
        c.drawRightString(R, H - 34, "DRAFT FOR VOLUNTEER REVIEW. NOT FOR FILING.")

    # ---------------- page 1: the form ----------------
    header("Draft prepared by SilverFile from the session's provenance records")
    y = H - 120
    c.setFillColor(black)
    rows = [
        ("Your name", intake.get("taxpayer_name") or "", "Your SSN", _mask((answers.get("taxpayer_ssn") or {}).get("value"))),
        ("Spouse's name", intake.get("spouse_name") or ("" if intake.get("filing_status") in ("single", "hoh") else "Not given"),
         "Spouse's SSN", _mask((answers.get("spouse_ssn") or {}).get("value")) if intake.get("filing_status") in ("mfj", "mfs", "qss") else ""),
        ("Filing status", FILING.get(intake.get("filing_status") or elig.get("filing_status"), "Not set"), "Date of birth", intake.get("birth_date") or ""),
    ]
    for a, av, b, bv in rows:
        c.setStrokeColor(RULE); c.setLineWidth(0.5); c.rect(L, y - 18, (R - L) * 0.62, 22, stroke=1, fill=0)
        c.rect(L + (R - L) * 0.62, y - 18, (R - L) * 0.38, 22, stroke=1, fill=0)
        c.setFont("Helvetica", 6.5); c.setFillColor(FAINT)
        c.drawString(L + 4, y - 1, a); c.drawString(L + (R - L) * 0.62 + 4, y - 1, b)
        c.setFont("Helvetica", 9.5); c.setFillColor(black)
        c.drawString(L + 4, y - 13, str(av)); c.drawString(L + (R - L) * 0.62 + 4, y - 13, str(bv))
        y -= 22
    # line 12d boxes
    y -= 6
    c.setFont("Helvetica-Bold", 8); c.drawString(L, y - 9, "12d")
    c.setFont("Helvetica", 8)
    x = L + 26
    for label, key in [("You: born before Jan 2, 1961", "taxpayer_65"), ("You: blind", "taxpayer_blind"),
                       ("Spouse: born before Jan 2, 1961", "spouse_65"), ("Spouse: blind", "spouse_blind")]:
        c.setStrokeColor(black); c.rect(x, y - 11, 8, 8, stroke=1, fill=0)
        if boxes.get(key):
            c.setLineWidth(1.2); c.line(x + 1.5, y - 7, x + 3.5, y - 9.5); c.line(x + 3.5, y - 9.5, x + 7, y - 4); c.setLineWidth(0.5)
        c.drawString(x + 11, y - 10, label)
        x += 11 + c.stringWidth(label, "Helvetica", 8) + 12
    c.setFont("Helvetica-Bold", 8); c.drawRightString(R, y - 10, f"Total: {elig.get('boxes_checked', 0)}")
    y -= 24

    rowh = 12.6
    for title, lines in SECTIONS:
        c.setFillColor(SHADE); c.rect(L, y - 12, R - L, 13, fill=1, stroke=0)
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 8.5); c.drawString(L + 4, y - 9, title)
        y -= 13
        for ln in lines:
            rec = prov.get(ln)
            if rec is None and ln not in ("1z", "2a", "2b", "3a", "3b", "4a", "4b", "5a", "5b", "6a", "6b",
                                          "9", "11a", "12e", "14", "15", "16", "24", "25d", "33", "34", "37"):
                continue
            bold = ln in TOTALS
            c.setStrokeColor(RULE); c.setLineWidth(0.4); c.line(L, y - rowh + 3, R, y - rowh + 3)
            c.setFillColor(black)
            c.setFont("Helvetica-Bold", 8.5); c.drawRightString(L + 26, y - 8.5, ln)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 8.5)
            c.drawString(L + 34, y - 8.5, labels.get(ln, f"Line {ln}"))
            # amount box
            c.setStrokeColor(black); c.setLineWidth(0.5)
            c.line(R - 96, y - rowh + 3, R - 96, y + 0.5)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
            c.drawRightString(R - 4, y - 8.5, _money(rec.get("amount")) if rec else "")
            c.setFont("Helvetica-Bold", 7.5); c.drawString(R - 112, y - 8.5, ln)
            y -= rowh
        y -= 3
    c.setFont("Helvetica", 7); c.setFillColor(FAINT)
    c.drawString(L, 26, "Every amount on this page is copied from a provenance record; page 2 lists the source of each one. "
                        "Lines left blank have no amount for this return.")
    c.showPage()

    # ---------------- page 2: provenance ----------------
    header("Where each amount came from")
    y = H - 122
    c.setFont("Helvetica-Bold", 8); c.setFillColor(INK)
    c.drawString(L, y, "Line"); c.drawString(L + 40, y, "Amount"); c.drawString(L + 110, y, "Kind"); c.drawString(L + 175, y, "Source or calculation")
    y -= 6; c.setStrokeColor(INK); c.line(L, y, R, y); y -= 11
    for ln in sorted(prov, key=_key):
        rec = prov[ln] or {}
        if y < 60:
            c.showPage(); header("Where each amount came from (continued)"); y = H - 122
        c.setFillColor(black); c.setFont("Helvetica-Bold", 8); c.drawString(L, y, ln)
        c.setFont("Helvetica", 8); c.drawRightString(L + 95, y, _money(rec.get("amount")))
        c.drawString(L + 110, y, str(rec.get("kind") or ""))
        src = _source(rec)
        maxw = R - (L + 175)
        while src:
            cut = len(src)
            while c.stringWidth(src[:cut], "Helvetica", 8) > maxw:
                cut = src.rfind(" ", 0, cut - 1) if " " in src[:cut - 1] else cut - 1
            c.drawString(L + 175, y, src[:cut]); src = src[cut:].lstrip(); y -= 10
        y -= 2
        c.setStrokeColor(SHADE); c.line(L, y + 6, R, y + 6)
    audit = state.get("audit") or {}
    gate = state.get("release_gate") or {}
    y -= 8
    c.setFont("Helvetica", 8); c.setFillColor(FAINT)
    if audit:
        c.drawString(L, y, f"Review & Audit: verdict {audit.get('verdict')}, {audit.get('checks_run')} checks, round {audit.get('round')}.")
        y -= 11
    if gate:
        c.drawString(L, y, f"Release gate: {'passed' if gate.get('passed') else 'blocked'}.")
    c.showPage()
    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from dashboard.app import LINE_LABELS, Console

    if len(sys.argv) < 2:
        sys.exit("usage: python -m dashboard.form_1040sr <session_id> [out.pdf]")
    sid = sys.argv[1]
    out = Path(sys.argv[2] if len(sys.argv) > 2 else f"traces/{sid}_1040sr.pdf")
    snap = Console().snapshot(sid)
    if snap is None:
        sys.exit(f"No session called {sid!r}.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_pdf(snap.values, LINE_LABELS))
    print(f"wrote {out}")
