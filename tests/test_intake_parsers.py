import pytest

from src.agents.intake_parsers import parse_choice, parse_count, parse_date, parse_money, parse_ssn, parse_yes_no
from src.agents.intake_script import FILING_OPTIONS


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah I am", True), ("I do", True), ("no", False), ("I do not", False),
    ("nope", False), ("I'm not", False), ("I was", True), ("not really", False),
])
def test_yes_no(text, expected):
    r = parse_yes_no(text)
    assert r.ok and r.value is expected


@pytest.mark.parametrize("text,expected", [
    ("March 4, 1950", "1950-03-04"), ("3/4/1950", "1950-03-04"), ("4 March 1950", "1950-03-04"),
    ("1950-03-04", "1950-03-04"), ("I was born on mar 4th 1950", "1950-03-04"),
])
def test_dates(text, expected):
    assert parse_date(text).value == expected


def test_bad_dates():
    assert not parse_date("1950").ok
    assert not parse_date("February 30 1950").ok


@pytest.mark.parametrize("text,expected,approx", [
    ("$1,200", 1200, False), ("1200.00", 1200, False), ("about 900", 900, True), ("none", 0, False),
])
def test_money(text, expected, approx):
    r = parse_money(text)
    assert r.ok and r.value == expected and r.approximate is approx


def test_money_with_cents_asks_user_to_round():
    r = parse_money("1200.50")
    assert not r.ok and "nearest dollar" in r.error


def test_counts():
    assert parse_count("two").value == 2
    assert parse_count("I have 3 of them").value == 3


def test_choice_by_number_and_word():
    opts = [FILING_OPTIONS["MFJ"], FILING_OPTIONS["MFS"]]
    assert parse_choice("2", opts).value == "mfs"
    assert parse_choice("jointly with my wife", opts).value == "mfj"
    assert not parse_choice("single", opts).ok


def test_ssn_only_test_range():
    assert parse_ssn("900-12-3456").ok
    assert parse_ssn("999 12 3456").ok
    assert not parse_ssn("123-45-6789").ok
    assert not parse_ssn("12345").ok
