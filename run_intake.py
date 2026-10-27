"""
Talk to the Intake agent in the terminal, with save/resume.

    python run_intake.py --session margaret          # start or resume
    python run_intake.py --session margaret --show   # print what's been collected
    python run_intake.py --session margaret --no-context-strategy

Type "quit" at any time to leave. Run the same command later (even on another
day) to pick up where you left off.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from langgraph.types import Command

from src.intake_graph import build_intake_graph
from src.agents.intake import PAUSE_NOTICE
from src.memory import get_checkpointer, intake_record, resume_summary, thread_config


def _pending(graph, cfg):
    snap = graph.get_state(cfg)
    for task in snap.tasks:
        for itr in task.interrupts:
            return snap, itr.value
    return snap, None


def _print_prompt(p: dict):
    if p.get("type") == "human_review":
        print("\n[VOLUNTEER NEEDED] " + p["text"])
        for e in p["escalations"]:
            print(f"  - {e['kind']}: {e['detail']}")
        print("Type the volunteer's resolution note to continue (or 'quit').")
    else:
        print("\nSilverFile: " + p["text"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--db", default=os.environ.get("SILVERFILE_DB", "silverfile_sessions.sqlite"))
    ap.add_argument("--no-context-strategy", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the intake record and exit")
    args = ap.parse_args()
    if args.no_context_strategy:
        os.environ["SILVERFILE_CONTEXT_STRATEGY"] = "off"

    graph = build_intake_graph(get_checkpointer(args.db))
    cfg = thread_config(args.session)
    snap, prompt = _pending(graph, cfg)

    if args.show:
        print(json.dumps(intake_record(snap.values), indent=2))
        return

    if snap.values and prompt:
        print(resume_summary(snap.values))
        prompt = {**prompt, "text": prompt["text"].replace(PAUSE_NOTICE, "").strip()}
    elif snap.values and snap.values.get("intake_complete"):
        print("This session's intake is already complete. Use --show to see it.")
        return
    else:
        print("Hello. I'm going to ask you some questions to get your tax information ready. "
              "One question at a time. You can say \"go back\", \"what does that mean\", "
              "\"I'm not sure\", or \"pause\" at any point.")
        result = graph.invoke({"session_id": args.session}, cfg)
        prompt = result["__interrupt__"][0].value if "__interrupt__" in result else None

    while prompt:
        _print_prompt(prompt)
        try:
            reply = input("> ").strip()
        except EOFError:
            reply = "quit"
        if reply.lower() in ("quit", "exit"):
            print("Saved. Run the same command again to continue.")
            return
        resume = {"resolution": reply} if prompt.get("type") == "human_review" else reply
        result = graph.invoke(Command(resume=resume), cfg)
        prompt = result["__interrupt__"][0].value if "__interrupt__" in result else None
        if prompt and prompt.get("text", "").startswith(PAUSE_NOTICE):
            _print_prompt(prompt)
            print("(Session saved. Run the same command later to continue.)")
            return

    print("\nIntake is complete. Here's the record passed to the other agents:")
    print(json.dumps(intake_record(graph.get_state(cfg).values), indent=2))


if __name__ == "__main__":
    sys.exit(main())
