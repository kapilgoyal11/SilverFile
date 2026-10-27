"""Computation agent (SilverFile).

Reads the shared state, calls the deterministic tools in tax_math.py in the
right order, and writes one provenance record per return line. It does no
arithmetic itself and makes no tax decisions: it maps document boxes to return
lines, and anything it cannot source becomes an escalation instead of a guess.

READS
  state["intake"]       required: filing_status ("single","mfj","mfs","hoh","qss")
                        mfs only: lived_apart_all_year (bool)
                        optional whole-dollar ints, default 0:
                          estimated_payments (line 26), adjustments_10,
                          adjustments_ss_worksheet (Schedule 1 lines 11-20, 23, 25;
                          required if adjustments_10 > 0 and there are SS benefits),
                          other_income_8, schedule_2_line_3, credits_21,
                          other_taxes_23, other_withholding_25c, other_payments_32
                        optional text: entered_by (default "taxpayer")
  state["eligibility"]  required: in_scope (must be True), boxes_checked (line 12d,
                        0-4), senior_taxpayer (bool); optional: senior_spouse (bool)
  state["documents"]    list of {"doc_id", "doc_type", "fields": {...}}
    W-2        box_1_wages, box_2_withholding
    SSA-1099   box_5_net_benefits, box_6_withheld
    1099-INT   box_1_interest, box_8_tax_exempt_interest, box_4_withholding
    1099-DIV   box_1a_ordinary_dividends, box_1b_qualified_dividends, box_4_withholding
    1099-R     box_1_gross, box_2a_taxable, box_2b_taxable_not_determined (bool),
               box_4_withholding, is_ira (bool)
    Withholding fields must be present (0 if none): a missing field is never read as 0.

WRITES
  provenance   {line: provenance record}. Absent if the agent is blocked.
  escalations  one entry if blocked: {"raised_by", "reason", "detail", "resolved"}
  handoff_log  one entry

UNVERIFIED (check against the 2025 PDF): SSA-1099 box 6 on line 25b; the structure
of lines 17-23 (taken from the 2024 layout).
"""
from src.tools import tax_math as tm
from src.tools.provenance import computed, from_document, user_input

TOOL = "computation_agent"


class Blocked(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _docs(state, doc_type):
    return [d for d in state.get("documents", []) if d.get("doc_type") == doc_type]


def _need(mapping, key, where):
    if key not in mapping:
        raise Blocked("missing_input", f"{where} is missing '{key}'")
    return mapping[key]


def _is_ira(doc):
    flag = _need(doc.get("fields", {}), "is_ira", f"document {doc.get('doc_id')}")
    if not isinstance(flag, bool):
        raise Blocked("invalid_input", f"document {doc.get('doc_id')}: is_ira must be True or False")
    return flag


def _from_docs(line, state, sources, where=None):
    """Record for a line that comes from documents.
    sources = [(doc_type, field), ...]; where = optional filter on the document."""
    found = []
    for doc_type, field in sources:
        for doc in _docs(state, doc_type):
            if where is not None and not where(doc):
                continue
            value = _need(doc.get("fields", {}), field, f"document {doc.get('doc_id')}")
            found.append((doc.get("doc_id"), field, value))
    if not found:
        return computed(line, 0, "no matching documents supplied", {"documents": 0}, TOOL)
    if len(found) == 1:
        doc_id, field, value = found[0]
        return from_document(line, value, doc_id, field)
    parts = {f"{doc_id}.{field}": value for doc_id, field, value in found}
    return tm.sum_record(line, " + ".join(parts), parts, TOOL)


def _user_line(line, intake, key):
    if key in intake:
        return user_input(line, intake[key], intake.get("entered_by", "taxpayer"),
                          f"intake answer '{key}'")
    return user_input(line, 0, "none reported", f"'{key}' not provided; treated as 0")


def _compute(state):
    intake = state.get("intake", {})
    elig = state.get("eligibility", {})
    if elig.get("in_scope") is not True:
        raise Blocked("not_cleared_by_eligibility", "eligibility has not marked this return in_scope=True")
    status = _need(intake, "filing_status", "intake")
    boxes_checked = _need(elig, "boxes_checked", "eligibility")
    senior_taxpayer = _need(elig, "senior_taxpayer", "eligibility")
    senior_spouse = elig.get("senior_spouse", False)

    for doc in _docs(state, "1099-R"):
        flag = _need(doc.get("fields", {}), "box_2b_taxable_not_determined",
                     f"document {doc.get('doc_id')}")
        if flag is True:
            raise Blocked("out_of_scope:1099r_taxable_not_determined",
                          f"{doc.get('doc_id')}: taxable amount not determined (box 2b); a human must handle it")

    r = {}

    def amt(line):
        return r[line]["amount"]

    # Income lines that come straight from documents
    r["1z"] = _from_docs("1z", state, [("W-2", "box_1_wages")])
    r["2a"] = _from_docs("2a", state, [("1099-INT", "box_8_tax_exempt_interest")])
    r["2b"] = _from_docs("2b", state, [("1099-INT", "box_1_interest")])
    r["3a"] = _from_docs("3a", state, [("1099-DIV", "box_1b_qualified_dividends")])
    if amt("3a") > 0:
        raise Blocked("needs_decision:qualified_dividends",
                      "line 3a is above zero: the IRS uses the Qualified Dividends and Capital Gain "
                      "Tax Worksheet for line 16, which is not implemented; a human must decide")
    r["3b"] = _from_docs("3b", state, [("1099-DIV", "box_1a_ordinary_dividends")])
    r["4a"] = _from_docs("4a", state, [("1099-R", "box_1_gross")], where=_is_ira)
    r["4b"] = _from_docs("4b", state, [("1099-R", "box_2a_taxable")], where=_is_ira)
    r["5a"] = _from_docs("5a", state, [("1099-R", "box_1_gross")], where=lambda d: not _is_ira(d))
    r["5b"] = _from_docs("5b", state, [("1099-R", "box_2a_taxable")], where=lambda d: not _is_ira(d))
    r["6a"] = _from_docs("6a", state, [("SSA-1099", "box_5_net_benefits")])

    # Taxpayer-entered amounts that feed income
    r["8"] = _user_line("8", intake, "other_income_8")
    r["10"] = _user_line("10", intake, "adjustments_10")

    # Line 6b needs the rest of income first, and then feeds line 9.
    if "adjustments_ss_worksheet" in intake:
        adjustments_for_ss = intake["adjustments_ss_worksheet"]
    elif amt("6a") > 0 and amt("10") > 0:
        raise Blocked("missing_input",
                      "adjustments were reported but 'adjustments_ss_worksheet' (Schedule 1 lines "
                      "11-20, 23 and 25) is missing, and Social Security worksheet line 7 needs it")
    else:
        adjustments_for_ss = amt("10")
    r["6b"] = tm.taxable_social_security(
        status, benefits=amt("6a"), wages_1z=amt("1z"), tax_exempt_2a=amt("2a"),
        interest_2b=amt("2b"), dividends_3b=amt("3b"), ira_4b=amt("4b"), pension_5b=amt("5b"),
        adjustments=adjustments_for_ss, other_income_8=amt("8"),
        lived_apart_all_year=intake.get("lived_apart_all_year"))

    r["9"] = tm.total_income_9(amt("1z"), amt("2b"), amt("3b"), amt("4b"), amt("5b"),
                               amt("6b"), other_income_8=amt("8"))
    r["11a"] = tm.adjusted_gross_income_11a(amt("9"), amt("10"))
    r["12e"] = tm.standard_deduction(status, boxes_checked)
    r["13b"] = tm.senior_deduction(status, amt("11a"), senior_taxpayer, senior_spouse)
    r["14"] = tm.total_deductions_14(amt("12e"), amt("13b"))
    r["15"] = tm.taxable_income_15(amt("11a"), amt("14"))
    r["16"] = tm.tax_on_taxable_income(status, amt("15"))

    r["17"] = _user_line("17", intake, "schedule_2_line_3")
    r["21"] = _user_line("21", intake, "credits_21")
    r["23"] = _user_line("23", intake, "other_taxes_23")
    r["24"] = tm.total_tax_24(amt("16"), amt("17"), amt("21"), amt("23"))

    r["25a"] = _from_docs("25a", state, [("W-2", "box_2_withholding")])
    r["25b"] = _from_docs("25b", state, [("1099-INT", "box_4_withholding"),
                                         ("1099-DIV", "box_4_withholding"),
                                         ("1099-R", "box_4_withholding"),
                                         ("SSA-1099", "box_6_withheld")])
    r["25c"] = _user_line("25c", intake, "other_withholding_25c")
    r["25d"] = tm.total_withholding_25d(amt("25a"), amt("25b"), amt("25c"))
    r["26"] = _user_line("26", intake, "estimated_payments")
    r["32"] = _user_line("32", intake, "other_payments_32")
    r["33"] = tm.total_payments_33(amt("25d"), amt("26"), amt("32"))
    result = tm.refund_or_amount_owed(amt("24"), amt("33"))
    r["34"], r["37"] = result["34"], result["37"]

    return {"provenance": r,
            "handoff_log": [{"agent": "computation", "result": "ok", "lines_written": len(r)}]}


def _blocked(reason, detail):
    return {"escalations": [{"raised_by": "computation", "reason": reason,
                             "detail": detail, "resolved": False}],
            "handoff_log": [{"agent": "computation", "result": "escalated", "reason": reason}]}


def computation_agent(state: dict) -> dict:
    """LangGraph node. Returns only the state fields it changes."""
    try:
        return _compute(state)
    except Blocked as err:
        return _blocked(err.reason, err.detail)
    except (TypeError, ValueError) as err:
        return _blocked("invalid_input", str(err))