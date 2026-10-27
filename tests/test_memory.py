"""Memory tests: reducers, resume across 'days', context strategy."""
import os

from src.intake_graph import build_intake_graph
from src.memory import (apply_context_strategy, build_model_context, count_tokens, get_checkpointer,
                        resume_summary)
from src.state import merge_answers, merge_escalations
from tests.intake_helpers import CLEAN_RUN, Session


def _rec(slot, value, turn, at):
    return {"slot": slot, "value": value, "raw_text": str(value), "source": "direct user input",
            "entered_by": "taxpayer", "entered_at": at, "turn": turn, "status": "answered", "history": []}


def test_parallel_writes_to_different_slots_both_survive():
    base = {"a": _rec("a", 1, 1, "t1")}
    left = merge_answers(base, {"b": _rec("b", 2, 2, "t2")})
    both = merge_answers(left, {"c": _rec("c", 3, 2, "t2")})
    assert set(both) == {"a", "b", "c"}


def test_overwrite_moves_old_value_into_history():
    base = {"a": _rec("a", 1, 1, "t1")}
    merged = merge_answers(base, {"a": _rec("a", 5, 3, "t3")})
    assert merged["a"]["value"] == 5
    assert [h["value"] for h in merged["a"]["history"]] == [1]


def test_escalation_resolution_replaces_by_id():
    e = {"id": "x", "kind": "k", "resolved": False}
    out = merge_escalations([e], [{**e, "resolved": True}, {"id": "y", "kind": "k", "resolved": False}])
    assert [(x["id"], x["resolved"]) for x in out] == [("x", True), ("y", False)]


def test_escalations_without_id_still_append_like_before():
    a = {"raised_by": "computation", "reason": "r", "detail": "d", "resolved": False}
    assert merge_escalations([a], [a]) == [a, a]


def test_resume_on_a_later_day_from_sqlite(tmp_path):
    db = str(tmp_path / "sessions.sqlite")
    # "Tuesday": answer a few questions, then the user leaves
    s1 = Session("margaret", db=db).say_all(CLEAN_RUN[:8])
    assert s1.last["slot"] == "spouse_name"
    del s1
    # "Friday": a brand-new process opens the same database
    g = build_intake_graph(get_checkpointer(db))
    cfg = {"configurable": {"thread_id": "margaret"}}
    snap = g.get_state(cfg)
    pending = snap.tasks[0].interrupts[0].value
    assert pending["slot"] == "spouse_name"
    summary = resume_summary(snap.values)
    assert "Margaret Ann Lee" in summary and "married filing jointly" in summary
    s2 = Session.__new__(Session)
    s2.graph, s2.cfg, s2.prompts = g, cfg, [pending]
    s2.say_all(CLEAN_RUN[8:])
    assert s2.state["intake_complete"]
    assert s2.state["answers"]["taxpayer_name"]["value"] == "Margaret Ann Lee"


def test_pause_keeps_everything_and_reasks():
    s = Session().say_all(["yes", "Margaret Lee"])
    s.say("I'm tired, can we stop for now")
    assert s.last["slot"] == "taxpayer_dob"
    assert "saved" in s.last["text"]
    assert s.state["answers"]["taxpayer_name"]["value"] == "Margaret Lee"


def test_context_strategy_is_flat_and_full_context_grows():
    s = Session().say_all(CLEAN_RUN[:-1])
    st = s.state
    early = {"transcript": st["transcript"][:6], "answers": st["answers"], "escalations": []}
    off_early = count_tokens(build_model_context(early, "q", "a", use_strategy=False))
    off_late = count_tokens(build_model_context(st, "q", "a", use_strategy=False))
    on_late = count_tokens(build_model_context(st, "q", "a", use_strategy=True))
    assert off_late > 3 * off_early
    assert on_late < off_late / 2


def test_model_context_never_contains_ssn():
    s = Session().say_all(CLEAN_RUN)
    for use in (True, False):
        text = " ".join(m["content"] for m in build_model_context(s.state, "q", "901-23-4567", use_strategy=use))
        assert "901-23-4567" not in text


def test_generic_apply_context_strategy_for_teammates():
    msgs = [{"role": "system", "content": "sys"}] + [{"role": "user", "content": str(i)} for i in range(20)]
    out = apply_context_strategy(msgs, "summary", use_strategy=True, keep_last=4)
    assert [m["content"] for m in out] == ["sys", "summary", "16", "17", "18", "19"]
    assert apply_context_strategy(msgs, "summary", use_strategy=False) == msgs


def test_env_flag_turns_strategy_off(monkeypatch):
    from src.memory import strategy_enabled
    monkeypatch.setenv("SILVERFILE_CONTEXT_STRATEGY", "off")
    assert strategy_enabled() is False
