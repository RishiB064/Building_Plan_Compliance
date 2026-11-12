# Speed and reliability target (SLO)

> **This is a proposal, written so you can defend it. It is your decision. Change the numbers and the reasons in your
> own words before you run the load test, then keep it fixed.** The brief asks you to declare the target first.

## What "fast" means here

The clock starts when the service accepts a request (`POST /v1/runs` returns 202) and stops when the result is
stored. That is the metric `precheck_e2e_seconds`. It includes waiting in the queue, which is what a person feels.

## Proposed targets

| Target | Value | Why |
|---|---|---|
| Time to answer, p95 | **45 s or less** with 10 people checking at once and 3 workers | A check is about 7 AI calls in a row. With a real provider at 2-4 s per call that is 15-30 s alone. 45 s leaves room for a short queue. An architect's office will wait under a minute for a check that replaces days of back-and-forth. |
| Checks that finish (not crash) | **99% or more** | A crash returns no answer at all, so it is the worst failure. Bad input (422) and escalations to a person are not failures. |
| Answers that are grounded | **100%** of returned findings have a quote that is word for word in the rule | A wrong confident answer is worse than a slow one. Anything that fails this is escalated, never returned. |

## Where the numbers come from (prediction)

One check now = about 9 AI calls in sequence (7 rule searches + finish + assess; the table below was measured with the earlier 5-search version, 7 calls). With the stub at 1.5 s per call that is about
13.5 s of waiting (10.5 s when measured), plus about 1 s because the test client asks "is it done?" once a second. So one check takes about
11.5 s of one slot. A slot is one check running at a time (workers x `WORKER_CONCURRENCY`). With 10 people each starting
a new check as soon as the last one ends, the time to answer is about `10 / slots x 11.5 s`.

| Setup | Slots | Predicted time to answer |
|---|---|---|
| 1 worker x 1 thread | 1 | about 115 s |
| 3 workers x 1 thread | 3 | about 38 s |
| 1 worker x 4 threads | 4 | about 29 s |

So the 45 s target at 10 people needs at least 3 slots. The slow part is waiting for the AI, not our own code, so
more slots help until the CPU (search and embedding) becomes the limit.

## How to measure it

**With Docker** (the real thing, on the demo machine):
1. Point `.env` at the stub (`LLM_BASE_URL=http://llm-stub:8080/v1`) so you measure this system, not the provider.
2. Set `RATE_LIMIT_PER_MIN=1000` in `.env` (the limiter would otherwise correctly refuse the test traffic).
3. `docker compose up -d --scale worker=3` (or set `WORKER_CONCURRENCY=4` and `up -d --force-recreate worker`), then:
   `locust -f locustfile.py --host http://localhost:8000 --headless -u 10 -r 10 -t 180s --csv results/run`
4. Read the row `submit->result (end to end)` in `results/run_stats.csv`: the 95% column is the number to compare.
5. Watch Grafana (http://localhost:3000) while it runs: queue depth and "Time per step" show the bottleneck.

**Without Docker** (what produced the table below): `python tests/loadrig.py --workers 3 --threads 1 --users 10 --seconds 180`
starts a real Redis server, the stub model, the API and the worker processes on one machine, then runs the same Locust file.

## Measured results

**Note:** measured before the move to the real rule text, which added two searches per check. Expect roughly 25% longer times; re-run `tests/loadrig.py` for exact figures.

Measured 2026-11-10 on a 2-core cloud machine, **no Docker**, stub model at 1.5 s per call, hashed embedder (not the real
embedding model), 10 simulated people, 180 s per setup, different numbers in every request (so the repeat-answer cache did not help).
Results files: `results/*_stats.csv`.

| Setup | Slots | Checks finished | Failures | p50 | p95 | Meets 45 s target? |
|---|---|---|---|---|---|---|
| 1 worker x 1 thread | 1 | 20 | 0 | 75 s | 108 s | No |
| 3 workers x 1 thread | 3 | 70 | 0 | 31 s | 41 s | Yes, with little room |
| 1 worker x 4 threads | 4 | 118 | 0 | 20 s | 31 s | Yes |

What this shows:
- Throughput grows almost in line with slots (20, 70, 118 checks), and the times match the prediction above, so the model
  of "waiting for the AI is the bottleneck" holds on this rig.
- Threads inside one worker did as well as more worker processes, and use less memory (one copy of the index and model).
  Scaling with `--scale worker=3` also works, and isolates crashes better.
- The 3-worker p95 of 41 s is close to the 45 s target. A slow real provider would break it, so with a real model re-measure
  and add slots (`WORKER_CONCURRENCY`) until p95 is back under target.
- The first, 1-slot setup is the "before": a queue builds up and the people at the back wait about 2 minutes.

Not covered here: the real embedding model's CPU cost, real provider latency and rate limits, and Docker overhead.
**Re-run the table on your machine with Docker before quoting it as final.** Keep this target fixed; do not change it
after seeing the results.

## Failure budget

99% of checks must finish. All 208 checks in this table finished with 0 failures. A bad request (422), an escalation to a
person, and a degraded answer while the AI is down are by design and are not counted as failures.
