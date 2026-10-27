"""Generate synthetic W-2 images and their answer keys.

    python data/generate_fixtures.py

For each case this writes, into data/fixtures/:
  <name>.png            the W-2 image (what the vision model reads)
  <name>.reading.json   the exact transcription we expect (used by FixtureReader and tests)

It also writes JSON fixtures for the non-W-2 forms (1099-INT, SSA-1099) that
Extraction reads without a model. All people, employers and SSNs are fake;
SSNs use the 900-999 test range, except the deliberate bad-SSN case.
Amounts are whole dollars (team rule), printed with .00 as on a real W-2.
JSON field names match what src/agents/computation.py reads.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "fixtures"

W, H = 1700, 1000
LEFT, MID, RIGHT = 20, 860, 1680

CASES = {
    "w2_1": {
        "box_a": "900-12-3456",
        "box_b": "12-3456789",
        "box_c": "Kushim Test Bakery LLC\n410 Example Ave\nSpringfield, IL 62701",
        "box_d": "CTRL-0001",
        "box_e": "Margaret T Example",
        "box_f": "22 Sample Lane\nSpringfield, IL 62704",
        "box_1": "18450.00", "box_2": "1210.00",
        "box_3": "18450.00", "box_4": "1144.00",
        "box_5": "18450.00", "box_6": "268.00",
        "box_7": None, "box_8": None, "box_10": None, "box_11": None,
        "box_12": [("a", "D", "1500.00")],
        "box_13_retirement_plan": "X",
        "box_14": None,
        "state": ("IL", "1234-5678", "18450.00", "913.00", None, None, None),
    },
    "w2_2": {
        "box_a": "900-65-4321",
        "box_b": "98-7654321",
        "box_c": "Fictional Library District\n9 Placeholder Rd\nSpringfield, IL 62702",
        "box_d": None,
        "box_e": "Robert J Example",
        "box_f": "22 Sample Lane\nSpringfield, IL 62704",
        "box_1": "9800.00", "box_2": "400.00",
        "box_3": "9800.00", "box_4": "608.00",
        "box_5": "9800.00", "box_6": "142.00",
        "box_7": None, "box_8": None, "box_10": None, "box_11": None,
        "box_12": [],
        "box_13_retirement_plan": None,
        "box_14": None,
        "state": ("IL", "8765-4321", "9800.00", "485.00", None, None, None),
    },
    # Deliberately invalid: the SSN is outside the test range, so Extraction must refuse it.
    "w2_bad_ssn": {
        "box_a": "123-45-6789",
        "box_b": "11-2223334",
        "box_c": "Bad Data Testing Co\n1 Nowhere St\nSpringfield, IL 62703",
        "box_d": None,
        "box_e": "Test Rejected Person",
        "box_f": "5 Example Ct\nSpringfield, IL 62704",
        "box_1": "5000.00", "box_2": "100.00",
        "box_3": "5000.00", "box_4": "310.00",
        "box_5": "5000.00", "box_6": "73.00",
        "box_7": None, "box_8": None, "box_10": None, "box_11": None,
        "box_12": [],
        "box_13_retirement_plan": None,
        "box_14": None,
        "state": ("IL", "0000-1111", "5000.00", "248.00", None, None, None),
    },
}

JSON_FIXTURES = {
    "int_1.json": {"doc_type": "1099-INT", "fields": {
        "payer_name": "Example Community Bank", "recipient_tin": "900-12-3456",
        "box_1_interest": 312, "box_4_withholding": 0, "box_8_tax_exempt_interest": 0}},
    "ssa_1.json": {"doc_type": "SSA-1099", "fields": {
        "beneficiary_name": "Margaret T Example", "beneficiary_ssn": "900-12-3456",
        "box_3_benefits_paid": 21600, "box_4_benefits_repaid": 0,
        "box_5_net_benefits": 21600, "box_6_withheld": 1200}},
}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


LABEL, VALUE, TITLE = _font(15), _font(24), _font(34)


def _box(d: ImageDraw.ImageDraw, x0, y0, x1, y1, label, value):
    d.rectangle([x0, y0, x1, y1], outline="black", width=2)
    d.text((x0 + 8, y0 + 6), label, fill="black", font=LABEL)
    if value:
        d.multiline_text((x0 + 14, y0 + 30), value, fill="black", font=VALUE, spacing=6)


def render(case: dict, path: Path) -> None:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((LEFT, 8), "SYNTHETIC TEST DOCUMENT - NOT A REAL W-2", fill="gray", font=LABEL)
    top = 30

    # Left column: identity boxes
    _box(d, LEFT, top, MID, top + 80, "a  Employee's social security number", case["box_a"])
    _box(d, LEFT, top + 80, MID, top + 160, "b  Employer identification number (EIN)", case["box_b"])
    _box(d, LEFT, top + 160, MID, top + 330, "c  Employer's name, address, and ZIP code", case["box_c"])
    _box(d, LEFT, top + 330, MID, top + 410, "d  Control number", case["box_d"])
    _box(d, LEFT, top + 410, MID, top + 720, "e  Employee's name   f  Employee's address and ZIP code",
         f"{case['box_e']}\n{case['box_f']}")

    # Right column: numbered boxes in pairs
    half = (MID + RIGHT) // 2
    b12 = {slot: f"{code}   {amt}" for slot, code, amt in case["box_12"]}
    pairs = [
        (("1  Wages, tips, other compensation", case["box_1"]), ("2  Federal income tax withheld", case["box_2"])),
        (("3  Social security wages", case["box_3"]), ("4  Social security tax withheld", case["box_4"])),
        (("5  Medicare wages and tips", case["box_5"]), ("6  Medicare tax withheld", case["box_6"])),
        (("7  Social security tips", case["box_7"]), ("8  Allocated tips", case["box_8"])),
        (("9", None), ("10  Dependent care benefits", case["box_10"])),
        (("11  Nonqualified plans", case["box_11"]), ("12a  See instructions for box 12", b12.get("a"))),
        (("13", None), ("12b", b12.get("b"))),
        (("14  Other", case["box_14"]), ("12c", b12.get("c"))),
    ]
    row_h = 80
    for i, (left, right) in enumerate(pairs):
        y = top + i * row_h
        _box(d, MID, y, half, y + row_h, *left)
        _box(d, half, y, RIGHT, y + row_h, *right)
    y13 = top + 6 * row_h
    for j, label in enumerate(["Statutory", "Retirement plan", "3rd-party sick"]):
        x = MID + 14 + j * 135
        d.rectangle([x, y13 + 34, x + 22, y13 + 56], outline="black", width=2)
        d.text((x + 28, y13 + 38), label, fill="black", font=LABEL)
        if j == 1 and case["box_13_retirement_plan"]:
            d.text((x + 4, y13 + 32), "X", fill="black", font=VALUE)
    y = top + 8 * row_h
    _box(d, MID, y, half, y + row_h, "", None)
    _box(d, half, y, RIGHT, y + row_h, "12d", b12.get("d"))

    # State and local row
    sy = top + 740
    cols = [LEFT, 180, 520, 800, 1080, 1300, 1500, RIGHT]
    labels = ["15 State", "Employer's state ID number", "16 State wages, tips, etc.", "17 State income tax",
              "18 Local wages, tips, etc.", "19 Local income tax", "20 Locality name"]
    for k in range(7):
        _box(d, cols[k], sy, cols[k + 1], sy + 90, labels[k], case["state"][k])

    d.text((LEFT, sy + 120), "Form W-2  Wage and Tax Statement", fill="black", font=TITLE)
    d.text((RIGHT - 120, sy + 120), "2025", fill="black", font=TITLE)
    d.text((LEFT, sy + 175), "Copy B - To Be Filed With Employee's FEDERAL Tax Return.", fill="black", font=LABEL)
    img.save(path)


def reading_for(case: dict) -> dict:
    """The transcription a perfect reader would return (vision.W2Reading shape)."""

    def r(text):
        return {"text": text, "confidence": 1.0}

    st = case["state"]
    return {
        "is_w2": True,
        "tax_year": r("2025"),
        **{k: r(case[k]) for k in ["box_a", "box_b", "box_c", "box_d", "box_e", "box_f", "box_1", "box_2",
                                   "box_3", "box_4", "box_5", "box_6", "box_7", "box_8", "box_10", "box_11",
                                   "box_14"]},
        "box_12": [{"slot": s, "code": r(c), "amount": r(a)} for s, c, a in case["box_12"]],
        "box_13_statutory_employee": r(None),
        "box_13_retirement_plan": r(case["box_13_retirement_plan"]),
        "box_13_third_party_sick_pay": r(None),
        "state_lines": [{
            "state": r(st[0]), "employer_state_id": r(st[1]), "box_16": r(st[2]), "box_17": r(st[3]),
            "box_18": r(st[4]), "box_19": r(st[5]), "box_20": r(st[6]),
        }],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, case in CASES.items():
        render(case, OUT / f"{name}.png")
        (OUT / f"{name}.reading.json").write_text(json.dumps(reading_for(case), indent=2) + "\n")
    for name, data in JSON_FIXTURES.items():
        (OUT / name).write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote fixtures to {OUT}")


if __name__ == "__main__":
    main()
