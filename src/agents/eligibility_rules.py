"""Deterministic rules engine for the Eligibility & Rules agent.

No LLM calls here. Each function implements one rule group from
data/policy.md and returns findings that cite rule IDs. Everything in this
file can be tested without an API key.
"""
from __future__ import annotations

from datetime import date

from .eligibility_policy import assert_cited
from .eligibility_schemas import (
    Document,
    EligibilityResult,
    Escalation,
    IntakeData,
    StdDeductionLookup,
)

TAX_YEAR = 2025
SR_CUTOFF = date(1961, 1, 2)          # R1.1 / R3.2: born BEFORE this date

IN_SCOPE_DOCS = {"W-2", "SSA-1099", "1099-R", "1099-INT", "1099-DIV"}   # R6.1

# Known out-of-scope documents → (rule, plain-English reason)
OUT_OF_SCOPE_DOCS = {
    "1099-NEC": ("R6.3", "Self-employment income (1099-NEC) needs a volunteer preparer."),
    "SCHEDULE C": ("R6.3", "Business income needs a volunteer preparer."),
    "1099-K": ("R6.3", "Payment-app or business income needs a volunteer preparer."),
    "1099-B": ("R6.5", "Sales of stock or other investments need a volunteer preparer."),
    "1099-DA": ("R6.6", "Digital assets need a volunteer preparer."),
    "1099-S": ("R6.5", "Real estate sales need a volunteer preparer."),
    "SCHEDULE E": ("R6.4", "Rental income needs a volunteer preparer."),
    "SCHEDULE A": ("R6.2", "Itemized deductions need a volunteer preparer."),
}

# Which income source each document evidences (used for R7.1 conflicts)
DOC_TO_SOURCE = {
    "W-2": "wages",
    "1099-INT": "interest",
    "1099-DIV": "dividends",
    "SSA-1099": "social_security",
    "1099-R": "retirement",
}


def _norm_doc(t: str) -> str:
    return t.strip().upper().replace("FORM ", "")


def _positive(v) -> bool:
    try:
        return float(v or 0) > 0
    except (TypeError, ValueError):
        return True      # unreadable amount: let a human look (fail safe)


def _spouse_death_year(intake: IntakeData) -> int | None:
    sp = intake.spouse
    if sp and sp.date_of_death:
        return sp.date_of_death.year
    return intake.spouse_death_year


def _missing(intake: IntakeData, out: EligibilityResult, slot: str, rule_id: str,
             what: str, source: str) -> None:
    """Missing answer: ask again, unless the taxpayer already said 'not sure'
    (then a volunteer has to confirm it; asking again would just loop)."""
    if slot in intake.unsure_slots:
        out.escalations.append(_esc(
            "MISSING_INFO", rule_id,
            f"The taxpayer wasn't sure of {what}. A volunteer needs to confirm it "
            "before this can be decided.", source))
    else:
        out.escalations.append(_esc(
            "MISSING_INFO", rule_id, f"{what[0].upper() + what[1:]} is missing.",
            source, action="reask_user"))


def _esc(code, rule_id, reason, source, **kw) -> Escalation:
    return Escalation(code=code, rule_id=assert_cited(rule_id), reason=reason,
                      source=source, **kw)


# ------------------------------------------------------------------ R1 / R3
def is_65_or_older(dob: date | None) -> bool:
    """R3.2. Born before 2 Jan 1961 → 65+ for tax year 2025."""
    return dob is not None and dob < SR_CUTOFF


def check_sr_eligibility(intake: IntakeData, out: EligibilityResult) -> None:
    tp_dob = intake.taxpayer.dob
    if tp_dob is None:
        _missing(intake, out, "taxpayer_dob", "R1.1", "the taxpayer's date of birth",
                 "intake.birth_date")
        return
    if is_65_or_older(tp_dob):
        out.eligible_1040sr = True
        out.citations += ["R1.1", "R1.3"]
        return
    spouse = intake.spouse
    if intake.filing_status == "mfj" and spouse and is_65_or_older(spouse.dob):
        out.eligible_1040sr = True
        out.citations += ["R1.2", "R1.3"]
        return
    out.eligible_1040sr = False
    out.citations.append("R1.4")
    out.escalations.append(_esc(
        "NOT_SR_ELIGIBLE", "R1.4",
        "Neither the taxpayer nor a joint-filing spouse was born before "
        "2 January 1961, so Form 1040-SR does not apply.",
        "intake.taxpayer.dob"))


def count_12d_boxes(intake: IntakeData, out: EligibilityResult) -> None:
    tp, sp = intake.taxpayer, intake.spouse
    boxes = {
        "taxpayer_65": is_65_or_older(tp.dob),
        "taxpayer_blind": bool(tp.blind),
        "spouse_65": False,
        "spouse_blind": False,
    }
    if intake.filing_status == "mfj" and sp is not None:      # R3.3
        boxes["spouse_65"] = is_65_or_older(sp.dob)
        boxes["spouse_blind"] = bool(sp.blind)
    out.boxes_12d = boxes
    out.boxes_checked = sum(boxes.values())
    out.age_box_count = boxes["taxpayer_65"] + boxes["spouse_65"]
    out.blind_box_count = boxes["taxpayer_blind"] + boxes["spouse_blind"]
    out.senior_taxpayer = boxes["taxpayer_65"]
    out.senior_spouse = boxes["spouse_65"]          # only ever True on mfj (R3.3)
    out.citations += ["R3.1", "R3.3"]


# ------------------------------------------------------------------ R2
def validate_filing_status(intake: IntakeData, out: EligibilityResult) -> None:
    fs, sp = intake.filing_status, intake.spouse
    out.filing_status = fs

    if fs is None:
        _missing(intake, out, "filing_status", "R2.1", "the filing status", "intake.filing_status")
        return

    died = _spouse_death_year(intake)
    # R2.3 — spouse died during the tax year: always ambiguous, always a human.
    if died == TAX_YEAR or (died is None and "spouse_death_year" in intake.unsure_slots
                            and intake.spouse_died_recent):
        out.escalations.append(_esc(
            "FILING_STATUS_AMBIGUOUS", "R2.3",
            "The spouse died during 2025 (or the year is unsure). Choosing between "
            "joint, surviving spouse and single status needs a volunteer preparer.",
            "intake.spouse_death_year"))
        return

    problems: list[tuple[str, str]] = []
    living_spouse = sp is not None and died is None and not intake.spouse_died_recent
    if fs in ("mfj", "mfs") and sp is None:
        problems.append(("R2.2", "A married status was chosen but no spouse details were given."))
    if fs == "hoh":
        if not intake.has_qualifying_person:
            problems.append(("R2.4", "Head of household needs a qualifying person."))
        if living_spouse:
            problems.append(("R2.4", "Head of household was chosen while a living spouse is reported."))
    if fs == "qss":
        died_ok = died in (TAX_YEAR - 1, TAX_YEAR - 2)
        has_child = any(d.qualifying_child for d in intake.dependents)
        if not (died_ok and has_child):
            problems.append(("R2.5", "Qualifying surviving spouse needs a spouse who died in "
                                     "2023 or 2024 and a qualifying child."))
    if fs == "single" and living_spouse:
        problems.append(("R2.6", "Single was chosen but a living spouse is reported."))

    for rule_id, reason in problems:
        out.escalations.append(_esc(
            "FILING_STATUS_AMBIGUOUS", rule_id, reason, "intake.filing_status"))
    out.filing_status_valid = not problems
    out.citations.append("R2.1")


# ------------------------------------------------------------------ R4
def check_spouse_details(intake: IntakeData, out: EligibilityResult) -> None:
    """On a joint return the spouse's age and blindness feed R1.2, R3 and R5."""
    if intake.filing_status == "mfj" and intake.spouse is not None and intake.spouse.dob is None:
        _missing(intake, out, "spouse_dob", "R3.1", "the spouse's date of birth",
                 "intake.spouse_birth_date")


# Other "not sure" answers that block a rule decision: slot -> (rule, words, source)
UNSURE_BLOCKERS = {
    "taxpayer_blind": ("R3.1", "whether they are legally blind", "intake.is_blind"),
    "spouse_blind": ("R3.1", "whether their spouse is legally blind", "intake.spouse_is_blind"),
    "claimable_as_dependent": ("R4.1", "whether someone else can claim them as a dependent",
                               "intake.claimable_as_dependent"),
    "full_year_resident": ("R4.3", "whether they lived in the US all year",
                           "intake.full_year_resident"),
    "spouse_itemizes": ("R4.2", "whether their spouse itemizes deductions", "intake.spouse_itemizes"),
    "box2b_checked": ("R6.9", "whether box 2b is checked on a 1099-R", "intake.box2b_checked"),
}


def check_unsure_answers(intake: IntakeData, out: EligibilityResult) -> None:
    for slot, (rule_id, what, src) in UNSURE_BLOCKERS.items():
        if slot in intake.unsure_slots:
            _missing(intake, out, slot, rule_id, what, src)


def check_reported_scope(intake: IntakeData, documents: list[Document],
                         out: EligibilityResult) -> None:
    """Out-of-scope facts the taxpayer told Intake about (before any document arrives)."""
    if intake.wants_itemize:
        out.escalations.append(_esc(
            "OUT_OF_SCOPE", "R6.2", "The taxpayer wants to itemize deductions. "
            "SilverFile only uses the standard deduction.", "intake.wants_itemize"))
    doc_flagged = any(_norm_doc(d.doc_type) == "1099-R"
                      and d.fields.get("box_2b_taxable_not_determined") for d in documents)
    if intake.box2b_reported and not doc_flagged:
        out.escalations.append(_esc(
            "UNDETERMINED_TAXABLE_1099R", "R6.9",
            "The taxpayer says a 1099-R has box 2b ('taxable amount not determined') "
            "checked. Working that out needs a volunteer preparer.", "intake.box2b_checked"))
    if intake.other_income:
        from .eligibility_guardrails import keyword_scan   # local import avoids a cycle
        detail = intake.other_income_detail or ""
        rules = sorted({f.rule_id for f in keyword_scan(detail) if f.category == "OUT_OF_SCOPE"}) \
            or ["R6.11"]
        for rule_id in rules:
            out.escalations.append(_esc(
                "OUT_OF_SCOPE", rule_id,
                "The taxpayer reported other income" + (f" ({detail[:80]})" if detail else "")
                + ". SilverFile only handles W-2, SSA-1099, 1099-R, 1099-INT and 1099-DIV.",
                "intake.other_income"))


def check_std_deduction_chart(intake: IntakeData, out: EligibilityResult) -> None:
    blockers = []
    if intake.can_be_claimed_as_dependent:
        blockers.append(("R4.1", "Someone else can claim the taxpayer as a dependent.",
                         "intake.can_be_claimed_as_dependent"))
    if intake.filing_status == "mfs" and intake.spouse and intake.spouse.itemizes:
        blockers.append(("R4.2", "Filing separately and the spouse itemizes deductions.",
                         "intake.spouse.itemizes"))
    if intake.dual_status_alien:
        blockers.append(("R4.3", "The taxpayer was a dual-status alien.",
                         "intake.dual_status_alien"))
    for rule_id, reason, src in blockers:
        out.escalations.append(_esc("STD_DEDUCTION_CHART_NA", rule_id,
                                    reason + " The standard deduction chart does not apply.", src))

    out.std_deduction_chart_applies = not blockers and intake.filing_status is not None
    if out.std_deduction_chart_applies:
        rate = "unmarried" if intake.filing_status in ("single", "hoh") else "married"
        out.std_deduction_lookup = StdDeductionLookup(
            filing_status=intake.filing_status,
            boxes_checked=out.boxes_checked,
            per_box_rate=rate,
        )
        out.citations.append("R4.0")


# ------------------------------------------------------------------ R5
def determine_schedules(intake: IntakeData, out: EligibilityResult) -> None:
    qualifiers = int(is_65_or_older(intake.taxpayer.dob))
    if intake.filing_status == "mfj" and intake.spouse:
        qualifiers += int(is_65_or_older(intake.spouse.dob))
    out.schedule_1a_qualifiers = qualifiers

    schedules = {s.strip() for s in intake.schedules_reported if s.strip() in {"1", "2", "3"}}
    if qualifiers:
        schedules.add("1-A")
        out.citations += ["R5.1", "R5.2"]
    if schedules - {"1-A"}:
        out.citations.append("R5.3")
    out.schedules = sorted(schedules)


# ------------------------------------------------------------------ R6
def check_scope(intake: IntakeData, documents: list[Document], out: EligibilityResult) -> None:
    for doc in documents:
        t = _norm_doc(doc.doc_type)
        src = f"documents.{doc.doc_id}"
        if t in OUT_OF_SCOPE_DOCS:
            rule_id, reason = OUT_OF_SCOPE_DOCS[t]
            out.escalations.append(_esc("OUT_OF_SCOPE", rule_id, reason, src))
        elif t not in IN_SCOPE_DOCS:
            out.escalations.append(_esc(
                "OUT_OF_SCOPE", "R6.1",
                f"Document type '{doc.doc_type}' is not one SilverFile handles.", src))
        elif t == "1099-R" and doc.fields.get("box_2b_taxable_not_determined"):
            out.escalations.append(_esc(
                "UNDETERMINED_TAXABLE_1099R", "R6.9",
                "This 1099-R says the taxable amount was not determined (box 2b). "
                "Working that out needs a volunteer preparer.", src))
        elif t == "1099-DIV" and _positive(doc.fields.get("box_1b_qualified_dividends")):
            out.escalations.append(_esc(
                "OUT_OF_SCOPE", "R6.10",
                "This 1099-DIV shows qualified dividends (box 1b). Taxing those needs "
                "a worksheet SilverFile does not handle, so a volunteer preparer "
                "should finish this return.", src))

    # Documents the user mentioned but has not uploaded yet
    for name in intake.documents_reported:
        t = _norm_doc(name)
        if t in OUT_OF_SCOPE_DOCS:
            rule_id, reason = OUT_OF_SCOPE_DOCS[t]
            out.escalations.append(_esc("OUT_OF_SCOPE", rule_id, reason,
                                        f"intake.documents_reported.{t}"))
    out.citations.append("R6.1")


# ------------------------------------------------------------------ R7
def check_user_document_conflicts(intake: IntakeData, documents: list[Document],
                                  out: EligibilityResult) -> None:
    denied = {s.strip().lower() for s in intake.income_sources_denied}
    for doc in documents:
        source = DOC_TO_SOURCE.get(_norm_doc(doc.doc_type))
        if source and source in denied:
            out.escalations.append(_esc(
                "USER_DOCUMENT_CONFLICT", "R7.1",
                f"The taxpayer said they had no {source.replace('_', ' ')} income, "
                f"but a {doc.doc_type} was supplied.",
                f"documents.{doc.doc_id}"))


# ------------------------------------------------------------------ R9 (structured)
def check_exploitation_signals(intake: IntakeData, out: EligibilityResult) -> None:
    if intake.refund.wants_direct_deposit and intake.refund.taxpayer_can_describe_account is False:
        out.escalations.append(_esc(
            "EXPLOITATION_RISK", "R9.1",
            "The refund would go to an account the taxpayer could not describe.",
            "intake.refund"))
    r = intake.respondent
    if not r.is_taxpayer and not (r.stated_authority or "").strip():
        out.escalations.append(_esc(
            "EXPLOITATION_RISK", "R9.2",
            "Someone other than the taxpayer is answering, with no stated authority "
            "(such as power of attorney).",
            "intake.respondent"))


# ------------------------------------------------------------------ entry point
def run_rules(intake: IntakeData, documents: list[Document]) -> EligibilityResult:
    out = EligibilityResult()
    validate_filing_status(intake, out)
    check_sr_eligibility(intake, out)
    count_12d_boxes(intake, out)
    check_spouse_details(intake, out)
    check_unsure_answers(intake, out)
    check_std_deduction_chart(intake, out)
    determine_schedules(intake, out)
    check_scope(intake, documents, out)
    check_reported_scope(intake, documents, out)
    check_user_document_conflicts(intake, documents, out)
    check_exploitation_signals(intake, out)

    out.in_scope = not any(
        e.code in ("OUT_OF_SCOPE", "UNDETERMINED_TAXABLE_1099R", "NOT_SR_ELIGIBLE")
        for e in out.escalations)
    out.citations = sorted(set(out.citations), key=lambda r: [int(x) for x in r[1:].split(".")])
    return out
