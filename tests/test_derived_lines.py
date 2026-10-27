"""Tests for the simple derived lines, plus one full return chained end to end."""
import json

import pytest

from src.tools.tax_math import (
    adjusted_gross_income_11a, refund_or_amount_owed, senior_deduction,
    standard_deduction, tax_on_taxable_income, taxable_income_15,
    taxable_social_security, total_deductions_14, total_income_9, total_payments_33,
    total_tax_24, total_wages_1z, total_withholding_25d,
)


def test_total_wages_sums_all_w2s():
    r = total_wages_1z([30000, 5000])
    assert r["amount"] == 35000 and r["line"] == "1z"


def test_total_wages_with_no_w2s_is_zero():
    assert total_wages_1z([])["amount"] == 0


def test_total_income_adds_every_income_line():
    r = total_income_9(20000, 1000, 500, 2000, 10000, 8325, other_income_8=100)
    assert r["amount"] == 41925


def test_agi_subtracts_adjustments():
    assert adjusted_gross_income_11a(50000, 1500)["amount"] == 48500


def test_total_deductions_adds_12e_13a_13b():
    assert total_deductions_14(17750, 6000)["amount"] == 23750


def test_taxable_income_cannot_go_below_zero():
    assert taxable_income_15(10000, 23750)["amount"] == 0
    assert taxable_income_15(39325, 23750)["amount"] == 15575


def test_total_tax_with_schedule_amounts():
    assert total_tax_24(1631)["amount"] == 1631
    assert total_tax_24(1631, 200, 100, 50)["amount"] == 1781


def test_credits_cannot_push_line_22_below_zero():
    assert total_tax_24(1000, 0, 2500, 300)["amount"] == 300


def test_withholding_and_payments():
    assert total_withholding_25d(2500, 100)["amount"] == 2600
    assert total_payments_33(2600, 400)["amount"] == 3000


def test_refund_when_payments_exceed_tax():
    r = refund_or_amount_owed(1631, 2500)
    assert r["34"]["amount"] == 869 and r["37"]["amount"] == 0


def test_amount_owed_when_tax_exceeds_payments():
    r = refund_or_amount_owed(3000, 2500)
    assert r["34"]["amount"] == 0 and r["37"]["amount"] == 500


def test_exactly_even_is_zero_both_ways():
    r = refund_or_amount_owed(2500, 2500)
    assert r["34"]["amount"] == 0 and r["37"]["amount"] == 0


def test_floats_rejected():
    with pytest.raises(TypeError):
        total_income_9(20000.5, 0, 0, 0, 0, 0)
    with pytest.raises(TypeError):
        taxable_income_15(1000, 10.5)


def test_full_single_return_end_to_end():
    """Single filer, 65+: wages 20,000, interest 1,000, pension 10,000, Social
    Security 15,000, 2,500 withheld. Worked by hand from the IRS worksheets."""
    ss = taxable_social_security("single", benefits=15000, wages_1z=20000, tax_exempt_2a=0,
                                 interest_2b=1000, dividends_3b=0, ira_4b=0, pension_5b=10000)
    wages = total_wages_1z([20000])
    income = total_income_9(wages["amount"], 1000, 0, 0, 10000, ss["amount"])
    agi = adjusted_gross_income_11a(income["amount"], 0)
    std = standard_deduction("single", 1)
    senior = senior_deduction("single", agi["amount"], True)
    deductions = total_deductions_14(std["amount"], senior["amount"])
    taxable = taxable_income_15(agi["amount"], deductions["amount"])
    tax = tax_on_taxable_income("single", taxable["amount"])
    total_tax = total_tax_24(tax["amount"])
    payments = total_payments_33(total_withholding_25d(2500)["amount"], 0)
    result = refund_or_amount_owed(total_tax["amount"], payments["amount"])

    assert ss["amount"] == 8325
    assert income["amount"] == 39325 and agi["amount"] == 39325
    assert std["amount"] == 17750 and senior["amount"] == 6000
    assert deductions["amount"] == 23750 and taxable["amount"] == 15575
    assert tax["amount"] == 1631 and total_tax["amount"] == 1631
    assert payments["amount"] == 2500
    assert result["34"]["amount"] == 869 and result["37"]["amount"] == 0
    for record in (ss, wages, income, agi, std, senior, deductions, taxable, tax,
                   total_tax, payments, *result.values()):
        json.dumps(record)  # every record must be saveable as JSON