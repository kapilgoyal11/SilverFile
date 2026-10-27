"""Data contract between the Review / Audit agent and the rest of SilverFile.

The TypedDicts describe the slice of the shared LangGraph state that this agent
READS. The Finding / AuditRecord types describe what it WRITES. Full examples
are in docs/review_audit_contract.md.
"""

from __future__ import annotations

import operator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Annotated, Any, NotRequired, TypedDict


class Severity(StrEnum):
    ERROR = "error"      # return is wrong -> must be sent back (or escalated)
    WARNING = "warning"  # suspicious; shown to the human reviewer, does not block


class Route(StrEnum):
    """Who has to act on a finding. The audit agent never fixes anything itself."""

    INTAKE = "intake"
    EXTRACTION = "document_extraction"
    ELIGIBILITY = "eligibility_rules"
    COMPUTATION = "computation"
    HUMAN = "human"  # LangGraph interrupt / HITL dashboard


class Verdict(StrEnum):
    APPROVE = "approve"    # no errors; draft can be generated
    REVISE = "revise"      # send back to the agents in send_back_to
    ESCALATE = "escalate"  # a human must decide (HITL interrupt)


# --- What the audit agent reads ---------------------------------------------

class Provenance(TypedDict, total=False):
    kind: str            # "document" | "computed" | "user_input" | "rule"
    doc_id: str          # kind=document
    field: str           # kind=document, e.g. "box1"
    tool: str            # kind=computed: MCP tool name, e.g. "tax_math.add"
    inputs: list[str]    # kind=computed: refs like "line:1a" or "doc:w2-1.box1"
    formula: str         # kind=computed, human-readable
    entered_by: str      # kind=user_input
    entered_at: str      # kind=user_input, ISO-8601 date/time
    source: str          # kind=rule, e.g. "Standard Deduction Chart"
    citation: str        # kind=rule, e.g. "policy.md#standard-deduction"


class LineEntry(TypedDict):
    value: float | int | str
    provenance: NotRequired[Provenance]


class Person(TypedDict, total=False):
    first_name: str
    last_name: str
    ssn: str
    dob: str  # YYYY-MM-DD
    blind: bool
    can_be_claimed_as_dependent: bool
    dual_status_alien: bool
    itemizes_separately: bool  # spouse only
    relationship: str          # dependents only


class TaxDocument(TypedDict, total=False):
    doc_id: str                 # e.g. "w2-1"
    type: str                   # "W-2" | "1099-INT" | "1099-DIV" | "1099-R" | "SSA-1099" | ...
    fields: dict[str, Any]      # e.g. {"box1": 52000.00, "employee_ssn": "901-23-4567"}
    confidence: dict[str, float]  # per-field extraction confidence 0..1
    extraction_attempts: int


class AuditInputState(TypedDict, total=False):
    case_id: str
    filing_status: str                     # single | mfj | mfs | hoh | qss
    taxpayer: Person
    spouse: Person | None
    dependents: list[Person]
    line_12d: dict[str, bool]              # taxpayer_65, taxpayer_blind, spouse_65, spouse_blind
    documents: list[TaxDocument]
    return_lines: dict[str, LineEntry]     # keyed by line id: "1a", "1z", "11a", ...
    intake_flags: dict[str, Any]           # see contract doc
    mfs_lived_apart_all_year: bool
    hitl_resolutions: list[dict[str, Any]]  # human decisions from the dashboard
    handoff_log: list[str]                 # agent names in the order they ran


class AuditStateUpdate(TypedDict, total=False):
    """Keys this node writes. Add these to the team's state.py."""

    audit: dict[str, Any]
    audit_history: Annotated[list[dict[str, Any]], operator.add]
    escalations: Annotated[list[dict[str, Any]], operator.add]
    review_round: int
    needs_human_review: bool


# --- What the audit agent writes --------------------------------------------

@dataclass(slots=True)
class Finding:
    code: str
    severity: Severity
    route_to: Route
    message: str                    # plain language, no PII
    line: str | None = None
    expected: str | None = None
    actual: str | None = None
    escalation_condition: str | None = None  # spec 4.4 condition number or "security"
    subject: str | None = None      # extra key part for findings not tied to a line

    @property
    def key(self) -> str:
        """Stable id used for HITL resolutions and loop detection."""
        return f"{self.code}:{self.line or self.subject or '-'}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = str(self.severity)
        d["route_to"] = str(self.route_to)
        d["key"] = self.key
        return d


@dataclass(slots=True)
class AuditRecord:
    case_id: str
    round: int
    verdict: Verdict
    send_back_to: list[str]
    findings: list[dict[str, Any]]
    resolved_by_human: list[dict[str, Any]]
    line_status: dict[str, str]
    checks_run: int
    return_fingerprint: str
    audited_at: str
    auditor_version: str
    loop_guard: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = str(self.verdict)
        return d
