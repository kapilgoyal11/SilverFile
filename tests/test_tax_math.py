"""Tests for tax_math.py. Expected tax-table values are rows copied from the
IRS Publication 1040 (2025) Tax Table, including the IRS's own example."""
import pytest

from src.tools.tax_math import round_half_up, standard_deduction, tax_on_taxable_income


def test_round_half_up_goes_up_not_to_even():
    assert round_half_up("2.5") == 3
    assert round_half_up("3.5") == 4
    assert round_half_up("1192.5") == 1193


# (filing status, taxable income, tax shown in the IRS table)
IRS_TABLE_ROWS = [
    ("single", 25300, 2801), ("mfj", 25300, 2562), ("qss", 25300, 2562),
    ("mfs", 25300, 2801), ("hoh", 25300, 2699),
    ("single", 12000, 1205), ("mfj", 12000, 1203),
    ("single", 3000, 303), ("single", 48500, 5590), ("hoh", 17050, 1709),
    ("single", 0, 0), ("single", 4, 0), ("single", 5, 1), ("single", 24, 2),
    ("single", 99950, 16909), ("mfj", 99950, 11823), ("hoh", 99950, 15170),
]


@pytest.mark.parametrize("status,income,expected", IRS_TABLE_ROWS)
def test_tax_table_matches_irs(status, income, expected):
    assert tax_on_taxable_income(status, income)["amount"] == expected


# Worksheet results, cross-checked against the rate schedule's base amounts.
@pytest.mark.parametrize("status,income,expected", [
    ("single", 100000, 16914), ("single", 103350, 17651),
    ("single", 197300, 40199), ("single", 250525, 57231),
    ("mfj", 206700, 35302),
])
def test_worksheet_values(status, income, expected):
    assert tax_on_taxable_income(status, income)["amount"] == expected


def test_zero_or_negative_income_gives_zero_tax():
    assert tax_on_taxable_income("single", 0)["amount"] == 0
    assert tax_on_taxable_income("single", -500)["amount"] == 0


@pytest.mark.parametrize("status,boxes,expected", [
    ("single", 0, 15750), ("single", 1, 17750), ("mfj", 1, 33100),
    ("mfj", 2, 34700), ("hoh", 1, 25625), ("mfs", 1, 17350), ("qss", 1, 33100),
])
def test_standard_deduction(status, boxes, expected):
    assert standard_deduction(status, boxes)["amount"] == expected


def test_results_carry_provenance():
    record = standard_deduction("single", 1)
    assert record["line"] == "12e"
    assert record["kind"] == "computed"
    assert record["inputs"]["boxes_checked"] == 1


def test_bad_inputs_are_rejected():
    with pytest.raises(ValueError):
        standard_deduction("married", 0)
    with pytest.raises(ValueError):
        standard_deduction("single", 5)
    with pytest.raises(TypeError):
        tax_on_taxable_income("single", 1000.5)