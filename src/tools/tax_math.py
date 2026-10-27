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