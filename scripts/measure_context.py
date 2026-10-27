"""
Measure the context-window strategy (§4.5).

Runs one long scripted intake session (60+ turns: four dependents, questions
about jargon, a tax-advice question, a correction, some drift) and records,
for every turn, how many input tokens a model call from Intake would carry.

    python -m scripts.measure_context                       # strategy ON
    python -m scripts.measure_context --no-context-strategy # strategy OFF
    python -m scripts.measure_context --both                # run both, write docs/metrics.md

By default tokens are counted with tiktoken (cl100k) on the exact messages the
agent would send. Add --live (and set SILVERFILE_INTAKE_MODEL) to make a real
model call each turn and record the provider's reported input_tokens instead.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path

from langgraph.types import Command

from src.intake_graph import build_intake_graph
from src.memory import build_model_context, count_tokens, get_checkpointer, thread_config

ROOT = Path(__file__).resolve().parent.parent

LONG_SESSION = [
    "yes", "Margaret Ann Lee", "what does that mean", "March 4, 1950",
    "can you say that again", "no", "no", "yes", "yes",
    "Would I pay less if we filed separately?", "1",
    "Robert Lee", "June 1 1948", "no", "yes",
    "Emma Lee", "granddaughter", "901-23-4567", "yes",
    "Leo Lee", "grandson", "902-11-2222", "yes",
    "Ava Lee", "granddaughter", "903-22-3333", "yes",
    "Noah Lee", "grandson", "904-33-4444", "no",
    "yes", "the weather has been so cold lately", "2",
    "Actually, I made a mistake. My birthday is March 5, 1950",
    "yes", "what is a 1099-R", "yes", "1", "no", "yes", "yes",
    "Can I deduct my medications?", "no", "no",
    "yes", "about 1200", "no", "no", "no", "my date of birth", "March 4, 1950", "yes",
]


def run(use_strategy: bool, live: bool = False) -> list[dict]:
    os.environ["SILVERFILE_CONTEXT_STRATEGY"] = "on" if use_strategy else "off"
    g = build_intake_graph(get_checkpointer(":memory:"))
    cfg = thread_config("metrics")
    result = g.invoke({"session_id": "metrics"}, cfg)
    model = None
    if live:
        from langchain.chat_models import init_chat_model
        model = init_chat_model(os.environ["SILVERFILE_INTAKE_MODEL"], temperature=0)
    rows = []
    for i, reply in enumerate(LONG_SESSION, 1):
        if "__interrupt__" not in result:
            break
        prompt = result["__interrupt__"][0].value
        state = g.get_state(cfg).values
        msgs = build_model_context(state, prompt.get("text", ""), reply, use_strategy=use_strategy)
        tokens, measured = count_tokens(msgs), "tiktoken"
        if model is not None:
            resp = model.invoke([(m["role"] if m["role"] != "assistant" else "ai", m["content"]) for m in msgs])
            usage = getattr(resp, "usage_metadata", None) or {}
            if usage.get("input_tokens"):
                tokens, measured = int(usage["input_tokens"]), "api"
        rows.append({"turn": i, "slot": prompt.get("slot") or prompt.get("type"), "input_tokens": tokens,
                     "messages": len(msgs), "measured": measured})
        result = g.invoke(Command(resume=reply), cfg)
    return rows


def write_trace(rows, use_strategy):
    out = ROOT / "traces" / f"context_{'on' if use_strategy else 'off'}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    return out


def write_metrics(on: list[dict], off: list[dict]):
    n = min(len(on), len(off))
    lines = [
        "# Context-window strategy: metrics",
        "",
        "Agent measured: **Intake** (the model-calling agent with the longest conversation).",
        "Strategy: **selective recall** = fixed system prompt + a code-generated summary of decisions so far "
        "+ only the last 4 turns (long ones clipped) + the current question. Disabled = the whole transcript is sent every turn.",
        "",
        f"Session: one scripted intake of {n} turns (`scripts/measure_context.py`, `LONG_SESSION`): four dependents, "
        "jargon questions, a tax-advice question, a correction, off-topic drift, and a change at the confirmation step.",
        "",
        f"Token counts: `{on[0]['measured']}`"
        + (" (cl100k encoding on the exact messages the agent sends; rerun with `--live` for provider-reported counts)."
           if on[0]["measured"] == "tiktoken" else " (provider-reported input tokens)."),
        "",
        "Reproduce:",
        "```",
        "python -m scripts.measure_context --both",
        "```",
        "",
        "## Summary",
        "",
        "| | Strategy OFF | Strategy ON |",
        "|---|---:|---:|",
        f"| First turn | {off[0]['input_tokens']} | {on[0]['input_tokens']} |",
        f"| Last turn | {off[n-1]['input_tokens']} | {on[n-1]['input_tokens']} |",
        f"| Max | {max(r['input_tokens'] for r in off[:n])} | {max(r['input_tokens'] for r in on[:n])} |",
        f"| Mean | {statistics.mean(r['input_tokens'] for r in off[:n]):.0f} | {statistics.mean(r['input_tokens'] for r in on[:n]):.0f} |",
        f"| Total over session | {sum(r['input_tokens'] for r in off[:n])} | {sum(r['input_tokens'] for r in on[:n])} |",
        f"| Growth per turn (last minus first, over turns) | {(off[n-1]['input_tokens']-off[0]['input_tokens'])/(n-1):.1f} | {(on[n-1]['input_tokens']-on[0]['input_tokens'])/(n-1):.1f} |",
        "",
        "With the strategy off, every turn re-sends the whole conversation, so cost grows in a straight line. "
        "With it on, the window of recent turns is fixed and the decisions summary is bounded, "
        "so the cost per turn stays roughly constant however long the session runs. "
        "Nothing is lost: the full answers live in the state's answer ledger, not in the prompt.",
        "",
        "The two small bumps near the end of the ON curve are the confirmation step: there the *current question* "
        "is the full list of answers read back to the taxpayer, which has to be sent in full.",
        "",
        "## Per-turn input tokens",
        "",
        "| Turn | Question | Strategy OFF | Strategy ON |",
        "|---:|---|---:|---:|",
    ]
    for a, b in zip(off[:n], on[:n]):
        lines.append(f"| {a['turn']} | `{a['slot']}` | {a['input_tokens']} | {b['input_tokens']} |")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "metrics.md").write_text("\n".join(lines) + "\n")
    _chart(on[:n], off[:n])


def _chart(on, off):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    ax.plot([r["turn"] for r in off], [r["input_tokens"] for r in off], label="Strategy off (full transcript)", color="#c0392b")
    ax.plot([r["turn"] for r in on], [r["input_tokens"] for r in on], label="Strategy on (selective recall)", color="#2471a3")
    ax.set_xlabel("Conversation turn")
    ax.set_ylabel("Input tokens per model call")
    ax.set_title("Intake agent: input tokens per turn")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(ROOT / "docs" / "context_tokens.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-context-strategy", action="store_true")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    modes = [True, False] if args.both else [not args.no_context_strategy]
    results = {}
    for m in modes:
        rows = run(m, live=args.live)
        results[m] = rows
        print(f"strategy {'ON ' if m else 'OFF'}: {len(rows)} turns, first={rows[0]['input_tokens']}, "
              f"last={rows[-1]['input_tokens']} tokens -> {write_trace(rows, m)}")
    on_p, off_p = ROOT / "traces" / "context_on.json", ROOT / "traces" / "context_off.json"
    if on_p.exists() and off_p.exists():
        write_metrics(json.loads(on_p.read_text()), json.loads(off_p.read_text()))
        print("wrote docs/metrics.md")


if __name__ == "__main__":
    main()
