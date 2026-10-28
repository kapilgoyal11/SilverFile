"""
Run a SilverFile session from the console.

    python run.py --session demo-1                                  # full intake conversation
    python run.py --session demo-1 --state data/fixtures/eligibility/clean_single.json
    python run.py --session demo-1 --state ... --doc data/fixtures/w2_1.png --doc data/fixtures/int_1.json
    python run.py --session demo-1 --resume                         # pick up a paused session

--state     seeds the session with a finished intake (skips the conversation)
--ssn       test SSN (900-999 prefix) for the taxpayer when seeding with --state;
            --spouse-ssn likewise. Review requires them; Intake collects them in a live session.
--doc       a document file to extract; repeatable. Images need ANTHROPIC_API_KEY,
            JSON fixtures do not. --offline reads W-2 images from their answer keys.
--db        SQLite file for checkpoints (default: silverfile_sessions.sqlite, or $SILVERFILE_DB)

When the graph pauses it prints why and asks you. Intake questions take a
plain answer. Human review takes "proceed <note>" or "stop <note>".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langgraph.types import Command

from src.agents.extraction import NODE as EXTRACTION, make_extraction_agent
from src.graph import build_graph
from src.memory import get_checkpointer, thread_config

LINES = ["1z", "2a", "2b", "3a", "3b", "4a", "4b", "5a", "5b", "6a", "6b", "9", "10", "11a",
         "12e", "13b", "14", "15", "16", "24", "25a", "25b", "25d", "26", "33", "34", "37"]


def doc_entry(path: str) -> dict:
    p = Path(path)
    if p.suffix.lower() == ".json":
        doc_type = json.loads(p.read_text()).get("doc_type", "W-2")
        return {"doc_id": p.stem, "doc_type": doc_type, "path": str(p), "media_type": "application/json"}
    return {"doc_id": p.stem, "doc_type": "W-2", "path": str(p)}


def answer_interrupt(payload: dict):
    kind = payload.get("type")
    if kind == "intake_question":
        print(f"\nSilverFile: {payload['text']}")
        return input("You: ")
    if kind == "human_review":
        print(f"\n*** {payload['message']}")
        for e in payload.get("escalations", []):
            print(f"  - [{e.get('raised_by')}] {e.get('reason')}: {e.get('detail') or e.get('message')}")
        print(payload.get("how_to_resume", ""))
        return input("Kushim staff: ")
    print(f"\nPaused: {json.dumps(payload, indent=2, default=str)[:800]}")
    return input("Resume with: ")


def show(result: dict) -> None:
    print(f"\nstatus: {result.get('status')}")
    hops = [e["to"] for e in result.get("handoff_log") or [] if e.get("from") == "orchestrator"]
    print("hops  :", " -> ".join(hops))
    prov = result.get("provenance") or {}
    if prov:
        print("\nDraft 1040-SR lines (every amount carries a provenance record):")
        for line in LINES:
            if line in prov:
                rec = prov[line]
                src = rec.get("formula") or rec.get("calculation") or rec.get("source") or rec.get("kind") or ""
                print(f"  line {line:>4}: ${rec.get('amount', 0):>10,}   {str(src)[:70]}")
    audit = result.get("audit") or {}
    if audit:
        print(f"\naudit: verdict={audit.get('verdict')} round={audit.get('round')} checks={audit.get('checks_run')}")
    gate = result.get("release_gate")
    if gate:
        print(f"release gate: {'passed' if gate['passed'] else 'blocked'} {gate.get('violations') or ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, help="thread id; reuse it to resume")
    ap.add_argument("--state", help="JSON file with a finished intake (skips the conversation)")
    ap.add_argument("--doc", action="append", default=[], help="document file to extract (repeatable)")
    ap.add_argument("--ssn", help="taxpayer test SSN when seeding with --state")
    ap.add_argument("--spouse-ssn", help="spouse test SSN when seeding with --state")
    ap.add_argument("--db", default=None, help="SQLite checkpoint file")
    ap.add_argument("--offline", action="store_true", help="read W-2 images from fixture answer keys")
    ap.add_argument("--resume", action="store_true", help="continue a paused session without new input")
    args = ap.parse_args()

    agents = {}
    if args.offline:
        from src.tools.w2_reader import FixtureReader
        agents[EXTRACTION] = make_extraction_agent(FixtureReader())
    graph = build_graph(get_checkpointer(args.db), agents)
    config = thread_config(args.session)

    if args.resume:
        state = graph.get_state(config).values
        payload = graph.get_state(config).interrupts
        result = graph.invoke(Command(resume=answer_interrupt(payload[0].value)) if payload else None, config)
    else:
        state = {"session_id": args.session}
        if args.state:
            state.update(json.loads(Path(args.state).read_text()))
            state["intake_complete"] = True
            answers = {}
            if args.ssn:
                answers["taxpayer_ssn"] = {"slot": "taxpayer_ssn", "value": args.ssn, "source": "direct user input"}
            if args.spouse_ssn:
                answers["spouse_ssn"] = {"slot": "spouse_ssn", "value": args.spouse_ssn, "source": "direct user input"}
            if answers:
                state["answers"] = {**(state.get("answers") or {}), **answers}
        if args.doc:
            state["document_files"] = [doc_entry(d) for d in args.doc]
        result = graph.invoke(state, config)

    while "__interrupt__" in result:
        reply = answer_interrupt(result["__interrupt__"][0].value)
        result = graph.invoke(Command(resume=reply), config)

    show(result)
    out = Path("traces") / f"{args.session}_state.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nfull state written to {out}")


if __name__ == "__main__":
    main()
