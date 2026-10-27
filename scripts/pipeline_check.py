"""End-to-end check: Eligibility -> routing -> Computation -> Audit.

Run with:  python -m scripts.pipeline_check
Uses Narain's sample taxpayers in data/fixtures/eligibility/.
No network and no API key needed.
"""
import copy
import json
from pathlib import Path

from src.agents.computation import computation_agent
from src.agents.eligibility import eligibility_agent
from src.agents.eligibility_schemas import open_escalations
from src.tools.audit_checks import audit_return

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "eligibility"
LINES = ["1z", "2b", "6a", "6b", "9", "11a", "12e", "13b", "14", "15",
         "16", "24", "25a", "25b", "25d", "33", "34", "37"]


def load(name):
    return json.loads((FIXTURES / name).read_text())


def merge(state, update):
    """Apply one agent's output to the state, the way the graph would."""
    out = dict(state)
    for key, value in update.items():
        if key == "escalations":
            out[key] = list(out.get(key) or []) + list(value)
        elif key in ("eligibility", "provenance"):
            out[key] = {**(out.get(key) or {}), **value}
        else:
            out[key] = value
    return out


def show_escalations(items):
    for e in items:
        print(f"    - {e.get('reason')}: {e.get('detail')}")


def run_case(name):
    print(f"\n=== {name} ===")
    state = load(name)
    state = merge(state, eligibility_agent(state))
    open_items = open_escalations(state.get("escalations") or [])
    if open_items:
        print("  Eligibility raised open escalations -> would go to a human, "
              "Computation is not run:")
        show_escalations(open_items)
        return
    elig = state.get("eligibility") or {}
    print(f"  Eligibility OK: in_scope={elig.get('in_scope')}, "
          f"boxes_checked={elig.get('boxes_checked')}, "
          f"senior_taxpayer={elig.get('senior_taxpayer')}, "
          f"senior_spouse={elig.get('senior_spouse')}")
    result = computation_agent(state)
    if "provenance" not in result:
        print("  Computation BLOCKED (escalated instead of guessing):")
        show_escalations(result.get("escalations") or [])
        return
    prov = result["provenance"]
    print("  Computation produced these lines:")
    for line in LINES:
        if line in prov:
            print(f"    line {line:>4}: ${prov[line]['amount']:,}")
    findings = audit_return(prov)
    if findings:
        print("  AUDIT FOUND PROBLEMS:")
        for f in findings:
            print(f"    - line {f['line']}: {f['problem']} (send back to {f['send_back_to']})")
    else:
        print("  Audit: all checks pass")


def duplicate_check():
    print("\n=== duplicate W-2 check ===")
    state = load("clean_single.json")
    state = merge(state, eligibility_agent(state))
    base = computation_agent(state)
    if "provenance" not in base:
        print("  skipped: the clean_single sample did not compute")
        return
    docs = list(state.get("documents") or [])
    w2 = next((d for d in docs if d.get("doc_type") == "W-2"), None)
    if w2 is None:
        print("  skipped: no W-2 in the sample")
        return
    doubled = dict(state, documents=docs + [copy.deepcopy(w2)])
    again = computation_agent(doubled)
    before = base["provenance"]["1z"]["amount"]
    after = (again.get("provenance") or {}).get("1z", {}).get("amount")
    if "provenance" not in again:
        print("  Computation refused the duplicate and escalated:")
        show_escalations(again.get("escalations") or [])
    elif after == before:
        print(f"  OK: line 1z stayed ${before:,} (duplicate ignored)")
    else:
        print(f"  PROBLEM: line 1z went from ${before:,} to ${after:,} "
              "(the same W-2 was counted twice)")


def main():
    for name in sorted(p.name for p in FIXTURES.glob("*.json")):
        try:
            run_case(name)
        except Exception as exc:
            print(f"  ERROR in {name}: {type(exc).__name__}: {exc}")
    try:
        duplicate_check()
    except Exception as exc:
        print(f"  ERROR in duplicate check: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()