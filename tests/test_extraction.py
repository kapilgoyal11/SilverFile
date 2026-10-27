"""Tests for the Document Extraction agent. No model is called: FixtureReader
returns the answer key saved next to each synthetic W-2 in data/fixtures/."""
import base64
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.computation import computation_agent
from src.agents.extraction import build_extraction_graph, extract_document
from src.tools.w2_reader import (AnthropicVisionReader, FixtureReader, ReaderError, W2Reading,
                                 default_reader)

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"


def w2(name="w2_1", doc_id="w2_1"):
    return {"doc_id": doc_id, "doc_type": "W-2", "path": str(FIXTURES / f"{name}.png")}


def json_doc(name, doc_type):
    return {"doc_id": name, "doc_type": doc_type, "path": str(FIXTURES / f"{name}.json")}


ALL_FILES = [w2("w2_1"), w2("w2_2", "w2_2"), json_doc("int_1", "1099-INT"), json_doc("ssa_1", "SSA-1099")]


class TweakedReader(FixtureReader):
    """Answer key with some boxes changed, to simulate a misread."""

    def __init__(self, **changes):
        self.changes = changes

    def read_w2(self, image_bytes, media_type, source_path):
        r = super().read_w2(image_bytes, media_type, source_path).model_dump()
        r.update(self.changes)
        return W2Reading.model_validate(r)


# ---------- one W-2 ----------

def test_clean_w2_gives_whole_dollar_fields_computation_can_read():
    out = extract_document(w2(), FixtureReader())
    [doc] = out["documents"]
    assert doc["status"] == "ok" and "escalations" not in out
    f = doc["fields"]
    assert f["box_1_wages"] == 18450 and f["box_2_withholding"] == 1210
    assert all(isinstance(f[k], int) for k in ("box_1_wages", "box_2_withholding", "box_4_ss_tax_withheld"))
    assert f["employee_ssn"] == "900-12-3456" and f["employer_ein"] == "12-3456789"
    assert (f["box_12a_code"], f["box_12a_amount"]) == ("D", 1500)
    assert f["box_13_retirement_plan"] is True
    assert f["box_7_ss_tips"] == 0  # blank box on the form


def test_every_field_has_provenance():
    [doc] = extract_document(w2(), FixtureReader())["documents"]
    assert set(doc["field_provenance"]) == set(doc["fields"])
    for name, p in doc["field_provenance"].items():
        assert p["kind"] == "document" and p["document"] == "w2_1" and p["field"] == name
        assert p["source_sha256"] == doc["source_sha256"]
    assert doc["field_provenance"]["box_1_wages"]["raw_text"] == "18450.00"


def test_extraction_never_maps_to_return_lines():
    [doc] = extract_document(w2(), FixtureReader())["documents"]
    assert not any("line" in name for name in doc["fields"])


def test_cents_are_rounded_to_whole_dollars_and_recorded():
    reader = TweakedReader(box_1={"text": "18450.60", "confidence": 0.99})
    [doc] = extract_document(w2(), reader)["documents"]
    assert doc["fields"]["box_1_wages"] == 18451
    assert doc["field_provenance"]["box_1_wages"]["rounded_from"] == "18450.60"


def test_real_looking_ssn_is_refused_and_never_stored():
    out = extract_document(w2("w2_bad_ssn", "w2_bad"), FixtureReader())
    assert "documents" not in out
    assert [e["reason"] for e in out["escalations"]] == ["invalid_ssn"]
    dumped = json.dumps(out)
    assert "123-45-6789" not in dumped and "123456789" not in dumped


def test_low_confidence_box_is_kept_but_escalated():
    out = extract_document(w2(), TweakedReader(box_2={"text": "1210.00", "confidence": 0.4}))
    [doc] = out["documents"]
    assert doc["status"] == "needs_confirmation" and doc["fields"]["box_2_withholding"] == 1210
    [esc] = out["escalations"]
    assert esc["reason"] == "extraction_needs_confirmation" and "box_2_withholding" in esc["detail"]


def test_unreadable_box_is_left_out_not_guessed_as_zero():
    out = extract_document(w2(), TweakedReader(box_2={"text": None, "confidence": 0.2}))
    [doc] = out["documents"]
    assert "box_2_withholding" not in doc["fields"]
    assert out["escalations"][0]["reason"] == "extraction_needs_confirmation"


def test_unparseable_amount_is_left_out():
    [doc] = extract_document(w2(), TweakedReader(box_1={"text": "18,45O.00", "confidence": 0.9}))["documents"]
    assert "box_1_wages" not in doc["fields"]
    assert doc["issues"][0]["code"] == "unparseable_amount"


def test_not_a_w2_is_rejected():
    out = extract_document(w2(), TweakedReader(is_w2=False))
    assert "documents" not in out and out["escalations"][0]["reason"] == "wrong_document_type"


def test_reader_failure_becomes_an_escalation_without_the_ssn():
    class Broken:
        model_id = "broken"

        def read_w2(self, *args):
            raise ReaderError("boom 123-45-6789")

    out = extract_document(w2(), Broken())
    assert out["escalations"][0]["reason"] == "extraction_failed"
    assert "123-45-6789" not in json.dumps(out)


def test_missing_file_becomes_an_escalation():
    out = extract_document({"doc_id": "x", "doc_type": "W-2", "path": "nope.png"}, FixtureReader())
    assert out["escalations"][0]["reason"] == "extraction_failed"


def test_json_forms_use_computation_field_names():
    [i] = extract_document(json_doc("int_1", "1099-INT"))["documents"]
    [s] = extract_document(json_doc("ssa_1", "SSA-1099"))["documents"]
    assert i["fields"]["box_1_interest"] == 312 and i["fields"]["box_4_withholding"] == 0
    assert s["fields"]["box_5_net_benefits"] == 21600 and s["fields"]["box_6_withheld"] == 1200


def test_json_with_a_different_form_type_is_rejected():
    out = extract_document(json_doc("int_1", "SSA-1099"))
    assert out["escalations"][0]["reason"] == "wrong_document_type"


# ---------- parallel fan-out in LangGraph ----------

class SlowReader(FixtureReader):
    def __init__(self):
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def read_w2(self, *args):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.3)
        with self.lock:
            self.active -= 1
        return super().read_w2(*args)


def test_fan_out_extracts_every_file_in_parallel():
    reader = SlowReader()
    out = build_extraction_graph(reader).invoke({"session_id": "t", "document_files": ALL_FILES})
    assert sorted(d["doc_id"] for d in out["documents"]) == ["int_1", "ssa_1", "w2_1", "w2_2"]
    assert reader.peak >= 2
    assert len(out["handoff_log"]) == 4


def test_handled_files_are_not_extracted_again():
    files = ALL_FILES + [w2("w2_bad_ssn", "w2_bad")]
    graph = build_extraction_graph(FixtureReader())
    first = graph.invoke({"session_id": "t", "document_files": files})
    second = graph.invoke(first)
    assert len(second["documents"]) == 4          # no duplicates
    assert len(second["escalations"]) == 1        # the rejected file is not retried in a loop


def test_no_files_is_a_no_op():
    out = build_extraction_graph(FixtureReader()).invoke({"session_id": "t", "document_files": []})
    assert not out.get("documents")


# ---------- handoff to Computation ----------

def test_computation_accepts_extraction_output():
    out = build_extraction_graph(FixtureReader()).invoke({"session_id": "t", "document_files": ALL_FILES})
    state = {"intake": {"filing_status": "mfj"},
             "eligibility": {"in_scope": True, "boxes_checked": 2,
                             "senior_taxpayer": True, "senior_spouse": True},
             "documents": out["documents"]}
    result = computation_agent(state)
    assert "escalations" not in result, result.get("escalations")
    p = result["provenance"]
    assert p["1z"]["amount"] == 18450 + 9800
    assert p["25a"]["amount"] == 1210 + 400
    assert p["2b"]["amount"] == 312 and p["6a"]["amount"] == 21600
    assert p["25b"]["amount"] == 1200


# ---------- the Claude reader (fake client, no network) ----------

class FakeClient:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)
        self._resp = SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def test_claude_request_shape():
    path = FIXTURES / "w2_1.png"
    parsed = FixtureReader().read_w2(b"", "image/png", str(path))
    client = FakeClient(parsed)
    out = extract_document(w2(), AnthropicVisionReader(model="claude-opus-5-5", client=client))
    assert out["documents"][0]["model"] == "claude-opus-5-5"
    call = client.calls[0]
    assert call["output_format"] is W2Reading
    image = call["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image["source"]["data"]) == path.read_bytes()


def test_refusal_raises():
    reader = AnthropicVisionReader(client=FakeClient(None, stop_reason="refusal"))
    with pytest.raises(ReaderError):
        reader.read_w2(b"x", "image/png", "x.png")


def test_placeholder_key_uses_offline_reader(monkeypatch):
    monkeypatch.delenv("EXTRACTION_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "your-key-here")
    assert isinstance(default_reader(), FixtureReader)
