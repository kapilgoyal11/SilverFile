"""
Intake agent: the only agent that talks to the taxpayer.

Owner: Karan.

How it works in the graph
-------------------------
Each time the node runs it asks exactly one question, using a LangGraph
`interrupt`. The graph pauses and is saved by the checkpointer. When the
user's reply arrives (graph.invoke(Command(resume=reply), config)), the node
handles the reply, writes to state, and the graph routes back to Intake for
the next question, or onward when Intake is finished.

Constraints from the brief, and how they're met
-----------------------------------------------
- Must not do arithmetic: the only numbers it handles are typed in by the user
  and normalised ("$1,200" -> "1200.00"). Nothing is added or computed.
- Must not interpret tax rules: it records facts. Rule-based decisions (is
  this in scope, does the chart apply, which filing status is valid) belong to
  Eligibility & Rules. Intake only raises flags for things it directly hears
  in conversation: advice requests, "I'm not sure", and signs that someone
  else is pressuring or answering for the taxpayer.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from langgraph.types import interrupt

from src.agents.intake_llm import llm_extract
from src.agents.intake_parsers import ParseResult, parse
from src.agents.intake_script import GLOSSARY, build_plan, get_slot, stale_slots
from src.memory import active_answers, current_values, intake_handoff, now_iso, plain_summary_lines
from src.security_hooks import find_ssns, redact_ssns


class K:
    """Escalation reasons Intake raises (the "reason" field in state["escalations"])."""
    TAX_ADVICE = "tax_advice_request"
    USER_UNSURE = "user_unsure"
    POSSIBLE_EXPLOITATION = "possible_exploitation"
    REPEATED_PARSE_FAILURE = "repeated_parse_failure"

AGENT = "intake"
PAUSE_NOTICE = ("That's completely fine. Everything you've told me is saved. "
                "When you come back, we'll start again from this question.")
MAX_FAILURES = 3

# ---------------------------------------------------------------------------
# Intent patterns (checked before the answer itself is parsed)
# ---------------------------------------------------------------------------
_EXPLOITATION = re.compile(
    r"(told me (to say|what to say|to put)|wants? (me to put|the refund)|(his|her|their) account|"
    r"not my (bank|account)|don'?t know (whose|the|that|this) (account|bank)|"
    r"pressur|making me|won'?t let me|threaten|scared of (him|her|them)|"
    r"(son|daughter|nephew|niece|caregiver|friend|grandson|granddaughter|he|she|they) (is|are|'s) (answering|filling)|"
    r"answering for (her|him|them|my)|on (her|his|their) behalf|filling (this|it) out for (her|him|them|my))",
    re.I)
_PAUSE = re.compile(r"\b(pause|take a break|stop for now|continue later|come back later|finish later|"
                    r"i'?m tired|need a break|can we stop)\b", re.I)
_REPEAT = re.compile(r"\b(repeat|say (that|it) again|what was the question|come again|pardon|didn'?t hear)\b|^\s*(sorry|what)\s*\??\s*$", re.I)
_ADVICE = re.compile(
    r"\b(should i|should we|would i (pay|save|get)|would we (pay|save|get)|is it better|"
    r"can i deduct|could i deduct|can we deduct|how (can|do) i (lower|reduce|save)|lower my tax|reduce my tax|"
    r"save (money )?on (my )?tax|roth|convert my ira|pay less|best way to|is it worth|"
    r"what would you (do|recommend|suggest)|do you recommend|advice)\b", re.I)
_HELP = re.compile(r"\b(what does|what is|what's an?|what do you mean|what are|explain|i don'?t understand|"
                   r"meaning of|what's that|confused|help)\b", re.I)
_UNSURE = re.compile(r"\b(not sure|unsure|don'?t know|do not know|no idea|can'?t remember|cannot remember|"
                     r"don'?t remember|do not remember|i forget|not certain|no clue)\b", re.I)
_BACK = re.compile(r"\b(go back|back up|previous question|last question|change my|i made a mistake|mistake|"
                   r"that'?s wrong|that was wrong|that'?s not right|correction|i meant|update my|fix my)\b", re.I)
_SOFT_BACK = re.compile(r"^\s*(actually|wait|oh|oops|sorry)\b|\bactually\b", re.I)
_NOTHING = re.compile(r"\b(nothing|never mind|nevermind|it'?s fine|all good|no changes?)\b", re.I)


def _progress(state: dict) -> dict:
    p = dict(state.get("intake_progress") or {})
    p.setdefault("turn", 0)
    p.setdefault("asked_order", [])
    p.setdefault("failures", {})
    p.setdefault("glossed_terms", [])
    p.setdefault("notice", "")
    p.setdefault("forced_slot", None)
    p.setdefault("awaiting_change", False)
    p.setdefault("confirmed_at_turn", None)
    return p


def has_open_blocking(state: dict) -> bool:
    return any(e.get("blocking") and not e.get("resolved") for e in state.get("escalations") or [])


# ---------------------------------------------------------------------------
# Choosing the next question
# ---------------------------------------------------------------------------
def choose_next_slot(state: dict, prog: dict) -> Optional[str]:
    answers = state.get("answers") or {}
    values = current_values(answers)
    plan = build_plan(values)
    stale = stale_slots(values)
    if prog.get("awaiting_change"):
        return "change_what"
    forced = prog.get("forced_slot")
    if forced and forced in plan:
        return forced
    for s in plan:
        if s == "confirm_all":
            confirmed = prog.get("confirmed_at_turn")
            latest = max((r.get("turn", 0) for r in active_answers(state).values()), default=0)
            if confirmed is not None and latest <= confirmed:
                return None
            return "confirm_all"
        if s not in answers or s in stale:
            return s
    return None


def _fill(text: str, slot_key: str, values: dict) -> str:
    if "{name}" in text:
        i = slot_key.split("_")[1]
        text = text.replace("{name}", str(values.get(f"dependent_{i}_name") or "this person"))
    return text


def compose_prompt(state: dict, prog: dict, slot_key: str) -> tuple[str, list[str]]:
    """Returns (prompt text, glossary terms explained in it). Deterministic:
    LangGraph re-runs the node on resume, so this must give the same text."""
    values = current_values(state.get("answers") or {})
    parts: list[str] = []
    if prog.get("notice"):
        parts.append(prog["notice"])
    new_terms: list[str] = []
    if slot_key == "confirm_all":
        lines = plain_summary_lines(state)
        parts.append("Here is everything you've told me.\n" + "\n".join(f"  - {l}" for l in lines))
        notes = state.get("volunteer_notes") or []
        if notes:
            parts.append(f"I've also noted {len(notes)} question{'s' if len(notes) != 1 else ''} for the volunteer.")
        parts.append("Is all of that correct?")
        return "\n".join(parts), new_terms
    slot = get_slot(slot_key)
    for term in slot.terms:
        if term not in prog.get("glossed_terms", []) and term not in new_terms:
            parts.append(GLOSSARY[term])
            new_terms.append(term)
    parts.append(_fill(slot.text(values), slot_key, values))
    return "\n".join(parts), new_terms


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------
def intake_node(state: dict) -> dict:
    if has_open_blocking(state):
        return {"next_agent": "human_review"}
    prog = _progress(state)
    slot_key = choose_next_slot(state, prog)
    if slot_key is None:
        prog["current_slot"] = None
        return {"intake_complete": True, "next_agent": "orchestrator", "intake_progress": prog,
                "intake": intake_handoff(state)}
    prompt, new_terms = compose_prompt(state, prog, slot_key)
    reply = interrupt({"type": "intake_question", "agent": AGENT, "slot": slot_key,
                       "text": prompt, "session_id": state.get("session_id")})
    if isinstance(reply, dict):
        reply = reply.get("text", "")
    return handle_reply(state, prog, slot_key, prompt, new_terms, str(reply or ""))


def route_after_intake(state: dict) -> str:
    """Conditional edge for the graph. 'intake' loops for the next question."""
    if has_open_blocking(state):
        return "human_review"
    if state.get("intake_complete"):
        return "done"
    return "intake"


# ---------------------------------------------------------------------------
# Handling a reply
# ---------------------------------------------------------------------------
def _entered_by(values: dict) -> str:
    if values.get("is_taxpayer") is False:
        return f"helper ({values.get('helper_relationship') or 'relationship not given'})"
    return "taxpayer"


def _record(answers: dict, slot: str, value: Any, raw: str, by: str, ts: str, turn: int, status: str) -> dict:
    old = answers.get(slot)
    history = []
    if old:
        history = list(old.get("history") or []) + [
            {k: old.get(k) for k in ("value", "raw_text", "entered_by", "entered_at", "turn", "status")}]
    return {slot: {"slot": slot, "value": value, "raw_text": raw, "source": "direct user input",
                   "entered_by": by, "entered_at": ts, "turn": turn, "status": status, "history": history}}


def _escalation(kind: str, detail: str, slot: Optional[str], ts: str, blocking: bool, eid: str) -> dict:
    # "raised_by", "reason", "detail", "resolved" are the team's shared escalation
    # format; the other keys are extras Intake adds.
    return {"id": eid, "raised_by": AGENT, "reason": kind, "detail": detail, "resolved": False,
            "kind": kind, "slot": slot, "raised_at": ts, "blocking": blocking, "resolution": None}


def find_target_slot(text: str, state: dict) -> Optional[str]:
    """Which earlier question is the user referring to? Longest keyword wins,
    so "my spouse's birthday" beats "birthday"."""
    t = text.lower()
    answers = state.get("answers") or {}
    candidates = set(build_plan(current_values(answers))) | set(answers)
    best, best_len = None, 0
    for key in candidates:
        if key in ("confirm_all", "change_what"):
            continue
        try:
            slot = get_slot(key)
        except KeyError:
            continue
        for kw in slot.keywords:
            if kw in t and len(kw) > best_len:
                best, best_len = key, len(kw)
    # "my dependent" / "my son's" style references go to the dependents question
    if best is None and re.search(r"\b(dependent|son|daughter|grandchild|kid|child)\b", t):
        best = "has_dependents"
    return best


def handle_reply(state: dict, prog: dict, slot_key: str, prompt: str, new_terms: list[str], reply: str) -> dict:
    answers = state.get("answers") or {}
    values = current_values(answers)
    slot = get_slot(slot_key)
    turn = prog["turn"] + 1
    ts = now_iso()
    by = _entered_by(values)

    # --- PII: never store an SSN we didn't ask for; never log one at all ----
    dashed_only = slot.kind == "money"
    volunteered = [] if slot.kind == "ssn" else find_ssns(reply, dashed_only=dashed_only)
    clean = redact_ssns(reply, dashed_only=dashed_only)
    prog = {**prog, "turn": turn, "current_slot": slot_key, "notice": "",
            "glossed_terms": prog["glossed_terms"] + new_terms,
            "asked_order": prog["asked_order"] + [slot_key]}
    out: dict = {"transcript": [{"role": "assistant", "text": prompt, "turn": turn, "slot": slot_key},
                                {"role": "user", "text": redact_ssns(clean), "turn": turn, "slot": slot_key}],
                 "escalations": [], "volunteer_notes": [], "answers": {}, "token_log": []}
    prefix = ""
    if volunteered:
        prefix = "I noticed what looks like a Social Security number. I don't need it for this question, so I didn't save it. "
        text = re.sub(r"[,;]?\s*(and\s+)?(my\s+|the\s+)?(social security number|social security|social|ssn)?(\s+number)?(\s+is)?\s*\[SSN removed\]",
                      "", clean, flags=re.I).strip(" ,.;")
    else:
        text = clean if slot.kind != "ssn" else reply

    def done(notice: str = "") -> dict:
        prog["notice"] = (prefix + notice).strip()
        out["intake_progress"] = prog
        return {k: v for k, v in out.items() if v != [] and v != {}}

    # --- 1. signs of pressure or someone answering without authority --------
    if slot_key not in ("is_taxpayer", "helper_relationship") and _EXPLOITATION.search(text):
        out["escalations"].append(_escalation(
            K.POSSIBLE_EXPLOITATION,
            f"During the question \"{slot.label}\", the person said: \"{redact_ssns(text)[:200]}\". "
            "This may mean someone else is directing the return or the refund.",
            slot_key, ts, True, f"exploitation:{turn}"))
        return done("Thank you for telling me. Before we go any further, I'd like a volunteer to check in with you. "
                    "Everything you've told me so far is saved.")

    # --- 2. pause --------------------------------------------------------
    if _PAUSE.search(text):
        return done(PAUSE_NOTICE)

    # --- 3. repeat -------------------------------------------------------
    if _REPEAT.search(text) and slot.kind not in ("text", "name"):
        return done("Of course.")

    # --- 4. tax advice (out of scope; note it for the volunteer) ----------
    if _ADVICE.search(text):
        out["volunteer_notes"].append(redact_ssns(text)[:300])
        out["escalations"].append(_escalation(
            K.TAX_ADVICE, f"Taxpayer asked: \"{redact_ssns(text)[:200]}\"", slot_key, ts, False, f"advice:{turn}"))
        return done("That's a fair question, but it's tax advice, and that's not something I can help with. "
                    "I've written it down for the volunteer preparer, who can talk it through with you.")

    # --- 5. "what does that mean?" ---------------------------------------
    if _HELP.search(text) and not _UNSURE.search(text):
        term = next((t for t in GLOSSARY if t.lower() in text.lower()), None) or (slot.terms[0] if slot.terms else None)
        if term:
            return done(GLOSSARY[term])
        return done("Let me put it another way. There's no wrong answer here, "
                    "and you can always say \"I'm not sure\".")

    # --- 6. going back / correcting an earlier answer ---------------------
    hard_back = bool(_BACK.search(text))
    soft_back = bool(_SOFT_BACK.search(text))
    if hard_back or soft_back:
        target = find_target_slot(text, state)
        if target and target != slot_key:
            tslot = get_slot(target)
            body = re.sub(_BACK, " ", re.sub(_SOFT_BACK, " ", text))
            parsed = parse(tslot.kind, body, tslot.options(values) if tslot.options else None) \
                if tslot.kind not in ("text", "name", "ssn") else ParseResult(False)
            if parsed.ok and target in answers:
                out["answers"].update(_record(answers, target, parsed.value, redact_ssns(text), by, ts, turn, "answered"))
                return done(f"Thanks. I've updated \"{tslot.label}\". Your earlier answer is kept on record too.")
            prog["forced_slot"] = target
            return done("Sure, let's go back to that.")
        if hard_back and not target:
            previous = [s for s in prog["asked_order"][:-1] if s != slot_key and s in answers]
            if previous:
                prog["forced_slot"] = previous[-1]
                return done("Sure, let's go back one question.")
        # soft words like "actually" with no target: treat as a normal answer

    # --- 7. confirmation step --------------------------------------------
    if slot_key == "confirm_all":
        r = parse("yes_no", text)
        if r.ok and r.value:
            prog["confirmed_at_turn"] = turn
            prog["forced_slot"] = None
            return done("Thank you. That's everything I need from you for now.")
        if r.ok and not r.value:
            prog["awaiting_change"] = True
            return done("No problem.")
        if _UNSURE.search(text):
            prog["confirmed_at_turn"] = turn
            out["escalations"].append(_escalation(K.USER_UNSURE, "Taxpayer wasn't sure the summary was right.",
                                                  slot_key, ts, False, "unsure:confirm_all"))
            return done("That's okay. A volunteer will go over it with you.")
        return done("Sorry, I didn't catch that. A simple yes or no is fine.")

    if slot_key == "change_what":
        if _NOTHING.search(text):
            prog["awaiting_change"] = False
            prog["confirmed_at_turn"] = turn
            return done("Okay, we'll leave everything as it is.")
        target = find_target_slot(text, state)
        if target:
            prog["awaiting_change"] = False
            prog["forced_slot"] = target
            return done("Sure.")
        return done("Sorry, I didn't catch which part.")

    # --- 8. "I'm not sure" ------------------------------------------------
    if _UNSURE.search(text):
        out["answers"].update(_record(answers, slot_key, None, redact_ssns(text), by, ts, turn, "unsure"))
        out["escalations"].append(_escalation(K.USER_UNSURE, f"Taxpayer wasn't sure about: {slot.label}.",
                                              slot_key, ts, False, f"unsure:{slot_key}"))
        _after_answer(slot_key, None, values, out, ts, turn)
        prog = _clear_forced(prog, slot_key)
        return done("That's okay. I've flagged this one for a volunteer to help with, and we'll move on.")

    # --- 9. the answer itself ---------------------------------------------
    options = slot.options(values) if slot.options else None
    r = parse(slot.kind, text, options)
    if not r.ok and slot.kind == "ssn" and r.error == "not_test_range":
        return done("For safety, this practice system only accepts test numbers that start with 9, "
                    "like 900-12-3456. Please don't type a real Social Security number. I haven't saved what you typed.")
    if not r.ok:
        llm_r, log = llm_extract(state, slot.kind, options, prompt, text, turn)
        if log:
            out["token_log"].append(log)
        if llm_r is not None:
            r = llm_r
    if not r.ok:
        fails = dict(prog["failures"])
        fails[slot_key] = fails.get(slot_key, 0) + 1
        prog["failures"] = fails
        if fails[slot_key] >= MAX_FAILURES:
            out["answers"].update(_record(answers, slot_key, None, redact_ssns(text), by, ts, turn, "unsure"))
            out["escalations"].append(_escalation(
                K.REPEATED_PARSE_FAILURE, f"Couldn't understand the answer to: {slot.label}.",
                slot_key, ts, False, f"parse_failure:{slot_key}"))
            _after_answer(slot_key, None, values, out, ts, turn)
            prog = _clear_forced(prog, slot_key)
            return done("This one seems tricky, which is completely fine. "
                        "I've flagged it for a volunteer, and we'll move on.")
        return done("Sorry, I didn't quite catch that. " + r.error)

    status = "approximate" if r.approximate else "answered"
    stored_raw = redact_ssns(text)
    out["answers"].update(_record(answers, slot_key, r.value, stored_raw, by, ts, turn, status))
    prog = _clear_forced(prog, slot_key)
    notice = ""
    if r.approximate:
        out["escalations"].append(_escalation(K.USER_UNSURE, f"Approximate amount given for {slot.label}.",
                                              slot_key, ts, False, f"approx:{slot_key}"))
        notice = "Thanks. Since that's an estimate, I've flagged it for a volunteer to double-check."
    if slot_key in answers and answers[slot_key].get("value") != r.value:
        notice = notice or f"Thanks. I've updated \"{slot.label}\". Your earlier answer is kept on record too."
    notice = _after_answer(slot_key, r.value, values, out, ts, turn) or notice
    return done(notice)


def _clear_forced(prog: dict, slot_key: str) -> dict:
    if prog.get("forced_slot") == slot_key:
        prog = {**prog, "forced_slot": None}
    return prog


def _after_answer(slot_key: str, value: Any, values: dict, out: dict, ts: str, turn: int) -> str:
    """Conversation-level follow-ups. Not tax rules: these are about who is
    answering, which the brief (§4.4, condition 6) asks us to notice."""
    if slot_key == "helper_has_authority" and value is not True:
        rel = values.get("helper_relationship") or "not given"
        out["escalations"].append(_escalation(
            K.POSSIBLE_EXPLOITATION,
            f"Someone other than the taxpayer is answering (relationship: {rel}) without a stated legal authority.",
            slot_key, ts, True, "third_party_no_authority"))
        return ("Thank you for being honest about that. Before we go further, a volunteer will need to "
                "speak with the taxpayer directly. Everything so far is saved.")
    return ""
