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

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

FilingStatus = Literal["single", "mfj", "mfs", "hoh", "qss"]

AGENT_NAME = "eligibility"   # value of "raised_by" on our escalations


# ---------------------------------------------------------------- inputs
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")


def parse_date(v: Any) -> Optional[date]:
    """Accept a date, an ISO string or a few common formats. Anything else
    becomes None (treated as 'not known'), never a crash."""
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                continue
    return None


class Person(BaseModel):
    dob: Optional[date] = None
    blind: bool = False
    ssn: Optional[str] = None          # only if genuinely needed

    @field_validator("dob", mode="before")
    @classmethod
    def _dob(cls, v):
        return parse_date(v)

    @field_validator("blind", mode="before")
    @classmethod
    def _blind(cls, v):
        return bool(v)                 # None (unsure) -> False; unsure is flagged separately


class Spouse(Person):
    date_of_death: Optional[date] = None
    itemizes: bool = False             # relevant for MFS (R4.2)

    @field_validator("date_of_death", mode="before")
    @classmethod
    def _dod(cls, v):
        return parse_date(v)

    @field_validator("itemizes", mode="before")
    @classmethod
    def _itemizes(cls, v):
        return bool(v)


CHILD_RELATIONSHIPS = {"son", "daughter", "child", "stepson", "stepdaughter", "stepchild",
                       "foster child", "adopted child", "grandson", "granddaughter", "grandchild"}


class Dependent(BaseModel):
    name: str
    relationship: Optional[str] = None
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
# Source of truth for Intake's keys: memory.intake_handoff() / docs/INTAKE_INTERFACE.md
FLAT_INTAKE_MAP: dict[str, tuple[str, ...]] = {
    "birth_date": ("taxpayer", "dob"),
    "claimable_as_dependent": ("can_be_claimed_as_dependent",),
    "spouse_death_year": ("spouse_death_year",),
    "spouse_died_recent": ("spouse_died_recent",),
    "married_end_of_year": ("married_end_of_year",),
    "wants_itemize": ("wants_itemize",),
    "box2b_checked": ("box2b_reported",),
    "other_income": ("other_income",),
    "other_income_detail": ("other_income_detail",),
    "unsure": ("unsure_slots",),
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
    # --- extra facts Intake (Karan) collects ---
    spouse_death_year: Optional[int] = None
    spouse_died_recent: Optional[bool] = None
    married_end_of_year: Optional[bool] = None
    wants_itemize: Optional[bool] = None
    box2b_reported: Optional[bool] = None       # user said a 1099-R has box 2b checked
    other_income: Optional[bool] = None         # income beyond the 5 in-scope forms
    other_income_detail: Optional[str] = None
    unsure_slots: list[str] = Field(default_factory=list)   # questions answered "I'm not sure"

    @field_validator("can_be_claimed_as_dependent", "dual_status_alien", "has_qualifying_person",
                     mode="before")
    @classmethod
    def _none_is_false(cls, v):
        return bool(v)

    @model_validator(mode="before")
    @classmethod
    def _from_team_format(cls, data: Any) -> Any:
        """Convert the team's flat state["intake"] into the nested form.
        Unknown keys (e.g. estimated_payments) are ignored."""
        if not isinstance(data, dict) or not any(k in data for k in FLAT_INTAKE_MAP):
            return data
        special = {"full_year_resident", "document_inventory", "dependents"}
        out = {k: v for k, v in data.items() if k not in FLAT_INTAKE_MAP and k not in special}
        for flat_key, path in FLAT_INTAKE_MAP.items():
            if flat_key not in data or data[flat_key] is None:
                continue
            if len(path) == 1:
                out[path[0]] = data[flat_key]
            else:
                out.setdefault(path[0], {})[path[1]] = data[flat_key]

        # Not a full-year US resident -> treat as possible dual-status alien (R4.3)
        if data.get("full_year_resident") is False:
            out["dual_status_alien"] = True
        # Intake sends {"W-2": 1, "1099-R": 0, ...}; a None count means "has some, number unsure"
        inv = data.get("document_inventory")
        if isinstance(inv, dict):
            out["documents_reported"] = [k for k, n in inv.items() if n is None or n]
        elif isinstance(inv, list):
            out["documents_reported"] = inv
        # Dependents: infer qualifying_child from the relationship when not given
        deps = []
        for d in data.get("dependents") or []:
            d = dict(d)
            if "qualifying_child" not in d:
                d["qualifying_child"] = (d.get("relationship") or "").strip().lower() in CHILD_RELATIONSHIPS
            d.setdefault("name", "dependent")
            deps.append(d)
        out["dependents"] = deps
        if "has_qualifying_person" not in data:
            out["has_qualifying_person"] = bool(deps)
        # Married statuses always have a spouse record (details may be missing/unsure)
        if data.get("filing_status") in ("mfj", "mfs"):
            out.setdefault("spouse", {})
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
            "id": f"{AGENT_NAME}:{self.code}:{self.rule_id}:{self.source}",  # merge_escalations replaces by id
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

    state["escalations"] uses merge_escalations: an entry whose "id" matches an
    existing one REPLACES it. So resolving = sending a copy with the same id and
    "resolved": True (see resolve()). Copies without ids are also handled.
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
    return {**escalation, "resolved": True, "resolved_by": reviewer, "note": note,
            "resolution": note or "resolved"}
