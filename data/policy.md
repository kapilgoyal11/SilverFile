# SilverFile Reference Corpus — Eligibility & Rules

Every decision made by the Eligibility & Rules agent cites one of the rule IDs
below. Rule IDs are machine-checked: `src/agents/policy.py` parses this file and
the test suite fails if the agent ever cites an ID that is not defined here.

Source of truth: the Kushim "SilverFile" assignment brief (§2, §3.2, §3.3, §4.4).
Where a rule goes beyond the brief, the extra source is named on the rule.
Tax year: 2025.

---

## R1 — Form 1040-SR eligibility

- **R1.1** The taxpayer qualifies for Form 1040-SR if born before 2 January 1961.
  (A person born on 1 January 1961 qualifies.)
- **R1.2** On a married-filing-jointly return, only one spouse needs to meet R1.1.
- **R1.3** There is no income restriction on using Form 1040-SR.
- **R1.4** If neither the taxpayer nor (on MFJ) the spouse meets R1.1, the return
  is not a 1040-SR return and the case is escalated to a human.

## R2 — Filing status validation

- **R2.1** The five filing statuses are: Single, Married Filing Jointly (MFJ),
  Married Filing Separately (MFS), Head of Household (HOH),
  Qualifying Surviving Spouse (QSS).
- **R2.2** MFJ and MFS require information about a spouse.
- **R2.3** A spouse who died during the tax year (2025) makes filing status
  ambiguous (MFJ vs QSS vs Single). Always escalate; never choose for the user.
- **R2.4** HOH requires a qualifying person and is not available while a spouse
  is reported for a joint/separate return. Inconsistency escalates.
- **R2.5** QSS requires a spouse who died in 2023 or 2024 and a qualifying
  dependent child (IRS Publication 501). Anything else escalates.
- **R2.6** A Single filer who also reports a living spouse is a contradiction
  and escalates.

## R3 — Line 12d boxes (age / blindness)

- **R3.1** One box each: taxpayer 65 or older, taxpayer blind, spouse 65 or
  older, spouse blind. Maximum 4.
- **R3.2** "65 or older" for tax year 2025 means born before 2 January 1961.
- **R3.3** Spouse boxes are counted on MFJ returns. For any other filing status
  spouse boxes are not counted by this system (MFS spouse-box cases escalate
  under R2 if they arise).

## R4 — Standard Deduction Chart applicability

- **R4.0** The Standard Deduction Chart printed on Form 1040-SR applies unless
  one of R4.1–R4.3 is true. Base amounts: $15,750 (Single, MFS), $31,500
  (MFJ, QSS), $23,625 (HOH). Each checked 12d box adds $2,000 if unmarried and
  not a surviving spouse (Single, HOH), otherwise $1,600 (MFJ, MFS, QSS).
  The *amount* is looked up by the Computation agent via an MCP tool; this
  agent only states which row/rate applies.
- **R4.1** The taxpayer can be claimed as a dependent by someone else → escalate.
- **R4.2** Filing MFS and the spouse itemizes deductions → escalate.
- **R4.3** The taxpayer was a dual-status alien → escalate.

## R5 — Schedules

- **R5.1** Schedule 1-A (enhanced senior deduction) applies when at least one
  qualifying person is 65 or older (taxpayer; and spouse on MFJ). This agent
  reports the number of qualifying people (0, 1 or 2). The $6,000 amount and
  the 6% phase-out above $75,000 / $150,000 MAGI are computed by the
  Computation agent, not here.
- **R5.2** The enhanced senior deduction is *in addition to* the additional
  standard deduction for age/blindness (R4.0). They are never merged.
- **R5.3** Schedules 1, 2 and 3 apply when the user reports values for them;
  the user enters those values directly.

## R6 — Scope (in-scope documents and out-of-scope items)

- **R6.1** In-scope documents: W-2, SSA-1099, 1099-R, 1099-INT, 1099-DIV.
  Any other document type escalates.
- **R6.2** Itemized deductions (Schedule A) → escalate.
- **R6.3** Self-employment or business income (Schedule C), including 1099-NEC
  nonemployee compensation → escalate.
- **R6.4** Rental income (Schedule E) → escalate.
- **R6.5** Capital gains requiring Schedule D (e.g. 1099-B, sold stock) → escalate.
- **R6.6** Digital assets (crypto, 1099-DA) → escalate.
- **R6.7** Foreign income or foreign accounts → escalate.
- **R6.8** Amended or prior-year returns; state returns → escalate.
- **R6.9** Any 1099-R with box 2b "taxable amount not determined" checked →
  escalate, regardless of any other information (Simplified Method / General
  Rule are out of scope).
- **R6.10** Any 1099-DIV with qualified dividends (box 1b) above $0 → escalate.
  Qualified dividends are taxed with the Qualified Dividends and Capital Gain
  Tax Worksheet, which is not implemented in the team's tax_math tools, so the
  Computation agent cannot compute line 16. (Source: team scope decision,
  SilverFile team checklist; IRS Form 1040 instructions, line 16.)

## R7 — Conflicts between the user and documents

- **R7.1** An unresolved conflict between a user statement and a document
  (e.g. the user says they had no wages but a W-2 was supplied) → escalate.
  The system never decides which one is right.

## R8 — Tax advice

- **R8.1** Requests for tax advice, planning or optimisation (e.g. Roth
  conversions, "would we pay less filing separately?", "can I deduct my
  medications?") are out of scope. The system explains its scope and notes the
  question for the volunteer preparer → escalate.
- **R8.2** Transcription and arithmetic are not advice.

## R9 — Financial exploitation and third-party pressure

- **R9.1** A refund routed to an account the taxpayer cannot describe → escalate.
- **R9.2** A third party answering on the taxpayer's behalf without a stated
  authority (e.g. power of attorney) → escalate.
- **R9.3** Any other indication of pressure or exploitation in the conversation
  → escalate. The system notices and stops; it does not adjudicate.

## R10 — Identity data guardrails

- **R10.1** Only test SSNs in the 900–999 series are accepted
  (format 9XX-XX-XXXX). Any other SSN is rejected and the user is asked again.
- **R10.2** SSNs are redacted (last four digits only) in logs, traces,
  escalation evidence and the dashboard.
- **R10.3** An SSN volunteered where it was not requested is not stored; it is
  redacted from the transcript and the user is told it was not needed.

## R11 — Release gate (before a draft reaches a human)

- **R11.1** Every monetary value on the draft has a provenance record.
- **R11.2** No escalation is unresolved.
- **R11.3** No non-test SSN appears anywhere in the draft.
- **R11.4** No user-facing system message contains advice language.
- **R11.5** Nothing is ever filed; the output is a draft for human review.
