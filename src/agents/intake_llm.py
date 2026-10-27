"""
Optional LLM help for understanding free-text replies.

Off by default. Turn on by setting SILVERFILE_INTAKE_MODEL, e.g.
    SILVERFILE_INTAKE_MODEL=anthropic:claude-sonnet-4-5   (needs langchain-anthropic)
    SILVERFILE_INTAKE_MODEL=openai:gpt-4o-mini            (needs langchain-openai)

Safety:
- Only called when the rule-based parser fails.
- Its context is built by memory.build_model_context, which applies the
  context strategy and redacts SSNs. SSN questions never go to the model.
- Whatever it returns is re-validated by the same rule-based parser, so it can
  only help phrase-match, never invent a value.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from src.agents.intake_parsers import ParseResult, parse
from src.memory import build_model_context, count_tokens, strategy_enabled

_model = None


def llm_available() -> bool:
    return bool(os.environ.get("SILVERFILE_INTAKE_MODEL"))


def _get_model():
    global _model
    if _model is None:
        from langchain.chat_models import init_chat_model  # imported lazily
        _model = init_chat_model(os.environ["SILVERFILE_INTAKE_MODEL"], temperature=0)
    return _model


def llm_extract(state: dict, slot_kind: str, options: Optional[list[dict]], question: str,
                user_text: str, turn: int) -> tuple[Optional[ParseResult], Optional[dict]]:
    if not llm_available() or slot_kind == "ssn":
        return None, None
    use = strategy_enabled()
    msgs = build_model_context(state, question, user_text, use_strategy=use)
    msgs[-1]["content"] += f"\nExpected type: {slot_kind}" + (
        f" (one of: {[o['value'] for o in options]})" if options else "")
    try:
        resp = _get_model().invoke([(m["role"] if m["role"] != "assistant" else "ai", m["content"]) for m in msgs])
        usage = getattr(resp, "usage_metadata", None) or {}
        log = {"agent": "intake", "turn": turn, "input_tokens": int(usage.get("input_tokens") or count_tokens(msgs)),
               "strategy": "selective_recall" if use else "none",
               "measured": "api" if usage.get("input_tokens") else "tiktoken"}
        m = re.search(r"\{.*\}", str(resp.content), re.S)
        value = json.loads(m.group(0)).get("value") if m else None
    except Exception:
        return None, None
    if value is None:
        return None, log
    if slot_kind == "choice" and options:
        result = parse("choice", str(value), options) if str(value) not in [o["value"] for o in options] \
            else ParseResult(True, str(value))
    elif slot_kind == "yes_no" and isinstance(value, bool):
        result = ParseResult(True, value)
    else:
        result = parse(slot_kind, str(value), options)
    return (result if result.ok else None), log
