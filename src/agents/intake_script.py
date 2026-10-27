"""
What Intake asks, in what order, in what words.

Design choices (these map to §4.3 of the brief):
- One question per prompt. Every question has exactly one question mark;
  tests/test_intake_flow.py checks this for every prompt in a full run.
- Plain language. Jargon is avoided; where a term can't be avoided it gets a
  one-line explanation the first time it appears (GLOSSARY).
- Numbered choices, so someone using a screen reader or voice can just say "2".
- Intake records facts only. It never decides what a fact means for the
  return (e.g. it records "can someone claim you as a dependent: yes" and
  Eligibility & Rules decides that this needs a human).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

MAX_DEPENDENTS = 10

GLOSSARY: dict[str, str] = {
    "legally blind": "Legally blind means your eyesight is 20/200 or worse with glasses, or a doctor has said your field of vision is very narrow.",
    "dependent": "A dependent is someone you support, such as a child or relative who lives with you.",
    "filing status": "Your filing status is the category you file under, based mostly on whether you're married.",
    "estimated payments": "Estimated payments are payments you sent to the IRS yourself during the year, for example every quarter.",
    "adjustments": "Adjustments are certain amounts that reduce your income, like money put into a traditional IRA. If you have them, they're on line 26 of Schedule 1.",
    "W-2": "A W-2 is the form an employer sends showing your wages.",
    "SSA-1099": "An SSA-1099 is the form from Social Security showing the benefits you received.",
    "1099-R": "A 1099-R shows money you received from a pension, annuity, or retirement account like an IRA.",
    "1099-INT": "A 1099-INT is a form from a bank showing interest you earned.",
    "1099-DIV": "A 1099-DIV shows dividends, which are payments from stocks or funds you own.",
    "itemize": "Itemizing means listing expenses one by one instead of taking the standard amount everyone gets.",
    "power of attorney": "A power of attorney is a legal document that lets someone act on another person's behalf.",
    "schedule 2 or 3": "Schedules 2 and 3 are extra pages of the tax return for additional taxes, credits, and payments. If you have them, they're usually from a preparer or last year's software.",
}

# Values are the team's shared filing-status strings.
FILING_OPTIONS = {
    "S": {"value": "single", "label": "Single", "keywords": ["single", "unmarried", "not married"]},
    "MFJ": {"value": "mfj", "label": "Married filing jointly. You and your spouse file one return together",
            "keywords": ["jointly", "joint", "together", "mfj"]},
    "MFS": {"value": "mfs", "label": "Married filing separately. You and your spouse each file your own",
            "keywords": ["separately", "separate", "mfs"]},
    "HOH": {"value": "hoh", "label": "Head of household. You're unmarried and paid more than half the cost of keeping up a home for someone who depends on you",
            "keywords": ["head", "household", "hoh"]},
    "QSS": {"value": "qss", "label": "Qualifying surviving spouse. Your spouse died in 2023 or 2024 and you have a dependent child",
            "keywords": ["surviving", "widow", "widower", "qss"]},
}


@dataclass
class Slot:
    key: str
    kind: str                       # yes_no | date | name | text | count | money | choice | year | ssn
    question: Callable[[dict], str] | str
    label: str                      # short name used in summaries ("Date of birth")
    terms: list[str] = field(default_factory=list)       # glossary terms to explain on first use
    keywords: list[str] = field(default_factory=list)    # words that let the user jump back to this slot
    options: Optional[Callable[[dict], list[dict]]] = None

    def text(self, a: dict) -> str:
        return self.question(a) if callable(self.question) else self.question


def _filing_options(a: dict) -> list[dict]:
    if a.get("spouse_death_year") == 2025:
        keys = ["MFJ", "MFS", "S", "HOH", "QSS"]   # Intake doesn't decide; offer everything, Rules escalates
    elif a.get("married_end_of_year") is True:
        keys = ["MFJ", "MFS"]
    else:
        keys = ["S", "HOH", "QSS"]
    return [FILING_OPTIONS[k] for k in keys]


def _filing_question(a: dict) -> str:
    opts = _filing_options(a)
    lines = "\n".join(f"  {i}. {o['label']}." for i, o in enumerate(opts, 1))
    return ("Here are the filing statuses that may fit.\n" + lines +
            f"\nYou can also say \"I'm not sure\".\nWhich one describes how you plan to file?")


def _person(a: dict, you: str, them: str) -> str:
    """'you' when the taxpayer is answering, 'they' when a helper is."""
    return you if a.get("is_taxpayer", True) is not False else them


BASE_SLOTS: dict[str, Slot] = {s.key: s for s in [
    Slot("is_taxpayer", "yes_no", "Are you the person this tax return is for?", "Answering for self",
         keywords=["who is answering"]),
    Slot("helper_relationship", "text", "What is your relationship to the person this return is for?",
         "Helper's relationship", keywords=["relationship to"]),
    Slot("helper_has_authority", "yes_no",
         "Do you have a power of attorney or other legal authority to handle their taxes?",
         "Helper has legal authority", terms=["power of attorney"], keywords=["authority", "power of attorney"]),
    Slot("taxpayer_name", "name",
         lambda a: _person(a, "What is your full name, as it appears on your tax return?",
                           "What is their full name, as it appears on their tax return?"),
         "Name", keywords=["my name", "name"]),
    Slot("taxpayer_dob", "date",
         lambda a: _person(a, "What is your date of birth?", "What is their date of birth?"),
         "Date of birth", keywords=["date of birth", "birthday", "born", "birth date"]),
    Slot("taxpayer_blind", "yes_no",
         lambda a: _person(a, "Are you legally blind?", "Are they legally blind?"),
         "Legally blind", terms=["legally blind"], keywords=["blind", "eyesight", "vision"]),
    Slot("claimable_as_dependent", "yes_no",
         lambda a: _person(a, "Could someone else, like a son or daughter, list you as a dependent on their tax return?",
                           "Could someone else list them as a dependent on their tax return?"),
         "Can be claimed as someone's dependent", terms=["dependent"], keywords=["claim me", "claimed"]),
    Slot("full_year_resident", "yes_no",
         lambda a: _person(a, "Were you a U.S. citizen or U.S. resident for all of 2025?",
                           "Were they a U.S. citizen or U.S. resident for all of 2025?"),
         "U.S. citizen or resident all year", keywords=["citizen", "resident"]),
    Slot("married_end_of_year", "yes_no",
         lambda a: _person(a, "On December 31, 2025, were you married?", "On December 31, 2025, were they married?"),
         "Married on Dec 31, 2025", keywords=["married", "marriage", "marital"]),
    Slot("spouse_died_recent", "yes_no",
         lambda a: _person(a, "I'm sorry to have to ask this. Did your husband or wife pass away in 2023, 2024, or 2025?",
                           "I'm sorry to have to ask this. Did their husband or wife pass away in 2023, 2024, or 2025?"),
         "Spouse passed away 2023-2025", keywords=["passed away", "died", "widow"]),
    Slot("spouse_death_year", "year", "In which year did they pass away?", "Year spouse passed away",
         keywords=["year they passed", "year of death"]),
    Slot("filing_status", "choice", _filing_question, "Filing status", terms=["filing status"],
         keywords=["filing status", "file jointly", "file separately", "filing"], options=_filing_options),
    Slot("spouse_name", "name", "What is your spouse's full name?", "Spouse's name",
         keywords=["spouse's name", "wife's name", "husband's name", "spouse name"]),
    Slot("spouse_dob", "date", "What is your spouse's date of birth?", "Spouse's date of birth",
         keywords=["spouse's birthday", "spouse's date of birth", "wife's birthday", "husband's birthday",
                   "spouse birthday", "spouse was born", "wife was born", "husband was born"]),
    Slot("spouse_blind", "yes_no", "Is your spouse legally blind?", "Spouse legally blind",
         terms=["legally blind"], keywords=["spouse blind", "spouse's eyesight"]),
    Slot("spouse_itemizes", "yes_no",
         "Is your spouse listing their deductions one by one on their own return, instead of taking the standard amount?",
         "Spouse itemizes", terms=["itemize"], keywords=["spouse itemiz"]),
    Slot("has_dependents", "yes_no",
         lambda a: _person(a, "Do you have any dependents to list on this return?",
                           "Do they have any dependents to list on this return?"),
         "Has dependents", terms=["dependent"], keywords=["dependents", "dependent", "children", "kids"]),
    Slot("has_w2", "yes_no",
         lambda a: _person(a, "Did you get a W-2 from an employer for 2025?", "Did they get a W-2 from an employer for 2025?"),
         "Has W-2", terms=["W-2"], keywords=["w-2", "w2", "wages", "job"]),
    Slot("w2_count", "count", "How many W-2 forms do you have?", "Number of W-2s", keywords=["how many w-2", "number of w-2"]),
    Slot("has_ssa1099", "yes_no", "Did you get an SSA-1099 from Social Security for 2025?", "Has SSA-1099",
         terms=["SSA-1099"], keywords=["ssa-1099", "social security"]),
    Slot("has_1099r", "yes_no", "Did you get a 1099-R for 2025?", "Has 1099-R",
         terms=["1099-R"], keywords=["1099-r", "pension", "ira", "annuity"]),
    Slot("count_1099r", "count", "How many 1099-R forms do you have?", "Number of 1099-Rs", keywords=["how many 1099-r"]),
    Slot("box2b_checked", "yes_no",
         "On any of your 1099-R forms, is there a check mark in box 2b, next to the words \"taxable amount not determined\"?",
         "1099-R box 2b checked", keywords=["box 2b", "taxable amount not determined"]),
    Slot("has_1099int", "yes_no", "Did you get a 1099-INT from a bank for 2025?", "Has 1099-INT",
         terms=["1099-INT"], keywords=["1099-int", "interest"]),
    Slot("has_1099div", "yes_no", "Did you get a 1099-DIV for 2025?", "Has 1099-DIV",
         terms=["1099-DIV"], keywords=["1099-div", "dividend"]),
    Slot("other_income", "yes_no",
         "Did you have any income from your own business or freelance work, renting out property, selling stocks or cryptocurrency, or from another country?",
         "Other kinds of income", keywords=["business", "rental", "rent", "crypto", "stocks", "foreign", "freelance"]),
    Slot("other_income_detail", "text", "In a few words, what kind of income was it?", "Other income description",
         keywords=["kind of income"]),
    Slot("wants_itemize", "yes_no",
         "Most people take the standard deduction, which is a set amount. Did you want to itemize instead?",
         "Wants to itemize", terms=["itemize"], keywords=["itemize", "itemise", "standard deduction"]),
    Slot("made_estimated_payments", "yes_no", "Did you make any estimated payments to the IRS for 2025?",
         "Made estimated payments", terms=["estimated payments"], keywords=["estimated"]),
    Slot("estimated_payments_total", "money", "What was the total of those estimated payments, in whole dollars?",
         "Estimated payments total", keywords=["estimated total", "how much i paid"]),
    Slot("lived_apart_all_year", "yes_no", "Did you and your spouse live apart for all of 2025?",
         "Lived apart from spouse all year", keywords=["lived apart", "live apart"]),
    Slot("has_adjustments", "yes_no", "Do you have any adjustments to your income?", "Has adjustments",
         terms=["adjustments"], keywords=["adjustment"]),
    Slot("adjustments_total", "money", "What is the amount on line 26 of Schedule 1, in whole dollars?",
         "Adjustments total", keywords=["schedule 1, line 26", "line 26"]),
    Slot("adjustments_ss_worksheet", "money",
         "On Schedule 1, what is the total of lines 11 through 20, plus lines 23 and 25, in whole dollars?",
         "Schedule 1 lines 11-20, 23 and 25", keywords=["lines 11 through 20"]),
    Slot("has_other_schedules", "yes_no",
         "Do you have a Schedule 2 or Schedule 3 that has already been filled in?",
         "Has Schedule 2 or 3", terms=["schedule 2 or 3"], keywords=["schedule 2", "schedule 3"]),
    Slot("schedule_2_line_3", "money", "What is the amount on Schedule 2, line 3, in whole dollars?",
         "Schedule 2, line 3", keywords=["schedule 2, line 3"]),
    Slot("schedule_2_line_21", "money", "What is the amount on Schedule 2, line 21, in whole dollars?",
         "Schedule 2, line 21", keywords=["schedule 2, line 21"]),
    Slot("schedule_3_line_8", "money", "What is the amount on Schedule 3, line 8, in whole dollars?",
         "Schedule 3, line 8", keywords=["schedule 3, line 8"]),
    Slot("schedule_3_line_15", "money", "What is the amount on Schedule 3, line 15, in whole dollars?",
         "Schedule 3, line 15", keywords=["schedule 3, line 15"]),
]}


def dependent_slot(i: int, part: str) -> Slot:
    key = f"dependent_{i}_{part}"
    if part == "name":
        q = "What is the full name of your first dependent?" if i == 1 else f"What is the full name of dependent number {i}?"
        kws = [f"dependent {i} name", f"dependent {i}'s name"] + (["dependent's name", "dependent name"] if i == 1 else [])
        return Slot(key, "name", q, f"Dependent {i} name", keywords=kws)
    if part == "relationship":
        return Slot(key, "text", "How is {name} related to you, for example son, daughter, or grandchild?",
                    f"Dependent {i} relationship", keywords=[f"dependent {i} relationship"])
    if part == "ssn":
        return Slot(key, "ssn", "What is {name}'s Social Security number?", f"Dependent {i} SSN",
                    keywords=[f"dependent {i} ssn", f"dependent {i} social security"])
    if part == "another":
        return Slot(key, "yes_no", "Do you have another dependent to add?", f"More dependents after {i}",
                    keywords=["another dependent", "add a dependent"])
    raise ValueError(part)


def get_slot(key: str) -> Slot:
    if key in BASE_SLOTS:
        return BASE_SLOTS[key]
    if key.startswith("dependent_"):
        _, i, part = key.split("_", 2)
        return dependent_slot(int(i), part)
    if key == "confirm_all":
        return Slot("confirm_all", "yes_no", "", "Confirmation")
    if key == "change_what":
        return Slot("change_what", "text", "Which part would you like to change? You can say something like \"date of birth\" or \"dependents\".",
                    "What to change")
    raise KeyError(key)


def stale_slots(a: dict[str, Any]) -> set[str]:
    """Answers that were valid when given but no longer fit after an earlier
    answer changed. Example: the user picked "married filing jointly", then
    went back and said they weren't married. The old filing status is kept in
    the ledger's history, but it has to be asked again."""
    stale = set()
    fs = a.get("filing_status")
    if fs is not None and fs not in {o["value"] for o in _filing_options(a)}:
        stale.add("filing_status")
    return stale


def build_plan(a: dict[str, Any]) -> list[str]:
    """The ordered list of questions that apply, given the current answers.

    Branching depends only on earlier answers (facts), never on tax rules.
    If an earlier answer changes, the plan changes with it: questions that no
    longer apply drop out of the plan, but their answers stay in the ledger
    (nothing is deleted), and `active_answers` hides them from other agents.
    """
    a = {k: v for k, v in a.items() if k not in stale_slots(a)}
    p = ["is_taxpayer"]
    if a.get("is_taxpayer") is False:
        p += ["helper_relationship", "helper_has_authority"]
    p += ["taxpayer_name", "taxpayer_dob", "taxpayer_blind", "claimable_as_dependent",
          "full_year_resident", "married_end_of_year"]
    if a.get("married_end_of_year") is False:
        p.append("spouse_died_recent")
        if a.get("spouse_died_recent") is True:
            p.append("spouse_death_year")
    p.append("filing_status")
    fs = a.get("filing_status")
    if fs == "mfj":
        p += ["spouse_name", "spouse_dob", "spouse_blind"]
    elif fs == "mfs":
        p += ["spouse_name", "spouse_itemizes", "lived_apart_all_year"]
    p.append("has_dependents")
    if a.get("has_dependents") is True:
        for i in range(1, MAX_DEPENDENTS + 1):
            p += [f"dependent_{i}_{part}" for part in ("name", "relationship", "ssn")]
            if i == MAX_DEPENDENTS:
                break
            p.append(f"dependent_{i}_another")
            if a.get(f"dependent_{i}_another") is not True:
                break
    p.append("has_w2")
    if a.get("has_w2") is True:
        p.append("w2_count")
    p.append("has_ssa1099")
    p.append("has_1099r")
    if a.get("has_1099r") is True:
        p += ["count_1099r", "box2b_checked"]
    p += ["has_1099int", "has_1099div", "other_income"]
    if a.get("other_income") is True:
        p.append("other_income_detail")
    p += ["wants_itemize", "made_estimated_payments"]
    if a.get("made_estimated_payments") is True:
        p.append("estimated_payments_total")
    p.append("has_adjustments")
    if a.get("has_adjustments") is True:
        p.append("adjustments_total")
        if a.get("has_ssa1099") is True:
            p.append("adjustments_ss_worksheet")
    p.append("has_other_schedules")
    if a.get("has_other_schedules") is True:
        p += ["schedule_2_line_3", "schedule_2_line_21", "schedule_3_line_8", "schedule_3_line_15"]
    p.append("confirm_all")
    return p
