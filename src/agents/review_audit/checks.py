"""The individual audit checks.

Each check tries to FALSIFY the assembled return (spec section 4): it compares a
reported value with an independent recomputation, a source document, a
provenance record, or a scope rule. A check never changes the return; it only
records a Finding that names who has to fix it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from . import rules_2025 as R
from .schema import Finding, Route, Severity
from .security import (
    EIN_FORMAT,
    find_injection,
    iter_strings,
    mask_ssn,
    ssn_is_valid_test_ssn,
)

ZERO = Decimal("0")
DEFAULT_TOLERANCE = Decimal("1")       # whole-dollar rounding on the form
CONFIDENCE_THRESHOLD = 0.85            # below this an extracted amount is re-checked
SS_WAGE_BASE_2025 = Decimal("176100")  # W-2 box 3 cap, used for a box 4 sanity check

# Lines that are pure inputs from documents or intake vs. lines that must be computed.
COMPUTED_LINES = ("1z", "9", "11a", "12e", "14", "15", "16", "24", "25d", "33")
NON_NEGATIVE_LINES = (
    "1a", "1b", "1c", "1d", "1e", "1f", "1g", "1h", "1z", "2a", "2b", "3a", "3b", "4a", "4b",
    "5a", "5b", "6a", "6b", "10", "12e", "13a", "13b", "14", "15", "16", "17", "18", "19",
    "20", "21", "22", "23", "24", "25a", "25b", "25c", "25d", "26", "27a", "28", "29", "30",
    "31", "32", "33", "34", "35a", "36", "37",
)
OUT_OF_SCOPE_DOC_TYPES = {
    "1099-NEC": "nonemployee compensation (self-employment)",
    "1099-MISC": "miscellaneous income",
    "1099-K": "payment card / third-party network income",
    "1099-B": "broker sales (capital gains)",
    "1099-DA": "digital asset sales",
    "SCHEDULE C": "business income",
    "SCHEDULE E": "rental income",
    "SCHEDULE D": "capital gains",
    "SCHEDULE A": "itemized deductions",
    "K-1": "partnership / S-corp income",
}
OUT_OF_SCOPE_INTAKE_FLAGS = {
    "itemized_deductions": "itemized deductions (Schedule A)",
    "self_employment": "self-employment or business income (Schedule C)",
    "rental_income": "rental income (Schedule E)",
    "capital_gains_schedule_d": "capital gains requiring Schedule D",
    "digital_assets": "digital assets",
    "foreign_income_or_accounts": "foreign income or accounts",
    "amended_or_prior_year": "an amended or prior-year return",
    "state_return": "a state return",
}


def to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class ReturnAuditor:
    """Runs every check against one snapshot of the state."""

    def __init__(self, state: dict[str, Any], tolerance: Decimal = DEFAULT_TOLERANCE):
        self.s = state
        self.tol = tolerance
        self.lines: dict[str, Any] = state.get("return_lines") or {}
        self.docs: list[dict[str, Any]] = state.get("documents") or []
        self.docs_by_id = {d.get("doc_id"): d for d in self.docs}
        self.flags: dict[str, Any] = state.get("intake_flags") or {}
        self.status: str = (state.get("filing_status") or "").lower()
        self.findings: list[Finding] = []
        self.checks_run = 0
        self.line_status: dict[str, str] = {k: "ok" for k in self.lines}

    # -- helpers ---------------------------------------------------------------
    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.line and finding.severity is Severity.ERROR:
            self.line_status[finding.line] = "failed"
        elif finding.line and self.line_status.get(finding.line) == "ok":
            self.line_status[finding.line] = "warning"

    def v(self, line: str) -> Decimal:
        """Reported value of a line; a missing line counts as 0."""
        entry = self.lines.get(line)
        if entry is None:
            return ZERO
        return to_dec(entry.get("value") if isinstance(entry, dict) else entry) or ZERO

    def has(self, line: str) -> bool:
        return line in self.lines

    def docs_of(self, doc_type: str) -> list[dict[str, Any]]:
        return [d for d in self.docs if (d.get("type") or "").upper() == doc_type.upper()]

    def field(self, doc: dict[str, Any], name: str) -> Decimal:
        return to_dec((doc.get("fields") or {}).get(name)) or ZERO

    def expect(self, line: str, expected: Decimal, how: str, route: Route = Route.COMPUTATION) -> None:
        """Core falsification step: reported value must equal the recomputation."""
        self.checks_run += 1
        actual = self.v(line)
        if abs(actual - expected) > self.tol:
            self.add(Finding(
                code="LINE_MISMATCH", severity=Severity.ERROR, route_to=route, line=line,
                expected=str(R.dollars(expected)), actual=str(actual),
                message=f"Line {line} should be {R.dollars(expected)} ({how}) but the return shows {actual}.",
            ))

    # -- 1. provenance (spec 3.3 central requirement, 4.2) ----------------------
    def check_provenance(self) -> None:
        for line, entry in self.lines.items():
            self.checks_run += 1
            value = self.v(line)
            prov = entry.get("provenance") if isinstance(entry, dict) else None
            if not prov:
                if value != ZERO:
                    self.add(Finding("NO_PROVENANCE", Severity.ERROR, Route.COMPUTATION,
                                     f"Line {line} has an amount but no record of where it came from.", line=line))
                continue
            kind = prov.get("kind")
            if kind == "document":
                self._check_document_provenance(line, value, prov)
            elif kind == "computed":
                if not prov.get("tool"):
                    self.add(Finding("NO_MCP_TOOL", Severity.ERROR, Route.COMPUTATION,
                                     f"Line {line} is marked as computed but does not name the deterministic tool that computed it.",
                                     line=line))
                for ref in prov.get("inputs") or []:
                    if not self._ref_exists(ref):
                        self.add(Finding("BROKEN_PROVENANCE_REF", Severity.ERROR, Route.COMPUTATION,
                                         f"Line {line} says it was computed from '{ref}', which does not exist.",
                                         line=line, subject=ref))
            elif kind == "user_input":
                if not prov.get("entered_by") or not parse_date(prov.get("entered_at")):
                    self.add(Finding("INCOMPLETE_USER_INPUT_RECORD", Severity.ERROR, Route.INTAKE,
                                     f"Line {line} is a direct user input but is missing who entered it or when.",
                                     line=line))
            elif kind == "rule":
                if not (prov.get("source") or prov.get("citation")):
                    self.add(Finding("RULE_WITHOUT_CITATION", Severity.ERROR, Route.ELIGIBILITY,
                                     f"Line {line} comes from a rule but does not cite it.", line=line))
            else:
                self.add(Finding("UNKNOWN_PROVENANCE_KIND", Severity.ERROR, Route.COMPUTATION,
                                 f"Line {line} has an unrecognised provenance type '{kind}'.", line=line))

    def _check_document_provenance(self, line: str, value: Decimal, prov: dict[str, Any]) -> None:
        doc = self.docs_by_id.get(prov.get("doc_id"))
        if doc is None:
            self.add(Finding("BROKEN_PROVENANCE_REF", Severity.ERROR, Route.COMPUTATION,
                             f"Line {line} cites a document that is not in the case file.",
                             line=line, subject=str(prov.get("doc_id"))))
            return
        src = to_dec((doc.get("fields") or {}).get(prov.get("field")))
        if src is None:
            self.add(Finding("BROKEN_PROVENANCE_REF", Severity.ERROR, Route.COMPUTATION,
                             f"Line {line} cites field '{prov.get('field')}' of {prov.get('doc_id')}, which has no value.",
                             line=line, subject=f"{prov.get('doc_id')}.{prov.get('field')}"))
        elif abs(src - value) > self.tol:
            self.add(Finding("PROVENANCE_VALUE_MISMATCH", Severity.ERROR, Route.COMPUTATION,
                             f"Line {line} shows {value} but the cited document field says {src}.",
                             line=line, expected=str(src), actual=str(value)))

    def _ref_exists(self, ref: str) -> bool:
        if ref.startswith("line:"):
            return ref[5:] in self.lines
        if ref.startswith("doc:"):
            doc_id, _, fld = ref[4:].partition(".")
            doc = self.docs_by_id.get(doc_id)
            return doc is not None and (not fld or fld in (doc.get("fields") or {}))
        return ref.startswith(("intake:", "rule:", "worksheet:"))

    # -- 2. recompute every derived line ---------------------------------------
    def check_arithmetic(self) -> None:
        for line in COMPUTED_LINES:
            self.checks_run += 1
            if not self.has(line):
                self.add(Finding("MISSING_REQUIRED_LINE", Severity.ERROR, Route.COMPUTATION,
                                 f"Required line {line} is missing from the draft.", line=line))
        if not (self.has("34") or self.has("37")):
            self.add(Finding("MISSING_REQUIRED_LINE", Severity.ERROR, Route.COMPUTATION,
                             "The draft has neither an overpayment (34) nor an amount owed (37).", line="34/37"))

        v = self.v
        self.expect("1z", sum((v(f"1{c}") for c in "abcdefgh"), ZERO), "lines 1a through 1h")
        total = v("1z") + v("2b") + v("3b") + v("4b") + v("5b") + v("6b") + v("7a") + v("8")
        self.expect("9", total, "1z + 2b + 3b + 4b + 5b + 6b + 7a + 8")
        self.expect("11a", v("9") - v("10"), "line 9 minus line 10")
        if self.has("11b"):
            self.expect("11b", v("11a"), "same as 11a")
        self.expect("14", v("12e") + v("13a") + v("13b"), "12e + 13a + 13b")
        agi = v("11b") if self.has("11b") else v("11a")
        self.expect("15", max(ZERO, agi - v("14")), "AGI minus line 14, not below zero")
        if self.status in R.RATE_SCHEDULES:
            self.expect("16", R.tax_from_rate_schedule(self.status, v("15")), "2025 rate schedule on line 15")
        if self.has("18"):
            self.expect("18", v("16") + v("17"), "16 + 17")
        if self.has("21"):
            self.expect("21", v("19") + v("20"), "19 + 20")
        line22 = max(ZERO, v("16") + v("17") - v("19") - v("20"))
        if self.has("22"):
            self.expect("22", line22, "18 minus 21, not below zero")
        self.expect("24", line22 + v("23"), "line 22 + line 23")
        self.expect("25d", v("25a") + v("25b") + v("25c"), "25a + 25b + 25c")
        line32 = v("27a") + v("28") + v("29") + v("30") + v("31")
        if self.has("32"):
            self.expect("32", line32, "27a + 28 + 29 + 30 + 31")
        self.expect("33", v("25d") + v("26") + line32, "25d + 26 + 32")
        diff = v("33") - v("24")
        if self.has("34") or diff > 0:
            self.expect("34", max(ZERO, diff), "33 minus 24 when payments exceed tax")
        if self.has("37") or diff < 0:
            self.expect("37", max(ZERO, -diff), "24 minus 33 when tax exceeds payments")

    # -- 3. internal contradictions --------------------------------------------
    def check_contradictions(self) -> None:
        v = self.v
        for line in NON_NEGATIVE_LINES:
            self.checks_run += 1
            if self.has(line) and v(line) < 0:
                self.add(Finding("NEGATIVE_AMOUNT", Severity.ERROR, Route.COMPUTATION,
                                 f"Line {line} is negative, which is not possible on this line.", line=line))
        pairs = [("3a", "3b"), ("4b", "4a"), ("5b", "5a")]
        for part, whole in pairs:
            self.checks_run += 1
            if v(part) > v(whole) + self.tol:
                self.add(Finding("PART_EXCEEDS_WHOLE", Severity.ERROR, Route.COMPUTATION,
                                 f"Line {part} ({v(part)}) cannot be larger than line {whole} ({v(whole)}).", line=part))
        self.checks_run += 1
        if v("6b") > v("6a") * Decimal("0.85") + self.tol:
            self.add(Finding("PART_EXCEEDS_WHOLE", Severity.ERROR, Route.COMPUTATION,
                             "Taxable Social Security (6b) is more than 85% of total benefits (6a).", line="6b"))
        self.checks_run += 1
        if v("34") > 0 and v("37") > 0:
            self.add(Finding("REFUND_AND_BALANCE_DUE", Severity.ERROR, Route.COMPUTATION,
                             "The draft shows both a refund (34) and an amount owed (37).", line="34"))
        self.checks_run += 1
        if v("35a") + v("36") > v("34") + self.tol:
            self.add(Finding("REFUND_SPLIT_TOO_LARGE", Severity.ERROR, Route.COMPUTATION,
                             "Refund (35a) plus amount applied to next year (36) is more than the overpayment (34).",
                             line="35a"))

    # -- 4. document totals (re-derived straight from the documents) -----------
    def check_document_totals(self) -> None:
        w2 = self.docs_of("W-2")
        if w2:
            self.expect("1a", sum((self.field(d, "box1") for d in w2), ZERO), "sum of W-2 box 1")
            self.expect("25a", sum((self.field(d, "box2") for d in w2), ZERO), "sum of W-2 box 2")
        ints = self.docs_of("1099-INT")
        if ints:
            self.expect("2b", sum((self.field(d, "box1") + self.field(d, "box3") for d in ints), ZERO),
                        "sum of 1099-INT boxes 1 and 3")
            self.expect("2a", sum((self.field(d, "box8") for d in ints), ZERO), "sum of 1099-INT box 8")
        divs = self.docs_of("1099-DIV")
        if divs:
            self.expect("3b", sum((self.field(d, "box1a") for d in divs), ZERO), "sum of 1099-DIV box 1a")
            self.expect("3a", sum((self.field(d, "box1b") for d in divs), ZERO), "sum of 1099-DIV box 1b")
        ssa = self.docs_of("SSA-1099")
        if ssa:
            self.expect("6a", sum((self.field(d, "box5") for d in ssa), ZERO), "sum of SSA-1099 box 5")
        r_docs = self.docs_of("1099-R")
        if r_docs:
            ira = [d for d in r_docs if (d.get("fields") or {}).get("ira_sep_simple")]
            pen = [d for d in r_docs if not (d.get("fields") or {}).get("ira_sep_simple")]
            self.expect("4a", sum((self.field(d, "box1") for d in ira), ZERO), "sum of IRA 1099-R box 1")
            self.expect("5a", sum((self.field(d, "box1") for d in pen), ZERO), "sum of pension 1099-R box 1")
            for doc in r_docs:
                code = str((doc.get("fields") or {}).get("box7", "")).upper().strip()
                self.checks_run += 1
                if code and code not in {"7", "G", "4"}:
                    self.add(Finding("UNUSUAL_1099R_CODE", Severity.WARNING, Route.ELIGIBILITY,
                                     f"A 1099-R has distribution code '{code}'. Please confirm it is in scope.",
                                     subject=doc.get("doc_id")))
        withheld = ZERO
        for t, box in (("1099-INT", "box4"), ("1099-DIV", "box4"), ("1099-R", "box4"), ("SSA-1099", "box6")):
            withheld += sum((self.field(d, box) for d in self.docs_of(t)), ZERO)
        if any(self.docs_of(t) for t in ("1099-INT", "1099-DIV", "1099-R", "SSA-1099")):
            self.expect("25b", withheld, "federal tax withheld on 1099 / SSA-1099 forms")

    # -- 5. the four rules from spec 3.2 ----------------------------------------
    def _boxes(self) -> dict[str, bool]:
        return {k: bool(v) for k, v in (self.s.get("line_12d") or {}).items()}

    def check_rules(self) -> None:
        tp = self.s.get("taxpayer") or {}
        sp = self.s.get("spouse") or {}
        self.checks_run += 1
        if self.status not in R.FILING_STATUSES:
            self.add(Finding("INVALID_FILING_STATUS", Severity.ERROR, Route.ELIGIBILITY,
                             f"Filing status '{self.status}' is not one of the five allowed statuses.",
                             subject="filing_status"))
            return

        # Rule 1: 1040-SR eligibility.
        tp_senior = R.is_senior(parse_date(tp.get("dob")))
        sp_senior = R.is_senior(parse_date(sp.get("dob"))) if sp else False
        self.checks_run += 1
        if not tp.get("dob"):
            self.add(Finding("MISSING_DOB", Severity.ERROR, Route.INTAKE,
                             "Taxpayer date of birth is missing, so 1040-SR eligibility cannot be checked.",
                             subject="taxpayer.dob"))
        elif not (tp_senior or (self.status == "mfj" and sp_senior)):
            self.add(Finding("NOT_ELIGIBLE_1040SR", Severity.ERROR, Route.HUMAN,
                             "Nobody on this return was born before 2 January 1961, so Form 1040-SR does not apply.",
                             escalation_condition="1", subject="1040-SR"))

        # Rule 2: line 12d boxes and the standard deduction chart.
        boxes = self._boxes()
        self.checks_run += 1
        if boxes.get("taxpayer_65", False) != tp_senior and tp.get("dob"):
            self.add(Finding("BOX_12D_INCONSISTENT", Severity.ERROR, Route.ELIGIBILITY,
                             "The taxpayer 'born before January 2, 1961' box does not match their date of birth.",
                             line="12d", subject="taxpayer_65"))
        if boxes.get("taxpayer_blind", False) != bool(tp.get("blind", False)):
            self.add(Finding("BOX_12D_INCONSISTENT", Severity.ERROR, Route.ELIGIBILITY,
                             "The taxpayer 'blind' box does not match what the taxpayer told intake.",
                             line="12d", subject="taxpayer_blind"))
        spouse_boxes_allowed = self.status in ("mfj", "mfs") and bool(sp)
        if not spouse_boxes_allowed and (boxes.get("spouse_65") or boxes.get("spouse_blind")):
            self.add(Finding("BOX_12D_INCONSISTENT", Severity.ERROR, Route.ELIGIBILITY,
                             "Spouse boxes on line 12d are checked but this filing status has no spouse on the return.",
                             line="12d", subject="spouse_boxes"))
        if spouse_boxes_allowed:
            if sp.get("dob") and boxes.get("spouse_65", False) != sp_senior:
                self.add(Finding("BOX_12D_INCONSISTENT", Severity.ERROR, Route.ELIGIBILITY,
                                 "The spouse 'born before January 2, 1961' box does not match the spouse's date of birth.",
                                 line="12d", subject="spouse_65"))
            if boxes.get("spouse_blind", False) != bool(sp.get("blind", False)):
                self.add(Finding("BOX_12D_INCONSISTENT", Severity.ERROR, Route.ELIGIBILITY,
                                 "The spouse 'blind' box does not match what intake recorded.",
                                 line="12d", subject="spouse_blind"))
        n_boxes = sum(boxes.values())
        if n_boxes > R.MAX_BOXES[self.status]:
            self.add(Finding("TOO_MANY_12D_BOXES", Severity.ERROR, Route.ELIGIBILITY,
                             f"{n_boxes} boxes are checked on line 12d; this filing status allows at most {R.MAX_BOXES[self.status]}.",
                             line="12d"))

        chart_blocked = [
            reason for flag, reason in (
                (tp.get("can_be_claimed_as_dependent"), "the taxpayer can be claimed as a dependent"),
                (sp.get("can_be_claimed_as_dependent") if self.status == "mfj" else False,
                 "the spouse can be claimed as a dependent"),
                (sp.get("itemizes_separately") if self.status == "mfs" else False,
                 "the separately-filing spouse itemizes"),
                (tp.get("dual_status_alien"), "the taxpayer was a dual-status alien"),
            ) if flag
        ]
        if chart_blocked:
            self.add(Finding("STANDARD_DEDUCTION_CHART_NOT_APPLICABLE", Severity.ERROR, Route.HUMAN,
                             "The Standard Deduction Chart cannot be used because " + "; ".join(chart_blocked) + ".",
                             line="12e", escalation_condition="1"))
        else:
            self.expect("12e", R.standard_deduction(self.status, n_boxes),
                        f"Standard Deduction Chart, {self.status.upper()} with {n_boxes} box(es)")

        # Rule 3: enhanced senior deduction (Schedule 1-A -> 13b).
        qualifying = int(tp_senior) + int(sp_senior and self.status == "mfj")
        magi = self.v("11b") if self.has("11b") else self.v("11a")
        expected_13b = R.senior_deduction(self.status, magi, qualifying) + self.v("13b_other")
        if self.status == "mfs" and qualifying and R.SENIOR_DEDUCTION_MFS_DISALLOWED and self.v("13b") > 0:
            self.add(Finding("SENIOR_DEDUCTION_MFS", Severity.WARNING, Route.ELIGIBILITY,
                             "A married person filing separately generally cannot take the $6,000 senior deduction. "
                             "Please confirm against the reference corpus.", line="13b"))
        else:
            self.expect("13b", expected_13b,
                        f"$6,000 x {qualifying} qualifying person(s), phased out at 6% above the MAGI threshold")
        self.checks_run += 1
        if self.v("13b") > 0 and self.v("13b") == self.v("12e") - R.STANDARD_DEDUCTION_BASE[self.status]:
            self.add(Finding("SENIOR_DEDUCTION_CONFLATED", Severity.WARNING, Route.COMPUTATION,
                             "Line 13b equals the extra 65+/blind amount already in 12e. The two are separate deductions; "
                             "check they were not mixed up.", line="13b"))

        # Rule 4: taxable Social Security (line 6b).
        if self.v("6a") > 0:
            other = sum((self.v(x) for x in ("1z", "2b", "3b", "4b", "5b", "7a", "8")), ZERO)
            self.expect("6b", R.taxable_social_security(
                status=self.status, benefits=self.v("6a"), other_income=other,
                tax_exempt_interest=self.v("2a"), adjustments=self.v("10"),
                mfs_lived_apart_all_year=bool(self.s.get("mfs_lived_apart_all_year")),
            ), "Social Security Benefits Worksheet")

        # Filing-status sanity checks.
        self.checks_run += 1
        if self.status in ("mfj", "mfs") and not sp:
            self.add(Finding("SPOUSE_MISSING", Severity.ERROR, Route.INTAKE,
                             "Filing status needs a spouse but no spouse details were collected.", subject="spouse"))
        if self.status in ("hoh", "qss") and not self.s.get("dependents"):
            self.add(Finding("QUALIFYING_PERSON_MISSING", Severity.WARNING, Route.ELIGIBILITY,
                             f"{self.status.upper()} normally needs a qualifying person, but no dependents are listed.",
                             subject="dependents"))

    # -- 6. scope: nothing out of scope may slip through (spec 3.3, 4.4) --------
    def check_scope(self) -> None:
        for flag, label in OUT_OF_SCOPE_INTAKE_FLAGS.items():
            self.checks_run += 1
            if self.flags.get(flag):
                self.add(Finding("OUT_OF_SCOPE", Severity.ERROR, Route.HUMAN,
                                 f"The case includes {label}, which SilverFile does not handle.",
                                 escalation_condition="1", subject=flag))
        for doc in self.docs:
            self.checks_run += 1
            label = OUT_OF_SCOPE_DOC_TYPES.get((doc.get("type") or "").upper())
            if label:
                self.add(Finding("OUT_OF_SCOPE", Severity.ERROR, Route.HUMAN,
                                 f"A {doc.get('type')} was supplied ({label}); this is out of scope.",
                                 escalation_condition="1", subject=doc.get("doc_id")))
            if (doc.get("type") or "").upper() == "1099-R" and (doc.get("fields") or {}).get("box2b_not_determined"):
                self.add(Finding("1099R_TAXABLE_NOT_DETERMINED", Severity.ERROR, Route.HUMAN,
                                 "A 1099-R has 'taxable amount not determined' (box 2b) checked. This needs the Simplified "
                                 "Method or General Rule, which a volunteer must handle.",
                                 escalation_condition="3", subject=doc.get("doc_id")))
        for line, label in (("13a", "a qualified business income deduction"), ("7a", "a capital gain or loss")):
            self.checks_run += 1
            if self.v(line) != 0:
                self.add(Finding("OUT_OF_SCOPE", Severity.ERROR, Route.HUMAN,
                                 f"Line {line} has {label}, which needs a schedule SilverFile does not prepare.",
                                 line=line, escalation_condition="1"))

        # Escalation conditions 2, 4, 5, 6 that must have reached a human.
        self.checks_run += 1
        for i, conflict in enumerate(self.flags.get("user_document_conflicts") or []):
            self.add(Finding("USER_DOCUMENT_CONFLICT", Severity.ERROR, Route.HUMAN,
                             f"Unresolved conflict between what the taxpayer said and a document: {conflict}",
                             escalation_condition="2", subject=f"conflict-{i}"))
        if self.flags.get("advice_requested"):
            self.add(Finding("ADVICE_REQUESTED", Severity.ERROR, Route.HUMAN,
                             "The taxpayer asked for tax advice or planning. A volunteer preparer must follow up.",
                             escalation_condition="4", subject="advice"))
        if self.flags.get("spouse_died_in_tax_year"):
            self.add(Finding("FILING_STATUS_AMBIGUOUS", Severity.ERROR, Route.HUMAN,
                             "A spouse died during the tax year, so MFJ vs QSS vs Single must be decided by a person.",
                             escalation_condition="5", subject="filing_status"))
        if self.flags.get("third_party_answering_without_authority"):
            self.add(Finding("POSSIBLE_EXPLOITATION", Severity.ERROR, Route.HUMAN,
                             "Someone other than the taxpayer answered questions without stated authority.",
                             escalation_condition="6", subject="third_party"))
        refund_to_account = bool(self.flags.get("direct_deposit_requested")) and self.v("35a") > 0
        if refund_to_account and not self.flags.get("refund_account_described_by_taxpayer", False):
            self.add(Finding("POSSIBLE_EXPLOITATION", Severity.ERROR, Route.HUMAN,
                             "The refund goes to a bank account the taxpayer could not describe.",
                             escalation_condition="6", subject="refund_account"))

    # -- 7. people, SSNs and documents (Security) -----------------------------
    def check_identity_and_security(self) -> None:
        people: list[tuple[str, dict[str, Any]]] = [("taxpayer", self.s.get("taxpayer") or {})]
        if self.s.get("spouse"):
            people.append(("spouse", self.s["spouse"]))
        people += [(f"dependent {i + 1}", d) for i, d in enumerate(self.s.get("dependents") or [])]
        seen: dict[str, str] = {}
        for who, person in people:
            self.checks_run += 1
            ok, reason = ssn_is_valid_test_ssn(person.get("ssn"))
            if not ok:
                self.add(Finding("INVALID_SSN", Severity.ERROR, Route.INTAKE,
                                 f"{who.capitalize()}: {reason}. Only 900-999 test SSNs are accepted.",
                                 subject=f"{who}.ssn", escalation_condition="security"))
            elif person["ssn"] in seen:
                self.add(Finding("DUPLICATE_SSN", Severity.ERROR, Route.INTAKE,
                                 f"{who.capitalize()} has the same SSN as {seen[person['ssn']]}.",
                                 subject=f"{who}.ssn"))
            else:
                seen[person["ssn"]] = who

        filer_ssns = {p.get("ssn") for _, p in people[:2]}
        filer_last_names = {(p.get("last_name") or "").strip().lower() for _, p in people[:2]}
        for doc in self.docs:
            fields = doc.get("fields") or {}
            doc_id = doc.get("doc_id")
            doc_ssn = fields.get("employee_ssn") or fields.get("recipient_tin")
            if doc_ssn:
                self.checks_run += 1
                ok, reason = ssn_is_valid_test_ssn(doc_ssn)
                if not ok:
                    self.add(Finding("REAL_SSN_ON_DOCUMENT", Severity.ERROR, Route.HUMAN,
                                     f"Document {doc_id}: {reason}. The document may contain real personal data.",
                                     subject=doc_id, escalation_condition="security"))
                elif doc_ssn not in filer_ssns:
                    self.add(Finding("USER_DOCUMENT_CONFLICT", Severity.ERROR, Route.HUMAN,
                                     f"Document {doc_id} belongs to SSN {mask_ssn(doc_ssn)}, which is not the taxpayer's "
                                     "or spouse's.", subject=doc_id, escalation_condition="2"))
            name = (fields.get("employee_name") or fields.get("recipient_name") or "").strip().lower()
            if name and filer_last_names - {""} and not any(ln and ln in name for ln in filer_last_names):
                self.add(Finding("NAME_MISMATCH", Severity.WARNING, Route.EXTRACTION,
                                 f"The name on document {doc_id} does not match the taxpayer or spouse.",
                                 subject=doc_id))
            # Untrusted text from an LLM-read image: screen for injected instructions.
            for path, text in iter_strings(fields):
                self.checks_run += 1
                hit = find_injection(text)
                if hit:
                    self.add(Finding("PROMPT_INJECTION_SUSPECTED", Severity.ERROR, Route.HUMAN,
                                     f"Document {doc_id} contains text that looks like an instruction to the system "
                                     f"('{hit}' in {path}). It was not acted on.",
                                     subject=f"{doc_id}.{path}", escalation_condition="security"))
            self._check_extraction_quality(doc)

    def _check_extraction_quality(self, doc: dict[str, Any]) -> None:
        doc_id = doc.get("doc_id")
        for name, conf in (doc.get("confidence") or {}).items():
            self.checks_run += 1
            if conf is not None and conf < CONFIDENCE_THRESHOLD:
                self.add(Finding("LOW_EXTRACTION_CONFIDENCE", Severity.ERROR, Route.EXTRACTION,
                                 f"Document {doc_id} field {name} was read with low confidence ({conf:.2f}). "
                                 "Please re-extract or ask the taxpayer.", subject=f"{doc_id}.{name}"))
        if (doc.get("type") or "").upper() != "W-2":
            return
        f = doc.get("fields") or {}
        box1, box2 = to_dec(f.get("box1")), to_dec(f.get("box2"))
        self.checks_run += 1
        if box1 is None:
            self.add(Finding("W2_FIELD_MISSING", Severity.ERROR, Route.EXTRACTION,
                             f"W-2 {doc_id} has no box 1 (wages).", subject=f"{doc_id}.box1"))
        elif box2 is not None and box2 > box1:
            self.add(Finding("W2_IMPLAUSIBLE", Severity.WARNING, Route.EXTRACTION,
                             f"W-2 {doc_id}: tax withheld (box 2) is more than wages (box 1). Possible misread.",
                             subject=f"{doc_id}.box2"))
        box3, box4 = to_dec(f.get("box3")), to_dec(f.get("box4"))
        if box3 is not None and box4 is not None:
            self.checks_run += 1
            expected4 = min(box3, SS_WAGE_BASE_2025) * Decimal("0.062")
            if abs(box4 - expected4) > Decimal("2"):
                self.add(Finding("W2_IMPLAUSIBLE", Severity.WARNING, Route.EXTRACTION,
                                 f"W-2 {doc_id}: Social Security tax (box 4) is not 6.2% of box 3. Possible misread.",
                                 subject=f"{doc_id}.box4"))
        ein = f.get("employer_ein")
        if ein:
            self.checks_run += 1
            if not EIN_FORMAT.match(str(ein)):
                self.add(Finding("W2_IMPLAUSIBLE", Severity.WARNING, Route.EXTRACTION,
                                 f"W-2 {doc_id}: employer EIN is not in the format XX-XXXXXXX.",
                                 subject=f"{doc_id}.employer_ein"))

    # -- run everything --------------------------------------------------------
    def run(self) -> list[Finding]:
        self.check_provenance()
        self.check_arithmetic()
        self.check_contradictions()
        self.check_document_totals()
        self.check_rules()
        self.check_scope()
        self.check_identity_and_security()
        # De-duplicate (two checks can reach the same conclusion).
        unique: dict[tuple[str, str | None, str | None], Finding] = {}
        for f in self.findings:
            unique.setdefault((f.key, f.expected, f.actual), f)
        return list(unique.values())
