# How to run and evaluate SilverFile

SilverFile is a LangGraph multi-agent system that prepares a **draft** Form
1040-SR (tax year 2025) and records where every dollar came from. It never files
a return and never gives tax advice. Kushim staff review held sessions in a web
console (the dashboard).

Everything below works **without an API key**. Without one, the system runs in
offline mode and does **not** read the W-2 images themselves. It uses the saved
answer key next to each image (for example `data/fixtures/w2_1.reading.json`),
which holds the values printed on that W-2. Reading a real W-2 image needs an
Anthropic API key, so the vision model can read it. See
[Using an API key](#using-an-api-key).

- [Option A: Docker (recommended)](#option-a-docker-recommended)
- [Option B: Python on your machine](#option-b-python-on-your-machine)
- [Evaluation walkthrough](#evaluation-walkthrough)
- [Where everything is](#where-everything-is)
- [Troubleshooting](#troubleshooting)

---

## Option A: Docker (recommended)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
running. Nothing else has to be installed.

```bash
git clone https://github.com/kapilgoyal11/SilverFile.git
cd SilverFile
docker compose up --build
```

The first build takes a few minutes. When the log says
`Uvicorn running on http://0.0.0.0:8000`, open **http://localhost:8000**. (The
container listens on all of its own interfaces, but Docker publishes the port only
on `127.0.0.1`, so the dashboard is reachable from this computer and not from
the network.)

The queue opens with the two evaluation cases already started:

| Session | What it shows |
|---|---|
| `eval-case-1` | A clean run: a single filer with a W-2 image and a 1099-INT. Status **Draft ready**. |
| `eval-case-2` | A HITL run: the spouse died during the year, so filing status needs a person. Status **Awaiting review**. |

Stop with **Ctrl+C**. Sessions are kept in a Docker volume, so they are still
there next time. To start again from an empty queue:

```bash
docker compose down -v
```

### Other commands in the same image

Run these from the `SilverFile` folder while the image is built:

```bash
docker compose run --rm silverfile python -m pytest -q                     # all tests
docker compose run --rm silverfile python run.py --session my-case        # live intake interview in the terminal
docker compose run --rm silverfile python -m scripts.measure_context --both   # context-window metrics
```

Sessions started with `run.py` here use the same database as the dashboard, so
they appear in the queue within a few seconds.

### Without Docker Compose

```bash
docker build -t silverfile .
docker run --rm -p 127.0.0.1:8000:8000 -v silverfile-data:/data silverfile
docker run --rm -it -v silverfile-data:/data silverfile python run.py --session my-case
docker run --rm silverfile python -m pytest -q
```

---

## Option B: Python on your machine

Needs **Python 3.11 or newer** (tested on 3.13). On macOS the built-in
`python3` is 3.9, so name the version explicitly, for example `python3.13`.

```bash
git clone https://github.com/kapilgoyal11/SilverFile.git
cd SilverFile
python3.13 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r dashboard/requirements.txt httpx
python -m pytest -q                    # every test should pass
SILVERFILE_DEMO=1 python -m dashboard  # open http://localhost:8000
```

`SILVERFILE_DEMO=1` starts the two evaluation cases when the database is empty.
Leave it off to start with an empty queue. On Windows PowerShell, run
`$env:SILVERFILE_DEMO="1"` first, then `python -m dashboard`.

The command-line runner works the same way:

```bash
python run.py --session demo-1 --state data/fixtures/eligibility/clean_single.json --ssn 900-12-3456 \
    --doc data/fixtures/w2_1.png --doc data/fixtures/int_1.json --offline
python run.py --session demo-2 --state data/fixtures/eligibility/hitl_spouse_died.json \
    --ssn 900-12-3456 --spouse-ssn 900-65-4321
python run.py --session demo-4         # full intake interview from scratch
python run.py --session demo-2 --resume
```

When a run pauses for a person, type `proceed <note>` or `stop <note>`.

---

## Evaluation walkthrough

Each step matches part D of the rubric.

**1. Clean run (eval case 1).** In the dashboard, open `eval-case-1`. It shows
**Draft ready** and an expected refund of $1,650. The **Draft return** tab is the
filled-in 1040-SR.

**2. Provenance.** Click any line on the return. The panel on the right shows
the formula, every input, and the document field or taxpayer answer it came
from. Inputs that are other lines are clickable, so you can follow line 34 back
through 33, 25d and 25a to box 2 of the W-2. **Documents** shows the extracted forms
(SSNs masked). **Audit** shows the Review & Audit verdict and checks run, and
**Activity** shows every hand-off the orchestrator made.

**3. W-2 read from an image.** Lines 1z and 25a come from the W-2 image
`data/fixtures/w2_1.png`. In offline mode the image is not read: the values come
from its saved answer key, `data/fixtures/w2_1.reading.json`. To have the vision
model read the image itself, set `ANTHROPIC_API_KEY` (see
[Using an API key](#using-an-api-key)) and start a session that includes
`w2_1.png`. The sidebar of the dashboard shows which mode is active.

**4. HITL event (eval case 2).** Open `eval-case-2`. The **Review** tab shows the
escalation: who raised it, the rule, and why. Enter a reviewer name and a note,
then click **Resolve all**. The session continues and ends at **Draft ready**.
**Stop session** ends it with no draft instead.

**5. Your own data.** Click **New session** and pick any prepared case. The ones
marked *Triggers review* pause for a person: advice request, possible
exploitation, 1099-R taxable amount unknown, qualified dividends, spouse died,
unsure of birth date. To enter made-up data from scratch, run the intake
interview: `python run.py --session my-case`, or the Docker command above. It
asks one question at a time. Only test SSNs from 900 to 999 are accepted.

**Bonus: HITL links in the dashboard.** Steps 1, 2 and 4 above. The queue lists
every form in progress and submitted, flags the ones waiting for a person, and
resolves them in the browser.

**Bonus: Docker.** Option A.

---

## Using an API key

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Never commit `.env`.

- Python: the key is read from `.env` automatically.
- Docker Compose: uncomment `env_file: .env` in `docker-compose.yml`.
- Plain Docker: add `--env-file .env` to `docker run`.

A real key starts with `sk-ant-`. With one present, W-2 images are read by the
vision model; the placeholder value in `.env.example` does not count, so the system
stays offline. To force offline mode with a key set, use `SILVERFILE_OFFLINE=1` for
the dashboard or `--offline` for `run.py`.

---

## Where everything is

| What | Where |
|---|---|
| Agents (one file each) | `src/agents/` |
| Orchestration graph and diagram | `src/graph.py`, `src/agents/orchestrator.py`, `docs/graph.png`, `docs/ORCHESTRATION.md` |
| Shared state | `src/state.py` |
| Deterministic tax tools and provenance | `src/tools/` |
| Memory, save and resume | `src/memory.py`, `docs/INTAKE_AND_MEMORY.md` |
| Context-window metrics | `docs/metrics.md`, `scripts/measure_context.py` |
| Dashboard | `dashboard/` (see `dashboard/README.md`) |
| Fixtures (W-2 images, forms, taxpayer cases) | `data/fixtures/` |
| Tests | `tests/` |
| AI-use disclosure | `docs/AI_DISCLOSURE.md` |
| Docker | `Dockerfile`, `docker-compose.yml` |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 8000 is already in use | Docker: change `"127.0.0.1:8000:8000"` to `"127.0.0.1:8001:8000"` in `docker-compose.yml` and open http://localhost:8001. Python: `PORT=8001 python -m dashboard`. |
| `docker: command not found`, or "Cannot connect to the Docker daemon" | Install Docker Desktop and start it, then try again. |
| `python3 --version` shows 3.9 on a Mac | Install a newer Python (`brew install python@3.13`) and create the venv with `python3.13 -m venv .venv`. |
| The queue is empty | Start sessions with **New session**, or delete `silverfile_sessions.sqlite` (Python) or run `docker compose down -v` (Docker) and start again with demo mode on. |
