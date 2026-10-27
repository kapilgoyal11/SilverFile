"""
Rule-based parsers for Intake answers.

Each parser takes the user's text and returns ParseResult. They never
interpret tax rules and never do arithmetic: they only turn words into a typed
value ("yes" -> True, "March 4, 1950" -> "1950-03-04", "$1,200" -> "1200.00").

If an LLM is configured it may suggest a value when these fail, but the
suggestion is fed back through the same parser, so the LLM can't invent a
value that wouldn't pass validation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from src.security_hooks import find_ssns, validate_test_ssn


@dataclass
class ParseResult:
    ok: bool
    value: Any = None
    error: str = ""          # plain-language hint shown to the user on failure
    approximate: bool = False


_YES = r"\b(yes|yeah|yep|yup|ya|correct|right|sure|true|i am|i do|i did|i have|i was|we did|we do|that's right|that is right|affirmative|y)\b"


_YES_FIRST = {"yes", "yeah", "yep", "yup", "ya", "y", "correct", "right", "sure", "true", "affirmative", "absolutely", "definitely"}
_NO_FIRST = {"no", "nope", "nah", "n", "never", "none", "false", "not"}
_NEGATION = r"\b(not|never|no|don't|dont|didn't|didnt|haven't|havent|wasn't|wasnt|isn't|isnt|aren't|arent|weren't|werent|won't|wont|nope|nah|none)\b"


def parse_yes_no(text: str) -> ParseResult:
    t = text.strip().lower()
    first = re.match(r"\W*([a-z']+)", t)
    fw = first.group(1) if first else ""
    if fw in _YES_FIRST:
        return ParseResult(True, True)
    if fw in _NO_FIRST:
        return ParseResult(True, False)
    if re.search(_NEGATION, t):
        return ParseResult(True, False)
    if re.search(_YES, t):
        return ParseResult(True, True)
    return ParseResult(False, error="A simple yes or no is fine.")


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def parse_date(text: str) -> ParseResult:
    t = text.strip().lower().replace(",", " ")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    hint = "Please tell me the month, day, and year. For example: March 4, 1950."
    y = m = d = None
    if mt := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        y, m, d = map(int, mt.groups())
    elif mt := re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", t):
        m, d, y = map(int, mt.groups())          # US order: month/day/year
    elif mt := re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})\s+(\d{4})\b", t):
        if mt.group(1) in _MONTHS:
            m, d, y = _MONTHS[mt.group(1)], int(mt.group(2)), int(mt.group(3))
    elif mt := re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})\b", t):
        if mt.group(2) in _MONTHS:
            d, m, y = int(mt.group(1)), _MONTHS[mt.group(2)], int(mt.group(3))
    if y is None:
        return ParseResult(False, error=hint)
    try:
        dt = date(y, m, d)
    except ValueError:
        return ParseResult(False, error="That date doesn't seem to exist. " + hint)
    if not (1900 <= dt.year <= 2025):
        return ParseResult(False, error="That year looks unusual. " + hint)
    return ParseResult(True, dt.isoformat())


def parse_name(text: str) -> ParseResult:
    t = re.sub(r"^(my name is|my name's|it's|it is|i'm|i am|this is|name is|his name is|her name is|their name is)\s+",
               "", text.strip(), flags=re.I).strip(" .")
    if not re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]{0,79}", t):
        return ParseResult(False, error="Please type the name using letters only.")
    return ParseResult(True, " ".join(w.capitalize() if w.islower() else w for w in t.split()))


def parse_text(text: str) -> ParseResult:
    t = text.strip()
    if not t:
        return ParseResult(False, error="Just a few words is fine.")
    return ParseResult(True, t[:300])


_NUM_WORDS = {"zero": 0, "none": 0, "no": 0, "one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "a single": 1, "just one": 1, "only one": 1, "a couple": 2}


def parse_count(text: str) -> ParseResult:
    t = text.strip().lower()
    if mt := re.search(r"\b(\d{1,2})\b", t):
        n = int(mt.group(1))
        if 0 <= n <= 20:
            return ParseResult(True, n)
    for w, n in sorted(_NUM_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{w}\b", t):
            return ParseResult(True, n)
    return ParseResult(False, error="Please give a number, like 1 or 2.")


_APPROX = r"\b(about|around|roughly|approximately|approx|maybe|i think|nearly|almost|or so|ish)\b"


def parse_money(text: str) -> ParseResult:
    """Whole-dollar amount as an int (the team stores all amounts as whole
    dollars). If cents are given, we ask the user to round rather than
    rounding ourselves: Intake does no arithmetic."""
    t = text.strip().lower()
    approx = bool(re.search(_APPROX, t))
    hint = "Please give the amount in whole dollars, like 1,200."
    if re.search(r"\b(none|nothing|zero)\b", t) and not re.search(r"\d", t):
        return ParseResult(True, 0)
    if re.search(r"-\s*\$?\d", t):
        return ParseResult(False, error="Please give the amount without a minus sign.")
    mt = re.search(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\b", t)
    if not mt:
        return ParseResult(False, error=hint)
    if mt.group(2) and int(mt.group(2)) != 0:
        return ParseResult(False, error="The form uses whole dollars. Please round to the nearest dollar, "
                                        "for example 1,234.56 becomes 1,235.")
    return ParseResult(True, int(mt.group(1).replace(",", "")), approximate=approx)


def parse_choice(text: str, options: list[dict]) -> ParseResult:
    """options: [{"value": "MFJ", "label": "...", "keywords": [...]}, ...]"""
    t = text.strip().lower()
    ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
    if mt := re.fullmatch(r"(?:option|number|#)?\s*(\d)\.?", t) or re.search(r"\b(?:option|number)\s*(\d)\b", t):
        i = int(mt.group(1))
        if 1 <= i <= len(options):
            return ParseResult(True, options[i - 1]["value"])
    for w, i in ordinals.items():
        if re.search(rf"\b(the )?{w}( one)?\b", t) and i <= len(options):
            return ParseResult(True, options[i - 1]["value"])
    hits = [o for o in options if any(re.search(rf"\b{re.escape(k)}\b", t) for k in o["keywords"])]
    if len(hits) == 1:
        return ParseResult(True, hits[0]["value"])
    return ParseResult(False, error=f"You can answer with a number from 1 to {len(options)}.")


def parse_year(text: str, allowed: tuple[int, ...] = (2023, 2024, 2025)) -> ParseResult:
    mt = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    words = {"this year": 2025, "last year": 2024}
    if not mt:
        for w, y in words.items():
            if w in text.lower():
                return ParseResult(True, y)
        return ParseResult(False, error="Please tell me the year, for example 2024.")
    y = int(mt.group(1))
    return ParseResult(True, y)


def parse_ssn(text: str) -> ParseResult:
    found = find_ssns(text)
    if not found:
        return ParseResult(False, error="Please type the 9-digit number, like 900-12-3456.")
    ok, reason = validate_test_ssn(found[0])
    if not ok:
        return ParseResult(False, error="not_test_range")   # handled specially by Intake
    return ParseResult(True, found[0])


def parse(kind: str, text: str, options: Optional[list[dict]] = None) -> ParseResult:
    if kind == "yes_no":
        return parse_yes_no(text)
    if kind == "date":
        return parse_date(text)
    if kind == "name":
        return parse_name(text)
    if kind == "text":
        return parse_text(text)
    if kind == "count":
        return parse_count(text)
    if kind == "money":
        return parse_money(text)
    if kind == "choice":
        return parse_choice(text, options or [])
    if kind == "year":
        return parse_year(text)
    if kind == "ssn":
        return parse_ssn(text)
    raise ValueError(f"unknown slot kind {kind}")
