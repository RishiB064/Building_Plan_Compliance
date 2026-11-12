# Runbook

For whoever is on call. Each section starts with what you see, then what to check, then how to fix it.

## The basics

| Thing | Where |
|---|---|
| The app | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Dashboard (per-agent view) | http://localhost:3000 |
| Traces (one request, step by step) | http://localhost:16686 (pick service `api`, then Find Traces) |
| Metrics and alerts | http://localhost:9090 and http://localhost:9090/alerts |
| Logs | `docker compose logs -f api worker` (JSON, one line per event, each has a `correlation_id`) |
| One run in full | `GET /v1/runs/<run_id>/trace` |
| Status of everything | `docker compose ps` |

Start: `docker compose up -d`. Stop: `docker compose down`. More workers: `docker compose up -d --scale worker=3`.
More runs per worker: set `WORKER_CONCURRENCY=4` in `.env`, then `docker compose up -d --force-recreate worker`.

## Worker keeps restarting or is down

*You see:* `docker compose ps` shows the worker `Restarting`; Grafana "Workers running" is 0 or flickers; alert `WorkerFlapping`.
1. `docker compose logs worker --tail 50`. Read the last traceback.
2. `redis.exceptions.TimeoutError` right after idle time: this is INC-001. Check `precheck/worker.py` still creates the client with `socket_timeout=30`.
3. `FileNotFoundError ... meta.json` or `index`: the index does not exist. Run `docker compose run --rm indexer`, then `docker compose restart worker`.
4. Out-of-memory (`Killed`, exit code 137): the embedding model and index live in each worker. Give Docker more memory, or run fewer workers.
5. After fixing: `docker compose up -d worker` and watch `docker compose logs -f worker` for `worker ready` followed by no traceback for 30 s.

## Checks stay queued

*You see:* the web page says "waiting in line"; Grafana "Jobs waiting in line" keeps rising; alert `QueueGrowing`.
1. Is a worker running? `docker compose ps worker`. If not, see the section above.
2. Workers busy? Add capacity: `--scale worker=3` and/or `WORKER_CONCURRENCY=4`.
3. Is the AI slow? Grafana "Time per step": long `decide` or `assess` bars mean the provider is slow, not us.
4. Someone flooding the API? `RATE_LIMIT_PER_MIN` (default 10 per client) should stop one client; check Grafana "Turned away at the door".

## Checks stay running

*You see:* a run stays `running` for several minutes (the run limit is `MAX_RUN_SECONDS`, 90 s). Cause: its worker was killed
mid-run. Known gap (see INC-001 follow-ups).
1. Find it: `docker compose exec redis redis-cli HGET run:<run_id> status`.
2. Re-queue it: `docker compose exec redis redis-cli RPUSH queue:runs <run_id>`. The input is still stored on the run.

## Checks fail

*You see:* alert `ChecksFailing`; results with `status: failed`; the web page says the check did not finish.
1. `docker compose logs worker | grep '"run failed"'`: the traceback is in the log line.
2. Get the run's `correlation_id` from `GET /v1/runs/<id>/trace` and search the logs for it.
3. A failed run never returns findings: it is stored as an escalation. Re-submit after the fix.

## Everything is degraded, or sent to a person

*You see:* the amber "A person needs to look at this" on every check; reason says the AI service was not available;
Grafana "AI service cut off?" is red; alert `AIProviderCutOff`.
1. `docker compose exec worker python -m precheck.cli check-llm` calls the configured model once and prints the error.
2. Wrong `LLM_BASE_URL`, `LLM_API_KEY` or `LLM_MODEL` in `.env`? Fix, then `docker compose up -d --force-recreate worker`.
3. Provider rate-limiting you (429)? Lower `WORKER_CONCURRENCY` and the number of workers.
4. The breaker closes by itself `BREAKER_RESET_S` (20 s) after the provider recovers.
5. With the stub: someone ran `stub-mode down`. Undo with `docker compose exec worker python -m precheck.cli stub-mode ok`.

## Answers look wrong, or everything escalates

*You see:* many "double-check failed" escalations; sampled quality score under 0.9 (alert `QualityDropping`); users clicking "No".
1. Run the evaluation: `docker compose --profile tools run --rm eval`. Compare with `eval/results/latest.md`.
2. Open a bad run's trace: which rules were retrieved? A missing rule means retrieval, not the model.
3. Did the rulebook change? Re-index: `docker compose run --rm indexer && docker compose restart api worker`.
4. Did a prompt change? Roll back: set `PROMPT_VERSION=v1` in `.env`, recreate api and worker. Cached answers are keyed by prompt version, so old answers are not reused.
5. `GET /v1/feedback/summary` lists recent reasons people gave.

## Checks are slow

*You see:* alert `SlowChecks` (p95 over 45 s, see `docs/SLO.md`).
1. Grafana "Jobs waiting in line" high: capacity problem, see "Checks stay queued".
2. Queue empty but steps are slow: the provider is slow. Check the `llm.*` spans in Jaeger for the long ones.
3. Many retries (Grafana "Model call retries"): provider errors. See the degraded section.

## Rulebook is out of date or missing

1. `docker compose exec worker python -m precheck.cli index-info` shows when it was built and which files.
2. Rebuild: `docker compose run --rm indexer && docker compose restart api worker`. Unchanged text is not embedded again (cache), so it is quick.
3. `/health` also reports the index build time and version.

## Redis is down

*You see:* `/health` returns 503; the web page cannot start checks.
1. `docker compose restart redis`, then `docker compose up -d`.
2. Redis here has no disk persistence (`--save ""`): queued and finished runs are lost on restart. That is fine for a demo; for real use turn on `--appendonly yes`.

## People get "too many checks in a minute"

That is the per-client limit (`RATE_LIMIT_PER_MIN`). Raise it in `.env` for load tests only.
