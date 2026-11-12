# Incident log

One entry per real failure. Facts below come from the actual logs of this project; edit the wording to be yours.

## INC-001: worker crash loop after the first start (2026-11-05)

**What happened.** After `docker compose up --build` everything started, the API was healthy and the worker printed
`worker ready`, but the worker container kept exiting with code 1 and restarting about every 10 seconds
(`redis.exceptions.TimeoutError: Timeout reading from socket`, raised inside `blpop`).

**Impact.** Submitted checks would sit in the queue or be picked up only in the few seconds a worker was alive.
A job popped from the queue just before a crash would be lost (see follow-ups). Nobody was harmed: this was
found during the first local run, before any demo.

**How it was found.** By reading `docker compose logs`. No alert existed.

**Root cause.** The worker waits for new jobs with `BLPOP ... timeout=5` (block up to 5 seconds). The Redis client
library (redis-py) sets a default socket timeout of 5 seconds. An idle worker waited exactly as long as the socket
was allowed to, so the read timed out at the same moment and the exception was not caught. `requirements.txt` only
said `redis>=5.0`, so a newer version with that default was installed.

**Fix.** `precheck/worker.py`: create the client with `socket_timeout=30` (must be longer than the 5 s wait), plus
`socket_connect_timeout=5` and `health_check_interval=15`. The loop now also catches Redis errors, logs them and
retries after 1 s instead of exiting. Verified: rebuilt the worker; it stayed `healthy` and a real check completed
(2026-11-05, about 04:20 UTC).

**Regression test.** `tests/test_service.py::test_worker_redis_timeout_regression` fails if the socket timeout is
ever not longer than the 5 s wait.

**Related bug found while writing that test.** A corrupted job in Redis (unparseable JSON) raised before the
worker's error handling and would also have crashed it. The parse is now inside the `try`, the run is marked
`failed` with an escalation result, and the worker carries on (`test_worker_marks_crashed_run_as_failed_with_safe_result`).

**Follow-ups.**
- [x] Prometheus alert `WorkerFlapping` and `WorkerUnreachable` (`prometheus/alerts.yml`), Grafana tile "Workers running".
- [ ] Not done: the queue is a plain Redis list, so a job that was popped and then loses its worker stays `running`
  forever. A reliable queue (move the job to a "processing" list and re-queue stale ones) would fix it. The manual
  workaround is in the runbook under "Checks stay running".
- [ ] Pin `redis` to a tested version in `requirements.txt`.

---

## Template for the next one

**What happened / Impact / How it was found / Root cause / Fix / Regression test / Follow-ups**
