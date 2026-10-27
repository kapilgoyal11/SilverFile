# Intake agent and agent memory: design notes

*Karan Shah — focus area: Agent Memory. Agent owned: Intake.*
*Draft for my section of the report. Edit freely.*

## 1. What the Intake agent does

Intake is the only agent that talks to the taxpayer. It asks one plain-language question at a time and collects:

- who is answering
- name and date of birth
- blindness
- whether someone else can claim them as a dependent
- residency
- marital situation
- filing status
- spouse details
- dependents
- which tax forms they have
- estimated payments
- Schedule 1 adjustments

It then reads everything back and asks the taxpayer to confirm.

Two rules from the brief shaped the design:

- **No arithmetic.** The only numbers Intake touches are ones the user types. They are normalised ("$1,200" → `"1200.00"`) but never added, compared or computed.
- **No tax interpretation.** Intake records facts; it doesn't judge them. For example, it records "someone else can claim me as a dependent: yes", and Eligibility & Rules decides that this needs a human. Intake raises flags only for things it hears directly in the conversation: advice requests, "I'm not sure", and signs that someone else is directing the return.

**Question wording comes from a script, not from an LLM.** That makes every question predictable, testable (a test checks every prompt has exactly one question mark), and free of accidental advice. An LLM can optionally help *understand* a free-text reply when the rules fail. Its suggestion is re-checked by the same validator, so it can't invent a value.

## 2. Accessibility and dignity (§4.3)

All six areas are covered. Each is tested in `tests/test_intake_flow.py` or `tests/test_memory.py`.

| Area | What we did | Why |
|---|---|---|
| **One question at a time** | Every prompt carries exactly one question. Multiple-choice questions are numbered, so "2" is a full answer. | Compound questions overload tired or anxious users and are hard to follow with a screen reader. Numbers are easy to say or type. |
| **Plain language** | Jargon is avoided. Terms that can't be avoided ("legally blind", "dependent", "1099-R", "estimated payments") get a one-sentence explanation the first time only. "Modified adjusted gross income" never appears. Asking "what does that mean?" repeats the explanation. | Explaining once respects the user. Explaining every time is patronising and slow with a screen reader. |
| **Repetition and drift** | "Go back", "change my birthday", or a correction in one sentence ("Actually I was born March 5, 1950") are all understood. Old answers move to a history list and are never deleted. Off-topic replies get a gentle hint. After three misses the question is flagged for a volunteer and the conversation moves on. A "no" at the confirmation step asks what to change. | Losing an answer is a failure per the brief. Getting stuck on one question is the fastest way to lose an elderly user. |
| **No unnecessary PII** | See section 3. | |
| **Resumability** | Every step is saved to SQLite under the session id. Coming back days later shows a short "welcome back" summary, then the exact question they were on. Saying "pause" or "I'm tired" is understood. | The brief's Tuesday-to-Friday case. The summary is short on purpose, for a tired reader. |
| **Honest uncertainty** | "I'm not sure" is always accepted. It records the answer as *unsure* (never as a guess), raises a `user_unsure` flag, and moves on. Approximate amounts ("about 1200") are saved and flagged for a volunteer to check. | "I'm not sure, let me flag this for a volunteer" is a correct answer from the system. Pretending to know is not. |

**Sensitive moments.** The question about a spouse who died is preceded by "I'm sorry to have to ask this." Advice questions are acknowledged as fair ("That's a fair question, but it's tax advice…") rather than refused flatly.

## 3. Personal information choices

| Decision | Reason |
|---|---|
| We **don't ask for the taxpayer's or spouse's SSN** at all. | SilverFile never files. The volunteer preparer adds those at filing time. Not collecting is the safest way to protect a number. |
| We **do** ask for each dependent's SSN. | The form's dependents section needs it (§3.1). |
| Only **900–999 test SSNs** are accepted. A real-looking number is rejected, not saved, and the user is told not to type real numbers. | Requirement in §3.2, and it stops accidental real-SSN entry. |
| A **volunteered SSN** (typed when we didn't ask) is removed before anything is saved, and the user is told: "I noticed what looks like a Social Security number. I don't need it for this question, so I didn't save it." The rest of their answer is still used. | Silently discarding it would leave them unsure whether it was stored. Telling them builds trust and teaches them not to share it. |
| SSNs are **redacted from the transcript and logs**, **masked** in summaries (`***-**-4567`), and **never sent to an LLM**. | The stored SSN value is only needed on the form. Nowhere else needs to see it. |

## 4. Escalations Intake raises

| Flag | Blocking? | Trigger |
|---|---|---|
| `possible_exploitation` | Yes: the conversation stops until a volunteer resolves it | A helper is answering without legal authority, or the user mentions pressure or a refund account they don't recognise ("my nephew told me to put the refund in his account") |
| `tax_advice_request` | No | "Should I…", "Would I pay less…", "Can I deduct…". The question is noted for the volunteer and the current question is asked again. |
| `user_unsure` | No | "I'm not sure", an approximate amount |
| `repeated_parse_failure` | No | Three replies Intake couldn't understand |

**On elder financial abuse (§4.4, condition 6).** The very first question is "Are you the person this tax return is for?" If not, Intake asks the helper's relationship, then whether they have a power of attorney or other legal authority. "No" or "not sure" stops the session for a human. If a volunteer clears it, Intake continues and records every later answer as `entered_by: "helper (her son)"`, so the provenance shows who said what.

## 5. Memory design

Memory works at three levels:

1. **Shared state** (`src/state.py`, the team schema). Intake adds its own fields and reducers:
   - The **answer ledger** (`answers`, reducer `merge_answers`) merges slot by slot. A re-answer pushes the old version into `history`, so nothing is ever overwritten.
   - **Escalations** (`merge_escalations`) still append like before. An entry with an `id` can also be replaced in place, which is how a human marks it resolved.
   - When intake finishes, the plain answers go into `intake` in the team's shared format: whole-dollar ints and `mfj`-style filing statuses.
2. **Long-term memory** (SQLite checkpointer). Every step is saved under the session's thread id. A brand-new process on a later day resumes exactly where the user left off (`test_resume_on_a_later_day_from_sqlite`).
3. **Working memory for model calls** (the context strategy). See section 6.

**Answers that stop applying.** If the user changes "married" to "not married", their spouse's details stay in the ledger but drop out of `active_answers`, so no other agent uses them. Their old "married filing jointly" is detected as no longer fitting, and Intake asks the filing-status question again.

**Provenance.** Every user answer records the value, the user's (redacted) words, who entered it, when, the turn number, a status (answered, unsure or approximate), and its earlier versions. Computation cites this as "direct user input" for amounts the user typed (§4.2).

## 6. Context-window strategy (§4.5)

The strategy is **selective recall**. Each model call gets:

- a fixed system prompt
- a **bounded** summary of key decisions (who is answering, filing status, number of dependents, forms, count of answers, open flags)
- the **last 4 turns**, with long messages clipped
- the current question

Without the strategy, the whole transcript is sent every time. The summary is generated by code from the answer ledger, not by an LLM, so it can't drop or invent a fact.

Measured over a 54-turn intake session (`docs/metrics.md`, `docs/context_tokens.png`), in input tokens:

| | Off | On |
|---|---:|---:|
| Mean per turn | 1,007 | 244 |
| Last turn | 2,602 | 560 |
| Total | 54,392 | 13,160 |

Off grows in a straight line. On stays roughly flat, about 75% fewer tokens overall. Reproduce with `python -m scripts.measure_context --both`. Counts are tiktoken estimates on the exact messages sent; add `--live` for provider-reported counts.

## 7. Limitations and failure modes

- **Rule-based understanding.** The reply parser covers common phrasings. Unusual wording ("my late husband" as an answer to "are you married?") may need the optional LLM fallback, or ends up flagged after three tries. That is safe, but slower for the user.
- **Keyword-based "go back".** It jumps to the best-matching earlier question. With several dependents, "change my dependent's name" goes to the first one.
- **Exploitation detection** is a pattern list. It catches the common phrasings in the brief but is not a classifier. It is designed to over-flag rather than miss.
- **Confirmation readback is long** for big households. It's needed so the user can check everything, but it is the one place the "short messages" principle bends.
- **Single language** (English) only.

## 8. How it was tested

There are 55 automated tests for Intake and memory (`pytest -q` runs these plus the rest of the team's). They cover:

- a clean run
- one question per prompt
- jargon explained once
- going back, corrections, and changing a branch
- the confirmation and change flow
- "I'm not sure"
- advice requests
- real-SSN rejection and volunteered-SSN removal
- a helper without authority, and pressure mid-conversation, both stopping for a human
- drift tolerance
- help and repeat
- a spouse who died this year
- approximate amounts
- multiple dependents
- reducer merges
- resume from SQLite on a "later day"
- pause
- context-strategy token growth
- no SSN ever reaching a model
- Intake's output being accepted by the Computation agent as-is
- an amount the user was unsure about blocking Computation instead of being treated as 0
