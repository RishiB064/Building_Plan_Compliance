# Requirement status

Legend: **DONE** = built and covered by automatic tests. **RUN ME** = built and tested without Docker, but needs your
Docker run to confirm. **YOU** = needs something only you can do (real data, real model, measurements).
Tests: `tests/test_pipeline.py` and `tests/test_service.py` (all pass). Evaluation: `eval/results/latest.md`.

| Req | Status | Where / note |
|---|---|---|
| F1 REST + data contract | DONE | `api.py`, `contracts.py`, `GET /v1/contract` |
| F2 validation + quarantine | DONE | tests; plain-English errors in the UI |
| F3 classify + entities | DONE (form fields) | does not parse entities out of free-text notes |
| F4 retrieve all binding provisions | DONE on the real Table 8 and height notes | **YOU:** check retrieval with the real MiniLM model (the offline eval uses a simple stand-in) |
| F5 cited findings | DONE | verifier checks quotes are word for word |
| F6 routing with reason | DONE | `n_route` returns a reason code and a plain-English summary |
| F7 escalate | DONE | conflict, exemption, out-of-corpus, failed check, injection, AI down |
| F8 health/metrics/logs | DONE | `/health`, `/metrics`, JSON logs with correlation id |
| A1 LangGraph, 3 tools, bounded | DONE | `agents.py`, `tools.py` |
| A2 two agents + failure doc | DONE | README design section |
| A3 verify before return | DONE | unverified results return no findings |
| A4 async FastAPI + queue | DONE | Redis list, worker threads (`WORKER_CONCURRENCY`) |
| A5 timeouts/backoff/breaker/degrade | DONE | demo: `precheck.cli stub-mode down` |
| A6 per-agent dashboard | RUN ME | Grafana provisioned, 30 panels, `grafana/dashboards/precheck.json` |
| A7 --scale + measurement | DONE on a local rig, **YOU:** repeat with Docker | 1 vs 3 vs 4 slots measured (p95 108 s, 41 s, 31 s); `docs/SLO.md`, `tests/loadrig.py` |
| A8 web client | DONE | `web/index.html`, plain-language, live progress |
| L1 LangChain + FAISS | DONE | `index.py` |
| L2 versioned prompts, compare 2 | YOU | harness ready (`PROMPT_VERSION`, eval); the stub ignores prompt wording, so compare with a real model |
| L3 eval set (20+), metrics, before/after | DONE on real regulation text | 35 scenarios; baseline 16/35 vs agent loop 35/35. Metrics are computed in plain code, **TruLens itself is not installed**. **YOU:** rerun with a real model and real bye-laws |
| L4 guardrails | DONE (heuristic) | `guardrails.py` |
| L5 token/cost per step | DONE | Grafana "Cost per check (USD)" |
| L6 feedback + sampled scoring | DONE | feedback buttons; `/v1/quality/summary`; Grafana "Automatic quality score of sampled live checks" |
| D1 contract + quarantine | DONE | |
| D2 idempotent ingest + freshness | DONE | `precheck.cli ingest`, `index-info` |
| D3 4+ services, health checks | RUN ME | 9 services; justification in compose header |
| D4 no credentials in repo | DONE | `.env.example` |
| D5 OTel/Jaeger/Grafana/correlation | RUN ME | trace spans through the queue (tested); Jaeger and Grafana containers need your run |
| D6 SLO + Locust | DONE on a local rig, **YOU:** confirm the target is yours and repeat with Docker | target declared before measuring; measured table in `docs/SLO.md` |
| D7 caching + rate limit | DONE | measure the cache effect in the load test |
| D8 runbook + incident log | DONE | `docs/RUNBOOK.md`; `docs/INCIDENTS.md` has one real incident (INC-001) |

## Next, in order
1. `docker compose up --build`. Confirm Grafana (:3000) and Jaeger (:16686) show data after a few checks; fix anything that breaks.
2. `docker compose --profile tools run --rm eval python -m eval.run --compare`; keep `eval/results/latest.md` for your report.
3. Repeat the three Locust setups in `docs/SLO.md` with Docker (the table there was measured on a local rig without Docker).
4. Put the real bye-law text in `corpus/`, rewrite `eval/reference.py` from the real clauses, rerun the eval.
5. Plug in a real model (`.env`), run the eval with `PROMPT_VERSION=v1` then `v2`, and write down the difference.
6. Read `docs/REPORT.md`, fill the two blanks (prompt v1 vs v2, cost per 100 runs), put in your names, and practise `docs/DEMO_SCRIPT.md`.
