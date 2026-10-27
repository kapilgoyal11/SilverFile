"""Tax-year 2025 reference values used by the Review / Audit agent.

These are the audit agent's OWN copy of the rules, kept separate from the
Computation agent's MCP tools on purpose: an auditor that re-uses the code it
is auditing cannot catch that code's bugs. Every value below is taken from the
assignment spec (section 3.2) or from Form 1040-SR (2025) itself.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TAX_YEAR = 2025

# 1040-SR eligibility / "age 65" test: born before 2 January 1961 (spec 3.2 rule 1).
SENIOR_BIRTH_CUTOFF = date(1961, 1, 2)

FILING_STATUSES = ("single", "mfj", "mfs", "hoh", "qss")

# Standard deduction base amounts (spec 3.2 rule 2; Form 1040-SR p.4 chart).
STANDARD_DEDUCTION_BASE: dict[str, Decimal] = {
    "single": Decimal("15750"),
    "mfs": Decimal("15750"),
    "mfj": Decimal("31500"),
    "qss": Decimal("31500"),
    "hoh": Decimal("23625"),
}

# Each box on line 12d adds $2,000 if unmarried and not a surviving spouse,
# otherwise $1,600 (spec 3.2 rule 2).
ADDITIONAL_PER_BOX: dict[str, Decimal] = {
    "single": Decimal("2000"),
    "hoh": Decimal("2000"),
    "mfj": Decimal("1600"),
    "qss": Decimal("1600"),
    "mfs": Decimal("1600"),
}

# Maximum boxes that can be checked on line 12d per filing status (chart p.4).
MAX_BOXES: dict[str, int] = {"single": 2, "hoh": 2, "qss": 2, "mfj": 4, "mfs": 4}

# Enhanced deduction for seniors, Schedule 1-A -> line 13b (spec 3.2 rule 3).
SENIOR_DEDUCTION_PER_PERSON = Decimal("6000")
SENIOR_DEDUCTION_PHASEOUT_RATE = Decimal("0.06")
SENIOR_DEDUCTION_THRESHOLD: dict[str, Decimal] = {
    "single": Decimal("75000"),
    "hoh": Decimal("75000"),
    "qss": Decimal("75000"),
    "mfs": Decimal("75000"),
    "mfj": Decimal("150000"),
}
# Under the statute a married person must file jointly to claim this deduction.
# The assignment spec does not mention it, so the audit raises it as a WARNING
# (not an error) and routes it to Eligibility & Rules to confirm.
SENIOR_DEDUCTION_MFS_DISALLOWED = True

# Taxable Social Security worksheet base / second-tier amounts (spec 3.2 rule 4).
SS_BASE: dict[str, Decimal] = {"mfj": Decimal("32000"), "default": Decimal("25000")}
SS_SECOND_TIER: dict[str, Decimal] = {"mfj": Decimal("44000"), "default": Decimal("34000")}

# 2025 rate schedules: list of (upper bound of bracket, rate). None = no cap.
_B = Decimal
RATE_SCHEDULES: dict[str, list[tuple[Decimal | None, Decimal]]] = {
    "single": [
        (_B("11925"), _B("0.10")), (_B("48475"), _B("0.12")), (_B("103350"), _B("0.22")),
        (_B("197300"), _B("0.24")), (_B("250525"), _B("0.32")), (_B("626350"), _B("0.35")),
        (None, _B("0.37")),
    ],
    "mfj": [
        (_B("23850"), _B("0.10")), (_B("96950"), _B("0.12")), (_B("206700"), _B("0.22")),
        (_B("394600"), _B("0.24")), (_B("501050"), _B("0.32")), (_B("751600"), _B("0.35")),
        (None, _B("0.37")),
    ],
    "mfs": [
        (_B("11925"), _B("0.10")), (_B("48475"), _B("0.12")), (_B("103350"), _B("0.22")),
        (_B("197300"), _B("0.24")), (_B("250525"), _B("0.32")), (_B("375800"), _B("0.35")),
        (None, _B("0.37")),
    ],
    "hoh": [
        (_B("17000"), _B("0.10")), (_B("64850"), _B("0.12")), (_B("103350"), _B("0.22")),
        (_B("197300"), _B("0.24")), (_B("250500"), _B("0.32")), (_B("626350"), _B("0.35")),
        (None, _B("0.37")),
    ],
}
RATE_SCHEDULES["qss"] = RATE_SCHEDULES["mfj"]


def dollars(value: Decimal) -> Decimal:
    """Round to whole dollars the way the form does (half up)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def is_senior(dob: date | None) -> bool:
    return dob is not None and dob < SENIOR_BIRTH_CUTOFF


def standard_deduction(status: str, boxes_checked: int) -> Decimal:
    return STANDARD_DEDUCTION_BASE[status] + ADDITIONAL_PER_BOX[status] * boxes_checked


def senior_deduction(status: str, magi: Decimal, qualifying_people: int) -> Decimal:
    """Schedule 1-A Part V. Phase-out is applied per qualifying individual."""
    if qualifying_people <= 0:
        return Decimal("0")
    if status == "mfs" and SENIOR_DEDUCTION_MFS_DISALLOWED:
        return Decimal("0")
    excess = max(Decimal("0"), magi - SENIOR_DEDUCTION_THRESHOLD[status])
    per_person = max(Decimal("0"), SENIOR_DEDUCTION_PER_PERSON - SENIOR_DEDUCTION_PHASEOUT_RATE * excess)
    return dollars(per_person * qualifying_people)


def tax_from_rate_schedule(status: str, taxable_income: Decimal) -> Decimal:
    """Line 16 per the rate schedule (spec 3.1 says 'Rate schedule')."""
    remaining_floor = Decimal("0")
    tax = Decimal("0")
    for upper, rate in RATE_SCHEDULES[status]:
        if upper is None or taxable_income <= upper:
            tax += (taxable_income - remaining_floor) * rate
            break
        tax += (upper - remaining_floor) * rate
        remaining_floor = upper
    return dollars(max(tax, Decimal("0")))


def taxable_social_security(
    *,
    status: str,
    benefits: Decimal,
    other_income: Decimal,
    tax_exempt_interest: Decimal,
    adjustments: Decimal,
    mfs_lived_apart_all_year: bool,
) -> Decimal:
    """Social Security Benefits Worksheet (Form 1040 instructions), line 6b.

    other_income = lines 1z + 2b + 3b + 4b + 5b + 7a + 8 (everything except 6b).
    """
    if benefits <= 0:
        return Decimal("0")
    half = benefits / 2
    provisional = half + other_income + tax_exempt_interest
    if adjustments >= provisional:
        return Decimal("0")
    line7 = provisional - adjustments
    # MFS and lived with spouse at any time: no base amount, 85% straight away.
    if status == "mfs" and not mfs_lived_apart_all_year:
        return dollars(min(line7 * Decimal("0.85"), benefits * Decimal("0.85")))
    key = "mfj" if status == "mfj" else "default"
    base = SS_BASE[key]
    if line7 <= base:
        return Decimal("0")
    line9 = line7 - base
    line10 = SS_SECOND_TIER[key] - base
    line11 = max(Decimal("0"), line9 - line10)
    line12 = min(line9, line10)
    line14 = min(half, line12 / 2)
    line16 = line14 + line11 * Decimal("0.85")
    return dollars(min(line16, benefits * Decimal("0.85")))
