"""Tax year 2025 constants for SilverFile.

Every number is traceable to a source in SOURCES. Nothing here is a
calculation; calculations live in tax_math.py. Decimal is used (not float)
so rounding matches the IRS tables exactly.
"""
from decimal import Decimal as D

TAX_YEAR = 2025

SOURCES = {
    "brief": "SilverFile assignment brief, section 3.2",
    "pub1040": "IRS Publication 1040 (2025): Tax Table and Tax Computation Worksheet, line 16 - https://www.irs.gov/publications/p1040",
    "pub915": "IRS Publication 915 (2025): Worksheet 1, taxable Social Security - https://www.irs.gov/publications/p915",
}

# Filing status codes. Every agent must use exactly these strings.
FILING_STATUSES = ("single", "mfj", "mfs", "hoh", "qss")

# --- Standard deduction (source: brief) ---
STANDARD_DEDUCTION_BASE = {"single": 15750, "mfs": 15750, "mfj": 31500, "qss": 31500, "hoh": 23625}
ADDITIONAL_PER_BOX = {"single": 2000, "hoh": 2000, "mfj": 1600, "mfs": 1600, "qss": 1600}

# --- Senior deduction, Schedule 1-A (source: brief; NOT yet checked against the IRS form) ---
SENIOR_DEDUCTION_PER_PERSON = 6000
SENIOR_PHASEOUT_START = {"single": 75000, "hoh": 75000, "qss": 75000, "mfj": 150000}
SENIOR_PHASEOUT_RATE = D("0.06")

# --- Line 16, Tax Table, taxable income under $100,000 (source: pub1040) ---
# (upper edge of bracket, rate). None = no upper edge. The table shows the tax at
# the midpoint of each row, rounded half up (checked against IRS table rows).
_SINGLE = [(11925, D("0.10")), (48475, D("0.12")), (None, D("0.22"))]
_MFJ = [(23850, D("0.10")), (96950, D("0.12")), (None, D("0.22"))]
_HOH = [(17000, D("0.10")), (64850, D("0.12")), (None, D("0.22"))]
TABLE_BRACKETS = {"single": _SINGLE, "mfs": _SINGLE, "mfj": _MFJ, "qss": _MFJ, "hoh": _HOH}

# --- Line 16, Tax Computation Worksheet, $100,000 and over (source: pub1040) ---
# Rows are (upper limit, multiply by, subtract). Tax = income x rate - subtract.
WORKSHEET_ROWS = {
    "single": [(103350, D("0.22"), D("5086")), (197300, D("0.24"), D("7153")),
               (250525, D("0.32"), D("22937")), (626350, D("0.35"), D("30452.75")),
               (None, D("0.37"), D("42979.75"))],
    "mfs": [(103350, D("0.22"), D("5086")), (197300, D("0.24"), D("7153")),
            (250525, D("0.32"), D("22937")), (375800, D("0.35"), D("30452.75")),
            (None, D("0.37"), D("37968.75"))],
    "mfj": [(206700, D("0.22"), D("10172")), (394600, D("0.24"), D("14306")),
            (501050, D("0.32"), D("45874")), (751600, D("0.35"), D("60905.50")),
            (None, D("0.37"), D("75937.50"))],
    "hoh": [(103350, D("0.22"), D("6825")), (197300, D("0.24"), D("8892")),
            (250500, D("0.32"), D("24676")), (626350, D("0.35"), D("32191")),
            (None, D("0.37"), D("44718"))],
}
WORKSHEET_ROWS["qss"] = WORKSHEET_ROWS["mfj"]

# --- Taxable Social Security, Worksheet 1 (source: pub915) ---
SS_BASE_MFJ = 32000
SS_BASE_OTHER = 25000          # single, hoh, qss, mfs who lived apart all year
SS_BAND_MFJ = 12000
SS_BAND_OTHER = 9000
SS_RATE_LOW = D("0.50")
SS_RATE_HIGH = D("0.85")