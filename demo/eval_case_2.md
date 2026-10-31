# Evaluation case 2: human-in-the-loop event and its resolution

A married couple filing jointly for 2025, the year the spouse died. Whether joint filing is
right in the year of death is a judgement for a person, so Eligibility & Rules raises an
escalation (rule R2.3, filing status ambiguous) and the graph pauses at `human_review`
through LangGraph's `interrupt()`.

## Data

| Item | Value |
|---|---|
| Taxpayer answers (finished intake) | `data/fixtures/eligibility/hitl_spouse_died.json` |
| Filing status given | married filing jointly; spouse died in 2025 |
| Test SSNs | taxpayer `900-12-3456`, spouse `900-65-4321` |
| Documents | an SSA-1099 is included in the case data ($24,000 of benefits) |

## Trigger the HITL event

**Dashboard:** with Docker, `eval-case-2` is started automatically and shows
**Awaiting review**. Otherwise click **New session**, choose **Spouse died this year**,
enter both test SSNs and click **Start session**.

**Command line:**

```
python run.py --session eval-case-2 --state data/fixtures/eligibility/hitl_spouse_died.json \
    --ssn 900-12-3456 --spouse-ssn 900-65-4321
```

The run stops and prints: *A Kushim employee needs to review this session before it continues*,
followed by the escalation `filing_status_ambiguous:R2.3`.

## Resolve it

**Dashboard:** open the session, go to the **Review** tab, enter a reviewer name and a note
(for example "Volunteer confirmed joint filing for the year of death") and click
**Resolve all**. To end the session without a draft instead, click **Stop session**.

**Command line:** at the `Kushim staff:` prompt type `proceed volunteer confirmed joint filing`
(or `stop <note>`). To come back to a paused session later:
`python run.py --session eval-case-2 --resume`.

## Expected result

After **Resolve all**, the escalation is marked resolved with the reviewer's name and note,
Eligibility is checked again, and the session finishes at **Draft ready**. Line 12d has both
boxes checked (both spouses born before January 2, 1961), the standard deduction (12e) is
34,700, the senior deduction (13b) is 12,000, and the balance on lines 34 and 37 is 0.
**Stop session** instead ends the run with status *Stopped* and no draft.

The filled form: **Download filled 1040-SR (PDF)** on the Draft return tab, or
`python -m dashboard.form_1040sr eval-case-2`.
