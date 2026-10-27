"""Input/output contract for the Eligibility & Rules agent.

Matches the team's src/state.py (SilverFileState):
  * state["intake"]      — flat dict written by Intake (birth_date, is_blind, spouse_*, ...)
  * state["documents"]   — list of {"doc_id", "doc_type", "fields", "source_file"}
  * state["eligibility"] — EligibilityResult, written by this agent
  * state["escalations"] — {"raised_by", "reason", "detail", "resolved", ...}

Internally the rules use the nested IntakeData model below. IntakeData accepts
EITHER the team's flat intake dict OR the nested form, so tests can stay short.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

FilingStatus = Literal["single", "mfj", "mfs", "hoh", "qss"]

AGENT_NAME = "eligibility"   # value of "raised_by" on our escalations


# ---------------------------------------------------------------- inputs
class Person(BaseModel):
    dob: Optional[date] = None
    blind: bool = False
    ssn: Optional[str] = None          # only if genuinely needed


class Spouse(Person):
    date_of_death: Optional[date] = None
    itemizes: bool = False             # relevant for MFS (R4.2)


class Dependent(BaseModel):
    name: str
    relationship: str
    ssn: Optional[str] = None
    qualifying_child: bool = False


class RefundInfo(BaseModel):
    wants_direct_deposit: bool = False
    taxpayer_can_describe_account: Optional[bool] = None  # None = not asked yet


class Respondent(BaseModel):
    is_taxpayer: bool = True
    relationship: Optional[str] = None
    stated_authority: Optional[str] = None   # e.g. "power of attorney"


# Team flat intake key  ->  (nested path in IntakeData)
FLAT_INTAKE_MAP: dict[str, tuple[str, ...]] = {
    "birth_date": ("taxpayer", "dob"),
    "is_blind": ("taxpayer", "blind"),
    "taxpayer_ssn": ("taxpayer", "ssn"),
    "spouse_birth_date": ("spouse", "dob"),
    "spouse_is_blind": ("spouse", "blind"),
    "spouse_ssn": ("spouse", "ssn"),
    "spouse_date_of_death": ("spouse", "date_of_death"),
    "spouse_itemizes": ("spouse", "itemizes"),
    "document_inventory": ("documents_reported",),
    "refund_direct_deposit": ("refund", "wants_direct_deposit"),
    "refund_account_known": ("refund", "taxpayer_can_describe_account"),
    "answered_by_taxpayer": ("respondent", "is_taxpayer"),
    "answered_by_relationship": ("respondent", "relationship"),
    "answered_by_authority": ("respondent", "stated_authority"),
}


class IntakeData(BaseModel):
    """Structured answers collected by the Intake agent (no arithmetic, no rules)."""
    filing_status: Optional[FilingStatus] = None
    taxpayer: Person = Field(default_factory=Person)
    spouse: Optional[Spouse] = None
    dependents: list[Dependent] = Field(default_factory=list)
    has_qualifying_person: bool = False      # for HOH
    can_be_claimed_as_dependent: bool = False
    dual_status_alien: bool = False
    documents_reported: list[str] = Field(default_factory=list)
    income_sources_denied: list[str] = Field(default_factory=list)  # e.g. ["wages"]
    schedules_reported: list[str] = Field(default_factory=list)     # e.g. ["1", "3"]
    refund: RefundInfo = Field(default_factory=RefundInfo)
    respondent: Respondent = Field(default_factory=Respondent)

    @model_validator(mode="before")
    @classmethod
    def _from_team_format(cls, data: Any) -> Any:
        """Convert the team's flat state["intake"] into the nested form.
        Unknown keys (e.g. estimated_payments) are ignored."""
        if not isinstance(data, dict) or not any(k in data for k in FLAT_INTAKE_MAP):
            return data
        out = {k: v for k, v in data.items() if k not in FLAT_INTAKE_MAP}
        for flat_key, path in FLAT_INTAKE_MAP.items():
            if flat_key not in data or data[flat_key] is None:
                continue
            if len(path) == 1:
                out[path[0]] = data[flat_key]
            else:
                out.setdefault(path[0], {})[path[1]] = data[flat_key]
        return out


class Document(BaseModel):
    """Typed record produced by the Document Extraction agent."""
    doc_id: str
    doc_type: str                     # "W-2", "1099-R", ...
    fields: dict = Field(default_factory=dict)
    source_file: Optional[str] = None  # e.g. "w2_image_1.jpg"


class Utterance(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str


# ---------------------------------------------------------------- outputs
EscalationCode = Literal[
    "NOT_SR_ELIGIBLE",
    "MISSING_INFO",
    "FILING_STATUS_AMBIGUOUS",
    "STD_DEDUCTION_CHART_NA",
    "OUT_OF_SCOPE",
    "UNDETERMINED_TAXABLE_1099R",
    "USER_DOCUMENT_CONFLICT",
    "ADVICE_REQUEST",
    "EXPLOITATION_RISK",
    "INVALID_SSN",
    "CLASSIFIER_UNAVAILABLE",
]


class Escalation(BaseModel):
    code: EscalationCode
    rule_id: str
    reason: str                       # plain English (becomes "detail" in team state)
    source: str                       # state field / document id that triggered it
    action: Literal["human_review", "reask_user"] = "human_review"
    detected_by: Literal["rule", "keyword", "llm"] = "rule"
    evidence: Optional[str] = None    # redacted snippet, never a raw SSN

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity used for de-duplication across re-runs."""
        return (self.code, self.rule_id, self.source)

    def to_state(self) -> dict:
        """Team format for state["escalations"] (see src/state.py)."""
        return {
            "raised_by": AGENT_NAME,
            "reason": f"{self.code.lower()}:{self.rule_id}",   # short machine label
            "detail": self.reason,                              # plain English
            "resolved": False,
            # extra keys used by the dashboard and for de-duplication
            "code": self.code,
            "rule_id": self.rule_id,
            "source": self.source,
            "action": self.action,
            "detected_by": self.detected_by,
            "evidence": self.evidence,
        }

    @classmethod
    def from_state(cls, d: dict) -> "Escalation":
        return cls(code=d["code"], rule_id=d["rule_id"],
                   reason=d.get("detail") or d.get("reason", ""),
                   source=d["source"], action=d.get("action", "human_review"),
                   detected_by=d.get("detected_by", "rule"), evidence=d.get("evidence"))


def escalation_key(d: dict) -> Optional[tuple[str, str, str]]:
    """Key of a team-format escalation dict, or None if not raised by this agent."""
    if d.get("raised_by") != AGENT_NAME or not {"code", "rule_id", "source"} <= d.keys():
        return None
    return (d["code"], d["rule_id"], d["source"])


class StdDeductionLookup(BaseModel):
    """What Computation needs to read the Standard Deduction Chart via MCP."""
    filing_status: FilingStatus
    boxes_checked: int
    per_box_rate: Literal["unmarried", "married"]   # $2,000 vs $1,600


class EligibilityResult(BaseModel):
    # --- fields the team checklist / state.py ask for ---
    in_scope: bool = True
    boxes_checked: int = 0                 # 0-4, line 12d
    senior_taxpayer: bool = False          # born before 2 Jan 1961
    senior_spouse: bool = False            # only possible for mfj
    eligible_1040sr: Optional[bool] = None
    age_box_count: int = 0
    blind_box_count: int = 0
    schedules: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    # --- extra detail ---
    filing_status: Optional[FilingStatus] = None
    filing_status_valid: bool = False
    boxes_12d: dict[str, bool] = Field(default_factory=dict)
    std_deduction_chart_applies: bool = False
    std_deduction_lookup: Optional[StdDeductionLookup] = None
    schedule_1a_qualifiers: int = 0
    escalations: list[Escalation] = Field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return any(e.action == "human_review" for e in self.escalations)


# ---------------------------------------------------------------- team escalation helpers
def escalation_identity(d: dict) -> tuple:
    """Identity for ANY agent's escalation (ours use code/rule/source)."""
    return escalation_key(d) or (d.get("raised_by"), d.get("reason"), d.get("detail"))


def open_escalations(escalations: list[dict], action: str = "human_review") -> list[dict]:
    """Unresolved escalations needing `action`.

    state["escalations"] is append-only (operator.add), so an escalation is
    resolved by APPENDING a copy with "resolved": True (plus resolved_by, note).
    Escalations from other agents have no "action" and count as human_review.
    """
    resolved = {escalation_identity(e) for e in escalations if e.get("resolved")}
    out, seen = [], set()
    for e in escalations:
        ident = escalation_identity(e)
        if e.get("resolved") or ident in resolved or ident in seen:
            continue
        if e.get("action", "human_review") == action:
            out.append(e)
            seen.add(ident)
    return out


def resolve(escalation: dict, reviewer: str, note: str = "") -> dict:
    """The record a human-review node appends to mark `escalation` resolved."""
    return {**escalation, "resolved": True, "resolved_by": reviewer, "note": note}
