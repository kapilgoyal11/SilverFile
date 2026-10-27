"""Loads data/policy.md and exposes the set of valid rule IDs.

Every citation the agent produces is checked against this set, so the agent
can never cite a rule that is not in the reference corpus.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "policy.md"
_RULE_RE = re.compile(r"\*\*(R\d+\.\d+)\*\*\s*(.+)")


@lru_cache(maxsize=1)
def load_rules(path: Path = POLICY_PATH) -> dict[str, str]:
    """Return {rule_id: first line of rule text}."""
    rules: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _RULE_RE.search(line)
        if m:
            rules[m.group(1)] = m.group(2).strip()
    return rules


def rule_text(rule_id: str) -> str:
    rules = load_rules()
    if rule_id not in rules:
        raise KeyError(f"Rule {rule_id} is not defined in policy.md")
    return rules[rule_id]


def assert_cited(rule_id: str) -> str:
    """Raise if rule_id is not in the corpus. Returns the id for chaining."""
    rule_text(rule_id)
    return rule_id
