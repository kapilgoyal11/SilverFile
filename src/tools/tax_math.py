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