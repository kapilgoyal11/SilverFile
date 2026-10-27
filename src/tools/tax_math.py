"""Deterministic tax calculations for SilverFile (tax year 2025).

Every function returns a provenance record (see provenance.py): a dict holding
the amount and how it was calculated. Amounts are whole-dollar ints. Agents
never do arithmetic themselves; they call these functions.

Not handled here: the Qualified Dividends and Capital Gain Tax Worksheet.
A return with line 3a above zero needs a decision from the instructor/humans.
"""
from decimal import ROUND_HALF_UP, Decimal

from src.tools import tax_constants as c
from src.tools.provenance import computed


def round_half_up(value) -> int:
    """Round to a whole dollar, with halves going up (as the IRS tables do).
    Python's round() sends halves to the nearest even number, which is wrong here."""
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _check_status(filing_status: str) -> None:
    if filing_status not in c.FILING_STATUSES:
        raise ValueError(f"unknown filing status {filing_status!r}; use one of {c.FILING_STATUSES}")


def _check_whole_dollars(name: str, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a whole-dollar int, got {value!r}")


def standard_deduction(filing_status: str, boxes_checked: int) -> dict:
    """Line 12e. boxes_checked = how many boxes are ticked on line 12d (0 to 4)."""
    _check_status(filing_status)
    if isinstance(boxes_checked, bool) or not isinstance(boxes_checked, int) or not 0 <= boxes_checked <= 4:
        raise ValueError("boxes_checked must be a whole number from 0 to 4")
    base = c.STANDARD_DEDUCTION_BASE[filing_status]
    per_box = c.ADDITIONAL_PER_BOX[filing_status]
    return computed(
        "12e",
        base + boxes_checked * per_box,
        "base amount + boxes checked x additional amount per box",
        {"filing_status": filing_status, "boxes_checked": boxes_checked,
         "base": base, "per_box": per_box, "source": c.SOURCES["brief"]},
        "standard_deduction",
    )


def _tax_at(amount: Decimal, brackets) -> Decimal:
    """Tax on `amount` using (upper edge, rate) brackets."""
    tax = Decimal(0)
    lower = 0
    for upper, rate in brackets:
        if amount <= lower:
            break
        top = upper if upper is not None else amount
        tax += (min(amount, top) - lower) * rate
        lower = top
    return tax


def _table_row(income: int):
    """The (at least, but less than) row of the IRS Tax Table that holds `income`."""
    if income < 5:
        return 0, 5
    if income < 15:
        return 5, 15
    if income < 25:
        return 15, 25
    if income < 3000:
        low = 25 + (income - 25) // 25 * 25
        return low, low + 25
    low = 3000 + (income - 3000) // 50 * 50
    return low, low + 50


def tax_on_taxable_income(filing_status: str, taxable_income: int) -> dict:
    """Line 16. Tax Table method under $100,000; Tax Computation Worksheet from $100,000."""
    _check_status(filing_status)
    _check_whole_dollars("taxable_income", taxable_income)
    source = c.SOURCES["pub1040"]

    if taxable_income <= 0:
        return computed("16", 0, "taxable income is zero or less, so tax is 0",
                        {"taxable_income": taxable_income, "filing_status": filing_status,
                         "source": source}, "tax_on_taxable_income")

    if taxable_income < 100000:
        low, high = _table_row(taxable_income)
        midpoint = Decimal(low + high) / 2
        amount = round_half_up(_tax_at(midpoint, c.TABLE_BRACKETS[filing_status]))
        return computed(
            "16", amount,
            "IRS Tax Table: tax at the midpoint of the row, rounded half up",
            {"taxable_income": taxable_income, "filing_status": filing_status,
             "method": "tax_table", "row_at_least": low, "row_but_less_than": high,
             "source": source},
            "tax_on_taxable_income",
        )

    for upper, rate, subtract in c.WORKSHEET_ROWS[filing_status]:
        if upper is None or taxable_income <= upper:
            amount = round_half_up(taxable_income * rate - subtract)
            return computed(
                "16", amount,
                "IRS Tax Computation Worksheet: taxable income x rate - subtraction amount, rounded half up",
                {"taxable_income": taxable_income, "filing_status": filing_status,
                 "method": "tax_computation_worksheet", "rate": str(rate),
                 "subtract": str(subtract), "source": source},
                "tax_on_taxable_income",
            )


def taxable_social_security(
    filing_status: str,
    benefits: int,
    wages_1z: int,
    tax_exempt_2a: int,
    interest_2b: int,
    dividends_3b: int,
    ira_4b: int,
    pension_5b: int,
    adjustments: int = 0,
    other_income_8: int = 0,
    capital_gain_7a: int = 0,
    lived_apart_all_year=None,
) -> dict:
    """Line 6b, following IRS Pub. 915 (2025) Worksheet 1, lines 1-19.

    benefits      = total of box 5 on all SSA-1099 forms (this is also line 6a).
    adjustments   = Schedule 1 lines 11-20, 23 and 25 added together (the part of
                    line 26 the worksheet uses; see worksheet line 7).
    lived_apart_all_year = required (True/False) only for filing status "mfs".
    """
    _check_status(filing_status)
    for name, value in (
        ("benefits", benefits), ("wages_1z", wages_1z), ("tax_exempt_2a", tax_exempt_2a),
        ("interest_2b", interest_2b), ("dividends_3b", dividends_3b), ("ira_4b", ira_4b),
        ("pension_5b", pension_5b), ("adjustments", adjustments),
        ("other_income_8", other_income_8), ("capital_gain_7a", capital_gain_7a),
    ):
        _check_whole_dollars(name, value)
    if filing_status == "mfs" and lived_apart_all_year is None:
        raise ValueError("filing status mfs needs lived_apart_all_year (True or False)")

    inputs = {
        "filing_status": filing_status, "benefits_box5": benefits, "wages_1z": wages_1z,
        "tax_exempt_2a": tax_exempt_2a, "interest_2b": interest_2b, "dividends_3b": dividends_3b,
        "ira_4b": ira_4b, "pension_5b": pension_5b, "adjustments": adjustments,
        "other_income_8": other_income_8, "capital_gain_7a": capital_gain_7a,
        "lived_apart_all_year": lived_apart_all_year, "source": c.SOURCES["pub915"],
    }
    lines = {}  # worksheet line number -> value, kept for the audit trail

    def finish(amount: int, note: str) -> dict:
        recorded = {f"line_{n}": str(v) for n, v in lines.items()}
        return computed(
            "6b", amount,
            "IRS Pub. 915 (2025) Worksheet 1, lines 1-19; only the final amount is rounded (half up)",
            {**inputs, "worksheet_lines": recorded, "result_note": note},
            "taxable_social_security",
        )

    lines[1] = benefits
    if benefits <= 0:
        return finish(0, "no net benefits, so none are taxable")
    lines[2] = Decimal(benefits) * c.SS_RATE_LOW
    lines[3] = (wages_1z + interest_2b + dividends_3b + ira_4b + pension_5b
                + other_income_8 + capital_gain_7a)
    lines[4] = tax_exempt_2a
    lines[5] = 0  # exclusions (foreign income, adoption benefits) are out of scope
    lines[6] = lines[2] + lines[3] + lines[4] + lines[5]
    lines[7] = adjustments
    if not lines[7] < lines[6]:
        return finish(0, "line 7 is not less than line 6: none taxable")
    lines[8] = lines[6] - lines[7]

    if filing_status == "mfs" and not lived_apart_all_year:
        # Worksheet note: skip lines 9-16, take 85% of line 8.
        lines[17] = lines[8] * c.SS_RATE_HIGH
    else:
        joint = filing_status == "mfj"
        lines[9] = c.SS_BASE_MFJ if joint else c.SS_BASE_OTHER
        if not lines[9] < lines[8]:
            return finish(0, "line 9 is not less than line 8: none taxable")
        lines[10] = lines[8] - lines[9]
        lines[11] = c.SS_BAND_MFJ if joint else c.SS_BAND_OTHER
        lines[12] = max(lines[10] - lines[11], 0)
        lines[13] = min(lines[10], lines[11])
        lines[14] = Decimal(lines[13]) * c.SS_RATE_LOW
        lines[15] = min(lines[2], lines[14])
        lines[16] = Decimal(lines[12]) * c.SS_RATE_HIGH
        lines[17] = lines[15] + lines[16]

    lines[18] = Decimal(benefits) * c.SS_RATE_HIGH
    lines[19] = min(lines[17], lines[18])
    return finish(round_half_up(lines[19]), "worksheet completed")



def senior_deduction(filing_status: str, agi: int, taxpayer_eligible: bool,
                     spouse_eligible: bool = False) -> dict:
    """Line 13b: Schedule 1-A Part V (lines 31-37), the only Schedule 1-A part in scope.

    agi = line 11a. For the in-scope returns MAGI equals AGI (the add-backs for
    foreign income exclusions are out of scope).
    taxpayer_eligible / spouse_eligible = born before 2 Jan 1961 (decided upstream).
    """
    _check_status(filing_status)
    _check_whole_dollars("agi", agi)
    if not isinstance(taxpayer_eligible, bool) or not isinstance(spouse_eligible, bool):
        raise TypeError("taxpayer_eligible and spouse_eligible must be True or False")
    if spouse_eligible and filing_status != "mfj":
        raise ValueError("only a joint return can claim the senior deduction for a spouse")

    inputs = {"filing_status": filing_status, "agi": agi,
              "taxpayer_eligible": taxpayer_eligible, "spouse_eligible": spouse_eligible,
              "source": c.SOURCES["brief"] + "; IRS Schedule 1-A (2025) Part V"}

    def finish(amount: int, note: str, extra=None) -> dict:
        return computed(
            "13b", amount,
            "Schedule 1-A Part V: each eligible person gets $6,000 minus 6% of (MAGI - threshold), not below 0",
            {**inputs, **(extra or {}), "result_note": note},
            "senior_deduction",
        )

    if filing_status == "mfs":
        return finish(0, "married filing separately cannot claim the enhanced deduction for seniors")
    people = int(taxpayer_eligible) + int(spouse_eligible)
    if people == 0:
        return finish(0, "no eligible person")

    threshold = c.SENIOR_PHASEOUT_START[filing_status]
    line_33 = max(agi - threshold, 0)
    line_34 = round_half_up(Decimal(line_33) * c.SENIOR_PHASEOUT_RATE)
    line_35 = max(c.SENIOR_DEDUCTION_PER_PERSON - line_34, 0)
    return finish(people * line_35, "Part V completed",
                  {"line_31_magi": agi, "line_32_threshold": threshold, "line_33": line_33,
                   "line_34": line_34, "line_35": line_35, "eligible_people": people})



# ---------------------------------------------------------------------------
# Simple derived lines. Formula text uses the same names as the `inputs` keys.
# ---------------------------------------------------------------------------

def _sum_record(line: str, formula: str, parts: dict, tool: str) -> dict:
    for name, value in parts.items():
        _check_whole_dollars(name, value)
    return computed(line, sum(parts.values()), formula, dict(parts), tool)


def total_wages_1z(w2_wages: list) -> dict:
    """Line 1z: W-2 box 1 summed over all W-2s (other 1a-1i items are out of scope)."""
    if not isinstance(w2_wages, list):
        raise TypeError("w2_wages must be a list of whole-dollar ints")
    parts = {f"w2_{i + 1}_box_1": value for i, value in enumerate(w2_wages)}
    return _sum_record("1z", "sum of W-2 box 1 amounts", parts, "total_wages_1z")


def total_income_9(wages_1z, interest_2b, dividends_3b, ira_4b, pension_5b,
                   social_security_6b, capital_gain_7a=0, other_income_8=0) -> dict:
    """Line 9. 7a (capital gains) is out of scope so it is normally 0."""
    return _sum_record(
        "9", "1z + 2b + 3b + 4b + 5b + 6b + 7a + 8",
        {"1z": wages_1z, "2b": interest_2b, "3b": dividends_3b, "4b": ira_4b,
         "5b": pension_5b, "6b": social_security_6b, "7a": capital_gain_7a,
         "8": other_income_8},
        "total_income_9",
    )


def adjusted_gross_income_11a(total_income_9: int, adjustments_10: int) -> dict:
    """Line 11a (the same amount is carried to 11b on page 2 of the form)."""
    _check_whole_dollars("total_income_9", total_income_9)
    _check_whole_dollars("adjustments_10", adjustments_10)
    return computed("11a", total_income_9 - adjustments_10, "9 - 10",
                    {"9": total_income_9, "10": adjustments_10}, "adjusted_gross_income_11a")


def total_deductions_14(standard_deduction_12e: int, additional_deductions_13b: int,
                        qbi_deduction_13a: int = 0) -> dict:
    """Line 14. 13a (business income deduction) is out of scope so it is normally 0."""
    return _sum_record(
        "14", "12e + 13a + 13b",
        {"12e": standard_deduction_12e, "13a": qbi_deduction_13a,
         "13b": additional_deductions_13b},
        "total_deductions_14",
    )


def taxable_income_15(agi_11b: int, total_deductions_14: int) -> dict:
    """Line 15: line 11b minus line 14, and never below zero."""
    _check_whole_dollars("agi_11b", agi_11b)
    _check_whole_dollars("total_deductions_14", total_deductions_14)
    return computed("15", max(agi_11b - total_deductions_14, 0), "max(0, 11b - 14)",
                    {"11b": agi_11b, "14": total_deductions_14}, "taxable_income_15")


def total_tax_24(tax_16: int, schedule_2_line_3_17: int = 0, credits_21: int = 0,
                 other_taxes_23: int = 0) -> dict:
    """Line 24. Lines 17-23 come from Schedules 2 and 3 entered by the user.
    NOTE: the line 17-23 structure is from the 2024 form layout; verify against the
    2025 PDF. credits_21 = line 19 + line 20 (child tax credit + Schedule 3 line 8)."""
    for name, value in (("tax_16", tax_16), ("schedule_2_line_3_17", schedule_2_line_3_17),
                        ("credits_21", credits_21), ("other_taxes_23", other_taxes_23)):
        _check_whole_dollars(name, value)
    amount = max(tax_16 + schedule_2_line_3_17 - credits_21, 0) + other_taxes_23
    return computed("24", amount, "max(0, 16 + 17 - 21) + 23",
                    {"16": tax_16, "17": schedule_2_line_3_17, "21": credits_21,
                     "23": other_taxes_23}, "total_tax_24")


def total_withholding_25d(w2_25a: int, forms_1099_25b: int = 0, other_25c: int = 0) -> dict:
    """Line 25d. Which form's withholding goes on which of 25a-25c is decided upstream."""
    return _sum_record("25d", "25a + 25b + 25c",
                       {"25a": w2_25a, "25b": forms_1099_25b, "25c": other_25c},
                       "total_withholding_25d")


def total_payments_33(withholding_25d: int, estimated_payments_26: int,
                      other_payments_32: int = 0) -> dict:
    """Line 33. Line 32 (other payments and refundable credits) is user input."""
    return _sum_record("33", "25d + 26 + 32",
                       {"25d": withholding_25d, "26": estimated_payments_26,
                        "32": other_payments_32},
                       "total_payments_33")


def refund_or_amount_owed(total_tax_24: int, total_payments_33: int) -> dict:
    """Lines 34 (overpaid) and 37 (amount owed). One of the two is always 0."""
    _check_whole_dollars("total_tax_24", total_tax_24)
    _check_whole_dollars("total_payments_33", total_payments_33)
    inputs = {"24": total_tax_24, "33": total_payments_33}
    return {
        "34": computed("34", max(total_payments_33 - total_tax_24, 0), "max(0, 33 - 24)",
                       inputs, "refund_or_amount_owed"),
        "37": computed("37", max(total_tax_24 - total_payments_33, 0), "max(0, 24 - 33)",
                       dict(inputs), "refund_or_amount_owed"),
    }