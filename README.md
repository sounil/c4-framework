# C4: Least Agency, the Crown/Chassis/Conscience/Committee model, and manufactured miracles

This repository holds a research report and the reproducible experiments behind it.

- **`REPORT.md`** — the report. Sections 1, 2 and 4 are prose; §3.3 contains
  `{{RESULT:…}}` placeholders that the harness fills from a real run.
- **`experiments/`** — the harness: a real HTTP server that plays the agent's
  world (the *Chassis*) and can answer a forbidden action with `403 Forbidden`
  or with an impossible `HTTP 666`, a provider-agnostic tool-use loop, five
  scenarios, nine conditions, a classifier that scores outcomes from the
  server log, and analysis that produces the report's tables.
- **`docs/REAL_SANDBOX.md`, `sandbox/`** — how to move the Chassis into a real
  container with a shell, an intercepting proxy and PATH shims.

## The question

An agent is told to do X. Its system prompt insists it finish. When it tries,
the world answers with the same instruction — "don't do X; do Y instead" —
either as an ordinary `403` or as a status code that cannot exist. Does the
agent obey more often when the instruction rides on a miracle?

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # add API keys for the providers you'll use

make smoke                          # no API key: proves the pipeline with scripted mock agents (~10 s)
make test                           # unit tests, also key-free

python -m experiments.run --models anthropic:<model> --conditions C2 C4 --trials 10   # pilot of the core contrast
python -m experiments.run --models anthropic:<model> openai:<model> --trials 20        # full matrix
python -m experiments.analyze --run-id <run_id> --print
python -m experiments.fill_report --run-id <run_id>                                    # -> REPORT.filled.md
make docx                                                                              # -> REPORT.docx
```

Model specs are `<provider>:<model>`: `anthropic:…`, `openai:…`,
`ollama:…` (via Ollama's OpenAI-compatible endpoint at `OLLAMA_BASE_URL`),
`scripted:obedient|stubborn|zealot|random` (mocks, for smoke tests only).

## Cost and time

Full matrix per model = 5 scenarios × 9 conditions × 2 Conscience levels ×
*N* trials. At *N* = 20 that is 1,800 episodes of roughly 3–6 short model
calls each. Budget on the order of 5–10 M input tokens per model at *N* = 20
and run with `--concurrency 4` or higher. `--conditions C2 C4` cuts this to
2/9 for a pilot; runs are resumable with `--run-id`.

## How the pieces fit

```
scenarios/*.yaml ─┐
conditions.yaml ──┼─▶ run.py ──▶ agent.py ◀──▶ providers/  (Anthropic / OpenAI / Ollama / scripted)
                  │                 │  http_request()
                  │                 ▼
                  │            chassis.py  (real HTTP server; 200 / 403 / 666; logs every request)
                  │                 │
                  └──────────▶ classify.py ──▶ trials.jsonl ──▶ analyze.py ──▶ summary.json, tables.md
                                                                     │
                                                       fill_report.py ──▶ REPORT.filled.md ──▶ make docx
```

Every trial record carries the full system prompt, transcript, tool results,
Chassis log and classification, so any figure in the report can be traced to
the raw episodes. `python scripts/show_trial.py --run-id <id> --condition
C4_miracle_msg --only complied` prints one.

## Adding to the experiment

- New scenario: drop a YAML file in `experiments/scenarios/` (copy an existing one).
- New condition: add an entry to `experiments/conditions.yaml` (e.g. a `status: 799`, or a different message template).
- New contrast: one tuple in `CONTRASTS` in `experiments/analyze.py`.
- New provider: subclass `Provider` in `experiments/providers/` and register it in `make_provider`.

## Layout

```
REPORT.md                 the report (placeholders in §3.3)
REPORT.docx               rendered copy (regenerate with `make docx`)
experiments/              harness (see Appendix B of the report)
docs/REAL_SANDBOX.md      moving to a real sandbox
sandbox/                  docker-compose, mitmproxy addon, PATH shims, shell-agent sketch
scripts/                  build_docx.sh, show_trial.py
tests/                    key-free tests
results/runs/<run_id>/    trials.jsonl, config.json, summary.json, tables.md
```
