import pytest

from src.tools.ssn import check_ssn, normalize_ein, redact_ssns


@pytest.mark.parametrize("raw,expected", [
    ("900-12-3456", ("ok", "900-12-3456")),
    ("999123456", ("ok", "999-12-3456")),
    (" 950-00-0001 ", ("ok", "950-00-0001")),
    ("123-45-6789", ("not_test_range", None)),
    ("899-99-9999", ("not_test_range", None)),
    ("90-12-3456", ("malformed", None)),
    ("abc", ("malformed", None)),
    ("", ("blank", None)),
    (None, ("blank", None)),
])
def test_check_ssn(raw, expected):
    assert check_ssn(raw) == expected


def test_normalize_ein():
    assert normalize_ein("123456789") == "12-3456789"
    assert normalize_ein("12-3456789") == "12-3456789"
    assert normalize_ein("1234") is None


def test_redact_keeps_test_ssns_and_masks_others():
    out = redact_ssns("a 900-12-3456 b 123-45-6789")
    assert "900-12-3456" in out
    assert "123-45-6789" not in out
