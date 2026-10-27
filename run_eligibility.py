"""Run the Eligibility & Rules agent on a fixture.

    python run_eligibility.py data/fixtures/eligibility/clean_mfj.json
    python run_eligibility.py data/fixtures/eligibility/hitl_1099r_box2b.json --graph

Without --graph: prints the decision (no LangGraph needed).
With --graph:    runs the mini LangGraph and lets you resolve any human-review
                 interrupt from the terminal.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents.eligibility import eligibility_agent


def show(result: dict) -> None:
    print("\n=== Eligibility result ===")
    for k in ("in_scope", "eligible_1040sr", "boxes_checked", "senior_taxpayer",
              "senior_spouse", "age_box_count", "blind_box_count", "filing_status",
              "filing_status_valid", "std_deduction_chart_applies", "std_deduction_lookup",
              "schedule_1a_qualifiers", "schedules", "citations"):
        print(f"  {k:28} {result.get(k)}")


def show_escalations(escs: list[dict]) -> None:
    if not escs:
        print("\n  No escalations.")
        return
    print(f"\n=== {len(escs)} escalation(s) ===")
    for i, e in enumerate(escs):
        print(f"  [{i}] {e['reason']:<34} {e['action']:<13} {e['detail']}")
        if e.get("evidence"):
            print(f"      evidence: {e['evidence']}")


def run_plain(state: dict) -> None:
    out = eligibility_agent(state)
    show(out["eligibility"])
    show_escalations(out["escalations"])


def run_graph(state: dict) -> None:
    from langgraph.types import Command
    from src.eligibility_standalone_graph import build_graph

    graph = build_graph()
    cfg = {"configurable": {"thread_id": "demo-1"}}
    result = graph.invoke(state, cfg)

    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print("\n>>> HUMAN REVIEW (LangGraph interrupt) <<<")
        show_escalations(payload["escalations"])
        note = input("\nReviewer note (Enter = 'Reviewed by volunteer'): ").strip() \
            or "Reviewed by volunteer"
        result = graph.invoke(Command(resume={
            "reviewer": "kushim-staff-1", "resolve_all": True, "note": note}), cfg)

    show(result["eligibility"])
    from src.eligibility_standalone_graph import human_rounds
    print(f"\n  status: {result.get('status')}  |  next: {result.get('next_agent')}"
          f"  |  human review rounds: {human_rounds(result)}")


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("fixture", type=Path)
    p.add_argument("--graph", action="store_true", help="run through the mini LangGraph")
    a = p.parse_args()
    state = json.loads(a.fixture.read_text())
    run_graph(state) if a.graph else run_plain(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
