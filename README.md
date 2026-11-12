# Building Plan Compliance Pre-Check

Multi-agent service that takes a building proposal, validates it, retrieves the bye-law provisions that bind it
(including limits stated separately from the tables), produces cited findings, verifies them against the retrieved
text, and routes to `clear-for-submission`, `revise-and-recheck` or `specialist-review`.
**It never certifies compliance.** A licensed architect or engineer decides.

## Run it (from a clean clone)

```bash
cp .env.example .env          # defaults point at the local stub LLM; no credentials needed
docker compose up --build     # first build downloads torch (CPU) and the embedding model
docker compose up --scale worker=3     # more agent workers
```

| Open | What you get |
|---|---|
| http://localhost:8000 | The app: type in the numbers, see which rules would be broken |
| http://localhost:3000 | Grafana dashboard (no login): speed, AI calls, cost, trust, protection |
| http://localhost:16686 | Jaeger: one check traced step by step (service `api`) |
| http://localhost:9090 | Prometheus (and `/alerts`) |
| http://localhost:8000/docs | API docs |

After pulling new code, rebuild: `docker compose up --build` (new Python packages were added for tracing).

Startup order is declared with health checks: redis + llm-stub -> `indexer` (one-shot ingest) -> api + worker.

### Use a real model instead of the stub
Edit `.env`, then recreate the containers (`docker compose up -d --build --force-recreate api worker`; a plain `restart` does not reread `.env`).
Tested setup with Groq's free tier:
```
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_...            # from console.groq.com/keys; never put it in .env.example
LLM_MODEL=qwen/qwen3.8-27b
LLM_JSON_MODE=true             # ask the provider for JSON replies
LLM_RATE_LIMIT_WAIT_S=65       # on a 429, wait as told instead of failing
MAX_RUN_SECONDS=900
PRICE_PROMPT_PER_1K=0.0008     # from the provider's price list, so cost tracking is real
PRICE_COMPLETION_PER_1K=0.004
```
Check it works: `docker compose exec worker python -m precheck.cli check-llm`, then
`docker compose exec worker python -m precheck.cli probe` (runs the main example once and shows the model's raw first reply,
whether it parses, and what each step did).

Notes from testing: `openai/gpt-oss-120b` returned empty replies to the research prompt, so Qwen is used instead. The free tier allows
about 7,000 input tokens a minute, and one check uses about 11,000, so a check takes around two minutes and the eval is slow: run it with
`--workers 1` and a subset (`--only s01,s02,s05`). Set a spending limit if you add billing.

### Use the real corpus
`corpus/` already holds the real rule text we have: Table 8 and the height notes from the Bengaluru Zonal Regulations Amendment 2025, plus extracts of the Karnataka Model Building Bye-Laws 2017. To add more (for example Table 9, Table 10 and parking norms, which we do not have), drop PDFs or text into `corpus/`, then:
```bash
docker compose run --rm indexer && docker compose restart api worker
```
PDF clause splitting is regex-based (`PDF_CLAUSE` in `precheck/index.py`): inspect the chunks in
`index/chunks.jsonl` and tune it. Tables that extract badly should be hand-converted to the `[ID] ...` text format
(one row per chunk, table title repeated) as in `corpus/Bengaluru_zoning_amendment_2025_and_bye-law_extracts.txt`. Scanned PDFs need OCR first.
**Not in the documents we have:** Table 9 (gaps above 12 m), Table 10 (floor area and coverage), parking norms, zones other than residential main. For those the system says "needs a person" or "not checked" instead of guessing.

## API

| Endpoint | Purpose |
|---|---|
| `POST /v1/runs` | Submit a proposal. Validates first; `202 {run_id}` or `422` + quarantine id. |
| `GET /v1/runs/{id}` | Poll status/result. |
| `GET /v1/runs/{id}/trace` | Every tool call, query, passage, prompt version, per-step latency and cost. |
| `POST /v1/runs/{id}/feedback` | `{verdict: satisfactory\|unsatisfactory, reason}` |
| `GET /v1/quality/summary` | Automatic quality scores of a sample of live runs (`SCORE_SAMPLE_RATE`). |
| `GET /v1/feedback/summary`, `/v1/quarantine`, `/v1/contract`, `/health`, `/metrics` | |

Try the section-4.1 case:
```bash
curl -s localhost:8000/v1/runs -H 'content-type: application/json' -d '{"plot_area_sqm":200,"road_width_m":5.5,
 "building_height_m":13.5,"num_floors":4,"built_up_area_sqm":400,"ground_coverage_pct":60,"parking_spaces":1,
 "stilt_height_m":0,"plot_depth_m":20,"plot_width_m":10,"front_setback_m":0.8,"rear_setback_m":1,
 "left_setback_m":1,"right_setback_m":1,"building_use":"residential","zone":"residential","notes":""}'
```

## Design

```
client -> api (validate, rate-limit, cache) -> redis queue -> worker --LangGraph--> result in redis
                \-> quarantine (rejects)            |  supervisor: classify -> research -> assess -> verify -> route
                                                    |  Research Agent: decide <-> act loop over tools
                                                    |  Assessor Agent: findings with verbatim evidence
                                                    |  Verifier (deterministic): citations resolve, quotes verbatim,
                                                    |    coverage (setbacks AND height-by-road-width) -> retry w/ gaps -> escalate
                                                    \-> degraded path on provider failure: retrieval-only, escalate
```
- **Tools** (the agent chooses): `search_corpus`, `get_clause`, `get_request_field`. Loop bounds: `MAX_STEPS`,
  `MAX_TOOL_CALLS`, `MAX_RUN_SECONDS`, `MAX_VERIFY_ATTEMPTS`.
- **Two agents**, hand-off = the set of retrieved passages. Assessor can only cite what the Research Agent retrieved.
  If either fails mid-run (provider down, breaker open, time budget gone) the supervisor goes to `degraded`:
  fixed retrieval from the request's own fields, `specialist-review`, no findings asserted.
- **Why this handles 4.1**: the verifier's coverage check demands a retrieved *and cited* provision that limits height by
  abutting road width. A single setback search will not return it, so verification fails with a gap message and the
  Research Agent searches again with a different strategy; if it still cannot, the run escalates rather than answers.
- **Chunking/embedding**: clause-level chunks, one chunk per table row with the table title repeated; local
  `sentence-transformers` embeddings cached by `sha1(model+text)` so a corpus change only embeds new chunks.
- **Guardrails**: injection patterns on notes (flagged notes are discarded and the run escalates), untrusted notes are
  delimiter-wrapped, toxicity screen, output filter blocks "compliant/approved/certified" language.
- **Why async**: runs take tens of seconds; 202 + poll decouples client latency from provider latency, and the queue lets
  workers scale independently.

## Evaluation
27 scenarios with an independent answer key (`eval/`). One command compares a one-search baseline with the agent loop:
```bash
docker compose --profile tools run --rm eval python -m eval.run --compare
```
Results go to `eval/results/latest.md`. See `eval/README.md` for what each metric means.

## Tests
```bash
pip install -r requirements.txt pytest fakeredis
python tests/test_pipeline.py      # pipeline: hashed embedder + stub LLM, no Docker or network
python tests/test_service.py       # API + queue + worker + tracing + scoring + eval smoke test
python tests/devserver.py          # whole app in one process on :8000 with no Docker (preview only)
```

## Tools for the demo and for operating it
```bash
docker compose exec worker python -m precheck.cli check-llm          # does the model in .env answer?
docker compose exec worker python -m precheck.cli stub-mode down     # stub only: break the AI on purpose (ok to undo)
docker compose exec worker python -m precheck.cli index-info         # when was the rulebook index built
```
New settings in `.env`: `WORKER_CONCURRENCY` (checks per worker at once), `SCORE_SAMPLE_RATE` (share of live runs scored).

## Load test (D6)
```bash
locust -f locustfile.py --host http://localhost:8000 --headless -u 10 -r 2 -t 3m --csv results/run
```
Target, prediction and measured results are in `docs/SLO.md`. No Docker? `python tests/loadrig.py --workers 3 --threads 1 --users 10 --seconds 180` runs the same test against a local rig (real Redis server, stub model). Raise `RATE_LIMIT_PER_MIN` for the test.

## Docs
`docs/SLO.md` (speed target), `docs/RUNBOOK.md` (what to do when it breaks), `docs/INCIDENTS.md` (real failure log),
`docs/DEMO_SCRIPT.md` (explaining it in plain words), `docs/REPORT.md` (the project report, with `docs/architecture.png`).

See `ROADMAP.md` for requirement status and what to do next.
