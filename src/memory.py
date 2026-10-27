"""
Agent memory for SilverFile.

Owner: Karan (Agent Memory).

Three kinds of memory:

1. Short-term, within a run: the LangGraph state (src/state.py).
2. Long-term, across days: a SQLite checkpointer. Every step of the graph is
   saved under the session's thread_id, so a session abandoned on Tuesday
   continues on Friday exactly where it stopped (get_checkpointer,
   resume_summary).
3. Working memory for model calls: the context strategy. Instead of sending a
   model the whole conversation every time, we send a short, deterministic
   summary of what's been decided plus only the most recent turns
   ("selective recall"). This keeps input tokens flat as a session gets long
   (build_model_context, docs/metrics.md).

The summaries here are built from the answer ledger by code, not written by
an LLM, so they can't drop or invent a fact.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from src.security_hooks import mask_ssn, redact_ssns

# Number of recent conversation turns kept verbatim when the strategy is on.
KEEP_LAST_TURNS = 4
# Long assistant messages (e.g. the confirmation list) are clipped in the window.
MAX_CHARS_PER_TURN = 280


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def get_checkpointer(db_path: Optional[str] = None):
    """SQLite-backed checkpointer. Pass the same db_path and thread_id later to
    resume. Uses an in-memory saver if db_path is ':memory:'."""
    if db_path == ":memory:":
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    from langgraph.checkpoint.sqlite import SqliteSaver
    db_path = db_path or os.environ.get("SILVERFILE_DB", "silverfile_sessions.sqlite")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


def thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


# --------------------------------------------------------------------------
# Reading the answer ledger
# --------------------------------------------------------------------------
def current_values(answers: dict) -> dict[str, Any]:
    """slot -> current value (the latest version of every answer)."""
    return {k: v.get("value") for k, v in (answers or {}).items()}


def active_answers(state: dict) -> dict[str, dict]:
    """Only the answers that still apply given the user's current answers.

    Example: the user said "married", gave a spouse's name, then went back and
    said "not married". The spouse's name is still in the ledger (never lost),
    but it's no longer active, so Computation and Rules shouldn't use it.
    """
    from src.agents.intake_script import build_plan, stale_slots
    answers = state.get("answers") or {}
    values = current_values(answers)
    plan = set(build_plan(values)) - stale_slots(values)
    return {k: v for k, v in answers.items() if k in plan}


def get_dependents(state: dict) -> list[dict]:
    a = active_answers(state)
    out = []
    i = 1
    while f"dependent_{i}_name" in a:
        out.append({
            "name": a[f"dependent_{i}_name"]["value"],
            "relationship": a.get(f"dependent_{i}_relationship", {}).get("value"),
            "ssn": a.get(f"dependent_{i}_ssn", {}).get("value"),
        })
        i += 1
    return out


def intake_handoff(state: dict) -> dict:
    """What Intake writes to state["intake"] when it finishes: plain answers in
    the team's shared format (see docs/INTAKE_INTERFACE.md).

    - Amounts are whole-dollar ints. A dollar key is only included when we
      actually know it: the user gave the amount, or said they have none (0).
      If they weren't sure, the key is present with value None, so Computation
      blocks instead of silently using 0.
    - Facts that need a tax-rule decision (eligibility, scope) are passed as
      raw facts for Eligibility & Rules to judge.
    """
    a = active_answers(state)
    val = lambda k: a.get(k, {}).get("value")
    asked = lambda k: k in a
    fs = val("filing_status")
    out: dict = {
        "entered_by": _entered_by_label(a),
        "taxpayer_name": val("taxpayer_name"),
        "birth_date": val("taxpayer_dob"),
        "is_blind": val("taxpayer_blind"),
        "claimable_as_dependent": val("claimable_as_dependent"),
        "full_year_resident": val("full_year_resident"),
        "married_end_of_year": val("married_end_of_year"),
        "spouse_died_recent": val("spouse_died_recent"),
        "spouse_death_year": val("spouse_death_year"),
        "filing_status": fs,
        "spouse_name": val("spouse_name") if fs in ("mfj", "mfs") else None,
        "spouse_birth_date": val("spouse_dob") if fs == "mfj" else None,
        "spouse_is_blind": val("spouse_blind") if fs == "mfj" else None,
        "spouse_itemizes": val("spouse_itemizes") if fs == "mfs" else None,
        "lived_apart_all_year": val("lived_apart_all_year") if fs == "mfs" else None,
        "dependents": get_dependents(state),
        "document_inventory": {
            "W-2": (val("w2_count") if val("w2_count") is not None else None) if val("has_w2") else 0,
            "SSA-1099": 1 if val("has_ssa1099") else 0,
            "1099-R": (val("count_1099r") if val("count_1099r") is not None else None) if val("has_1099r") else 0,
            "1099-INT": 1 if val("has_1099int") else 0,
            "1099-DIV": 1 if val("has_1099div") else 0,
        },
        "box2b_checked": val("box2b_checked"),
        "other_income": val("other_income"),
        "other_income_detail": val("other_income_detail"),
        "wants_itemize": val("wants_itemize"),
        "unsure": sorted(k for k, v in a.items() if v.get("status") == "unsure"),
        "volunteer_questions": list(state.get("volunteer_notes") or []),
    }
    # Dollar amounts (Computation's input keys)
    if asked("made_estimated_payments"):
        out["estimated_payments"] = val("estimated_payments_total") if val("made_estimated_payments") else (
            0 if val("made_estimated_payments") is False else None)
    if asked("has_adjustments"):
        out["adjustments_10"] = val("adjustments_total") if val("has_adjustments") else (
            0 if val("has_adjustments") is False else None)
        if asked("adjustments_ss_worksheet"):
            out["adjustments_ss_worksheet"] = val("adjustments_ss_worksheet")
    if asked("has_other_schedules"):
        sched = {"other_income_8": "schedule_1_line_10",      # 1040-SR line 8
                 "schedule_2_line_3": "schedule_2_line_3",   # line 17
                 "other_taxes_23": "schedule_2_line_21",      # line 23
                 "credits_21": "schedule_3_line_8",           # line 20 (no line 19 credits in scope)
                 "other_payments_32": "schedule_3_line_15"}   # line 31
        for key, slot in sched.items():
            out[key] = val(slot) if val("has_other_schedules") else (
                0 if val("has_other_schedules") is False else None)
    if asked("has_other_withholding"):
        out["other_withholding_25c"] = val("other_withholding_total") if val("has_other_withholding") else (
            0 if val("has_other_withholding") is False else None)
    return out


def _entered_by_label(a: dict) -> str:
    if a.get("is_taxpayer", {}).get("value") is False:
        rel = a.get("helper_relationship", {}).get("value") or "relationship not given"
        return f"helper ({rel})"
    return "taxpayer"


# Kept for the stand-alone demo script.
intake_record = intake_handoff


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------
_STATUS_WORDS = {"single": "single", "mfj": "married filing jointly", "mfs": "married filing separately",
                 "hoh": "head of household", "qss": "qualifying surviving spouse"}


def _display(slot: str, value: Any) -> str:
    if value is None:
        return "not sure yet (flagged for a volunteer)"
    if slot.endswith("_ssn"):
        return mask_ssn(value)
    if slot == "filing_status":
        return _STATUS_WORDS.get(value, str(value))
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int) and _is_money_slot(slot):
        return f"${value:,}"
    if slot.endswith("_dob"):
        try:
            d = datetime.fromisoformat(value)
            return f"{d:%B} {d.day}, {d.year}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _is_money_slot(slot: str) -> bool:
    from src.agents.intake_script import get_slot
    try:
        return get_slot(slot).kind == "money"
    except (KeyError, ValueError):
        return False


def plain_summary_lines(state: dict) -> list[str]:
    """Every active answer as 'Label: value', SSNs masked. Used in the
    confirmation step and for the volunteer."""
    from src.agents.intake_script import get_slot
    a = active_answers(state)
    lines = []
    for slot, rec in sorted(a.items(), key=lambda kv: kv[1].get("turn", 0)):
        if slot in ("confirm_all", "change_what") or slot.endswith("_another"):
            continue
        lines.append(f"{get_slot(slot).label}: {_display(slot, rec.get('value'))}")
    return lines


def resume_summary(state: dict) -> str:
    """Short 'welcome back' message for a returning user. Kept to a few lines
    on purpose: the reader may be tired or using a screen reader."""
    a = active_answers(state)
    if not a:
        return "Welcome back. We hadn't started yet, so let's begin."
    val = lambda k: a.get(k, {}).get("value")
    bits = []
    if val("taxpayer_name"):
        bits.append(f"your name ({val('taxpayer_name')})")
    if "taxpayer_dob" in a:
        bits.append("your date of birth")
    if val("filing_status"):
        bits.append(f"your filing status ({_display('filing_status', val('filing_status'))})")
    deps = get_dependents(state)
    if deps:
        bits.append(f"{len(deps)} dependent{'s' if len(deps) != 1 else ''}")
    docs = [d for d, k in (("W-2", "has_w2"), ("SSA-1099", "has_ssa1099"), ("1099-R", "has_1099r"),
                           ("1099-INT", "has_1099int"), ("1099-DIV", "has_1099div")) if val(k) is True]
    if docs:
        bits.append("your forms (" + ", ".join(docs) + ")")
    text = "Welcome back. Everything you told me last time is saved."
    if bits:
        text += "\nSo far we've covered " + _join(bits) + "."
    notes = state.get("volunteer_notes") or []
    if notes:
        text += f"\nI noted {len(notes)} question{'s' if len(notes) != 1 else ''} for a volunteer."
    text += "\nLet's pick up where we left off."
    return text


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def session_summary(state: dict) -> str:
    """Fixed-size summary of the session's key decisions. This is the
    'summary of session decisions' in the selective-recall strategy.

    It is deliberately bounded: headline decisions plus counts, not every
    answer. A model parsing the reply to one question doesn't need the other
    40 answers verbatim, and a bounded summary is what keeps the token curve
    flat. (The full detail is always in the answer ledger.)
    """
    from collections import Counter
    a = active_answers(state)
    val = lambda k: a.get(k, {}).get("value")
    who = "taxpayer" if val("is_taxpayer") is not False else f"helper ({val('helper_relationship')})"
    docs = [d for d, k in (("W-2", "has_w2"), ("SSA-1099", "has_ssa1099"), ("1099-R", "has_1099r"),
                           ("1099-INT", "has_1099int"), ("1099-DIV", "has_1099div")) if val(k) is True]
    open_esc = Counter(e["kind"] for e in state.get("escalations") or [] if not e.get("resolved"))
    flags = ", ".join(f"{k} x{n}" for k, n in sorted(open_esc.items())) or "none"
    return (f"Session so far: answering={who}; filing_status={val('filing_status') or 'not yet'}; "
            f"dependents={len(get_dependents(state))}; documents={', '.join(docs) or 'none yet'}; "
            f"answers_recorded={len(a)}; open_flags={flags}.")


# --------------------------------------------------------------------------
# Context-window strategy
# --------------------------------------------------------------------------
INTAKE_MODEL_SYSTEM = (
    "You help the SilverFile intake assistant understand a taxpayer's reply. "
    "You never give tax advice and never do arithmetic. Given the current question and the "
    "taxpayer's reply, return JSON {\"value\": <value or null>} where value matches the "
    "requested type. If the reply does not answer the question, return null."
)


def build_model_context(state: dict, current_question: str, user_text: str,
                        use_strategy: bool = True, keep_last: int = KEEP_LAST_TURNS,
                        system_prompt: str = INTAKE_MODEL_SYSTEM) -> list[dict]:
    """Messages to send a model-calling agent.

    use_strategy=False: the whole transcript is sent (context grows every turn).
    use_strategy=True: selective recall = a deterministic summary of decisions
    so far + only the last `keep_last` turns + the current question.

    Every message is SSN-redacted before it leaves this function.
    """
    transcript = state.get("transcript") or []
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    if use_strategy:
        msgs.append({"role": "system", "content": session_summary(state)})
        recent = transcript[-keep_last:] if keep_last else []
    else:
        recent = transcript
    for t in recent:
        text = t["text"]
        if use_strategy and len(text) > MAX_CHARS_PER_TURN:
            text = text[:MAX_CHARS_PER_TURN] + " [...]"
        msgs.append({"role": t["role"], "content": text})
    msgs.append({"role": "user", "content": f"Current question: {current_question}\nTaxpayer's reply: {user_text}"})
    return [{**m, "content": redact_ssns(m["content"])} for m in msgs]


_ENCODER = None


def count_tokens(messages: Iterable[dict]) -> int:
    """Estimate input tokens with tiktoken (cl100k). The exact count from a
    live API differs a little by provider, but the *shape* of the curve is
    what the metric compares. Use --live in scripts/measure_context.py for
    real API counts."""
    global _ENCODER
    try:
        if _ENCODER is None:
            import tiktoken
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        return sum(len(_ENCODER.encode(m["content"])) + 4 for m in messages) + 2
    except Exception:
        return sum(len(m["content"]) // 4 + 4 for m in messages) + 2


def strategy_enabled() -> bool:
    """Global switch, set by the --no-context-strategy flag (env var so every
    agent sees it)."""
    return os.environ.get("SILVERFILE_CONTEXT_STRATEGY", "on").lower() not in ("off", "0", "false", "no")


def apply_context_strategy(messages: list[dict], summary: str, use_strategy: Optional[bool] = None,
                           keep_last: int = KEEP_LAST_TURNS) -> list[dict]:
    """Generic version for teammates' agents: give it your agent's message list
    (system message first) and a summary string; get back a trimmed list."""
    use = strategy_enabled() if use_strategy is None else use_strategy
    if not use or len(messages) <= keep_last + 1:
        return messages
    system = [m for m in messages[:1] if m["role"] == "system"]
    rest = messages[len(system):]
    return system + [{"role": "system", "content": summary}] + rest[-keep_last:]
