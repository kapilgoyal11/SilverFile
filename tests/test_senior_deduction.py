"""Tests for senior_deduction(). Arithmetic follows Schedule 1-A Part V:
each eligible person gets 6000 - 6% x (MAGI - threshold), not below 0."""
import json

import pytest

from src.tools.tax_math import senior_deduction


@pytest.mark.parametrize("status,agi,taxpayer,spouse,expected", [
    ("single", 60000, True, False, 6000),     # under the threshold: full amount
    ("single", 75000, True, False, 6000),     # exactly at the threshold
    ("single", 100000, True, False, 4500),    # 25,000 over x 6% = 1,500
    ("single", 175000, True, False, 0),       # fully phased out
    ("single", 200000, True, False, 0),       # never negative
    ("single", 75010, True, False, 5999),     # 0.60 rounds up to 1
    ("single", 75025, True, False, 5998),     # 1.50 rounds half up to 2
    ("hoh", 100000, True, False, 4500),
    ("qss", 100000, True, False, 4500),
    ("mfj", 150000, True, True, 12000),
    ("mfj", 120000, True, True, 12000),
    ("mfj", 200000, True, True, 6000),        # matches the filled-in IRS form example
    ("mfj", 200000, True, False, 3000),       # only one spouse eligible
    ("mfj", 250000, True, True, 0),
    ("mfs", 50000, True, False, 0),           # married filing separately cannot claim
    ("single", 50000, False, False, 0),       # nobody eligible
])
def test_senior_deduction_amounts(status, agi, taxpayer, spouse, expected):
    assert senior_deduction(status, agi, taxpayer, spouse)["amount"] == expected


def test_spouse_flag_needs_joint_return():
    with pytest.raises(ValueError):
        senior_deduction("single", 50000, True, True)


def test_flags_must_be_true_or_false():
    with pytest.raises(TypeError):
        senior_deduction("single", 50000, 1)


def test_record_shows_schedule_1a_lines():
    r = senior_deduction("mfj", 200000, True, True)
    assert r["line"] == "13b" and r["kind"] == "computed"
    assert r["inputs"]["line_35"] == 3000 and r["inputs"]["eligible_people"] == 2
    json.dumps(r)