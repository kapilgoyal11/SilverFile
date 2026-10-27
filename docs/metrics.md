# Context-window strategy: metrics

Agent measured: **Intake** (the model-calling agent with the longest conversation).
Strategy: **selective recall** = fixed system prompt + a code-generated summary of decisions so far + only the last 4 turns (long ones clipped) + the current question. Disabled = the whole transcript is sent every turn.

Session: one scripted intake of 54 turns (`scripts/measure_context.py`, `LONG_SESSION`): four dependents, jargon questions, a tax-advice question, a correction, off-topic drift, and a change at the confirmation step.

Token counts: `tiktoken` (cl100k encoding on the exact messages the agent sends; rerun with `--live` for provider-reported counts).

Reproduce:
```
python -m scripts.measure_context --both
```

## Summary

| | Strategy OFF | Strategy ON |
|---|---:|---:|
| First turn | 107 | 143 |
| Last turn | 2602 | 560 |
| Max | 2602 | 599 |
| Mean | 1007 | 244 |
| Total over session | 54392 | 13160 |
| Growth per turn (last minus first, over turns) | 47.1 | 7.9 |

With the strategy off, every turn re-sends the whole conversation, so cost grows in a straight line. With it on, the window of recent turns is fixed and the decisions summary is bounded, so the cost per turn stays roughly constant however long the session runs. Nothing is lost: the full answers live in the state's answer ledger, not in the prompt.

The two small bumps near the end of the ON curve are the confirmation step: there the *current question* is the full list of answers read back to the taxpayer, which has to be sent in full.

## Per-turn input tokens

| Turn | Question | Strategy OFF | Strategy ON |
|---:|---|---:|---:|
| 1 | `is_taxpayer` | 107 | 143 |
| 2 | `taxpayer_name` | 132 | 168 |
| 3 | `taxpayer_dob` | 151 | 187 |
| 4 | `taxpayer_dob` | 192 | 210 |
| 5 | `taxpayer_blind` | 241 | 233 |
| 6 | `taxpayer_blind` | 257 | 231 |
| 7 | `claimable_as_dependent` | 307 | 240 |
| 8 | `full_year_resident` | 330 | 214 |
| 9 | `married_end_of_year` | 347 | 215 |
| 10 | `filing_status` | 453 | 270 |
| 11 | `filing_status` | 569 | 351 |
| 12 | `spouse_name` | 586 | 313 |
| 13 | `spouse_dob` | 606 | 244 |
| 14 | `spouse_blind` | 621 | 180 |
| 15 | `has_dependents` | 641 | 182 |
| 16 | `dependent_1_name` | 661 | 183 |
| 17 | `dependent_1_relationship` | 690 | 197 |
| 18 | `dependent_1_ssn` | 712 | 199 |
| 19 | `dependent_1_another` | 729 | 195 |
| 20 | `dependent_2_name` | 749 | 186 |
| 21 | `dependent_2_relationship` | 776 | 192 |
| 22 | `dependent_2_ssn` | 797 | 196 |
| 23 | `dependent_2_another` | 815 | 194 |
| 24 | `dependent_3_name` | 835 | 186 |
| 25 | `dependent_3_relationship` | 863 | 193 |
| 26 | `dependent_3_ssn` | 884 | 197 |
| 27 | `dependent_3_another` | 902 | 195 |
| 28 | `dependent_4_name` | 922 | 186 |
| 29 | `dependent_4_relationship` | 950 | 193 |
| 30 | `dependent_4_ssn` | 972 | 198 |
| 31 | `dependent_4_another` | 989 | 194 |
| 32 | `has_w2` | 1022 | 199 |
| 33 | `w2_count` | 1045 | 200 |
| 34 | `w2_count` | 1077 | 215 |
| 35 | `has_ssa1099` | 1132 | 237 |
| 36 | `has_ssa1099` | 1174 | 256 |
| 37 | `has_1099r` | 1216 | 268 |
| 38 | `has_1099r` | 1256 | 253 |
| 39 | `count_1099r` | 1272 | 230 |
| 40 | `box2b_checked` | 1307 | 222 |
| 41 | `has_1099int` | 1342 | 218 |
| 42 | `has_1099div` | 1377 | 239 |
| 43 | `other_income` | 1427 | 256 |
| 44 | `other_income` | 1514 | 309 |
| 45 | `wants_itemize` | 1571 | 323 |
| 46 | `made_estimated_payments` | 1619 | 320 |
| 47 | `estimated_payments_total` | 1645 | 267 |
| 48 | `has_adjustments` | 1721 | 291 |
| 49 | `has_other_schedules` | 1802 | 324 |
| 50 | `has_other_withholding` | 1842 | 335 |
| 51 | `confirm_all` | 2182 | 599 |
| 52 | `change_what` | 2221 | 297 |
| 53 | `taxpayer_dob` | 2240 | 277 |
| 54 | `confirm_all` | 2602 | 560 |
