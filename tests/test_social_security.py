"""Tests for taxable_social_security(). Expected values are the worked examples
printed in IRS Publication 915 (2025)."""
import json

import pytest

from src.tools.tax_math import taxable_social_security as tss


def run(**overrides):
    base = dict(filing_status="single", benefits=0, wages_1z=0, tax_exempt_2a=0,
                interest_2b=0, dividends_3b=0, ira_4b=0, pension_5b=0)
    base.update(overrides)
    return tss(**base)


def test_pub915_example1_single_fifty_percent_tier():
    r = run(benefits=5980, pension_5b=18600, wages_1z=9400, interest_2b=990)
    assert r["amount"] == 2990


def test_pub915_example2_joint_nothing_taxable_after_ira_deduction():
    r = run(filing_status="mfj", benefits=5600, pension_5b=15500, wages_1z=14000,
            interest_2b=250, adjustments=1000)
    assert r["amount"] == 0


def test_pub915_example3_joint_eighty_five_percent_tier():
    r = run(filing_status="mfj", benefits=10000, pension_5b=38000, interest_2b=2500)
    assert r["amount"] == 6275


def test_pub915_example4_mfs_lived_together_is_eighty_five_percent():
    r = run(filing_status="mfs", lived_apart_all_year=False, benefits=4000, wages_1z=8000)
    assert r["amount"] == 3400


def test_pub915_terry_jackson_regular_method():
    r = run(benefits=11000, wages_1z=3500, interest_2b=2500, dividends_3b=1500, pension_5b=18000)
    assert r["amount"] == 3000


def test_pub915_worksheet_a_example_nothing_taxable():
    r = run(benefits=1500, pension_5b=17000, interest_2b=700)
    assert r["amount"] == 0


def test_mfs_must_say_whether_they_lived_apart():
    with pytest.raises(ValueError):
        run(filing_status="mfs", benefits=4000, wages_1z=8000)


def test_mfs_lived_apart_uses_normal_thresholds():
    r = run(filing_status="mfs", lived_apart_all_year=True, benefits=11000,
            wages_1z=3500, interest_2b=2500, dividends_3b=1500, pension_5b=18000)
    assert r["amount"] == 3000


def test_record_is_json_ready_and_shows_worksheet_lines():
    r = run(benefits=5980, pension_5b=18600, wages_1z=9400, interest_2b=990)
    assert r["line"] == "6b" and r["kind"] == "computed"
    json.dumps(r)  # must not raise: the dashboard and saved state need JSON
    assert r["inputs"]["worksheet_lines"]["line_19"] == "2990.00"