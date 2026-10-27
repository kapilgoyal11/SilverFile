"""Merge-order tests (brief section 4.1: parallel branch + correct reducer).

1. The reducer function itself behaves.
2. A real LangGraph graph with two parallel branches loses no writes, and the
   final state does not depend on the order the branches were registered.
"""
from langgraph.graph import END, START, StateGraph

from src.state import SilverFileState, merge_dicts


# ---------- 1. the reducer ----------

def test_disjoint_keys_merge_in_either_order():
    a = {"1z": 50000}
    b = {"2b": 2000}
    assert merge_dicts(a, b) == merge_dicts(b, a) == {"1z": 50000, "2b": 2000}


def test_same_key_later_write_wins():
    assert merge_dicts({"9": 100}, {"9": 200}) == {"9": 200}


def test_none_is_treated_as_empty():
    assert merge_dicts(None, {"a": 1}) == {"a": 1}
    assert merge_dicts({"a": 1}, None) == {"a": 1}
    assert merge_dicts(None, None) == {}


def test_inputs_are_not_mutated():
    left = {"a": 1}
    right = {"b": 2}
    merge_dicts(left, right)
    assert left == {"a": 1}
    assert right == {"b": 2}


# ---------- 2. a real parallel graph ----------

def extract_w2(state):
    return {
        "documents": [{"doc_id": "w2_1", "doc_type": "W-2",
                       "fields": {"box_1_wages": 50000}}],
        "provenance": {"1z": {"amount": 50000}},
    }


def extract_1099(state):
    return {
        "documents": [{"doc_id": "int_1", "doc_type": "1099-INT",
                       "fields": {"box_1_interest": 2000}}],
        "provenance": {"2b": {"amount": 2000}},
    }


def join(state):
    # Runs only after both branches finished.
    return {"handoff_log": [{"agent": "join",
                             "documents_seen": len(state["documents"])}]}


def build_graph(first, second):
    builder = StateGraph(SilverFileState)
    builder.add_node(first.__name__, first)
    builder.add_node(second.__name__, second)
    builder.add_node("join", join)
    builder.add_edge(START, first.__name__)
    builder.add_edge(START, second.__name__)
    builder.add_edge(first.__name__, "join")
    builder.add_edge(second.__name__, "join")
    builder.add_edge("join", END)
    return builder.compile()


def run(first, second):
    return build_graph(first, second).invoke({"session_id": "test"})


def sorted_docs(result):
    return sorted(result["documents"], key=lambda d: d["doc_id"])


def test_parallel_branches_lose_no_writes():
    result = run(extract_w2, extract_1099)
    assert {d["doc_id"] for d in result["documents"]} == {"w2_1", "int_1"}
    assert set(result["provenance"]) == {"1z", "2b"}


def test_join_runs_after_both_branches():
    result = run(extract_w2, extract_1099)
    assert result["handoff_log"] == [{"agent": "join", "documents_seen": 2}]


def test_result_does_not_depend_on_registration_order():
    a = run(extract_w2, extract_1099)
    b = run(extract_1099, extract_w2)
    assert sorted_docs(a) == sorted_docs(b)
    assert a["provenance"] == b["provenance"]