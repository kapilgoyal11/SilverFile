# Evaluation case 1: clean run

A single filer aged 65 with one W-2 and one 1099-INT. Nothing needs a person, so the
run goes straight to a finished draft.

## Data

| Item | File |
|---|---|
| Taxpayer answers (finished intake) | `data/fixtures/eligibility/clean_single.json` |
| W-2 image | `data/fixtures/w2_1.png` (copy: `demo/w2_image_1.jpg`) |
| W-2 answer key, used in offline mode | `data/fixtures/w2_1.reading.json` |
| 1099-INT | `data/fixtures/int_1.json` |
| Test SSN | `900-12-3456` |

Filing status single, born 1961-01-01 (counts as 65 or older for 2025), not blind.

## Run it

**Dashboard (Docker):** `docker compose up --build`, open http://localhost:8000, and open
`eval-case-1`. Docker starts this case automatically on an empty database.

**Dashboard (Python):** `SILVERFILE_DEMO=1 python -m dashboard`, same steps.

**Command line:**

```
python run.py --session eval-case-1 --state data/fixtures/eligibility/clean_single.json \
    --ssn 900-12-3456 --doc data/fixtures/w2_1.png --doc data/fixtures/int_1.json --offline
```

Without an Anthropic API key (or with `--offline`) the W-2 image is not read; its values
come from the saved answer key. With a real key, drop `--offline` and Claude reads the image.

## Expected result

Status **Draft ready**, no human input, Review & Audit verdict **approve** (145 checks),
release gate **passed**.

| Line | Amount | Source |
|---|---:|---|
| 1z | 18,500 | W-2 `w2_1`, box 1 |
| 2b | 312 | 1099-INT `int_1`, box 1 |
| 9 / 11a | 18,812 | 1z + 2b |
| 12e | 17,750 | 15,750 single + 2,000 for one 12d box |
| 13b | 6,000 | Schedule 1-A senior deduction |
| 14 | 23,750 | 12e + 13b |
| 15 | 0 | max(0, 11b − 14) |
| 24 | 0 | no tax |
| 25a / 25d / 33 | 1,650 | W-2 `w2_1`, box 2 |
| **34** | **1,650** | refund: max(0, 33 − 24) |

## Filled 1040-SR

In the dashboard, open the **Draft return** tab and click **Download filled 1040-SR (PDF)**.
From the command line: `python -m dashboard.form_1040sr eval-case-1`, which writes
`traces/eval-case-1_1040sr.pdf`. Page 2 lists the source of every amount.
