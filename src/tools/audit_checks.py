"""Independent audit checks on a finished set of provenance records.

audit_return(provenance) returns a list of findings; an empty list means every
check passed. A finding has the format the shared state uses for
review_findings: {"line", "problem", "send_back_to"}.

This module only reads. It never changes a record (the Review agent must not
fix things itself; it sends work back).
"""
from src.tools import tax_math as tm

REQUIRED_LINES = ("1z", "2a", "2b", "3a", "3b", "4a", "4b", "5a", "5b", "6a", "6b",
                  "9", "10", "11a", "12e", "13b", "14", "15", "16", "24",
                  "25a", "25b", "25c", "25d", "26", "33", "34", "37")
KINDS = {"document", "computed", "user_input"}
KIND_FIELDS = {"document": ("document", "field"),
               "computed": ("formula", "inputs", "tool"),
               "user_input": ("entered_by",)}
# Input names that really mean "the amount on another line".
LINE_ALIASES = {"11b": "11a", "benefits_box5": "6a", "wages_1z": "1z", "tax_exempt_2a": "2a",
                "interest_2b": "2b", "dividends_3b": "3b", "ira_4b": "4b", "pension_5b": "5b",
                "other_income_8": "8", "agi": "11a", "taxable_income": "15"}


def _finding(line, problem, send_back_to="computation"):
    return {"line": line, "problem": problem, "send_back_to": send_back_to}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_structure(provenance):
    findings, valid = [], {}
    for line in REQUIRED_LINES:
        if line not in provenance:
            findings.append(_finding(line, "no provenance record for this line"))
    for line, rec in provenance.items():
        if not isinstance(rec, dict):
            findings.append(_finding(line, "record is not a dict"))
            continue
        problems = []
        if rec.get("line") != line:
            problems.append(f"record says it is line {rec.get('line')!r}")
        amount = rec.get("amount")
        if not _is_int(amount):
            problems.append("amount is not a whole-dollar int")
        elif amount < 0 and line != "11a":
            problems.append("amount is negative")
        kind = rec.get("kind")
        if kind not in KINDS:
            problems.append(f"unknown kind {kind!r}")
        else:
            problems += [f"{kind} record is missing '{key}'"
                         for key in KIND_FIELDS[kind] if key not in rec]
            if kind == "computed" and not isinstance(rec.get("inputs"), dict):
                problems.append("inputs is not a dict")
        if problems:
            findings.append(_finding(line, "; ".join(problems)))
        else:
            valid[line] = rec
    return findings, valid


def _check_inputs(valid):
    """Sums add up, and recorded inputs match the lines they came from."""
    findings = []
    for line, rec in valid.items():
        if rec["kind"] != "computed":
            continue
        inputs = rec["inputs"]
        tokens = [t.strip() for t in str(rec["formula"]).split(" + ")]
        if set(tokens) == set(inputs) and all(_is_int(v) for v in inputs.values()):
            if sum(inputs.values()) != rec["amount"]:
                findings.append(_finding(
                    line, f"amount {rec['amount']} is not the sum of its inputs ({sum(inputs.values())})"))
        for name, value in inputs.items():
            source = LINE_ALIASES.get(name, name)
            if source == line or source not in valid or not _is_int(value):
                continue
            if value != valid[source]["amount"]:
                findings.append(_finding(
                    line, f"input '{name}' is {value} but line {source} is {valid[source]['amount']}"))
    return findings


def _check_relations(valid):
    findings = []

    def amt(line):
        return valid[line]["amount"]

    def has(*lines):
        return all(line in valid for line in lines)

    def own_input(line, name):
        rec = valid.get(line)
        if rec and rec["kind"] == "computed":
            value = rec["inputs"].get(name, 0)
            return value if _is_int(value) else 0
        return 0

    def expect(line, expected, why):
        if line in valid and amt(line) != expected:
            findings.append(_finding(line, f"line {line} is {amt(line)}, but {why} gives {expected}"))

    if has("9", "10"):
        expect("11a", amt("9") - amt("10"), "9 - 10")
    if has("12e", "13b"):
        expect("14", amt("12e") + amt("13b") + own_input("14", "13a"), "12e + 13a + 13b")
    if has("11a", "14"):
        expect("15", max(amt("11a") - amt("14"), 0), "max(0, 11a - 14)")
    if has("16"):
        expect("24", max(amt("16") + own_input("24", "17") - own_input("24", "21"), 0)
               + own_input("24", "23"), "max(0, 16 + 17 - 21) + 23")
    if has("25a", "25b", "25c"):
        expect("25d", amt("25a") + amt("25b") + amt("25c"), "25a + 25b + 25c")
    if has("25d", "26"):
        expect("33", amt("25d") + amt("26") + own_input("33", "32"), "25d + 26 + 32")
    if has("24", "33"):
        expect("34", max(amt("33") - amt("24"), 0), "max(0, 33 - 24)")
        expect("37", max(amt("24") - amt("33"), 0), "max(0, 24 - 33)")
    if has("34", "37") and amt("34") > 0 and amt("37") > 0:
        findings.append(_finding("34", "both a refund (34) and an amount owed (37) are above zero"))

    # Contradictions between lines
    for total, taxable in (("4a", "4b"), ("5a", "5b"), ("6a", "6b")):
        if has(total, taxable) and amt(taxable) > amt(total):
            findings.append(_finding(
                taxable, f"taxable amount {amt(taxable)} is more than the total {amt(total)} (line {total})"))
    if has("6a", "6b") and amt("6b") > (85 * amt("6a") + 99) // 100:
        findings.append(_finding("6b", "taxable Social Security is more than 85% of the benefits"))
    if has("11a", "15") and amt("15") > max(amt("11a"), 0):
        findings.append(_finding("15", "taxable income is more than adjusted gross income"))

    # Out-of-scope amounts that must never reach the return
    if has("3a") and amt("3a") != 0:
        findings.append(_finding("3a", "qualified dividends need a human decision (line 16 worksheet "
                                       "not implemented)", send_back_to="human"))
    if own_input("9", "7a") != 0:
        findings.append(_finding("9", "capital gains (line 7a) are out of scope", send_back_to="human"))
    if own_input("14", "13a") != 0:
        findings.append(_finding("14", "business income deduction (13a) is out of scope",
                                 send_back_to="human"))
    return findings


def _recompute(line, valid, call):
    rec = valid.get(line)
    if rec is None or rec["kind"] != "computed":
        return []
    try:
        fresh = call(rec["inputs"])
    except Exception as err:
        return [_finding(line, f"could not be recomputed from its recorded inputs: {err}")]
    if fresh["amount"] != rec["amount"]:
        return [_finding(line, f"recorded {rec['amount']} but recomputing from its inputs "
                               f"gives {fresh['amount']}")]
    return []


def _check_recompute(valid):
    findings = []
    findings += _recompute("6b", valid, lambda i: tm.taxable_social_security(
        i["filing_status"], i["benefits_box5"], i["wages_1z"], i["tax_exempt_2a"],
        i["interest_2b"], i["dividends_3b"], i["ira_4b"], i["pension_5b"], i["adjustments"],
        i["other_income_8"], i["capital_gain_7a"], i["lived_apart_all_year"]))
    findings += _recompute("12e", valid, lambda i: tm.standard_deduction(
        i["filing_status"], i["boxes_checked"]))
    findings += _recompute("13b", valid, lambda i: tm.senior_deduction(
        i["filing_status"], i["agi"], i["taxpayer_eligible"], i["spouse_eligible"]))
    findings += _recompute("16", valid, lambda i: tm.tax_on_taxable_income(
        i["filing_status"], i["taxable_income"]))
    statuses = {valid[line]["inputs"].get("filing_status") for line in ("6b", "12e", "13b", "16")
                if line in valid and valid[line]["kind"] == "computed"}
    if len(statuses) > 1:
        findings.append(_finding("all", f"records disagree about the filing status: {sorted(map(str, statuses))}"))
    return findings


def audit_return(provenance) -> list:
    """Returns a list of findings. An empty list means every check passed."""
    if not isinstance(provenance, dict) or not provenance:
        return [_finding("all", "provenance is missing or empty")]
    findings, valid = _check_structure(provenance)
    findings += _check_inputs(valid)
    findings += _check_relations(valid)
    findings += _check_recompute(valid)
    return findings